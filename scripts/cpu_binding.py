"""Single-node Open MPI 5 physical-core plans and independent affinity audit.

Rankfile slots are hwloc logical Core indices, never Linux CPU numbers. No
affinity is changed inside a rank. The observer uses /proc only, without CPU
tick sampling, and checks every observed task after exec.
"""
from __future__ import annotations
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests/benchmark'))
from cpu_sampling import capacity, cpu_set, host_context, parse_stat, sample, save


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''): h.update(b)
    return h.hexdigest()


def limit_values(cap):
    return {a['path']:{k:a[k] for k in ('cpu.max','cpuset.cpus.effective')} for a in cap['ancestors']}


def hwloc_xml():
    lib = ctypes.CDLL(ctypes.util.find_library('hwloc'))
    topology = ctypes.c_void_p()
    if lib.hwloc_topology_init(ctypes.byref(topology)) or lib.hwloc_topology_load(topology):
        raise RuntimeError('hwloc topology load failed')
    buf, length = ctypes.c_void_p(), ctypes.c_int()
    try:
        if lib.hwloc_topology_export_xmlbuffer(topology, ctypes.byref(buf), ctypes.byref(length), 0):
            raise RuntimeError('hwloc XML export failed')
        return ctypes.string_at(buf, length.value).rstrip(b'\0').decode()
    finally:
        if buf.value: lib.hwloc_free_xmlbuffer(topology, buf)
        lib.hwloc_topology_destroy(topology)


def cores_from_xml(xml):
    # hwloc XML preserves logical object order; Core os_index may repeat per socket.
    objects = ET.fromstring(xml).findall('.//object[@type="Core"]')
    result = []
    for logical, obj in enumerate(objects):
        cpus = sorted(int(p.attrib['os_index']) for p in obj.findall('.//object[@type="PU"]'))
        if not cpus: raise ValueError('hwloc Core without processing units')
        cpu = Path('/sys/devices/system/cpu') / f'cpu{cpus[0]}'
        result.append(dict(logical_core=logical, os_cpus=cpus,
                           core_id=int((cpu / 'topology/core_id').read_text()),
                           socket=int((cpu / 'topology/physical_package_id').read_text()),
                           numa=[int(n.name[4:]) for n in cpu.glob('node[0-9]*')]))
    if not result: raise ValueError('no hwloc physical cores')
    return result


def reject_conflicts(arguments, environment=None):
    forbidden = ('--map-by', '--bind-to', '--rank-by', '--mapby', '--bindto', '--rankby',
                 '-map-by', '-bind-to', '-rank-by', '--rankfile', '-rf', '--cpu-set',
                 '--cpu-list', '--cpus-per', '--oversubscribe', '--nooversubscribe',
                 '--use-hwthread-cpus', '--bind-to-core', '--bind-to-socket', '--bynode',
                 '--bycore', '--byslot', '--host', '-H', '--machinefile', '--app', '--tune',
                 '--np', '-np', '-n', '--n', '--wdir', '--wd')
    for arg in arguments:
        key=arg.lower()
        normalized=key.replace('_','').replace('-','')
        if key == '--report-bindings': continue
        if any(key == f or key.startswith(f + '=') or (f == '--cpus-per' and key.startswith(f))
               for f in forbidden):
            raise ValueError(f'fixed CPU policy conflicts with launcher argument: {arg}')
        if any(s in normalized for s in ('rmaps','hwlocbase','ranking','binding','oversub','rankfile','mapby','bindto','rankby','cpusper','cpuset','cpulist')):
            raise ValueError(f'fixed CPU policy conflicts with MCA override: {arg}')
    for k in (environment or os.environ):
        if k.startswith(('OMPI_MCA_', 'PRTE_MCA_')) and any(
                s in k.lower().replace('_','').replace('-','') for s in ('rmaps','hwloc','ranking','binding','oversub','rankfile','mapby','bindto','rankby','cpusper','cpuset','cpulist')):
            raise ValueError(f'fixed CPU policy conflicts with inherited {k}')


def allocate(cores, allowed, ranks, k, placement='core', nodes=None, explicit=None, quota=None):
    if ranks < 1 or k < 1: raise ValueError('positive ranks and physical cores required')
    if quota is not None and quota + 1e-9 < ranks * k:
        raise ValueError(f'cgroup quota {quota} < P*K={ranks*k}')
    # Require complete sibling sets: Open MPI core binding must not escape cpuset.
    available = {c['logical_core']: c for c in cores if set(c['os_cpus']) <= set(allowed)}
    selected, used = [], set()
    for rank in range(ranks):
        candidates = [i for i, c in available.items() if i not in used and
                      (not nodes or c['numa'] == [nodes[rank % len(nodes)]])]
        if explicit is not None:
            ids = explicit[rank]
        elif placement == 'numa':
            node_ids = sorted({n for i in candidates for n in available[i]['numa']})
            if not node_ids: raise ValueError('NUMA placement lacks topology/capacity')
            preferred = nodes[rank % len(nodes)] if nodes else node_ids[rank % len(node_ids)]
            ids = [i for i in candidates if available[i]['numa'] == [preferred]][:k]
        else:
            ids = candidates[:k]
        if len(ids) != k or len(set(ids)) != k or any(i not in available or i in used for i in ids):
            raise ValueError(f'rank {rank}: insufficient/unavailable/shared physical cores')
        if nodes and any(available[i]['numa'] != [nodes[rank % len(nodes)]] for i in ids):
            raise ValueError('explicit rankfile conflicts with requested NUMA nodes')
        if placement == 'numa' and len({tuple(available[i]['numa']) for i in ids}) != 1:
            raise ValueError('rank spans NUMA nodes')
        used.update(ids)
        selected.append(dict(rank=rank, logical_cores=ids,
                             os_cpus=sorted(p for i in ids for p in available[i]['os_cpus']),
                             cores=[available[i] for i in ids]))
    return selected


def parse_rankfile(text, ranks):
    entries = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        m = re.fullmatch(r'\s*rank\s+(\d+)=(\S+)\s+slot=([\d,\-]+)\s*', line)
        if not m or m[2] not in ('+n0', 'localhost', socket.gethostname()):
            raise ValueError('rankfile requires local host and global logical Core indices')
        rank = int(m[1])
        if rank in entries: raise ValueError('duplicate rank in rankfile')
        # Reject duplicate slot tokens too (cpu_set normalizes them).
        slots = []
        for token in m[3].split(','):
            a = token.split('-'); slots.extend(range(int(a[0]), int(a[-1])+1))
        if len(slots) != len(set(slots)): raise ValueError('duplicate rankfile core')
        entries[rank] = slots
    if set(entries) != set(range(ranks)): raise ValueError('rankfile missing/extra rank')
    return [entries[r] for r in range(ranks)]


def resolve(ranks, k, placement='core', rankfile=None, nodes=None, arguments=()):
    reject_conflicts(arguments)
    xml = hwloc_xml(); cores = cores_from_xml(xml)
    own = sample(os.getpid()); cap = capacity(os.getpid(), [own])
    if cap['status'] != 'ok': raise ValueError(f'cannot verify resource limits: {cap}')
    scheduler = {k: v for k, v in os.environ.items() if k.startswith(('SLURM_', 'PBS_', 'LSB_'))}
    # Single-node cpuset-enforced scheduler allocations are supported. Refuse ambiguous
    # allocations without a CPU envelope, or a declared CPU budget smaller than P*K.
    if scheduler:
        if int(os.environ.get('SLURM_JOB_NUM_NODES', '1')) != 1:
            raise ValueError('only single-node binding is supported')
        for key in ('SLURM_CPUS_ON_NODE', 'PBS_NP', 'LSB_DJOB_NUMPROC'):
            if key in scheduler and int(scheduler[key]) < ranks*k:
                raise ValueError(f'{key} < P*K')
        if len(cap['allowed_cpus']) == len(cpu_set(Path('/sys/devices/system/cpu/online').read_text())):
            raise ValueError('scheduler allocation has no enforced CPU envelope; require cpuset/taskset')
    text = Path(rankfile).read_text() if rankfile else None
    if (placement == 'rankfile') != bool(rankfile): raise ValueError('rankfile placement requires --cpu-rankfile only')
    selected = allocate(cores, cap['allowed_cpus'], ranks, k, placement, nodes,
                        parse_rankfile(text, ranks) if text else None, cap['quota_cores'])
    content = ''.join(f'rank {r["rank"]}=localhost slot={",".join(map(str,r["logical_cores"]))}\n' for r in selected)
    return dict(schema_version=1, host=host_context(), ranks=ranks, cores_per_rank=k,
                placement=placement, requested_nodes=nodes, sharing=False, topology=cores,
                hwloc_xml=xml, capacity=cap, scheduler=scheduler, mapping=selected,
                rankfile_content=content, rankfile_sha256=hashlib.sha256(content.encode()).hexdigest(),
                source_rankfile=text, memory_policy='inherited; CPU affinity does not bind pages')


class Audit:
    def __init__(self, directory, plan, candidate):
        self.directory = Path(directory).resolve(); self.directory.mkdir(parents=True,exist_ok=False)
        self.plan = plan
        save(self.directory/'resolved.json', plan)
        (self.directory/'rankfile').write_text(plan['rankfile_content'])
        # System PMIx pmix_check_cli_option splits even option *values* at '-'.
        # A short hyphen-free alias preserves the exact rankfile, bypassing that
        # parser defect without changing mapping, affinity or the MPI stack.
        self.launch_rankfile = Path(tempfile.mkdtemp(prefix='dmgmd_binding_',dir='/tmp'))/'rankfile'
        self.launch_rankfile.symlink_to(self.directory/'rankfile')
        save(self.directory/'launcher-rankfile.json',dict(path=str(self.launch_rankfile),
                                                        sha256=plan['rankfile_sha256']))
        self.candidate = str(Path(candidate).resolve())
        self.binary_hash = digest(candidate)
        self.file = (self.directory/'observations.jsonl').open('x')
        self.resource_file = (self.directory/'resources.jsonl').open('x')
        self.next_resource_check = 0.
        current=capacity(os.getpid(),[sample(os.getpid())])
        if current['status']!='ok' or limit_values(current)!=limit_values(plan['capacity']):
            raise ValueError('CPU resource limits changed since plan resolution')
        self.seen = set(); self.errors = []; self.processes = {}
        self.counts = {r: 0 for r in range(plan['ranks'])}

    def mpi_args(self):
        return ['--map-by', f'rankfile:file={self.launch_rankfile}', '--bind-to', 'core', '--nooversubscribe']

    def wrapper(self, command):
        return [sys.executable, str(Path(__file__).resolve()), 'wrap', str(self.directory), '--', *command]

    def poll(self):
        if time.monotonic()>=self.next_resource_check:
            limits={};stats={}
            for path in limit_values(self.plan['capacity']):
                limits[path]={}
                for key in ('cpu.max','cpuset.cpus.effective','cpu.stat'):
                    try:value=(Path(path)/key).read_text().strip()
                    except FileNotFoundError:value=None
                    except OSError as e:value='UNREADABLE:'+str(e)
                    if key=='cpu.stat':stats[path]=value
                    else:limits[path][key]=value
            expected={p:{k:v.strip() if v is not None else None for k,v in values.items()}
                      for p,values in limit_values(self.plan['capacity']).items()}
            if limits!=expected:self.errors.append('visible cgroup CPU limits changed during trial')
            self.resource_file.write(json.dumps(dict(t_ns=time.monotonic_ns(),limits=limits,cpu_stat=stats))+'\n')
            self.resource_file.flush();self.next_resource_check=time.monotonic()+2
        for rank in range(self.plan['ranks']):
            reg = self.directory/f'rank-{rank}.json'
            if not reg.exists(): continue
            rec = json.loads(reg.read_text()); pid = rec['pid']
            try:
                if os.readlink(f'/proc/{pid}/exe') != self.candidate: continue
                st = parse_stat(Path(f'/proc/{pid}/stat').read_text())
                if st['state'] == 'Z' or st['start_ticks'] != rec['start_ticks']: continue
                if rank not in self.seen:
                    if digest(f'/proc/{pid}/exe') != self.binary_hash: raise ValueError('exec binary mismatch')
                    self.seen.add(rank); self.processes[rank] = rec
                if Path(f'/proc/{pid}/cgroup').read_text()!=self.plan['capacity']['raw_cgroup']:
                    self.errors.append('rank cgroup differs from resolved single-node allocation')
                tasks = []
                for t in Path(f'/proc/{pid}/task').iterdir():
                    try: cpus = sorted(os.sched_getaffinity(int(t.name)))
                    except ProcessLookupError: continue
                    tasks.append(dict(tid=int(t.name), os_cpus=cpus))
                    if cpus != self.plan['mapping'][rank]['os_cpus']:
                        self.errors.append(f'rank {rank} task {t.name}: affinity {cpus}')
                self.file.write(json.dumps(dict(rank=rank,pid=pid,start_ticks=st['start_ticks'],
                                               t_ns=time.monotonic_ns(),tasks=tasks))+'\n')
                self.file.flush(); self.counts[rank] += 1
            except (FileNotFoundError, ProcessLookupError): continue

    def finish(self, stdout, minimum=2, gpu_check=True):
        self.poll()
        if digest(self.directory/'rankfile')!=self.plan['rankfile_sha256'] or digest(self.launch_rankfile)!=self.plan['rankfile_sha256']:
            self.errors.append('rankfile content/alias changed during trial')
        self.close()
        if self.seen != set(range(self.plan['ranks'])) or min(self.counts.values()) < minimum:
            self.errors.append('insufficient post-exec observations')
        gpu = []
        if gpu_check:
            for line in stdout.splitlines():
                if line.startswith('DMGMD_MPI rank='):
                    gpu.append(dict(t.split('=',1) for t in line.split()[1:] if '=' in t))
            if len(gpu) != self.plan['ranks'] or len({g['cuda_uuid'] for g in gpu}) != self.plan['ranks']:
                self.errors.append('missing/duplicate runtime rank GPU mappings')
            if {int(g['rank']) for g in gpu} != set(range(self.plan['ranks'])) or any(
                    g.get('local_rank') != g['rank'] or g.get('world_size') != str(self.plan['ranks']) or
                    g.get('local_size') != str(self.plan['ranks']) or g.get('hostname') != socket.gethostname() for g in gpu):
                self.errors.append('runtime rank/size/host differs from launcher registration')
        result = dict(status='INVALID' if self.errors else 'PASS', errors=self.errors,
                      observations=self.counts, processes=self.processes, gpu=gpu,
                      memory_residency='UNKNOWN', clock_id='CLOCK_MONOTONIC')
        save(self.directory/'verification.json', result)
        if self.errors: raise ValueError('; '.join(self.errors[:5]))
        return result

    def close(self):
        self.file.close();self.resource_file.close()


def wrap(directory, command):
    directory = Path(directory); plan = json.loads((directory/'resolved.json').read_text())
    rank = int(os.environ['OMPI_COMM_WORLD_RANK']); size = int(os.environ['OMPI_COMM_WORLD_SIZE'])
    if (host_context() != plan['host'] or size != plan['ranks'] or
            int(os.environ['OMPI_COMM_WORLD_LOCAL_SIZE']) != size or
            int(os.environ['OMPI_COMM_WORLD_LOCAL_RANK']) != rank):
        raise ValueError('single-node rank/namespace mismatch')
    if sorted(os.sched_getaffinity(0)) != plan['mapping'][rank]['os_cpus']:
        raise ValueError('launcher did not apply resolved CPU binding')
    rec = sample(os.getpid())
    rec.update(rank=rank, local_rank=rank, hostname=socket.gethostname())
    # libnuma/get_mempolicy may be denied by a container; keep raw result, not a claim.
    mem = subprocess.run(['numactl','--show'],capture_output=True,text=True) if __import__('shutil').which('numactl') else None
    rec['memory_policy'] = dict(stdout=mem.stdout,stderr=mem.stderr,returncode=mem.returncode) if mem else None
    save(directory/f'rank-{rank}.json', rec)
    os.execv(command[0],command)


if __name__ == '__main__':
    if sys.argv[1] != 'wrap': raise SystemExit('internal wrap entry only')
    wrap(sys.argv[2], sys.argv[4:])
