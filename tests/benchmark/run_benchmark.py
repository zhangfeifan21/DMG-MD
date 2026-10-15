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
sys.path.insert(0, str(ROOT / 'tests/lammps_reference'))
import long_nve_common as fixtures
import lammps_inputs
from check_environment import validate_environment
from cpu_sampling import Session as CpuSession, sample as cpu_sample, cpu_set
from analyze_cpu import analyze as analyze_cpu


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


def create_output_directory(prefix):
    """Reserve a fresh timestamped directory, including concurrent same-second runs."""
    prefix = Path(prefix).absolute()
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    base = prefix.with_name(f'{prefix.name}-{stamp}')
    base.parent.mkdir(parents=True, exist_ok=True)
    counter = 0
    while True:
        output = base if counter == 0 else base.with_name(f'{base.name}-{counter}')
        try:
            output.mkdir()
            return output.resolve()
        except FileExistsError:
            counter += 1


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
    if engine == 'lammps':
        return lammps_inputs.input_file(fixtures.generate_potential(case),
                                        float(case['time_step_fs']), steps, warmup=warmup)
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


def parse_lammps_thermo(text, final_step, atoms):
    headers = list(re.finditer(r'^\s*Step\s+Atoms\s+Temp\s+PotEng\s+KinEng\s+TotEng\s+Press\s*$',
                               text, re.MULTILINE))
    if len(headers) != 2:
        raise ValueError('expected LAMMPS thermo headers for warmup and measured runs')
    segment = text[headers[-1].end():]
    segment = segment.split('Loop time of', 1)[0]
    rows = []
    for line in segment.splitlines():
        tokens = line.split()
        if len(tokens) == 7:
            try:
                rows.append([float(value) for value in tokens])
            except ValueError:
                continue
    if not rows or not all(math.isfinite(value) for value in rows[-1]):
        raise ValueError('missing finite LAMMPS final thermo')
    if int(rows[-1][0]) != final_step or int(rows[-1][1]) != atoms:
        raise ValueError('LAMMPS final thermo step/atom count mismatch')
    return rows[-1]


def parse_timing(text, engine, ranks, atoms, warmup, steps, backend, selected=None):
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
    elif engine == 'lammps':
        if ('KOKKOS mode with Kokkos version' not in text or
                'will use up to 1 GPU(s) per node' not in text or
                'NEP Kokkos potential nep.txt loaded successfully' not in text or
                'pair matpl/nep/kk' not in text or 'kokkos_device' not in text):
            raise ValueError('LAMMPS did not use the expected NEP/KOKKOS GPU path')
        bindings = [fields(line) for line in text.splitlines()
                    if line.startswith('LAMMPS_GPU_BIND rank=')]
        if (len(bindings) != ranks or
                {int(binding['rank']) for binding in bindings} != set(range(ranks)) or
                {int(binding['local_rank']) for binding in bindings} != set(range(ranks)) or
                len({binding['cuda_uuid'] for binding in bindings}) != ranks):
            raise ValueError('LAMMPS ranks were not assigned unique GPUs')
        if selected is not None and {binding['cuda_uuid'] for binding in bindings} != set(selected):
            raise ValueError('LAMMPS GPU binding differs from selected devices')
        records = re.findall(
            r'Loop time of ([\deE+.\-]+) on (\d+) procs for (\d+) steps with (\d+) atoms', text)
        if len(records) != 2:
            raise ValueError('expected two LAMMPS run timers')
        for record, expected_steps in zip(records, (warmup, steps)):
            if (int(record[1]), int(record[2]), int(record[3])) != (ranks, expected_steps, atoms):
                raise ValueError('LAMMPS timing rank/step/atom count mismatch')
        seconds = float(records[-1][0])
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


def actual_dmgmd_profiling(text, requested):
    """Require the runtime's mode record, not an inherited/exported environment."""
    modes = [fields(line) for line in text.splitlines()
             if line.startswith('DMGMD_DOMAIN_DIAGNOSTICS detailed=')]
    if len(modes) != 1:
        raise ValueError('missing or duplicate DMGMD domain diagnostic mode')
    expected = 'on' if requested == 'trace' else 'off'
    if (modes[0].get('timing'), modes[0].get('trace')) != (expected, expected):
        raise ValueError('runtime profiling mode differs from requested configuration')
    trace = [line for line in text.splitlines() if line.startswith('DMGMD_DOMAIN_TRACE rank=')]
    timing = [line for line in text.splitlines() if line.startswith('DMGMD_DOMAIN_TIMING sequence=')]
    if requested == 'trace' and (not trace or len(timing) != 4):
        raise ValueError('trace mode lacks step trace or both segment timing summaries')
    if requested == 'off' and (trace or timing):
        raise ValueError('profiling output appeared in off mode')
    return {'timing': modes[0]['timing'], 'trace': modes[0]['trace'],
            'step_trace_rows': len(trace), 'timing_summary_rows': len(timing)}


def actual_dmgmd_partition(text, requested):
    configs = [fields(line) for line in text.splitlines() if line.startswith('DMGMD_PARTITION_CONFIG ')]
    summaries = [fields(line) for line in text.splitlines() if line.startswith('DMGMD_PARTITION_SUMMARY ')]
    if requested == 'off':
        if configs or summaries:
            raise ValueError('partition output appeared in off mode')
        return {'mode': 'off', 'split_calls': 0, 'classifications': 0}
    if len(configs) != 1 or configs[0].get('mode') != requested:
        raise ValueError('runtime partition mode differs from requested configuration')
    if 'DMGMD_PARTITION mode=full reason=m1-fallback' in text:
        return {'mode': requested, 'fallback': 'm1', 'split_calls': 0, 'classifications': 0}
    latest = {}
    for row in summaries:
        rank = int(row['rank'])
        if rank not in latest or int(row['sequence']) > int(latest[rank]['sequence']): latest[rank] = row
    if not latest:
        raise ValueError('partition mode lacks actual execution counters')
    return {'mode': requested,
            'split_calls': sum(int(row['cumulative_split_calls']) for row in latest.values()),
            'classifications': sum(int(row['cumulative_classifications']) for row in latest.values())}


def write_trace_markers(text, cpu_directory):
    """Join runtime timestamps to C1's verified PID/start identity for reuse by its analyzer."""
    cpu_directory = Path(cpu_directory)
    config = json.loads((cpu_directory / 'config.json').read_text())
    registrations = {rank: json.loads((cpu_directory / f'rank-{rank}.json').read_text())
                     for rank in range(config['ranks'])}
    markers = []
    for line in text.splitlines():
        if not line.startswith(('DMGMD_DOMAIN_TRACE rank=', 'DMGMD_DOMAIN_TRACE_SEGMENT rank=')):
            continue
        record = fields(line)
        rank = int(record['rank'])
        registration = registrations[rank]
        if int(record['pid']) != registration['pid'] or record['clock'] != 'CLOCK_MONOTONIC':
            raise ValueError('trace PID or clock differs from C1 rank registration')
        segment = line.startswith('DMGMD_DOMAIN_TRACE_SEGMENT rank=')
        phase = 'run' if segment else 'step_' + record['class']
        sequence = int(record['sequence']) if segment else f'{record["sequence"]}:{record["step"]}'
        begin, end = int(record['begin_ns']), int(record['end_ns'])
        if begin <= 0 or end <= begin:
            raise ValueError('invalid trace marker window')
        common = dict(schema_version=1, rank=rank, pid=registration['pid'],
                      process_start_ticks=registration['process_start_ticks'],
                      clock_domain_id=registration['clock_domain_id'],
                      clock_id='CLOCK_MONOTONIC', sequence=sequence, phase=phase)
        for edge, stamp in (('begin', begin), ('end', end)):
            markers.append(dict(common, edge=edge, t_ns=stamp))
    if not markers:
        raise ValueError('no runtime trace markers to join with C1 samples')
    path = cpu_directory / 'markers.jsonl'
    path.write_text(''.join(json.dumps(marker) + '\n' for marker in markers))
    return path


def verify_cpu_binding(cpu_directory, physical_cores, numa_node):
    """Check observed task masks against the C1 topology snapshot, including SMT siblings."""
    cpu_directory = Path(cpu_directory)
    config = json.loads((cpu_directory / 'config.json').read_text())
    topology = {}
    for path, value in config['topology'].items():
        match = re.search(r'/cpu(\d+)/topology/(core_id|physical_package_id)$', path)
        if match and value is not None:
            topology.setdefault(int(match[1]), {})[match[2]] = int(value)
    nodes = {}
    for path, value in config['numa'].items():
        match = re.search(r'/node(\d+)/cpulist$', path)
        if match and value is not None:
            for cpu in cpu_set(value):
                nodes[cpu] = int(match[1])
    rows = [json.loads(line) for line in (cpu_directory / 'samples.jsonl').read_text().splitlines()]
    verified = []
    for rank in range(config['ranks']):
        process = [row for row in rows if row.get('type') == 'proc_sample' and
                   row.get('rank') == rank and row.get('status') == 'ok']
        if not process:
            raise ValueError(f'rank {rank}: no valid C1 process affinity sample')
        masks = {tuple(row['affinity']) for row in process}
        if len(masks) != 1:
            raise ValueError(f'rank {rank}: process affinity changed during trial')
        mask = set(next(iter(masks)))
        tasks = [row for row in rows if row.get('type') == 'task_sample' and
                 row.get('rank') == rank and row.get('status') == 'ok']
        if not tasks or any(not set(row['affinity']) <= mask for row in tasks):
            raise ValueError(f'rank {rank}: task affinity unavailable or exceeds process mask')
        if not mask or any(cpu not in topology or cpu not in nodes or
                           not {'core_id', 'physical_package_id'} <= topology[cpu].keys()
                           for cpu in mask):
            raise ValueError(f'rank {rank}: incomplete CPU topology for observed mask')
        cores = {(topology[cpu]['physical_package_id'], topology[cpu]['core_id']) for cpu in mask}
        actual_nodes = {nodes[cpu] for cpu in mask}
        if len(cores) != physical_cores or actual_nodes != {numa_node}:
            raise ValueError(f'rank {rank}: observed {len(cores)} physical cores on NUMA '
                             f'{sorted(actual_nodes)}, expected {physical_cores} on {numa_node}')
        verified.append(dict(rank=rank, logical_cpus=sorted(mask), physical_cores=len(cores),
                             physical_core_ids=sorted([list(core) for core in cores]),
                             numa_nodes=sorted(actual_nodes), process_samples=len(process),
                             task_samples=len(tasks)))
    if any(set(a['logical_cpus']) & set(b['logical_cpus'])
           for i, a in enumerate(verified) for b in verified[i + 1:]):
        raise ValueError('ranks share CPUs under the requested fixed binding')
    return verified


GPU_FIELDS = ('index,uuid,name,pci.bus_id,driver_version,memory.total,memory.used,'
              'utilization.gpu,temperature.gpu,power.draw,power.limit,clocks.sm,clocks.mem')


def snapshot():
    begin = time.monotonic_ns()
    raw = command_output(['nvidia-smi', f'--query-gpu={GPU_FIELDS}', '--format=csv,noheader,nounits'])
    gpus = [dict(zip(GPU_FIELDS.split(','), [v.strip() for v in row]))
            for row in csv.reader(io.StringIO(raw)) if row]
    return {'time': dt.datetime.now(dt.timezone.utc).isoformat(), 'gpus': gpus,
            'clock_id': 'CLOCK_MONOTONIC', 't_begin_ns': begin, 't_end_ns': time.monotonic_ns()}


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


def process_record(pid):
    # comm may contain spaces/parentheses; fields after its final ')' start at state.
    values = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return {'parent': int(values[1]), 'group': int(values[2]), 'start': values[19]}


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


def execute(command, directory, environment, timeout, interval, cpu_session=None):
    timed_out = False
    start = time.monotonic()
    with (directory / 'stdout.txt').open('w') as out, (directory / 'stderr.txt').open('w') as err, \
            (directory / 'telemetry.jsonl').open('w') as telemetry:
        process = subprocess.Popen(command, cwd=directory, env=environment, stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            if cpu_session:
                save(cpu_session.directory / 'launcher.json', cpu_sample(process.pid))
            while process.poll() is None:
                sample = snapshot()
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
    return process.returncode, time.monotonic() - start, timed_out


def summary(results, repeats):
    groups = {}
    for result in results:
        if result['status'] == 'ok':
            key = tuple(result[k] for k in ('case', 'scaling', 'engine', 'backend', 'ranks', 'atoms')) + \
                  (result.get('profiling', 'off'),)
            groups.setdefault(key, []).append(result['seconds'])
    rows = []
    for key, times in sorted(groups.items()):
        row = dict(zip(('case', 'scaling', 'engine', 'backend', 'ranks', 'atoms', 'profiling'), key))
        row.update(samples=len(times), complete=len(times) == repeats,
                   seconds_median=statistics.median(times), seconds_min=min(times), seconds_max=max(times),
                   relative_range=(max(times)-min(times))/statistics.median(times))
        steps = next(r['steps'] for r in results if r['case'] == row['case'])
        row['atom_steps_per_second'] = row['atoms'] * steps / row['seconds_median']
        row['ms_per_step'] = row['seconds_median'] * 1000 / steps
        rows.append(row)
    for row in rows:
        baseline = next((r for r in rows if r['case'] == row['case'] and r['engine'] == row['engine']
                         and r['backend'] == row['backend'] and r['profiling'] == row['profiling']
                         and r['ranks'] == 1 and r['complete']), None)
        row['speedup'] = row['efficiency'] = None
        row['dmgmd_over_gpumd'] = row['dmgmd_over_lammps'] = None
        if row['complete'] and baseline:
            ratio = baseline['seconds_median'] / row['seconds_median']
            row['speedup'] = ratio if row['scaling'] == 'strong' else None
            row['efficiency'] = ratio / row['ranks'] if row['scaling'] == 'strong' else ratio
        reference = next((r for r in rows if r['case'] == row['case'] and r['engine'] == 'gpumd'
                          and r['ranks'] == row['ranks'] and r['atoms'] == row['atoms']
                          and r['complete']), None)
        if row['engine'] == 'dmgmd' and row['profiling'] == 'off' and row['complete'] and reference:
            row['dmgmd_over_gpumd'] = reference['seconds_median'] / row['seconds_median']
        lammps = next((r for r in rows if r['case'] == row['case'] and r['engine'] == 'lammps'
                       and r['ranks'] == row['ranks'] and r['atoms'] == row['atoms']
                       and r['complete']), None)
        if row['engine'] == 'dmgmd' and row['profiling'] == 'off' and row['complete'] and lammps:
            row['dmgmd_over_lammps'] = lammps['seconds_median'] / row['seconds_median']
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
    lines = ['# Benchmark results', '', '| case | engine/backend/profile | GPUs | atoms | samples | ms/step | atom-step/s | speedup | efficiency | DMG/GPUMD | DMG/LAMMPS |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    def fmt(value):
        return '—' if value is None else f'{value:.4g}'
    for r in rows:
        lines.append(f'| {r["case"]} | {r["engine"]}/{r["backend"]}/{r["profiling"]} | {r["ranks"]} | {r["atoms"]} | '
                     f'{r["samples"]}/{repeats} | {fmt(r["ms_per_step"])} | {fmt(r["atom_steps_per_second"])} | '
                     f'{fmt(r["speedup"])} | {fmt(r["efficiency"])} | {fmt(r["dmgmd_over_gpumd"])} | '
                     f'{fmt(r["dmgmd_over_lammps"])} |')
    lines.extend(['', 'Only status=ok samples are included; ratios require complete repetitions.',
                  'Smoke/pilot are orchestration/calibration data, not publication results.',
                  'Three-engine ratios remain provisional until case-specific physical equivalence '
                  'and fixed CPU/NUMA binding are verified.', '', '## Non-valid trials', ''])
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
    p.add_argument('--engines', default='gpumd,lammps,dmgmd')
    p.add_argument('--candidate', type=Path, default=ROOT / 'build/dmg-md')
    p.add_argument('--reference', type=Path, default=ROOT.parent / 'gpumd-reference/src/gpumd')
    p.add_argument('--lammps-bin', type=Path, default=ROOT / 'build-lammps-nep-kk-preserved/lmp')
    p.add_argument('--mpiexec', default='mpiexec')
    p.add_argument('--mpiexec-arg', action='append', default=[])
    p.add_argument('--dmgmd-partition', choices=('off', 'serial'), default='off',
                   help='O2 completed-halo serial partition; explicitly passed and verified, no overlap')
    p.add_argument('--dmgmd-profiling', default='off',
                   help='comma-separated DMG-MD modes: off,trace; trace enables detailed timing '
                        'and per-step CLOCK_MONOTONIC timeline (M2a ranks > 1 only)')
    p.add_argument('--expected-cores-per-rank', type=int,
                   help='verify this many observed physical cores per rank from C1 samples')
    p.add_argument('--expected-numa-node', type=int,
                   help='verify every observed rank affinity remains on this NUMA node')
    p.add_argument('--output', type=Path, default=ROOT / 'dmgmd-benchmark',
                   help='output directory prefix; appends local YYYYMMDD-HHMMSS and a counter on collision')
    for name in ('warmup', 'steps', 'repeats'):
        p.add_argument('--' + name, type=int)
    p.add_argument('--min-seconds', type=float)
    p.add_argument('--timeout', type=float, default=7200, help='per-trial wall time limit')
    p.add_argument('--sample-interval', type=float, default=2)
    p.add_argument('--dry-run', action='store_true', help='print matrix; no GPU access, model generation or output writes')
    p.add_argument('--cpu-sampling', choices=['off', 'proc'], default='off',
                   help='opt-in local Linux rank/task sampler; requires --engines dmgmd')
    p.add_argument('--cpu-sample-interval', type=float, default=1., help='CPU sampling seconds, independent of GPU telemetry')
    p.add_argument('--cpu-output', type=Path, help='CPU root: ROOT/run-directory-name/trial-id; default trial/cpu')
    p.add_argument('--cpu-diagnostics', choices=['none', 'pidstat', 'perf'], default='none',
                   help='optional raw diagnostic attachment; failures recorded unavailable')
    p.add_argument('--cpu-capacity-scope', choices=['unknown', 'host'], default='unknown',
                   help='host attests complete host cgroup hierarchy is visible; unknown disables quota normalization')
    args = p.parse_args()
    if not math.isfinite(args.cpu_sample_interval) or args.cpu_sample_interval <= 0:
        p.error('CPU interval must be finite and positive')
    if args.cpu_sampling == 'proc':
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupted)
    if args.cpu_sampling == 'proc' and args.engines != 'dmgmd':
        p.error('CPU sampling supports --engines dmgmd only')
    if args.cpu_sampling == 'off' and (args.cpu_output or args.cpu_diagnostics != 'none' or args.cpu_capacity_scope != 'unknown'):
        p.error('CPU output/diagnostics/capacity options require --cpu-sampling proc')
    if (args.expected_cores_per_rank is None) != (args.expected_numa_node is None):
        p.error('both --expected-cores-per-rank and --expected-numa-node are required together')
    if args.expected_cores_per_rank is not None and (args.cpu_sampling != 'proc' or
            args.expected_cores_per_rank <= 0 or args.expected_numa_node < 0):
        p.error('fixed affinity verification requires --cpu-sampling proc and valid core/NUMA counts')
    cpu_options = dict(mode=args.cpu_sampling, interval=args.cpu_sample_interval,
                       output=str(args.cpu_output.resolve()) if args.cpu_output else 'TRIAL/cpu',
                       diagnostics=args.cpu_diagnostics, capacity_scope=args.cpu_capacity_scope,
                       support='single-node, same PID/mount/time/cgroup namespaces',
                       expected_cores_per_rank=args.expected_cores_per_rank,
                       expected_numa_node=args.expected_numa_node)
    profile = dict(manifest['profiles'][args.profile])
    for key in ('warmup', 'steps', 'repeats', 'min_seconds'):
        if getattr(args, key) is not None:
            profile[key] = getattr(args, key)
    ranks = [int(v) for v in args.ranks.split(',')]
    devices = args.devices.split(',')
    cases = args.cases.split(',') if args.cases else profile['cases']
    engines, backends = args.engines.split(','), args.backends.split(',')
    profiling = args.dmgmd_profiling.split(',')
    if not profiling or len(set(profiling)) != len(profiling) or not set(profiling) <= {'off', 'trace'}:
        p.error('--dmgmd-profiling must be a unique comma-separated subset of off,trace')
    if 'trace' in profiling and ('dmgmd' not in engines or 1 in ranks):
        p.error('trace requires --engines dmgmd and M2a ranks > 1')
    if (not set(ranks) <= set(manifest['ranks']) or len(ranks) != len(set(ranks))
            or len(devices) < max(ranks) or len(devices) != len(set(devices))):
        p.error('unique ranks must be 1/2/4/8, with enough distinct devices')
    if not set(engines) <= {'gpumd', 'lammps', 'dmgmd'} or not set(backends) <= set(manifest['backends']):
        p.error('invalid engine/backend')
    if len(set(engines)) != len(engines) or len(set(backends)) != len(backends) or len(set(cases)) != len(cases):
        p.error('duplicate matrix entries')
    if any(profile[k] <= 0 for k in ('warmup', 'steps', 'repeats')) or profile['min_seconds'] < 0:
        p.error('warmup/steps/repeats must be positive; min-seconds must be nonnegative')
    if args.timeout <= 0 or args.sample_interval <= 0:
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
                    for mode in (profiling if engine == 'dmgmd' else ['off']):
                        plan.append(dict(case=name, scaling=spec['scaling'], ranks=rank, atoms=case['atoms'],
                                         cells=case['cells'], box_A=lengths, engine=engine, backend=backend,
                                         profiling=mode, partition=(args.dmgmd_partition if engine == 'dmgmd' else 'off'), steps=profile['steps']))
    trials = []
    rng = random.Random(manifest['order_seed'])
    for repeat in range(profile['repeats']):
        batch = [dict(job, repeat=repeat) for job in plan]
        rng.shuffle(batch)
        trials.extend(batch)
    for i, job in enumerate(trials):
        job['id'] = (f'{i:04d}-{job["case"]}-{job["engine"]}-{job["backend"]}'
                     f'-p{job["ranks"]}-r{job["repeat"]}' +
                     (f'-{job["profiling"]}' if len(profiling) > 1 or job['profiling'] != 'off' else ''))
    if args.dry_run:
        print(json.dumps({'profile': profile, 'trials': trials, 'cpu_sampling': cpu_options,
                          'dmgmd_profiling': profiling, 'dmgmd_partition': args.dmgmd_partition,
                          'dmgmd_launch_environment': {
                              mode: {'DMGMD_DOMAIN_TIMING': str(int(mode == 'trace')),
                                     'DMGMD_DOMAIN_TRACE': str(int(mode == 'trace')),
                                     'DMGMD_DOMAIN_DIAGNOSTICS': '0',
                                     'DMGMD_DOMAIN_PARTITION': args.dmgmd_partition} for mode in profiling},
                          'mpiexec_args': args.mpiexec_arg}, indent=2))
        return 0
    output = create_output_directory(args.output)
    print(f'Output: {output}', flush=True)
    binaries = {'dmgmd': args.candidate.resolve(), 'gpumd': args.reference.resolve(),
                'lammps': args.lammps_bin.resolve()}
    identity = {name: {'path': str(binaries[name]), 'sha256': sha(binaries[name])} for name in engines}
    snap = snapshot()
    selected = resolve_devices(devices, snap)
    save(output / 'initial-gpus.json', snap)
    metadata = {'schema_version': 1, 'profile_name': args.profile, 'profile': profile,
                'hostname': socket.gethostname(), 'argv': sys.argv, 'devices': selected,
                'dmgmd_profiling': profiling, 'dmgmd_partition': args.dmgmd_partition, 'mpiexec_args': args.mpiexec_arg,
                'binaries': identity, 'manifest': manifest, 'plan': trials,
                'environment': {k: v for k, v in os.environ.items()
                                if k.startswith(('CUDA_', 'OMPI_', 'UCX_', 'PMIX_', 'DMGMD_', 'OMP_'))
                                or k in ('PATH', 'LD_LIBRARY_PATH')},
                'sources': {str(f.relative_to(ROOT)): sha(f) for f in
                            [Path(__file__), HERE / 'manifest.json', ROOT / 'tests/long_nve/long_nve_common.py',
                             ROOT / 'tests/long_nve/manifest.json',
                             ROOT / 'tests/lammps_reference/lammps_inputs.py',
                             ROOT / 'tests/lammps_reference/launch_rank.py']}}
    for name, cmd in {'dmgmd_revision': ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                      'dmgmd_status': ['git', '-C', str(ROOT), 'status', '--porcelain'],
                      'reference_revision': ['git', '-C', str(ROOT.parent / 'gpumd-reference'), 'rev-parse', 'HEAD'],
                      'reference_status': ['git', '-C', str(ROOT.parent / 'gpumd-reference'), 'status', '--porcelain'],
                      'lammps_revision': ['git', '-C', str(ROOT.parent / 'lammps-reference'), 'rev-parse', 'HEAD'],
                      'lammps_status': ['git', '-C', str(ROOT.parent / 'lammps-reference'), 'status', '--porcelain'],
                      'matpl_revision': ['git', '-C', str(ROOT.parent / 'lammps-matpl-reference'), 'rev-parse', 'HEAD'],
                      'matpl_status': ['git', '-C', str(ROOT.parent / 'lammps-matpl-reference'), 'status', '--porcelain'],
                      'gpu_topology': ['nvidia-smi', 'topo', '-m'],
                      'gpu_details': ['nvidia-smi', '-q'], 'cpu': ['lscpu'],
                      'nvcc': ['nvcc', '--version'], 'mpi': ['mpiexec', '--version'],
                      'ucx': ['ucx_info', '-v']}.items():
        try:
            metadata[name] = command_output(cmd)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            metadata[name] = str(error)
    metadata['cpu_sampling'] = cpu_options
    if args.cpu_sampling == 'proc':
        for path in (HERE / 'cpu_sampling.py', HERE / 'analyze_cpu.py'):
            metadata['sources'][str(path.relative_to(ROOT))] = sha(path)
    patch = command_output(['git', '-C', str(ROOT), 'diff', 'HEAD', '--binary'])
    (output / 'source-dirty.patch').write_text(patch)
    metadata['dirty_patch_sha256'] = sha(output / 'source-dirty.patch')
    untracked = command_output(['git', '-C', str(ROOT), 'ls-files', '--others', '--exclude-standard', '-z'])
    metadata['untracked_source_sha256'] = {f: sha(ROOT / f) for f in untracked.split('\0')
                                           if f and (ROOT / f).is_file() and
                                           f.startswith(('tests/', 'scripts/', 'src/', 'include/', 'docs/'))}
    save(output / 'metadata.json', metadata)
    if 'gpumd' in engines and metadata['reference_revision'].strip() != manifest['reference_commit']:
        raise RuntimeError('reference checkout does not match pinned commit')
    if 'lammps' in engines:
        if (metadata['lammps_revision'].strip() != manifest['lammps_commit'] or
                metadata['matpl_revision'].strip() != manifest['matpl_commit'] or
                metadata['lammps_status'].strip() or metadata['matpl_status'].strip()):
            raise RuntimeError('LAMMPS/MatPL reference checkout differs from pinned clean revisions')
        overlay = ROOT / 'third_party/lammps-matpl-overlay/manifest.json'
        metadata['lammps_overlay_manifest_sha256'] = sha(overlay)
    save(output / 'metadata.json', metadata)
    if 'dmgmd' in engines or 'lammps' in engines:
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
            cpu_session = None
            print(f'BENCHMARK {job["id"]} atoms={job["atoms"]}', flush=True)
            try:
                key = (job['case'], tuple(job['cells']))
                if key not in inputs:
                    source = output / 'inputs' / (job['case'] + '-' + 'x'.join(map(str, job['cells'])))
                    source.mkdir(parents=True)
                    case, _ = geometry(manifest['cases'][job['case']], job['ranks'])
                    (source / 'model.xyz').write_text(fixtures.generate_model(case, manifest['seed']))
                    (source / 'nep.txt').write_text(fixtures.generate_potential(case))
                    if 'lammps' in engines:
                        model_text = (source / 'model.xyz').read_text()
                        potential_text = (source / 'nep.txt').read_text()
                        converted, conversion = lammps_inputs.normalize_potential(potential_text)
                        (source / 'nep.lammps.txt').write_text(converted)
                        (source / 'data.lmp').write_text(lammps_inputs.data_file(model_text, converted))
                        save(source / 'potential-conversion.json', conversion)
                    inputs[key] = (source, case)
                source, case = inputs[key]
                for filename in ('model.xyz', 'nep.txt'):
                    (directory / filename).symlink_to(source / filename)
                if job['engine'] == 'lammps':
                    (directory / 'data.lmp').symlink_to(source / 'data.lmp')
                    (directory / 'nep.txt').unlink()
                    (directory / 'nep.txt').symlink_to(source / 'nep.lammps.txt')
                    (directory / 'in.lmp').write_text(run_input('lammps', job['ranks'], case,
                                                              profile['warmup'], profile['steps']))
                    files = ('model.xyz', 'nep.txt', 'data.lmp', 'in.lmp')
                    result['potential_conversion'] = json.loads((source / 'potential-conversion.json').read_text())
                else:
                    (directory / 'run.in').write_text(run_input(job['engine'], job['ranks'], case,
                                                              profile['warmup'], profile['steps']))
                    files = ('model.xyz', 'nep.txt', 'run.in')
                result['input_sha256'] = {filename: sha(directory / filename) for filename in files}
                visible = selected[:job['ranks']]
                # Remove ALL inherited DMGMD diagnostic/override variables.
                environment = {k: v for k, v in os.environ.items() if not k.startswith('DMGMD_')}
                environment.update(CUDA_DEVICE_ORDER='PCI_BUS_ID', CUDA_VISIBLE_DEVICES=','.join(visible),
                                   OMP_NUM_THREADS='1')
                command = [str(binaries[job['engine']])]
                if job['engine'] == 'dmgmd':
                    detailed = job['profiling'] == 'trace'
                    environment.update(DMGMD_COMM_BACKEND=job['backend'],
                                       DMGMD_DOMAIN_PARTITION=args.dmgmd_partition,
                                       DMGMD_DOMAIN_TIMING=str(int(detailed)),
                                       DMGMD_DOMAIN_TRACE=str(int(detailed)),
                                       DMGMD_DOMAIN_DIAGNOSTICS='0',
                                       DMGMD_COMM_LOG_INTERVAL=('1' if detailed else
                                           str(max(profile['warmup'], profile['steps']) + 1)))
                    if args.cpu_sampling == 'proc':
                        cpu_dir = (args.cpu_output.resolve() / output.name / job['id']
                                   if args.cpu_output else directory / 'cpu')
                        cpu_session = CpuSession(cpu_dir, job['id'], job['ranks'], binaries['dmgmd'],
                                                 args.cpu_sample_interval, args.cpu_diagnostics,
                                                 args.cpu_capacity_scope == 'host',
                                                 metadata=dict(trial_directory=str(directory),
                                                               benchmark_metadata=str(output / 'metadata.json'),
                                                               visible_devices=visible, job=job,
                                                               input_sha256=result['input_sha256']))
                        result['cpu_directory'] = str(cpu_dir)
                        command = cpu_session.wrapper(command)
                    command = [args.mpiexec, *args.mpiexec_arg, '-n', str(job['ranks']), *command]
                elif job['engine'] == 'lammps':
                    environment['LAMMPS_RANK_GPU_UUIDS'] = ','.join(visible)
                    command = [args.mpiexec, *args.mpiexec_arg, '-n', str(job['ranks']),
                               sys.executable, str(ROOT / 'tests/lammps_reference/launch_rank.py'),
                               '--binary', str(binaries['lammps']), '--input', 'in.lmp']
                result['command'] = command
                result['launch_environment'] = {k: environment[k] for k in environment
                                                if k.startswith(('CUDA_', 'DMGMD_', 'LAMMPS_', 'OMP_'))}
                if sha(binaries[job['engine']]) != identity[job['engine']]['sha256']:
                    raise RuntimeError('binary changed during benchmark')
                before = snapshot()
                save(directory / 'before.json', before)
                if cpu_session:
                    cpu_session.start()
                try:
                    code, wall, timeout = execute(command, directory, environment,
                                                  args.timeout, args.sample_interval, cpu_session)
                finally:
                    if cpu_session:
                        cpu_session.stop('launcher_finished_or_interrupted')
                result.update(returncode=code, process_wall_seconds=wall)
                if timeout:
                    raise RuntimeError('trial timeout (process group terminated)')
                if code:
                    raise RuntimeError(f'exit {code}; inspect stdout.txt/stderr.txt (including possible OOM)')
                seconds = parse_timing((directory / 'stdout.txt').read_text(), job['engine'], job['ranks'],
                                       job['atoms'], profile['warmup'], profile['steps'], job['backend'],
                                       visible if job['engine'] == 'lammps' else None)
                if job['engine'] == 'dmgmd' and job['ranks'] > 1:
                    result['actual_profiling'] = actual_dmgmd_profiling(
                        (directory / 'stdout.txt').read_text(), job['profiling'])
                if job['engine'] == 'dmgmd':
                    result['actual_partition'] = actual_dmgmd_partition(
                        (directory / 'stdout.txt').read_text(), args.dmgmd_partition)
                if job['engine'] == 'lammps':
                    thermo = parse_lammps_thermo((directory / 'stdout.txt').read_text(),
                                                  profile['warmup'] + profile['steps'], job['atoms'])
                    result['thermo_schema'] = ['step', 'atoms', 'temp_K', 'pe_eV', 'ke_eV',
                                               'etotal_eV', 'press_bar']
                else:
                    thermo = parse_thermo((directory / 'thermo.out').read_text())
                result.update(seconds=seconds, atom_steps_per_second=job['atoms'] * job['steps'] / seconds,
                              final_thermo=thermo, status='ok')
                if seconds < profile['min_seconds']:
                    result.update(status='too_short', error='increase --steps; timing below min-seconds')
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
                result['error'] = str(error)
            except KeyboardInterrupt:
                result.update(status='interrupted', error='user interrupted')
                raise
            finally:
                if cpu_session:
                    cpu_session.stop(result.get('error', result['status']))
                    try:
                        markers = None
                        if job['profiling'] == 'trace' and result['status'] == 'ok':
                            markers = write_trace_markers(
                                (directory / 'stdout.txt').read_text(), cpu_session.directory)
                            result['cpu_markers'] = str(markers)
                        cpu_summary = analyze_cpu(cpu_session.directory, markers_path=markers)
                        result['cpu_status'] = ('ok' if cpu_summary['collector_complete'] and
                                                all(m['status'] == 'verified' for m in cpu_summary['mappings'])
                                                else 'partial')
                        if result['status'] == 'ok' and result['cpu_status'] != 'ok':
                            result.update(status='invalid_cpu_observation',
                                          error='C1 collector or rank/GPU mapping was incomplete')
                        if args.expected_cores_per_rank is not None:
                            result['cpu_binding'] = verify_cpu_binding(
                                cpu_session.directory, args.expected_cores_per_rank,
                                args.expected_numa_node)
                    except (OSError, ValueError, KeyError) as error:
                        result.update(cpu_status='partial', cpu_error=str(error))
                        if args.expected_cores_per_rank is not None:
                            result.update(status='invalid_cpu_binding', error=str(error))
                results.append(result)
                save(directory / 'result.json', result)
                report(output, results, profile['repeats'])
                print(f'BENCHMARK status={result["status"]} {result.get("error", "")}', flush=True)
    finally:
        report(output, results, profile['repeats'])
    print(f'Report: {output / "summary.md"}')
    return 0 if len(results) == len(trials) and all(r['status'] == 'ok' and r.get('cpu_status', 'ok') == 'ok' for r in results) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError) as error:
        print(f'BENCHMARK ERROR: {error}', file=sys.stderr)
        sys.exit(1)
