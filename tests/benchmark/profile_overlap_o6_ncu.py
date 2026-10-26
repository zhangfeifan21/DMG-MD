#!/usr/bin/env python3
"""Collect available steady O6 hardware metrics; preserve permission failures.

Only rank zero is instrumented; it uses GPU0 for every frozen scope. Kernel
replay never includes MPI, but disturbs peer waits, caches and scheduling. Its
durations must not replace the separately collected CUPTI or main wall times.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import run_benchmark as b
from run_overlap_o5 import controlled_environment, freeze, MODES
from run_overlap_o6 import identity_check, KERNELS, load
from cpu_binding import Audit, digest
from o5_rank_output import redirect_and_exec


def rank_exec(directory, config, command):
    job = load(config)
    if os.environ['OMPI_COMM_WORLD_RANK'] == '0':
        command = [*job['ncu_prefix'], *command]
    redirect_and_exec(directory, command)


def parse_metrics(text):
    lines = text.splitlines()
    start = next((i for i,s in enumerate(lines) if s.startswith('"ID","Process ID"')), None)
    if start is None:
        raise ValueError('NCU raw metric header missing')
    rows = list(csv.DictReader(io.StringIO('\n'.join(lines[start:]))))
    if not rows or any(not r.get('Metric Name') for r in rows):
        raise ValueError('empty or malformed NCU metrics')
    return rows


def collect(root):
    experiment, base = identity_check(root)
    windows = load(root/'ncu-windows.json')
    if len(windows) != 9 or any(not w['ordinary'] for w in windows):
        raise ValueError('nine ordinary steady windows required before NCU collection')
    jobs = []
    for window in sorted(windows, key=lambda w:(w['case']!='carbon_200k', w['ranks'], w['mode'])):
        for cache in ('none', 'all'):
            jobs.append(dict(window, cache_control=cache,
                id=f'{window["case"]}-p{window["ranks"]}-{window["mode"]}-cache-{cache}',
                sections=base['profiler']['ncu_sections']))
    plan = dict(identity=base['identity'], windows_sha256=digest(root/'ncu-windows.json'),
                tool_sha256=digest(__file__), jobs=jobs,
                permission_policy='after ERR_NVGPUCTRPERM on GPU0, mark remaining same-GPU jobs NOT_RUN; try one static LaunchStats fallback',
                cache_control='none preserves existing cache state but replay still perturbs it; all explicitly flushes caches',
                clocks='none; no exclusive reservation or fixed frequency',
                replay='kernel; collect target kernels only; no MPI replays; diagnostic walls never performance samples')
    freeze(root/'ncu-plan.json', plan)
    results, blocked = [], False
    for job in jobs:
        if blocked:
            results.append(dict(id=job['id'], status='NOT_RUN', reason='same GPU0 performance-counter permission denied'))
            continue
        result = attempt(experiment, job)
        results.append(result)
        if result['status'] == 'INCONCLUSIVE' and 'ERR_NVGPUCTRPERM' in result.get('error', ''):
            blocked = True
            fallback = dict(job, id=job['id']+'-launch-stats', sections=['LaunchStats'])
            results.append(attempt(experiment, fallback))
    analyze(root)


def analyze(root):
    """Rebuild availability solely from the frozen schedule and raw attempts."""
    plan = load(root/'ncu-plan.json')
    results, blocked = [], False
    for job in plan['jobs']:
        path = root/'ncu'/job['id']/'result.json'
        if path.exists():
            result = load(path)
            results.append(result)
            blocked |= 'ERR_NVGPUCTRPERM' in result.get('error', '')
            fallback = root/'ncu'/(job['id']+'-launch-stats')/'result.json'
            if fallback.exists():
                results.append(load(fallback))
        else:
            results.append(dict(id=job['id'], status='NOT_RUN' if blocked else 'NOT_OBSERVED',
                                reason='same GPU0 performance-counter permission denied' if blocked else 'missing attempt'))
    status = 'PASS' if all(r['status']=='PASS' for r in results) else 'INCONCLUSIVE'
    b.save(root/'ncu-analysis.json', dict(status=status, results=results,
        unavailable_metrics=['effective threads/warps', 'achieved occupancy', 'memory transactions/cache hit rates',
                             'scheduler issue/stall metrics'] if blocked else [],
        static_evidence='Nsight Systems CUPTI grid/block/register/local-memory attributes remain available',
        cause='performance-counter permission' if blocked else 'see per-attempt metadata',
        exact_unprofiled_wall_decomposition='NOT_IDENTIFIED'))
    print('NCU', status, flush=True)


def attempt(experiment, job):
    directory = experiment.root/'ncu'/job['id']
    if (directory/'result.json').exists():
        return load(directory/'result.json')
    directory.mkdir(parents=True, exist_ok=False)
    source, case = experiment.inputs[job['case']]
    for name in ('model.xyz', 'nep.txt'):
        (directory/name).symlink_to(source/name)
    (directory/'run.in').write_text(b.run_input('dmgmd', job['ranks'], case, job['warmup'], job['steps']))
    prefix = ['ncu', '--target-processes', 'all', '--kernel-name-base', 'function',
              '--kernel-name', 'regex:(^|::)(('+ '|'.join(KERNELS) +'))$',
              '--launch-skip', str(job['launch_skip']), '--launch-count', str(job['launch_count']),
              '--replay-mode', 'kernel', '--cache-control', job['cache_control'], '--clock-control', 'none',
              '--export', str(directory/'metrics')]
    for section in job['sections']:
        prefix.extend(['--section', section])
    freeze(directory/'rank-config.json', dict(job, ncu_prefix=prefix))
    audit = Audit(directory/'binding', experiment.bindings[job['ranks']], experiment.binaries['o4'])
    command = ['mpiexec', *audit.mpi_args(), '-n', str(job['ranks']), sys.executable, str(Path(__file__).resolve()),
               'rank', str(directory), str(directory/'rank-config.json'), '--',
               *audit.wrapper([str(experiment.binaries['o4'])])]
    partition, overlap = MODES[job['mode']]
    env = controlled_environment()
    env.update(CUDA_DEVICE_ORDER='PCI_BUS_ID', CUDA_VISIBLE_DEVICES=','.join(experiment.devices[:job['ranks']]),
               OMP_NUM_THREADS='1', DMGMD_COMM_BACKEND='CudaAware', DMGMD_DOMAIN_PARTITION=partition,
               DMGMD_DOMAIN_OVERLAP=overlap, DMGMD_DOMAIN_DIAGNOSTICS='0', DMGMD_DOMAIN_TIMING='0',
               DMGMD_DOMAIN_TRACE='0', DMGMD_OVERLAP_TRACE='0', DMGMD_COMM_LOG_INTERVAL=str(job['warmup']+job['steps']+1))
    result = dict(job, command=command, environment=env, status='INCONCLUSIVE',
                  binary_sha256=digest(experiment.binaries['o4']),
                  input_sha256={n:digest(directory/n) for n in ('model.xyz','nep.txt','run.in')})
    b.save(directory/'launch.json', result)
    try:
        code, wall, timeout = b.execute(command, directory, env, 900, 2, binding_audit=audit)
        output = ''.join(p.read_text() for p in sorted((directory/'rank-output').glob('*')))
        (directory/'combined-rank-output.txt').write_text(output)
        result.update(returncode=code, process_wall_seconds=wall, timeout=timeout)
        try:
            result['binding'] = audit.finish(output)
        except ValueError as error:
            result['binding_error'] = str(error)
        if 'ERR_NVGPUCTRPERM' in output:
            result['error'] = 'ERR_NVGPUCTRPERM: GPU performance counters restricted to administrators'
        elif code or timeout:
            result['error'] = f'NCU exit={code}, timeout={timeout}; see rank output'
        else:
            export = ['ncu', '--import', str(directory/'metrics.ncu-rep'), '--page', 'raw', '--csv', '--print-units', 'base']
            b.save(directory/'export-command.json', dict(command=export))
            csv_text = subprocess.check_output(export, text=True)
            (directory/'metrics.csv').write_text(csv_text)
            metrics = parse_metrics(csv_text)
            if len({r['ID'] for r in metrics}) != job['launch_count']:
                raise ValueError('NCU kernel count differs from frozen steady window')
            b.save(directory/'metrics.json', metrics)
            result['metric_rows'] = len(metrics)
            result['science_sha256'] = digest(directory/'thermo.out')
            oracle = experiment.root/'profiles'/job['source_profile']/'thermo.out'
            result['scientific_output_matches_profile'] = result['science_sha256'] == digest(oracle)
            if result.get('binding_error') or not result['scientific_output_matches_profile']:
                result['error'] = 'binding or science validation failed; metrics not admitted'
            else:
                result['status'] = 'PASS'
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        result['error'] = str(error)
    finally:
        if not audit.file.closed:
            audit.close()
        b.save(directory/'result.json', result)
    print(job['id'], result['status'], result.get('error', ''), flush=True)
    return result


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='rank':
        if sys.argv[4]!='--':
            raise SystemExit('invalid rank wrapper')
        rank_exec(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[5:])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--root', type=Path, required=True)
        args = parser.parse_args()
        root = args.root.resolve()
        with (root/'scheduler.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            collect(root)
