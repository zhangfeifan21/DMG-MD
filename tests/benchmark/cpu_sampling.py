"""Linux /proc sampler, exec wrapper and benchmark lifecycle (schema v1).

No MPI/CUDA imports. Sampling is a separate process; default benchmark path does
not instantiate it. Identity always includes process start ticks and namespaces.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
SCHEMA = 1


def save(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def parse_stat(raw):
    end = raw.rindex(')')
    fields = raw[end + 1:].split()  # starts at stat field 3
    return dict(comm=raw[raw.index('(') + 1:end], state=fields[0],
                utime_ticks=int(fields[11]), stime_ticks=int(fields[12]),
                num_threads=int(fields[17]), start_ticks=int(fields[19]),
                last_cpu=int(fields[36]))


def status_fields(raw):
    return dict(line.split(':', 1) for line in raw.splitlines() if ':' in line)


def cpu_set(raw):
    result = set()
    for item in raw.strip().split(','):
        if item:
            pair = item.split('-')
            result.update(range(int(pair[0]), int(pair[-1]) + 1))
    return sorted(result)


def host_context():
    return dict(hostname=socket.gethostname(),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                namespaces={n: os.readlink('/proc/self/ns/' + n)
                            for n in ('pid', 'mnt', 'time', 'cgroup')})


def envelope(config, kind, **values):
    ctx = config['host']
    return dict(schema_version=SCHEMA, type=kind, trial_id=config['trial_id'],
                host_id=ctx['hostname'] + ':' + ctx['boot_id'],
                clock_domain_id=ctx['boot_id'] + ':' + ctx['namespaces']['time'],
                clock_id='CLOCK_MONOTONIC', source='linux_proc', status='ok', reason=None,
                **values)


def sample(pid, tid=None):
    begin = time.monotonic_ns()
    base = Path(f'/proc/{pid}') if tid is None else Path(f'/proc/{pid}/task/{tid}')
    try:
        raw_stat = (base / 'stat').read_text()
        raw_status = (base / 'status').read_text()
        values = parse_stat(raw_stat)
        sf = status_fields(raw_status)
        values.update(raw_stat=raw_stat, raw_status=raw_status,
                      affinity=sorted(os.sched_getaffinity(pid if tid is None else tid)),
                      voluntary_ctxt_switches=int(sf['voluntary_ctxt_switches']),
                      nonvoluntary_ctxt_switches=int(sf['nonvoluntary_ctxt_switches']),
                      nspid=[int(v) for v in sf.get('NSpid', '').split()],
                      status='ok', reason=None)
        if parse_stat((base / 'stat').read_text())['start_ticks'] != values['start_ticks']:
            raise ValueError('identity changed during read')
    except (OSError, ValueError, KeyError) as error:
        values = dict(status='unavailable', reason=f'{type(error).__name__}: {error}')
    end = time.monotonic_ns()
    return dict(values, pid=pid, tid=tid, t_begin_ns=begin, t_end_ns=end,
                t_ns=(begin + end) // 2, clk_tck=os.sysconf('SC_CLK_TCK'))


def read_optional(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def unescape_mount(text):
    import re
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), text)


def capacity(pid, tasks, host_verified=False):
    """Keep the complete visible cgroup chain, not just a container's leaf quota."""
    begin = time.monotonic_ns()
    result = dict(status='unavailable', reason='cgroup_v2_not_visible', hierarchy_complete=False,
                  shared_with_other_processes='unknown', ancestors=[], allowed_cpus=None)
    try:
        cg = Path(f'/proc/{pid}/cgroup').read_text()
        mounts = Path(f'/proc/{pid}/mountinfo').read_text()
        result.update(raw_cgroup=cg, raw_mountinfo=mounts)
        path = next(line.split(':', 2)[2] for line in cg.splitlines() if line.startswith('0::'))
        if '..' in Path(path).parts:
            raise ValueError('cgroup path escapes visible namespace')
        choices = []
        for line in mounts.splitlines():
            left, right = line.split(' - ', 1)
            if right.split()[0] == 'cgroup2':
                f = left.split()
                root, mount = unescape_mount(f[3]), unescape_mount(f[4])
                if path == root or path.startswith(root.rstrip('/') + '/'):
                    choices.append((root, mount))
        root, mount = max(choices, key=lambda v: len(v[0]))
        base = Path(mount)
        leaf = base / path[len(root):].lstrip('/')
        result.update(cgroup_path=path, mount_root=root, mount_point=mount,
                      capacity_id=f'{os.stat(base).st_dev}:{path}')
        node = leaf
        while True:
            values = {name: read_optional(node / name) for name in
                      ('cpu.max', 'cpu.weight', 'cpu.stat', 'cpuset.cpus.effective')}
            result['ancestors'].append(dict(path=str(node), **values))
            if node == base:
                break
            node = node.parent
        usable = [t for t in tasks if t['status'] == 'ok']
        cpuset = result['ancestors'][0]['cpuset.cpus.effective']
        online = read_optional('/sys/devices/system/cpu/online')
        if len(usable) != len(tasks) or not usable or cpuset is None or online is None:
            raise ValueError('incomplete task affinity/cpuset snapshot')
        allowed = set().union(*(set(t['affinity']) for t in usable))
        allowed &= set(cpu_set(cpuset)) & set(cpu_set(online))
        quotas = []
        for a in result['ancestors']:
            if a['cpu.max'] is None:
                if a['path'] != str(base):
                    raise ValueError('unreadable ancestor cpu.max')
                continue  # cgroup root need not expose cpu.max
            q, period = a['cpu.max'].split()
            if q != 'max':
                quotas.append(int(q) / int(period))
        complete = host_verified and root == '/'
        result.update(status='ok', reason=None if complete else 'unverified_hidden_ancestors',
                      allowed_cpus=sorted(allowed), quota_cores=min(quotas) if quotas else None,
                      capacity_upper_bound=min([len(allowed), *quotas]),
                      hierarchy_complete=complete)
    except (OSError, ValueError, StopIteration) as error:
        result['reason'] = str(error)
    result.update(t_begin_ns=begin, t_end_ns=time.monotonic_ns())
    return result


def wrap(config_file, command):
    config = json.loads(Path(config_file).read_text())
    ctx = host_context()
    if ctx != config['host']:
        raise RuntimeError('unsupported_multi_node_or_namespace: wrapper differs from sampler')
    rank = int(os.environ['OMPI_COMM_WORLD_RANK'])
    local_rank = int(os.environ['OMPI_COMM_WORLD_LOCAL_RANK'])
    size = int(os.environ['OMPI_COMM_WORLD_SIZE'])
    local_size = int(os.environ['OMPI_COMM_WORLD_LOCAL_SIZE'])
    if size != config['ranks'] or local_size != size or local_rank != rank:
        raise RuntimeError('unsupported_multi_node_or_rank_mapping')
    pid = os.getpid()
    info = sample(pid)
    if info['status'] != 'ok':
        raise RuntimeError('wrapper cannot read own /proc identity')
    record = envelope(config, 'rank_map', rank=rank, local_rank=local_rank,
                      world_size=size, local_size=local_size, hostname=ctx['hostname'],
                      pid=pid, process_start_ticks=info['start_ticks'], main_tid=pid,
                      clk_tck=info['clk_tck'], nspid=info['nspid'], namespaces=ctx['namespaces'],
                      argv=command, exe=str(Path(command[0]).resolve()),
                      registered_ns=time.monotonic_ns(), gpu_uuid=None)
    directory = Path(config_file).parent
    target = directory / f'rank-{rank}.json'
    if target.exists():
        raise RuntimeError('duplicate rank registration')
    save(target, record)
    deadline = time.monotonic() + 15
    while not (directory / f'ack-{rank}').exists():
        if time.monotonic() > deadline or (directory / 'stop').exists():
            raise RuntimeError('sampler registration acknowledgement unavailable')
        time.sleep(.005)
    os.execv(command[0], command)


class Session:
    """One collector per trial. Only Popen handles owned by this session are stopped."""
    def __init__(self, directory, trial, ranks, candidate, interval=1., diagnostics='none',
                 host_verified=False, metadata=None):
        import math
        if not math.isfinite(interval) or interval <= 0 or ranks < 1:
            raise ValueError('positive finite interval and rank count required')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        config = dict(schema_version=SCHEMA, trial_id=trial, ranks=ranks,
                      candidate=str(Path(candidate).resolve()), candidate_sha256=digest(candidate),
                      interval=interval, diagnostics=diagnostics, host_verified=host_verified,
                      host=host_context(), clk_tck=os.sysconf('SC_CLK_TCK'),
                      clock_resolution_seconds=time.get_clock_info('monotonic').resolution,
                      metadata=metadata or {}, python=sys.version,
                      runner_identity=sample(os.getpid()),
                      online_cpus=read_optional('/sys/devices/system/cpu/online'),
                      numa={str(p): read_optional(p) for p in Path('/sys/devices/system/node').glob('node*/cpulist')},
                      topology={str(p): read_optional(p) for p in
                                Path('/sys/devices/system/cpu').glob('cpu[0-9]*/topology/*_id')})
        save(self.directory / 'config.json', config)
        self.process = None
        self.stream = None

    def start(self):
        self.stream = (self.directory / 'sampler.stderr').open('w')
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'collect',
                                         str(self.directory / 'config.json')],
                                        stderr=self.stream, start_new_session=True)
        deadline = time.monotonic() + 10
        while not (self.directory / 'ready').exists():
            if self.process.poll() is not None or time.monotonic() > deadline:
                self.stop('collector_start_failed')
                raise RuntimeError('CPU collector failed to start; see sampler.stderr')
            time.sleep(.01)

    def wrapper(self, command):
        return [sys.executable, str(Path(__file__).resolve()), 'wrap',
                str(self.directory / 'config.json'), '--', *command]

    def stop(self, reason):
        if not (self.directory / 'stop').exists():
            save(self.directory / 'stop', dict(reason=reason, t_ns=time.monotonic_ns()))
        if self.process is not None:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            save(self.directory / 'collector-exit.json', dict(returncode=self.process.returncode,
                                                            reason=reason))
        if self.stream:
            self.stream.close()


def collect(config_file):
    directory = Path(config_file).parent
    config = json.loads(Path(config_file).read_text())
    ranks, verified, tasks_seen, diagnostics = {}, set(), {}, []
    requested_stop = False
    def stop_signal(signum, frame):
        nonlocal requested_stop
        requested_stop = True
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    with (directory / 'samples.jsonl').open('w', buffering=1) as out:
        def emit(kind, **values):
            row = envelope(config, kind)
            row.update(values)
            out.write(json.dumps(row, ensure_ascii=False) + '\n')
        tool = config['diagnostics']
        if tool != 'none' and shutil.which(tool):
            command = [shutil.which(tool), '-V' if tool == 'pidstat' else '--version']
            try:
                version = subprocess.run(command, capture_output=True, text=True, timeout=2)
                emit('diagnostics_version', argv=command, returncode=version.returncode,
                     stdout=version.stdout, stderr=version.stderr,
                     status='ok' if version.returncode == 0 else 'unavailable')
            except (OSError, subprocess.TimeoutExpired) as error:
                emit('diagnostics_version', status='unavailable', reason=str(error))
        emit('diagnostics', requested=tool, tools={n: shutil.which(n) for n in ('pidstat', 'perf')},
             status='unavailable', reason='not_requested' if tool == 'none' else 'pending_attach',
             cycles=None, instructions=None, cpu_migrations=None)
        emit('collector_start', pid=os.getpid(), self_sample=sample(os.getpid()))
        (directory / 'ready').touch()
        tick = 0
        deadline = time.monotonic()
        try:
            while not requested_stop and not (directory / 'stop').exists():
                owner = sample(config['runner_identity']['pid'])
                if owner.get('start_ticks') != config['runner_identity']['start_ticks'] or owner.get('state') == 'Z':
                    emit('owner_exit', status='partial', reason='runner_exited_or_pid_reused')
                    break
                for path in directory.glob('rank-*.json'):
                    reg = json.loads(path.read_text())
                    rank = reg['rank']
                    if rank not in ranks:
                        ranks[rank] = reg
                        emit('rank_map', **{k: v for k, v in reg.items() if k not in
                             ('type', 'schema_version', 'trial_id', 'host_id', 'clock_domain_id', 'clock_id', 'source')})
                        (directory / f'ack-{rank}').touch()
                # Confirm exec, not just a wrapper with the same PID. cmdline also
                # distinguishes a Python candidate from this Python wrapper.
                for rank, reg in ranks.items():
                    if rank in verified:
                        continue
                    try:
                        pid = reg['pid']
                        st = parse_stat(Path(f'/proc/{pid}/stat').read_text())
                        exe = os.readlink(f'/proc/{pid}/exe')
                        argv = Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
                        if st['start_ticks'] != reg['process_start_ticks']:
                            continue
                        if exe != reg['exe'] or argv != reg['argv']:
                            continue
                        if digest(f'/proc/{pid}/exe') != config['candidate_sha256']:
                            emit('exec_rejected', rank=rank, reason='candidate_hash_mismatch', status='conflict')
                            continue
                        verified.add(rank)
                        emit('exec_verified', rank=rank, pid=pid, process_start_ticks=st['start_ticks'],
                             t_ns=time.monotonic_ns(), initialization_left_censored=True)
                        if tool != 'none':
                            executable = shutil.which(tool)
                            if executable is None:
                                emit('diagnostics', rank=rank, status='unavailable', reason='tool_missing')
                            else:
                                cmd = ([executable, '-h', '-u', '-w', '-t', '-p', str(pid), '1'] if tool == 'pidstat'
                                       else [executable, 'stat', '-x,', '-I', '1000', '-p', str(pid), '-e',
                                             'task-clock,context-switches,cpu-migrations,cycles,instructions'])
                                log = (directory / f'{tool}-rank-{rank}.txt').open('w')
                                try:
                                    proc = subprocess.Popen(cmd, stdout=log, stderr=log)
                                except OSError as error:
                                    log.close()
                                    emit('diagnostics', rank=rank, status='unavailable', reason=str(error))
                                else:
                                    diagnostics.append((rank, proc, log, cmd))
                    except (OSError, ValueError, UnicodeError):
                        pass
                now = time.monotonic()
                if now >= deadline:
                    round_begin = time.monotonic_ns()
                    for rank, reg in ranks.items():
                        if rank not in verified:
                            continue
                        pid = reg['pid']
                        proc = sample(pid)
                        if proc.get('start_ticks') != reg['process_start_ticks'] or proc.get('state') == 'Z':
                            proc.update(status='unavailable', reason='exited_or_pid_reused')
                        emit('proc_sample', rank=rank, sample_id=tick, **proc)
                        current = set()
                        task_rows = []
                        if proc['status'] == 'ok':
                            try:
                                tids = sorted(int(p.name) for p in Path(f'/proc/{pid}/task').iterdir())
                                for tid in tids:
                                    row = sample(pid, tid)
                                    task_rows.append(row)
                                    emit('task_sample', rank=rank, sample_id=tick,
                                         process_start_ticks=reg['process_start_ticks'], **row)
                                    if row['status'] == 'ok':
                                        current.add((tid, row['start_ticks']))
                                cap = capacity(pid, task_rows, config['host_verified'])
                                if len(current) != proc['num_threads']:
                                    cap.update(hierarchy_complete=False, reason='task_snapshot_changed')
                                emit('capacity', rank=rank, sample_id=tick, **cap)
                            except OSError as error:
                                emit('capacity', rank=rank, sample_id=tick, status='unavailable', reason=str(error))
                        previous = tasks_seen.get(rank, set())
                        for identity in current - previous:
                            emit('thread_seen', rank=rank, sample_id=tick, tid=identity[0],
                                 task_start_ticks=identity[1], t_ns=time.monotonic_ns())
                        for identity in previous - current:
                            emit('thread_lost', rank=rank, sample_id=tick, tid=identity[0],
                                 task_start_ticks=identity[1], t_ns=time.monotonic_ns(), right_censored=True)
                        tasks_seen[rank] = current
                    observers = [('runner', config['runner_identity'])]
                    observer_file = directory / 'launcher.json'
                    if observer_file.exists():
                        observers.append(('launcher', json.loads(observer_file.read_text())))
                    for name, identity in observers:
                        observed = sample(identity['pid'])
                        if observed.get('start_ticks') != identity.get('start_ticks'):
                            observed.update(status='unavailable', reason='observer_exited_or_pid_reused')
                        emit('observer_sample', observer=name, sample_id=tick, **observed)
                    end = time.monotonic()
                    missed = max(0, int((end - deadline) / config['interval']))
                    emit('collector', sample_id=tick, t_begin_ns=round_begin, t_end_ns=time.monotonic_ns(),
                         self_sample=sample(os.getpid()), bytes_written=out.tell(),
                         late_seconds=max(0, now - deadline), missed_deadlines=missed,
                         diagnostic_samples=[dict(rank=r, **sample(p.pid)) for r,p,_,_ in diagnostics
                                             if p.poll() is None])
                    tick += missed + 1
                    deadline += (missed + 1) * config['interval']
                # Discovery polling ends once all ranks are exec-verified.
                wait = max(0., deadline - time.monotonic())
                time.sleep(min(wait, .01 if len(verified) < config['ranks'] else .1))
        finally:
            for rank, proc, log, cmd in diagnostics:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                log.close()
                emit('diagnostics_exit', rank=rank, argv=cmd, returncode=proc.returncode,
                     status='unavailable' if proc.returncode else 'ok',
                     reason='inspect_raw_log_permission_or_short_lifetime' if proc.returncode else None,
                     cycles=None, instructions=None, cpu_migrations=None)
            emit('collector_end', self_sample=sample(os.getpid()),
                 stop=read_optional(directory / 'stop'), exec_verified_ranks=sorted(verified),
                 status='partial' if len(verified) != config['ranks'] else 'ok')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['wrap', 'collect'])
    parser.add_argument('config', type=Path, help='trial cpu/config.json written by benchmark')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.mode == 'wrap':
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        wrap(args.config, command)
    else:
        collect(args.config)


if __name__ == '__main__':
    main()
