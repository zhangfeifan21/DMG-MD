#!/usr/bin/env python3
"""Independent O8 experiments with original full, O7 full and O8 full controls."""
import argparse
import fcntl
import json
from pathlib import Path
import random
import shutil
import statistics

import run_benchmark as benchmark
import run_overlap_o5 as o5
import o5_rank_output
from run_overlap_o7 import binding_identity, validate_result
from analyze_overlap_o5 import distribution, paired_ratio, save, write_csv
from cpu_binding import digest


VERSIONS = ('original', 'o7', 'o8')


def load(path):
    return json.loads(Path(path).read_text())


def design(root):
    generator = random.Random(2026102308)
    scopes = [['carbon_200k', 2], ['water_400k', 8]]
    jobs = []
    for repeat in range(5):
        configurations = [(case, ranks, backend) for case, ranks in scopes
                          for backend in ('HostStaged', 'CudaAware')]
        generator.shuffle(configurations)
        for case, ranks, backend in configurations:
            versions = list(VERSIONS)
            generator.shuffle(versions)
            for version in versions:
                jobs.append(dict(case=case, ranks=ranks, backend=backend, version=version,
                    mode='full', repeat=repeat, steps=1500 if ranks == 2 else 4300,
                    warmup=200 if ranks == 2 else 1600,
                    id=f'{len(jobs):03d}-b{repeat}-{case}-p{ranks}-{backend}-{version}'))
    o5.freeze(root/'frozen-design.json', dict(stage='O8', date='2026-10-23',
        head=benchmark.command_output(['git', 'rev-parse', 'HEAD']).strip(), scopes=scopes,
        scope_reason='Carbon radial bottleneck and highest-rank water; P4, other inputs and overlap adoption are outside this incremental experiment.',
        controls={version: digest(root/f'dmg-md-{version}') for version in VERSIONS[:2]},
        minimum_formal_seconds=10, target_formal_seconds=[20, 30], jobs=jobs,
        statistics=dict(estimator='ratio of medians', bootstrap='paired process blocks',
                        samples=20000, seed=2026102309, minimum_blocks=5),
        extension_rule='If any contrast CI width / estimate > 0.10, add five complete three-version blocks for that scope/backend, once; preserve all failures and slow samples.',
        invalid_rule='Retain invalid raw trials; replacement requires a separately frozen complete block.',
        adoption='Adopt only after all correctness/count gates pass and no significant full regression; inconclusive speed remains inconclusive.',
        diagnostics='Separate trace/proc sampling, actual PMPI count probe; no profiler in main; formal CPU is null.'))
    print('FROZEN O8 design', flush=True)


def experiment(root):
    result = o5.Experiment(root)
    result.binaries.update({version: root/f'dmg-md-{version}' for version in VERSIONS})
    return result


def freeze(root, previous):
    frozen = load(root/'frozen-design.json')
    for version, expected in frozen['controls'].items():
        if digest(root/f'dmg-md-{version}') != expected:
            raise ValueError('control binary changed')
    o5.freeze(root/'design.json', dict(cases=sorted({case for case, ranks in frozen['scopes']}),
        ranks=sorted({ranks for case, ranks in frozen['scopes']}),
        resource=dict(gpu_indices=list(range(8)))))
    alias = root/'dmg-md-o4'
    if not alias.exists():
        alias.symlink_to('dmg-md-o8')
    for case in load(root/'design.json')['cases']:
        target = root/'inputs'/case
        target.mkdir(parents=True, exist_ok=True)
        for filename in ('model.xyz', 'nep.txt'):
            source = previous/'inputs'/case/filename
            if not (target/filename).exists():
                shutil.copy2(source, target/filename)
            if digest(source) != digest(target/filename):
                raise ValueError('accepted input changed')
    current = experiment(root)
    sources = [Path(__file__), benchmark.HERE/'test_overlap_o8.py', benchmark.HERE/'o5_rank_output.py',
               benchmark.ROOT/'src/domain_runtime.cu', benchmark.ROOT/'tests/mpi/run_mpi_domain.py',
               benchmark.ROOT/'tests/mpi/run_thermo_demand.py', benchmark.ROOT/'tests/mpi/thermo_demand_wrappers.cpp']
    o5.freeze(root/'frozen-plan.json', dict(design_sha256=digest(root/'frozen-design.json'),
        identity=current.identity(VERSIONS), bindings=binding_identity(current.bindings),
        sources={str(path.relative_to(benchmark.ROOT)): digest(path) for path in sources}))
    if not (root/'machine.json').exists():
        save(root/'machine.json', benchmark.snapshot())
        (root/'implementation.patch').write_text(benchmark.command_output(['git', 'diff', '--binary']))
        for name, command in [('cuda', ['nvcc', '--version']), ('mpi', ['mpiexec', '--version']),
                              ('ucx', ['ucx_info', '-v'])]:
            (root/f'{name}-version.txt').write_text(benchmark.command_output(command))
    print('FROZEN O8 identity', flush=True)


def checked(root, offline=False):
    plan = load(root/'frozen-plan.json')
    if digest(root/'frozen-design.json') != plan['design_sha256']:
        raise ValueError('design changed')
    for version, metadata in plan['identity']['binaries'].items():
        if digest(root/f'dmg-md-{version}') != metadata['sha256']:
            raise ValueError('binary changed')
    for case, files in plan['identity']['inputs'].items():
        for filename, expected in files.items():
            if digest(root/'inputs'/case/filename) != expected:
                raise ValueError('input changed')
    for name, expected in plan['sources'].items():
        if digest(benchmark.ROOT/name) != expected:
            raise ValueError('collector changed: '+name)
    current = None if offline else experiment(root)
    if current and (current.identity(VERSIONS) != plan['identity'] or
                    binding_identity(current.bindings) != plan['bindings']):
        raise ValueError('resource/environment identity changed')
    return current, load(root/'frozen-design.json')


def validate_trial(result):
    validate_result(result)
    if result['version'] == 'o8':
        text = (Path(result['directory'])/'stdout.txt').read_text()
        rows = [benchmark.fields(line) for line in text.splitlines()
                if line.startswith('DMGMD_DOMAIN_SUMMARY ')]
        for row in rows:
            expected = 1 if int(row['sequence']) == 1 else 0
            if (int(row['thermo_steps']) != expected or
                    int(row['thermo_skipped_steps']) != int(row['steps'])-expected):
                raise ValueError('O8 actual thermo schedule differs: '+result['id'])


def science(root):
    current, frozen = checked(root)
    results = []
    for case, ranks in frozen['scopes']:
        for backend in ('HostStaged', 'CudaAware'):
            reference = None
            for version in VERSIONS:
                for mode in ('full', 'split-serial', 'overlap-wait', 'overlap-poll'):
                    job = dict(case=case, ranks=ranks, backend=backend, version=version,
                        mode=mode, steps=3, warmup=2,
                        id=f'{case}-p{ranks}-{backend}-{version}-{mode}')
                    result = current.trial('science', job)
                    validate_trial(result)
                    reference = reference or result['scientific_outputs']
                    if result['scientific_outputs'] != reference:
                        raise ValueError('science files differ: '+job['id'])
                    results.append(result)
    o5.freeze(root/'science.json', dict(status='PASS', jobs=len(results),
        candidate_sha256=digest(root/'dmg-md-o8'), all_scientific_files_identical=True))


def gates(root):
    markers = {'environment-p8.log': 'PASS environment:',
               'golden.log': 'PASS: all 4 baseline cases match',
               'mpi-domain.log': 'PASS: M2a domain matrix',
               'thermo-demand.log': 'PASS O8 thermo demand differential',
               'long-nve.log': 'PASS long-NVE profile=nightly'}
    for filename, marker in markers.items():
        if marker not in (root/filename).read_text():
            raise ValueError('missing gate: '+filename)
    long_nve = load(root/'long-nve.json')
    if (long_nve['candidate_sha256'] != digest(root/'dmg-md-o8') or
            long_nve['completed_configs'] != long_nve['total_configs']):
        raise ValueError('incomplete long NVE gate')
    demand = load(root/'thermo-demand/summary.json')
    if demand['status'] != 'PASS' or demand['candidate_sha256'] != digest(root/'dmg-md-o8'):
        raise ValueError('count gate differs')
    if demand['results_sha256'] != digest(root/'thermo-demand/results.json'):
        raise ValueError('count gate results changed')
    if load(root/'science.json')['status'] != 'PASS':
        raise ValueError('missing actual-input science gate')
    o5.freeze(root/'acceptance.json', {name: digest(root/name)
        for name in [*markers, 'long-nve.json', 'thermo-demand/summary.json', 'science.json']})


def main_trials(root):
    current, frozen = checked(root)
    gates(root)
    jobs = list(frozen['jobs'])
    if (root/'extension-plan.json').exists():
        jobs += load(root/'extension-plan.json')['jobs']
    for job in jobs:
        validate_trial(current.trial('main', job, minimum=frozen['minimum_formal_seconds']))


def diagnostics(root):
    o5_rank_output.install()
    current, frozen = checked(root)
    gates(root)
    for case, ranks in frozen['scopes']:
        for backend in ('HostStaged', 'CudaAware'):
            for version in ('o7', 'o8'):
                job = dict(case=case, ranks=ranks, backend=backend, version=version,
                    mode='full', steps=700 if ranks == 2 else 1800,
                    warmup=200 if ranks == 2 else 1600, diagnostics='trace', sampling='proc',
                    id=f'{case}-p{ranks}-{backend}-{version}')
                result = current.trial('diagnostics', job)
                if result.get('error') or result.get('cpu_status') != 'PASS':
                    raise ValueError('incomplete CPU diagnostic: '+str(result.get('error')))
                validate_trial(result)
                summary = load(Path(result['directory'])/'cpu/summary.json')
                phases = [row for row in summary['phases'] if row['phase'] == 'run' and row['sequence'] == 1]
                if len(phases) != ranks or any(row['coverage'] < .8 for row in phases):
                    raise ValueError('incomplete formal marker CPU coverage')
    print('PASS O8 marker CPU diagnostics', flush=True)


def analyze(root):
    _, frozen = checked(root, offline=True)
    gates(root)
    plan = load(root/'frozen-plan.json')
    jobs = list(frozen['jobs'])
    if (root/'extension-plan.json').exists():
        jobs += load(root/'extension-plan.json')['jobs']
    rows = []
    for job in jobs:
        result = load(root/'main'/job['id']/'result.json')
        validate_trial(result)
        if (any(result.get(key) != value for key, value in job.items()) or
                result['binary_sha256'] != plan['identity']['binaries'][job['version']]['sha256'] or
                result['input_hashes'] != plan['identity']['inputs'][job['case']] or
                result['seconds'] < frozen['minimum_formal_seconds'] or result['formal_cpu'] is not None):
            raise ValueError('formal trial contract changed')
        rows.append({key: result[key] for key in ('id', 'case', 'ranks', 'backend', 'version',
            'repeat', 'steps', 'warmup', 'seconds', 'ms_per_step', 'scientific_outputs', 'formal_cpu')})
    summaries = []
    extensions = []
    for case, ranks in frozen['scopes']:
        for backend in ('HostStaged', 'CudaAware'):
            selected = [row for row in rows if (row['case'], row['ranks'], row['backend']) == (case, ranks, backend)]
            values = {version: {row['repeat']: row for row in selected if row['version'] == version}
                      for version in VERSIONS}
            if len(values['original']) < 5 or any(set(values[version]) != set(values['original']) for version in VERSIONS):
                raise ValueError('incomplete three-version blocks')
            for repeat, reference in values['original'].items():
                if any(values[version][repeat]['scientific_outputs'] != reference['scientific_outputs']
                       for version in VERSIONS):
                    raise ValueError('paired scientific bytes differ')
            for before, after in [('original', 'o7'), ('o7', 'o8'), ('original', 'o8')]:
                measurements = {version: {repeat: row['ms_per_step'] for repeat, row in values[version].items()}
                                for version in (before, after)}
                ratio = paired_ratio(measurements[before], measurements[after], seed=frozen['statistics']['seed'])
                low, high = ratio['ci95']
                if (high-low)/ratio['estimate'] > .1:
                    extensions.append((case, ranks, backend))
                summaries.append(dict(case=case, ranks=ranks, backend=backend, before=before, after=after,
                    baseline=distribution(list(measurements[before].values())),
                    candidate=distribution(list(measurements[after].values())), speedup=ratio,
                    delta_ms=statistics.median(measurements[before].values())-statistics.median(measurements[after].values()),
                    conclusion='SPEEDUP' if low > 1 else 'REGRESSION' if high < 1 else 'INCONCLUSIVE'))
    save(root/'analysis.json', dict(status='PASS', formal_trials=len(rows), summaries=summaries,
        extension_scopes=sorted(set(extensions)), formal_cpu=None,
        minimum_seconds=min(row['seconds'] for row in rows), maximum_seconds=max(row['seconds'] for row in rows)))
    write_csv(root/'trials.csv', rows)
    print(json.dumps(summaries, indent=2), flush=True)
    if extensions and not (root/'extension-plan.json').exists():
        raise ValueError('five additional complete blocks required: '+str(sorted(set(extensions))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('design', 'freeze', 'science', 'main', 'diagnostics', 'analyze'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--previous', type=Path, default=benchmark.ROOT/'dmgmd-overlap-o7-20261023')
    args = parser.parse_args()
    root = args.root.resolve()
    with (root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.phase == 'freeze':
            freeze(root, args.previous.resolve())
        else:
            globals()[{'main': 'main_trials'}.get(args.phase, args.phase)](root)


if __name__ == '__main__':
    main()
