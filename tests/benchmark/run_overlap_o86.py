#!/usr/bin/env python3
"""Frozen O8.6 six-control experiments; GPU jobs run serially and retain raw attempts."""
import argparse
import fcntl
import json
import math
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
BACKENDS = ['HostStaged', 'CudaAware']
VARIANTS = [('baseline','full'), ('baseline','split-serial'),
            ('baseline','producer-serial'), ('baseline','producer-wait'),
            ('candidate','full'), ('candidate','split-serial'),
            ('candidate','producer-serial'), ('candidate','producer-wait'),
            ('candidate','compact-serial'), ('candidate','compact-wait'),
            ('candidate','geometry-serial'), ('candidate','geometry-wait')]
VERSIONS = ('baseline','candidate')
SCHEDULES = {'full':'split','split-serial':'split',
    'producer-serial':'producer','producer-wait':'producer',
    'compact-serial':'producer-compact','compact-wait':'producer-compact',
    'geometry-serial':'producer-geometry','geometry-wait':'producer-geometry'}
o5.MODES.update({name:('serial','wait' if name.endswith('-wait') else 'off')
                 for name in SCHEDULES if name not in ('full','split-serial')})



def load(path):
    return json.loads(Path(path).read_text())


def schedule(repeats, seed, steps, group):
    rng, jobs = random.Random(seed), []
    for repeat in range(repeats):
        configurations = [(case, ranks, backend) for case, ranks in SCOPES for backend in BACKENDS]
        rng.shuffle(configurations)
        for case, ranks, backend in configurations:
            variants = list(VARIANTS)
            rng.shuffle(variants)
            for version, mode in variants:
                jobs.append(dict(id=f'{len(jobs):03d}-b{repeat}-{case}-p{ranks}-{backend}-{version}-{mode}',
                    case=case, ranks=ranks, backend=backend, version=version, mode=mode,
                    schedule=SCHEDULES[mode], repeat=repeat,
                    warmup=200 if ranks == 2 else 1600, steps=steps[case],
                    **(dict(diagnostics='trace') if group == 'profiles' else {})))
    return jobs


def experiment(root):
    current = o5.Experiment(root)
    current.binaries.update({v: root/f'dmg-md-{v}' for v in VERSIONS})
    return current


def initialize(root):
    design = load(root/'implementation-plan.json')
    o5.freeze(root/'design.json', dict(stage='O8.6', cases=[c for c, _ in SCOPES], ranks=[2, 8],
        resource=dict(gpu_indices=list(range(8)))))
    if not (root/'dmg-md-o4').exists():
        (root/'dmg-md-o4').symlink_to('dmg-md-baseline')
    for case, _ in SCOPES:
        target = root/'inputs'/case
        target.mkdir(parents=True, exist_ok=True)
        for name in ('model.xyz', 'nep.txt'):
            source = b.ROOT/'dmgmd-overlap-o8-20261023/inputs'/case/name
            if not (target/name).exists():
                shutil.copy2(source, target/name)
            if digest(source) != digest(target/name):
                raise ValueError('accepted input changed')
    current = experiment(root)
    cpu = [dict(j, id='cpu-'+j['id'], sampling='proc', diagnostics='trace') for j in
           schedule(1, design['seeds']['profiles']+1, {'carbon_200k':700, 'water_400k':1800}, 'profiles')
           if j['version']=='candidate' and j['schedule'].startswith('producer')]
    source_names = [str(p.relative_to(b.ROOT)) for pattern in ('src/*.cu', 'src/gpumd_compat/*.cu',
        'src/gpumd_compat/*.cuh', 'src/gpumd_compat/*.hpp') for p in b.ROOT.glob(pattern)]
    source_names += ['tests/benchmark/'+name for name in ('run_overlap_o86.py', 'analyze_overlap_o86.py',
                     'test_overlap_o86.py', 'recompute_overlap_o86.py', 'o5_rank_output.py')]
    source_names += ['tests/mpi/run_mpi_domain.py', 'tests/long_nve/run_long_nve.py',
                     'tests/domain_neighbor_cuda_tests.cu']
    o5.freeze(root/'frozen-plan.json', dict(design=design, design_sha256=digest(root/'implementation-plan.json'),
        identity=current.identity(VERSIONS), bindings=current.bindings,
        diagnostic_amendment=None,
        predata_amendment=load(root/'predata-amendment.json') if (root/'predata-amendment.json').exists() else None,
        sources={name:digest(b.ROOT/name) for name in sorted(source_names)},
        pilot=schedule(1, design['seeds']['pilot'], {c:256 for c, _ in SCOPES}, 'pilot'),
        profiles=schedule(1, design['seeds']['profiles'], {c:128 for c, _ in SCOPES}, 'profiles'), cpu=cpu))
    if not (root/'machine.json').exists():
        b.save(root/'machine.json', b.snapshot())
        for name, command in [('cuda', ['nvcc', '--version']), ('mpi', ['mpiexec', '--version']),
                              ('ucx', ['ucx_info', '-v']), ('nsys', ['nsys', '--version'])]:
            (root/f'{name}-version.txt').write_text(b.command_output(command))
    print('FROZEN O8.6 identity and design', flush=True)


def checked(root, offline=False):
    plan = load(root/'frozen-plan.json')
    if digest(root/'implementation-plan.json') != plan['design_sha256']:
        raise ValueError('design changed')
    for version, identity in plan['identity']['binaries'].items():
        if digest(root/f'dmg-md-{version}') != identity['sha256']:
            raise ValueError('frozen binary changed')
    for case, files in plan['identity']['inputs'].items():
        for name, expected in files.items():
            if digest(root/'inputs'/case/name) != expected:
                raise ValueError('frozen input changed')
    sources = dict(plan['sources'])
    amendments = root/'collector-amendments.json'
    if amendments.exists():
        for amendment in load(amendments):
            for name, change in amendment['files'].items():
                if not name.startswith('tests/benchmark/') or sources.get(name) != change['before']:
                    raise ValueError('invalid collector amendment chain: '+name)
                sources[name] = change['after']
            for name, expected in amendment['validation'].items():
                if digest(root/name) != expected: raise ValueError('collector amendment validation changed')
    for name, expected in sources.items():
        if digest(b.ROOT/name) != expected:
            raise ValueError('source changed after freeze: '+name)
    current = None if offline else experiment(root)
    if current:
        identity = current.identity(VERSIONS)
        for key in ('devices', 'libraries', 'environment_entry_sha256', 'binaries', 'inputs', 'sources'):
            if identity[key] != plan['identity'][key]:
                raise ValueError('frozen environment changed: '+key)
        for ranks, binding in current.bindings.items():
            if binding['mapping'] != plan['bindings'][str(ranks)]['mapping']:
                raise ValueError('frozen CPU/NUMA mapping changed')
    return current, plan


def validate_trial(root, job, group):
    directory = root/group/job['id']
    result = load(directory/'result.json')
    if result['status'] != 'PASS' or any(result.get(k) != v for k, v in job.items()):
        raise ValueError('failed/mismatched trial: '+job['id'])
    text = (directory/'stdout.txt').read_text()
    partition, overlap = o5.MODES[job['mode']]
    actual_overlap = o5.validate_schedule(text, job['ranks'], overlap)
    split = result.get('actual_partition', {}).get('split_calls', 0)
    if partition != 'off' and not split:
        raise ValueError('all-fallback trial cannot stand for split')
    if overlap != 'off' and actual_overlap['calls'] != split:
        raise ValueError('actual overlap and split differ')
    if job['version'] == 'candidate' or job['schedule'] == 'producer':
        configs = [b.fields(s) for s in text.splitlines() if s.startswith('DMGMD_SCHEDULE_CONFIG ')]
        if len(configs) != 1 or configs[0].get('schedule') != job['schedule']:
            raise ValueError('actual producer configuration differs')
        if partition != 'off' and job['schedule'].startswith('producer'):
            rows = [b.fields(s) for s in text.splitlines() if s.startswith('DMGMD_PARTITION_SUMMARY ')]
            if len(rows) != job['ranks']*2:
                raise ValueError('missing producer summary')
            for row in rows:
                s, f = int(row['cumulative_split_calls']), int(row['cumulative_full_calls'])
                if int(row['cumulative_producer_calls']) != s:
                    raise ValueError('producer execution not observed')
                for name, expected in [('filter', 2*s+f), ('descriptor', 2*s+f),
                                       ('radial', s+f), ('partial', s+f), ('many_body', s+f)]:
                    if int(row[f'cumulative_{name}_launches']) != expected:
                        raise ValueError('actual seven-launch count differs: '+name)
    if job['schedule'] in ('producer-compact','producer-geometry'):
        summaries=[b.fields(s) for s in text.splitlines() if s.startswith('DMGMD_PARTITION_SUMMARY ')]
        if any(int(r.get('cumulative_compact_calls',-1)) != int(r['cumulative_split_calls']) for r in summaries):
            raise ValueError('compact mapping not actually executed')
    if group == 'main' and (result['seconds'] < 10 or result['formal_cpu'] is not None):
        raise ValueError('formal timing/CPU contract failed')
    if result['binary_sha256'] != digest(root/f'dmg-md-{job["version"]}'):
        raise ValueError('trial binary differs')
    return result


def trial(current, root, group, job):
    original = o5.controlled_environment
    def environment(source=None):
        result = original(source)
        result['DMGMD_DOMAIN_SCHEDULE'] = job['schedule']
        return result
    o5.controlled_environment = environment
    try:
        current.trial(group, job, minimum=10 if group == 'main' else 0)
    finally:
        o5.controlled_environment = original
    return validate_trial(root, job, group)


def gates(root):
    current, plan = checked(root)
    required = {
        'baseline-environment.log':'PASS environment:', 'candidate-environment.log':'PASS environment:',
        'baseline-golden.log':'PASS: all 4 baseline cases match',
        'candidate-golden.log':'PASS: all 4 baseline cases match',
        'gpu-ctest-final.log':'100% tests passed', 'analysis-unit-final.log':'OK',
        **{f'{kind}-{rule}.log':marker for rule in ('compact','geometry') for kind,marker in (
            ('mpi','PASS: M2a domain matrix'),('schedule','PASS O4 schedule:'),
            ('nightly','PASS long-NVE profile=nightly'))},
    }
    for name, marker in required.items():
        if marker not in (root/name).read_text():
            raise ValueError('incomplete correctness gate: '+name)
    for rule in ('compact','geometry'):
        report=load(root/f'nightly-{rule}.json')
        if report.get('completed_configs') != 42 or report.get('total_configs') != 42:
            raise ValueError('nightly gate not PASS: '+rule)
    jobs = []
    for job in schedule(1, 2026102880, {c:3 for c, _ in SCOPES}, 'science'):
        job = dict(job, warmup=2)
        trial(current, root, 'science', job)
        jobs.append(job)
    for case, ranks in SCOPES:
        for backend in BACKENDS:
            rows = [load(root/'science'/j['id']/'result.json')['scientific_outputs'] for j in jobs
                    if (j['case'], j['backend']) == (case, backend)]
            if any(row != rows[0] for row in rows):
                raise ValueError('twelve-control scientific output differs')
    o5.freeze(root/'gates.json', dict(status='PASS', jobs=jobs,
        evidence={name:digest(root/name) for name in [*required,'nightly-compact.json','nightly-geometry.json']},
        binaries=plan['identity']['binaries']))


def main_jobs(root):
    jobs = load(root/'main-plan.json')['jobs']
    recovery = root/'main-recovery-plan.json'
    if not recovery.exists():
        return jobs
    plan = load(recovery)
    if plan['original_sha256'] != digest(root/'main-plan.json'):
        raise ValueError('recovery original plan changed')
    new = plan['jobs']
    key = lambda j:json.dumps({k:v for k,v in j.items() if k != 'id'}, sort_keys=True)
    if sorted(map(key, jobs)) != sorted(map(key, new)) or len({j['id'] for j in new}) != len(new):
        raise ValueError('recovery changed configurations')
    for repeat in plan['replaced_blocks']:
        if {j['id'] for j in jobs if j['repeat'] == repeat} & {j['id'] for j in new if j['repeat'] == repeat}:
            raise ValueError('recovery must replace entire paired block')
    if [j for j in jobs if j['repeat'] not in plan['replaced_blocks']] != [
            j for j in new if j['repeat'] not in plan['replaced_blocks']]:
        raise ValueError('recovery changed unaffected blocks')
    return new


def run_group(root, group):
    current, plan = checked(root)
    if not (root/'gates.json').exists():
        raise ValueError('complete correctness gates required')
    for name, expected in load(root/'gates.json')['evidence'].items():
        if digest(root/name) != expected: raise ValueError('gate evidence changed: '+name)
    if group == 'main':
        if not (root/'main-plan.json').exists():
            steps = {}
            for case, _ in SCOPES:
                rows = [validate_trial(root, j, 'pilot') for j in plan['pilot'] if j['case'] == case]
                steps[case] = math.ceil(25000/min(r['ms_per_step'] for r in rows)/100)*100
            o5.freeze(root/'main-plan.json', dict(steps=steps,
                jobs=schedule(5, plan['design']['seeds']['main'], steps, 'main')))
        jobs = main_jobs(root)
    elif group == 'extend':
        scopes = load(root/'wall-analysis.json')['extension_scopes']
        if not scopes:
            print('No extension required', flush=True)
            return
        jobs = [dict(j, repeat=j['repeat']+5, id='extension-'+j['id']) for j in schedule(5,
            plan['design']['seeds']['main']+1, load(root/'main-plan.json')['steps'], 'main')
            if [j['case'], j['backend']] in scopes]
        o5.freeze(root/'extension-plan.json', dict(jobs=jobs, scopes=scopes))
        group = 'main'
    else:
        jobs = plan[group]
    original = b.execute
    if group == 'profiles':
        def execute(command, directory, environment, timeout, interval, cpu_session=None, binding_audit=None):
            wrapped = ['nsys', 'profile', '--trace=cuda,mpi', '--mpi-impl=openmpi', '--sample=none',
                       '--cpuctxsw=none', '--cuda-event-trace=false', '--output', str(directory/'trace'), *command]
            b.save(directory/'profiler-command.json', dict(command=wrapped, environment=environment))
            return original(wrapped, directory, environment, timeout, interval, cpu_session, binding_audit)
        b.execute = execute
    try:
        for job in jobs:
            result = trial(current, root, group, job)
            directory = Path(result['directory'])
            if group == 'profiles' and not (directory/'trace.sqlite').exists():
                command = ['nsys', 'export', '--type=sqlite', '--output', str(directory/'trace.sqlite'),
                           str(directory/'trace.nsys-rep')]
                with (directory/'export.log').open('x') as stream:
                    subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True)
    finally:
        b.execute = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['freeze', 'gates', 'pilot', 'main', 'extend', 'profiles', 'cpu'])
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    with (root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        o5_rank_output.install()
        if args.phase == 'freeze': initialize(root)
        elif args.phase == 'gates': gates(root)
        else: run_group(root, args.phase)


if __name__ == '__main__':
    main()
