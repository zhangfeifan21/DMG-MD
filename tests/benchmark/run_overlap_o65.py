#!/usr/bin/env python3
"""Independent O6.5 baseline, activity traces, and immutable snapshot replays.

Run from the repository root after sourcing ../env/md-mpi.sh. GPU jobs are
serialized; short pilot/replay/profile jobs never enter formal wall statistics.
"""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys

import run_benchmark as b
import run_overlap_o5 as o5
import o5_rank_output
from cpu_binding import digest

SCOPES = [('carbon_200k', 2), ('water_400k', 8)]
MODES = ['full', 'split-serial', 'overlap-wait']


def load(path):
    return json.loads(Path(path).read_text())


def experiment(root):
    result = o5.Experiment(root)
    result.binaries['o4'] = root/'dmg-md-baseline'
    result.binaries['diagnostic'] = root/'dmg-md-diagnostic'
    return result


def schedule(repeats, seed, steps, group):
    rng = random.Random(seed)
    result = []
    for repeat in range(repeats):
        block = [(case, ranks, mode) for case, ranks in SCOPES for mode in MODES]
        rng.shuffle(block)
        for case, ranks, mode in block:
            result.append(dict(id=f'{len(result):03d}-b{repeat}-{case}-p{ranks}-{mode}',
                case=case, ranks=ranks, mode=mode, repeat=repeat, version='o4',
                backend='CudaAware', steps=steps[case],
                warmup=200 if ranks == 2 else 1600,
                **(dict(diagnostics='trace') if group == 'profiles' else {})))
    return result


def initialize(root):
    o5.freeze(root/'design.json', dict(stage='O6.5', cases=[c for c, _ in SCOPES],
        ranks=[2, 8], resource=dict(gpu_indices=list(range(8)))))
    alias = root/'dmg-md-o4'
    if not alias.exists():
        alias.symlink_to('dmg-md-baseline')
    for case, _ in SCOPES:
        target = root/'inputs'/case
        target.mkdir(parents=True, exist_ok=True)
        for name in ('model.xyz', 'nep.txt'):
            if not (target/name).exists():
                shutil.copy2(b.ROOT/'dmgmd-overlap-o8-20261023/inputs'/case/name, target/name)
    current = experiment(root)
    o5.freeze(root/'frozen-design.json', dict(stage='O6.5', date='2026-10-26',
        identity=current.identity(), bindings=current.bindings,
        pilot=schedule(1, 2026102664, {c:256 for c, _ in SCOPES}, 'pilot'),
        profiles=schedule(3, 2026102665, {c:128 for c, _ in SCOPES}, 'profiles'),
        main_seed=2026102666, bootstrap_seed=2026102667,
        main_rule='After pilot freeze common steps per scope: ceil(25 seconds / fastest pilot ms per step), round up to 100 steps; >=10s; five paired blocks.',
        extension='If any ratio CI width/estimate >0.10, append five complete blocks for that scope once; retain all slow/invalid attempts.',
        statistics='ratio of medians; 20000 paired-block bootstrap samples; pointwise 95% intervals; ranks/steps/replays are not independent repetitions',
        invalid='Stop and preserve invalid attempt. Replacements need separately frozen complete blocks.',
        replay=dict(variants=['A', 'B', 'C', 'D'], warmup=20, iterations=100,
            repeats=5, seed=2026102668,
            snapshot='first ordinary formal step at or after warmup+16, derived from new split-serial trace per rank',
            unit='one independent process per snapshot/rank in each block; aggregate ranks within block',
            order='shuffle variants independently per process, frozen before replays',
            timing='CUDA events around target kernel(s) only; output reset, compaction and consumer validation excluded',
            cache='producer-ready loaded immutable snapshots; repeated warm cache, no cache flushing; differs from real pipeline'),
        conditions=dict(exclusive=False, clocks_locked=False, memory_residency='UNKNOWN'),
        production='HostStaged/full, overlap off; no adoption in O6.5'))
    b.save(root/'machine.json', b.snapshot())
    for name, command in [('cuda', ['nvcc', '--version']), ('mpi', ['mpiexec', '--version']),
                          ('ucx', ['ucx_info', '-v']), ('nsys', ['nsys', '--version']), ('ncu', ['ncu', '--version'])]:
        (root/f'{name}-version.txt').write_text(b.command_output(command))
    print('FROZEN O6.5 design', flush=True)


def checked(root):
    current = experiment(root)
    frozen = load(root/'frozen-design.json')
    identity = current.identity()
    for key in ('devices', 'libraries', 'environment_entry_sha256', 'binaries', 'inputs', 'sources'):
        if identity[key] != frozen['identity'][key]:
            raise ValueError('frozen baseline identity differs: '+key)
    for ranks, binding in current.bindings.items():
        if binding['mapping'] != frozen['bindings'][str(ranks)]['mapping']:
            raise ValueError('frozen CPU/NUMA mapping differs')
    return current, frozen


def main_jobs(root):
    """Use only a separately frozen complete-block replacement after interruption."""
    original = load(root/'main-plan.json')['jobs']
    recovery_path = root/'main-recovery-plan.json'
    if not recovery_path.exists():
        return original
    recovery = load(recovery_path)
    if recovery['original_plan_sha256'] != digest(root/'main-plan.json'):
        raise ValueError('main recovery original plan changed')
    jobs = recovery['jobs']
    configuration = lambda job: json.dumps({k:v for k,v in job.items() if k != 'id'}, sort_keys=True)
    if sorted(map(configuration, jobs)) != sorted(map(configuration, original)):
        raise ValueError('main recovery changed paired configurations')
    if len({job['id'] for job in jobs}) != len(jobs):
        raise ValueError('duplicate main recovery job')
    for repeat in recovery['replaced_blocks']:
        old = {job['id'] for job in original if job['repeat'] == repeat}
        new = {job['id'] for job in jobs if job['repeat'] == repeat}
        if old & new:
            raise ValueError('recovery must replace the entire paired block')
    untouched = lambda rows: [job for job in rows if job['repeat'] not in recovery['replaced_blocks']]
    if untouched(jobs) != untouched(original):
        raise ValueError('recovery changed unaffected blocks')
    return jobs


def run_group(root, group):
    current, frozen = checked(root)
    if not (root/'baseline-gates.json').exists():
        raise ValueError('baseline environment and golden gates required')
    if group == 'extend':
        previous = load(root/'wall-analysis.json')
        if not previous['extension_scopes']:
            print('No main extension required', flush=True)
            return
        main = load(root/'main-plan.json')
        scopes = {tuple(row) for row in previous['extension_scopes']}
        jobs = schedule(5, frozen['main_seed']+1, main['steps'], 'main')
        jobs = [dict(job, repeat=job['repeat']+5, id='extension-'+job['id'])
                for job in jobs if (job['case'], job['ranks']) in scopes]
        o5.freeze(root/'extension-plan.json', dict(jobs=jobs,
            reason='predeclared CI width/estimate exceeds 10%', scopes=sorted(scopes)))
        group = 'main'
    elif group == 'main':
        if not (root/'main-plan.json').exists():
            steps = {}
            for case, _ in SCOPES:
                rows = [load(root/'pilot'/j['id']/'result.json') for j in frozen['pilot'] if j['case'] == case]
                if any(r['status'] != 'PASS' for r in rows):
                    raise ValueError('incomplete pilot')
                steps[case] = math.ceil(25000/min(r['ms_per_step'] for r in rows)/100)*100
            o5.freeze(root/'main-plan.json', dict(steps=steps,
                jobs=schedule(5, frozen['main_seed'], steps, 'main')))
        jobs = main_jobs(root)
    else:
        jobs = frozen[group]
    original = b.execute
    if group == 'profiles':
        def execute(command, directory, environment, timeout, interval, cpu_session=None, binding_audit=None):
            wrapped = ['nsys', 'profile', '--trace=cuda,mpi', '--mpi-impl=openmpi',
                '--sample=none', '--cpuctxsw=none', '--cuda-event-trace=false',
                '--output', str(directory/'trace'), *command]
            b.save(directory/'profiler-command.json', dict(command=wrapped, environment=environment))
            return original(wrapped, directory, environment, timeout, interval, cpu_session, binding_audit)
        b.execute = execute
    try:
        for job in jobs:
            result = current.trial(group, job, minimum=10 if group == 'main' else 0)
            if result['status'] != 'PASS':
                raise ValueError('failed trial: '+str(result.get('error')))
            if group == 'profiles':
                directory = Path(result['directory'])
                if not (directory/'trace.sqlite').exists():
                    command = ['nsys', 'export', '--type=sqlite', '--output', str(directory/'trace.sqlite'), str(directory/'trace.nsys-rep')]
                    with (directory/'export.log').open('x') as output:
                        subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)
    finally:
        b.execute = original


def baseline_gates(root):
    current, frozen = checked(root)
    commands = [
        ('environment', [sys.executable, str(b.ROOT/'tests/mpi/check_environment.py'), '--candidate',
            str(current.binaries['o4']), '--devices', ','.join(current.devices), '--ranks', '8']),
        ('golden', [sys.executable, str(b.ROOT/'tests/baseline/run_baselines.py'), '--candidate',
            str(current.binaries['o4']), '--device', current.devices[0]])]
    for name, command in commands:
        if not (root/f'baseline-{name}.log').exists():
            b.save(root/f'baseline-{name}-command.json', dict(command=command))
            with (root/f'baseline-{name}.log').open('x') as output:
                subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)
        marker = 'PASS environment:' if name == 'environment' else 'PASS: all 4 baseline cases match'
        if marker not in (root/f'baseline-{name}.log').read_text():
            raise ValueError('incomplete baseline gate: '+name)
    outputs = []
    for case, ranks in SCOPES:
        reference = None
        for mode in [*MODES, 'verify-wait']:
            job = dict(id=f'{case}-p{ranks}-{mode}', case=case, ranks=ranks, mode=mode,
                backend='CudaAware', version='o4', steps=3, warmup=2)
            row = current.trial('baseline-science', job)
            reference = reference or row.get('scientific_outputs')
            if row['status'] != 'PASS' or row['scientific_outputs'] != reference:
                raise ValueError('baseline science differs')
            outputs.append(row['id'])
    o5.freeze(root/'baseline-gates.json', dict(status='PASS', jobs=outputs,
        binary_sha256=digest(current.binaries['o4'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['freeze', 'gates', 'pilot', 'profiles', 'main', 'extend'])
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        o5_rank_output.install()
        if args.phase == 'freeze':
            initialize(root)
        elif args.phase == 'gates':
            baseline_gates(root)
        else:
            run_group(root, args.phase)


if __name__ == '__main__':
    main()
