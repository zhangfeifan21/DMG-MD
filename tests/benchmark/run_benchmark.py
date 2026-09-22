#!/usr/bin/env python3
"""Single-node scaling benchmark. Only Python standard library is required."""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import re
import signal
import socket
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests/long_nve'))
sys.path.insert(0, str(ROOT / 'tests/mpi'))
import long_nve_common as fixtures
from check_environment import validate_environment


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)


def command_output(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f'{command}: {result.stderr or result.stdout}')
    return result.stdout


def geometry(spec, ranks):
    case = dict(fixtures.load_manifest()['cases'][spec['fixture']])
    case['cells'] = list(spec['cells'])
    if spec['scaling'] == 'weak':
        case['cells'][1] *= ranks
    per_cell = {'diamond': 8, 'dense_water': 3}[case['generator']]
    case['atoms'] = math.prod(case['cells']) * per_cell
    spacing = case.get('lattice_constant_A', case.get('spacing_A'))
    lengths = [c * spacing for c in case['cells']]
    # Both implementations must use y; avoid tie-dependent axis selection.
    if not lengths[1] > max(lengths[0], lengths[2]):
        raise ValueError('benchmark requires a uniquely longest y axis')
    potential = fixtures.generate_potential(case)
    fixtures.validate_generated_potential(case, potential)
    cutoff_line = next(s for s in potential.splitlines() if s.startswith('cutoff '))
    cutoff = float(cutoff_line.split()[1])
    # Pinned GPUMD NEP_MULTIGPU::compute: floor(L/(rc/2))/P >= 10.
    if ranks > 1 and (int(lengths[1] / (cutoff / 2)) // ranks < 10
                     or min(lengths) <= 2.5 * (cutoff + 1)):
        raise ValueError(f'ineligible multi-GPU geometry: {lengths}, ranks={ranks}')
    return case, lengths


def run_input(engine, ranks, case, warmup, steps):
    axis = ' y' if engine == 'gpumd' and ranks > 1 else ''
    # Explicit velocities from model.xyz, no independently randomized velocity command.
    # Final thermo is included in the timed segment and forces a host-visible result.
    return (f'potential nep.txt{axis}\ntime_step {case["time_step_fs"]}\n'
            f'ensemble nve\nrun {warmup}\n'
            f'dump_thermo {steps}\nrun {steps}\n')


def fields(line):
    return dict(token.split('=', 1) for token in line.split()[1:] if '=' in token)


def parse_thermo(text):
    rows = [[float(v) for v in line.split()] for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]
    if len(rows) != 1 or len(rows[0]) != 18 or not all(math.isfinite(v) for v in rows[0]):
        raise ValueError('expected one finite final thermo row with 18 columns')
    return rows[0]


def parse_timing(text, engine, ranks, atoms, warmup, steps, backend):
    if engine == 'dmgmd':
        records = [fields(s) for s in text.splitlines() if s.startswith('DMGMD_TIMING phase=run ')]
        if len(records) != 2:
            raise ValueError('expected exactly warmup and measured DMGMD_TIMING segments')
        for i, (record, count) in enumerate(zip(records, (warmup, steps))):
            for key, expected in [('sequence', i), ('steps', count), ('atoms', atoms), ('ranks', ranks)]:
                if int(record[key]) != expected:
                    raise ValueError(f'timing {key} mismatch: {record}')
            if record['backend'] != backend:
                raise ValueError('communication backend silently fell back')
        modes = [fields(s) for s in text.splitlines() if s.startswith('DMGMD_DOMAIN mode=')]
        expected = 'm2a' if ranks > 1 else 'm1-fallback'
        if len(modes) != 1 or modes[0]['mode'] != expected:
            raise ValueError('unexpected domain path')
        if ranks > 1 and modes[0]['axis'] != 'y':
            raise ValueError('unexpected partition axis')
        bindings = [fields(s) for s in text.splitlines() if s.startswith('DMGMD_MPI rank=')]
        if len(bindings) != ranks or len({b['cuda_uuid'] for b in bindings}) != ranks:
            raise ValueError('ranks did not bind unique GPUs')
        seconds = float(records[-1]['seconds_max'])
    else:
        records = re.findall(r'Time used for this run = ([\deE+.\-]+) second\.', text)
        if len(records) != 2:
            raise ValueError('expected exactly warmup and measured GPUMD run timers')
        used = re.findall(r'Try to use (\d+) GPUs for the NEP part\.', text)
        if (ranks > 1 and used != [str(ranks)]) or (ranks == 1 and used):
            raise ValueError('GPUMD did not select the expected NEP path')
        seconds = float(records[-1])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('invalid timing')
    return seconds


GPU_FIELDS = ('index,uuid,name,pci.bus_id,driver_version,memory.total,memory.used,'
              'utilization.gpu,temperature.gpu,power.draw,power.limit,clocks.sm,clocks.mem')


def snapshot():
    raw = command_output(['nvidia-smi', f'--query-gpu={GPU_FIELDS}', '--format=csv,noheader,nounits'])
    gpus = [dict(zip(GPU_FIELDS.split(','), [v.strip() for v in row]))
            for row in csv.reader(io.StringIO(raw)) if row]
    raw = command_output(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,used_memory',
                          '--format=csv,noheader,nounits'])
    apps = [dict(zip(('uuid', 'pid', 'memory'), [v.strip() for v in row]))
            for row in csv.reader(io.StringIO(raw)) if len(row) == 3]
    return {'time': dt.datetime.now(dt.timezone.utc).isoformat(), 'gpus': gpus, 'apps': apps}


def resolve_devices(requested, snap):
    resolved = []
    for device in requested:
        matches = [g['uuid'] for g in snap['gpus'] if device in (g['index'], g['uuid'])]
        if len(matches) != 1:
            raise ValueError(f'unknown/ambiguous GPU: {device}; use nvidia-smi index or full UUID')
        resolved.append(matches[0])
    if len(set(resolved)) != len(resolved):
        raise ValueError('duplicate GPU selection')
    return resolved


def ensure_idle(snap, selected, max_memory, max_util):
    busy = [a for a in snap['apps'] if a['uuid'] in selected]
    for gpu in snap['gpus']:
        if gpu['uuid'] in selected and (float(gpu['memory.used']) > max_memory
                                       or float(gpu['utilization.gpu']) > max_util):
            busy.append(gpu)
    if busy:
        raise RuntimeError(f'selected GPU is busy; choose idle GPUs or reschedule: {busy}')


def process_record(pid):
    # comm may contain spaces/parentheses; fields after its final ')' start at state.
    values = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return {'parent': int(values[1]), 'group': int(values[2]), 'start': values[19]}


def belongs_to_job(pid, launcher):
    visited = set()
    while pid > 1 and pid not in visited:
        if pid == launcher:
            return True
        visited.add(pid)
        pid = process_record(pid)['parent']
    return False


def stop_process_tree(process):
    # PRRTE can create a separate process group for each rank. Snapshot ancestry
    # before terminating the launcher, and guard later signals against PID reuse.
    records = {}
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            with contextlib.suppress(OSError, ValueError):
                records[int(entry.name)] = process_record(int(entry.name))
    owned = {process.pid}
    while True:
        children = {pid for pid, record in records.items() if record['parent'] in owned}
        if children <= owned:
            break
        owned.update(children)
    def signal_owned(sig):
        for pid in owned:
            with contextlib.suppress(ProcessLookupError, FileNotFoundError, PermissionError):
                if pid in records and process_record(pid)['start'] == records[pid]['start']:
                    os.kill(pid, sig)
    signal_owned(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    signal_owned(signal.SIGKILL)
    process.wait()


def foreign_apps(snap, selected, launcher):
    foreign = []
    for app in snap['apps']:
        if app['uuid'] not in selected:
            continue
        try:
            if belongs_to_job(int(app['pid']), launcher):
                continue
        except (ProcessLookupError, FileNotFoundError):
            continue
        except PermissionError:
            pass
        foreign.append(app)
    return foreign


def execute(command, directory, environment, selected, timeout, interval):
    contaminated = False
    timed_out = False
    start = time.monotonic()
    with (directory / 'stdout.txt').open('w') as out, (directory / 'stderr.txt').open('w') as err, \
            (directory / 'telemetry.jsonl').open('w') as telemetry:
        process = subprocess.Popen(command, cwd=directory, env=environment, stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            while process.poll() is None:
                sample = snapshot()
                sample['foreign_apps'] = foreign_apps(sample, selected, process.pid)
                contaminated |= bool(sample['foreign_apps'])
                telemetry.write(json.dumps(sample) + '\n')
                telemetry.flush()
                if time.monotonic() - start > timeout:
                    timed_out = True
                    break
                try:
                    process.wait(timeout=interval)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                stop_process_tree(process)
    return process.returncode, time.monotonic() - start, contaminated, timed_out


def summary(results, repeats):
    groups = {}
    for result in results:
        if result['status'] == 'ok':
            key = tuple(result[k] for k in ('case', 'scaling', 'engine', 'backend', 'ranks', 'atoms'))
            groups.setdefault(key, []).append(result['seconds'])
    rows = []
    for key, times in sorted(groups.items()):
        row = dict(zip(('case', 'scaling', 'engine', 'backend', 'ranks', 'atoms'), key))
        row.update(samples=len(times), complete=len(times) == repeats,
                   seconds_median=statistics.median(times), seconds_min=min(times), seconds_max=max(times),
                   relative_range=(max(times)-min(times))/statistics.median(times))
        steps = next(r['steps'] for r in results if r['case'] == row['case'])
        row['atom_steps_per_second'] = row['atoms'] * steps / row['seconds_median']
        row['ms_per_step'] = row['seconds_median'] * 1000 / steps
        rows.append(row)
    for row in rows:
        baseline = next((r for r in rows if r['case'] == row['case'] and r['engine'] == row['engine']
                         and r['backend'] == row['backend'] and r['ranks'] == 1 and r['complete']), None)
        row['speedup'] = row['efficiency'] = row['dmgmd_over_gpumd'] = None
        if row['complete'] and baseline:
            ratio = baseline['seconds_median'] / row['seconds_median']
            row['speedup'] = ratio if row['scaling'] == 'strong' else None
            row['efficiency'] = ratio / row['ranks'] if row['scaling'] == 'strong' else ratio
        reference = next((r for r in rows if r['case'] == row['case'] and r['engine'] == 'gpumd'
                          and r['ranks'] == row['ranks'] and r['atoms'] == row['atoms']
                          and r['complete']), None)
        if row['engine'] == 'dmgmd' and row['complete'] and reference:
            row['dmgmd_over_gpumd'] = reference['seconds_median'] / row['seconds_median']
    return rows


def report(output, results, repeats):
    save(output / 'results.json', results)
    rows = summary(results, repeats)
    save(output / 'summary.json', rows)
    if rows:
        with (output / 'summary.csv').open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ['# Benchmark results', '', '| case | engine/backend | GPUs | atoms | samples | ms/step | atom-step/s | speedup | efficiency | DMG/GPUMD |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    def fmt(value):
        return '—' if value is None else f'{value:.4g}'
    for r in rows:
        lines.append(f'| {r["case"]} | {r["engine"]}/{r["backend"]} | {r["ranks"]} | {r["atoms"]} | '
                     f'{r["samples"]}/{repeats} | {fmt(r["ms_per_step"])} | {fmt(r["atom_steps_per_second"])} | '
                     f'{fmt(r["speedup"])} | {fmt(r["efficiency"])} | {fmt(r["dmgmd_over_gpumd"])} |')
    lines.extend(['', 'Only status=ok samples are included; ratios require complete repetitions.',
                  'Smoke/pilot are orchestration/calibration data, not publication results.', '', '## Non-valid trials', ''])
    lines.extend(f'- {r["id"]}: {r["status"]}: {r.get("error", "")}' for r in results if r['status'] != 'ok')
    (output / 'summary.md').write_text('\n'.join(lines) + '\n')


def main():
    manifest = json.loads((HERE / 'manifest.json').read_text())
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', choices=manifest['profiles'], default='standard')
    p.add_argument('--devices', default='0,1,2,3,4,5,6,7', help='physical nvidia-smi indices or full UUIDs')
    p.add_argument('--ranks', default='1,2,4,8')
    p.add_argument('--cases', help='comma-separated manifest case names; overrides profile selection')
    p.add_argument('--backends', default=','.join(manifest['backends']))
    p.add_argument('--engines', default='gpumd,dmgmd')
    p.add_argument('--candidate', type=Path, default=ROOT / 'build/dmg-md')
    p.add_argument('--reference', type=Path, default=ROOT.parent / 'gpumd-reference/src/gpumd')
    p.add_argument('--mpiexec', default='mpiexec')
    p.add_argument('--mpiexec-arg', action='append', default=[])
    p.add_argument('--output', type=Path, default=ROOT / ('dmgmd-benchmark-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S')))
    for name in ('warmup', 'steps', 'repeats'):
        p.add_argument('--' + name, type=int)
    p.add_argument('--min-seconds', type=float)
    p.add_argument('--timeout', type=float, default=7200, help='per-trial wall time limit')
    p.add_argument('--sample-interval', type=float, default=2)
    p.add_argument('--idle-memory-mib', type=float, default=256)
    p.add_argument('--idle-util-percent', type=float, default=10)
    p.add_argument('--dry-run', action='store_true', help='print matrix; no GPU access, model generation or output writes')
    args = p.parse_args()
    profile = dict(manifest['profiles'][args.profile])
    for key in ('warmup', 'steps', 'repeats', 'min_seconds'):
        if getattr(args, key) is not None:
            profile[key] = getattr(args, key)
    ranks = [int(v) for v in args.ranks.split(',')]
    devices = args.devices.split(',')
    cases = args.cases.split(',') if args.cases else profile['cases']
    engines, backends = args.engines.split(','), args.backends.split(',')
    if (not set(ranks) <= set(manifest['ranks']) or len(ranks) != len(set(ranks))
            or len(devices) < max(ranks) or len(devices) != len(set(devices))):
        p.error('unique ranks must be 1/2/4/8, with enough distinct devices')
    if not set(engines) <= {'gpumd', 'dmgmd'} or not set(backends) <= set(manifest['backends']):
        p.error('invalid engine/backend')
    if len(set(engines)) != len(engines) or len(set(backends)) != len(backends) or len(set(cases)) != len(cases):
        p.error('duplicate matrix entries')
    if any(profile[k] <= 0 for k in ('warmup', 'steps', 'repeats')) or profile['min_seconds'] < 0:
        p.error('warmup/steps/repeats must be positive; min-seconds must be nonnegative')
    if args.timeout <= 0 or args.sample_interval <= 0 or args.idle_memory_mib < 0 or args.idle_util_percent < 0:
        p.error('invalid monitoring limits')
    plan = []
    for name in cases:
        if name not in manifest['cases']:
            p.error(f'unknown case: {name}')
        spec = manifest['cases'][name]
        for rank in ranks:
            case, lengths = geometry(spec, rank)
            for engine in engines:
                for backend in (backends if engine == 'dmgmd' else ['none']):
                    plan.append(dict(case=name, scaling=spec['scaling'], ranks=rank, atoms=case['atoms'],
                                     cells=case['cells'], box_A=lengths, engine=engine, backend=backend,
                                     steps=profile['steps']))
    trials = []
    rng = random.Random(manifest['order_seed'])
    for repeat in range(profile['repeats']):
        batch = [dict(job, repeat=repeat) for job in plan]
        rng.shuffle(batch)
        trials.extend(batch)
    for i, job in enumerate(trials):
        job['id'] = f'{i:04d}-{job["case"]}-{job["engine"]}-{job["backend"]}-p{job["ranks"]}-r{job["repeat"]}'
    if args.dry_run:
        print(json.dumps({'profile': profile, 'trials': trials}, indent=2))
        return 0
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binaries = {'dmgmd': args.candidate.resolve(), 'gpumd': args.reference.resolve()}
    identity = {name: {'path': str(binaries[name]), 'sha256': sha(binaries[name])} for name in engines}
    snap = snapshot()
    selected = resolve_devices(devices, snap)
    save(output / 'initial-gpus.json', snap)
    ensure_idle(snap, selected[:max(ranks)], args.idle_memory_mib, args.idle_util_percent)
    metadata = {'schema_version': 1, 'profile_name': args.profile, 'profile': profile,
                'hostname': socket.gethostname(), 'argv': sys.argv, 'devices': selected,
                'binaries': identity, 'manifest': manifest, 'plan': trials,
                'environment': {k: v for k, v in os.environ.items()
                                if k.startswith(('CUDA_', 'OMPI_', 'UCX_', 'PMIX_', 'DMGMD_', 'OMP_'))
                                or k in ('PATH', 'LD_LIBRARY_PATH')},
                'sources': {str(f.relative_to(ROOT)): sha(f) for f in
                            [Path(__file__), HERE / 'manifest.json', ROOT / 'tests/long_nve/long_nve_common.py',
                             ROOT / 'tests/long_nve/manifest.json']}}
    for name, cmd in {'dmgmd_revision': ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                      'dmgmd_status': ['git', '-C', str(ROOT), 'status', '--porcelain'],
                      'reference_revision': ['git', '-C', str(ROOT.parent / 'gpumd-reference'), 'rev-parse', 'HEAD'],
                      'reference_status': ['git', '-C', str(ROOT.parent / 'gpumd-reference'), 'status', '--porcelain'],
                      'gpu_topology': ['nvidia-smi', 'topo', '-m'],
                      'gpu_details': ['nvidia-smi', '-q'], 'cpu': ['lscpu'],
                      'nvcc': ['nvcc', '--version'], 'mpi': ['mpiexec', '--version'],
                      'ucx': ['ucx_info', '-v']}.items():
        try:
            metadata[name] = command_output(cmd)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            metadata[name] = str(error)
    save(output / 'metadata.json', metadata)
    if 'gpumd' in engines and metadata['reference_revision'].strip() != manifest['reference_commit']:
        raise RuntimeError('reference checkout does not match pinned commit')
    if 'dmgmd' in engines:
        with (output / 'preflight.txt').open('w') as stream, contextlib.redirect_stdout(stream):
            validate_environment(binaries['dmgmd'], args.mpiexec, selected, max(ranks),
                                 min(300, int(args.timeout)), args.mpiexec_arg)
    results = []
    inputs = {}
    try:
        for job in trials:
            directory = output / job['id']
            directory.mkdir()
            result = dict(job, status='failed')
            print(f'BENCHMARK {job["id"]} atoms={job["atoms"]}', flush=True)
            try:
                key = (job['case'], tuple(job['cells']))
                if key not in inputs:
                    source = output / 'inputs' / (job['case'] + '-' + 'x'.join(map(str, job['cells'])))
                    source.mkdir(parents=True)
                    case, _ = geometry(manifest['cases'][job['case']], job['ranks'])
                    (source / 'model.xyz').write_text(fixtures.generate_model(case, manifest['seed']))
                    (source / 'nep.txt').write_text(fixtures.generate_potential(case))
                    inputs[key] = (source, case)
                source, case = inputs[key]
                for filename in ('model.xyz', 'nep.txt'):
                    (directory / filename).symlink_to(source / filename)
                (directory / 'run.in').write_text(run_input(job['engine'], job['ranks'], case,
                                                          profile['warmup'], profile['steps']))
                result['input_sha256'] = {f: sha(directory / f) for f in ('model.xyz', 'nep.txt', 'run.in')}
                visible = selected[:job['ranks']]
                # Remove ALL inherited DMGMD diagnostic/override variables.
                environment = {k: v for k, v in os.environ.items() if not k.startswith('DMGMD_')}
                environment.update(CUDA_DEVICE_ORDER='PCI_BUS_ID', CUDA_VISIBLE_DEVICES=','.join(visible),
                                   OMP_NUM_THREADS='1')
                command = [str(binaries[job['engine']])]
                if job['engine'] == 'dmgmd':
                    environment.update(DMGMD_COMM_BACKEND=job['backend'], DMGMD_DOMAIN_TIMING='0',
                                       DMGMD_DOMAIN_DIAGNOSTICS='0',
                                       DMGMD_COMM_LOG_INTERVAL=str(max(profile['warmup'], profile['steps']) + 1))
                    command = [args.mpiexec, *args.mpiexec_arg, '-n', str(job['ranks']), *command]
                result['command'] = command
                result['launch_environment'] = {k: environment[k] for k in environment
                                                if k.startswith(('CUDA_', 'DMGMD_', 'OMP_'))}
                if sha(binaries[job['engine']]) != identity[job['engine']]['sha256']:
                    raise RuntimeError('binary changed during benchmark')
                before = snapshot()
                save(directory / 'before.json', before)
                ensure_idle(before, visible, args.idle_memory_mib, args.idle_util_percent)
                code, wall, contaminated, timeout = execute(command, directory, environment, visible,
                                                            args.timeout, args.sample_interval)
                result.update(returncode=code, process_wall_seconds=wall, contaminated=contaminated)
                if timeout:
                    raise RuntimeError('trial timeout (process group terminated)')
                if code:
                    raise RuntimeError(f'exit {code}; inspect stdout.txt/stderr.txt (including possible OOM)')
                seconds = parse_timing((directory / 'stdout.txt').read_text(), job['engine'], job['ranks'],
                                       job['atoms'], profile['warmup'], profile['steps'], job['backend'])
                thermo = parse_thermo((directory / 'thermo.out').read_text())
                result.update(seconds=seconds, atom_steps_per_second=job['atoms'] * job['steps'] / seconds,
                              final_thermo=thermo, status='ok')
                if contaminated:
                    result.update(status='contaminated', error='foreign GPU process detected during trial')
                elif seconds < profile['min_seconds']:
                    result.update(status='too_short', error='increase --steps; timing below min-seconds')
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
                result['error'] = str(error)
            except KeyboardInterrupt:
                result.update(status='interrupted', error='user interrupted')
                raise
            finally:
                results.append(result)
                save(directory / 'result.json', result)
                report(output, results, profile['repeats'])
                print(f'BENCHMARK status={result["status"]} {result.get("error", "")}', flush=True)
            if result['status'] == 'failed' and 'busy' in result.get('error', ''):
                break
    finally:
        report(output, results, profile['repeats'])
    print(f'Report: {output / "summary.md"}')
    return 0 if len(results) == len(trials) and all(r['status'] == 'ok' for r in results) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError) as error:
        print(f'BENCHMARK ERROR: {error}', file=sys.stderr)
        sys.exit(1)
