#!/usr/bin/env python3
"""Frozen O6 diagnostics using the unchanged O4 production executable.

GPU jobs are serialized. O5 data are read only as provenance and input sources;
all new trials, including failures, live under an independent --root.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys

import run_benchmark as b
import run_overlap_o5 as o5
from cpu_binding import Audit, digest
import o5_rank_output as rank_output

HERE = Path(__file__).resolve().parent
SCOPES = [('carbon_200k', 2), ('water_400k', 4), ('water_400k', 8)]
MODES = ['full', 'split-serial', 'overlap-wait']
KERNELS = ['find_neighbor_list_large_box', 'find_descriptor', 'find_force_radial',
           'find_partial_force_angular', 'gpu_find_force_many_body_domain']


def load(path):
    return json.loads(Path(path).read_text())


def jobs(repeats, seed, steps, warmups):
    rng = random.Random(seed)
    result = []
    for repeat in range(repeats):
        block = [dict(case=c, ranks=p, mode=m, backend='CudaAware', version='o4',
                      repeat=repeat, steps=steps[c], warmup=warmups[c])
                 for c, p in SCOPES for m in MODES]
        rng.shuffle(block)
        for job in block:
            job['id'] = f'{len(result):03d}-b{repeat}-{job["case"]}-p{job["ranks"]}-{job["mode"]}'
            result.append(job)
    return result


def initialize(root, previous, binary):
    root.mkdir(parents=True, exist_ok=True)
    if (root/'frozen-plan.json').exists():
        return
    if digest(binary) != digest(previous/'dmg-md-o4'):
        raise RuntimeError('O6 requires the exact previously accepted production binary')
    design = dict(schema_version=1, stage='O6', cases=['carbon_200k', 'water_400k'],
                  ranks=[2, 4, 8], resource=dict(gpu_indices=list(range(8))),
                  previous=str(previous), backend='CudaAware')
    o5.freeze(root/'design.json', design)
    target = root/'dmg-md-o4'
    if not target.exists():
        shutil.copy2(binary, target)
    for case in design['cases']:
        directory = root/'inputs'/case
        directory.mkdir(parents=True, exist_ok=True)
        for name in ('model.xyz', 'nep.txt'):
            if not (directory/name).exists():
                shutil.copy2(previous/'inputs'/case/name, directory/name)
    experiment = o5.Experiment(root)
    warmups = dict(carbon_200k=200, water_400k=1600)
    # O5's accepted stabilization and fastest durations set conservative lengths.
    # These are new paired blocks, not O5 samples. Every formal trial must be >=10s.
    main = jobs(5, 2026102106, dict(carbon_200k=1500, water_400k=4300), warmups)
    profiles = jobs(3, 2026102107, dict(carbon_200k=128, water_400k=128), warmups)
    plan = dict(schema_version=1, stage='O6', identity=experiment.identity(),
                bindings=experiment.bindings, source_revision=b.command_output(['git', 'rev-parse', 'HEAD']).strip(),
                dirty_before=b.command_output(['git', 'status', '--short']),
                tool_sha256={str(p.relative_to(b.ROOT)):digest(p) for p in
                             (Path(__file__), HERE/'o5_rank_output.py', HERE/'profile_overlap_o5.py')},
                scopes=SCOPES, modes=MODES, main=main, profiles=profiles,
                profile_window=dict(sequence=1, steps=128, exclude='setup and rebuild steps',
                    mapping='last 128 normalize_global_thermo launches, checked against per-rank step logs; no clock splice',
                    repetitions='three independent processes; steps/ranks are not independent repetitions'),
                profiler=dict(nsys_trace='cuda,mpi', cuda_event_trace=False,
                    cpu_sample='none', kernel_names=KERNELS,
                    ncu_rank=0, ncu_formal_step=64, ncu_replay='kernel',
                    ncu_cache_controls=['none', 'all'], ncu_clock_control='none',
                    ncu_sections=['LaunchStats', 'Occupancy', 'SchedulerStats',
                                  'WarpStateStats', 'MemoryWorkloadAnalysis', 'SourceCounters'],
                    ncu_rule='derive exact launch skip from corresponding steady Nsight trace; permission failures INCONCLUSIVE'),
                main_minimum_seconds=10, formal_cpu=None,
                statistics=dict(unit='independent paired process block', repeats=5,
                    estimator='ratio of medians', interval='paired-block bootstrap 20000 resamples, pointwise 95%',
                    seed=2026102108, speed_outliers='never removed',
                    extension='append five complete blocks for a scope only if any ratio CI width/estimate >10%; one extension maximum'),
                invalid='preserve attempt and stop; append explicitly frozen replacement only for contract-invalid runs',
                conditions=dict(exclusive=False, clocks_locked=False, memory_residency='UNKNOWN'),
                gates='canonical environment, committed single-rank golden, actual-input short full/serial/wait/verify-wait; reuse unchanged-binary O4/O5 long_nve evidence',
                ncu_wall='diagnostic only; never an exclusive decomposition of unprofiled step wall')
    o5.freeze(root/'frozen-plan.json', plan)
    (root/'initial.patch').write_text(b.command_output(['git', 'diff', '--binary']))
    for name, cmd in [('nsys', ['nsys', '--version']), ('ncu', ['ncu', '--version']),
                      ('nvcc', ['nvcc', '--version']), ('mpi', ['mpiexec', '--version']),
                      ('ucx', ['ucx_info', '-v'])]:
        (root/f'{name}-version.txt').write_text(b.command_output(cmd))
    b.save(root/'machine.json', b.snapshot())
    print('FROZEN', root, flush=True)


def identity_check(root):
    experiment = o5.Experiment(root)
    plan = load(root/'frozen-plan.json')
    if experiment.identity() != plan['identity']:
        raise RuntimeError('frozen binary/input/toolchain/environment identity differs')
    for name, expected in plan['tool_sha256'].items():
        if digest(b.ROOT/name) != expected:
            raise RuntimeError(f'frozen collection tool changed: {name}')
    return experiment, plan


def gates(experiment, plan):
    root = experiment.root
    if (root/'gates.json').exists():
        if load(root/'gates.json')['status'] != 'PASS':
            raise RuntimeError('previous gate failed; retain it and diagnose')
        return
    directory = root/'gates'
    directory.mkdir(exist_ok=True)
    audit = Audit(directory/'environment-binding', experiment.bindings[8], experiment.binaries['o4'])
    audit.close()
    commands = [
        ('environment', [sys.executable, str(b.ROOT/'tests/mpi/check_environment.py'),
                         '--candidate', str(experiment.binaries['o4']), '--devices', ','.join(experiment.devices),
                         '--ranks', '8', *['--mpiexec-arg='+v for v in audit.mpi_args()]]),
        ('golden', [sys.executable, str(b.ROOT/'tests/baseline/run_baselines.py'),
                    '--candidate', str(experiment.binaries['o4']), '--device', experiment.devices[0]])]
    for name, command in commands:
        b.save(directory/f'{name}-command.json', dict(command=command, environment=o5.controlled_environment()))
        with (directory/f'{name}.log').open('x') as output:
            subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)
        print('GATE', name, 'PASS', flush=True)
    results = []
    for case, ranks in SCOPES:
        for mode in [*MODES, 'verify-wait']:
            job = dict(case=case, ranks=ranks, mode=mode, backend='CudaAware', version='o4',
                       steps=2, warmup=2, id=f'{case}-p{ranks}-{mode}')
            result = experiment.trial('science', job)
            if result['status'] != 'PASS':
                raise RuntimeError('science execution failed')
            results.append(result)
    comparisons = []
    for result in results:
        ref = next(r for r in results if (r['case'], r['ranks'], r['mode']) == (result['case'], result['ranks'], 'full'))
        comparisons.append(dict(id=result['id'], reference=ref['id'],
                                passed=result['scientific_outputs'] == ref['scientific_outputs']))
    status = 'PASS' if all(c['passed'] for c in comparisons) else 'FAILED'
    b.save(root/'gates.json', dict(status=status, comparisons=comparisons,
                                 binary_sha256=plan['identity']['binaries']['o4']['sha256']))
    if status != 'PASS':
        raise RuntimeError('science output differs')


def run_trials(experiment, plan, group):
    if load(experiment.root/'gates.json')['status'] != 'PASS':
        raise RuntimeError('gates required')
    original = b.execute
    if group == 'profiles':
        def profile(command, directory, environment, timeout, interval, cpu_session=None, binding_audit=None):
            wrapped = ['nsys', 'profile', '--trace=cuda,mpi', '--mpi-impl=openmpi',
                       '--sample=none', '--cpuctxsw=none', '--cuda-event-trace=false',
                       '--output', str(directory/'trace'), *command]
            b.save(directory/'profiler-command.json', dict(command=wrapped, environment=environment))
            return original(wrapped, directory, environment, timeout, interval, cpu_session, binding_audit)
        b.execute = profile
    try:
        for base in plan['main' if group == 'main' else 'profiles']:
            job = dict(base)
            if group == 'profiles':
                job.update(diagnostics='trace', sampling='off')
            result = experiment.trial(group, job, minimum=plan['main_minimum_seconds'] if group == 'main' else 0)
            if result['status'] != 'PASS':
                raise RuntimeError('trial failed; preserve raw attempt')
            if group == 'profiles':
                directory = Path(result['directory'])
                if not (directory/'trace.sqlite').exists():
                    command = ['nsys', 'export', '--type=sqlite', '--output', str(directory/'trace.sqlite'), str(directory/'trace.nsys-rep')]
                    b.save(directory/'export-command.json', dict(command=command))
                    with (directory/'export.log').open('x') as output:
                        subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)
    finally:
        b.execute = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['freeze', 'gates', 'main', 'profiles'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--previous', type=Path, default=b.ROOT/'dmgmd-overlap-o5-20261016')
    parser.add_argument('--binary', type=Path, default=b.ROOT/'build/dmg-md')
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.phase == 'freeze':
            initialize(root, args.previous.resolve(), args.binary.resolve())
            return
        experiment, plan = identity_check(root)
        rank_output.install()
        if args.phase == 'gates':
            gates(experiment, plan)
        else:
            run_trials(experiment, plan, args.phase)


if __name__ == '__main__':
    main()
