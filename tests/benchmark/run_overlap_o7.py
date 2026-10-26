#!/usr/bin/env python3
"""Frozen O7 before/after experiments; reuse O5 input, binding and trial contracts.

GPU jobs are serialized. Diagnostics, science and formal timing stay separate.
No O5/O6 sample is modified or included in the new paired statistics.
"""
import argparse
import fcntl
import json
from pathlib import Path
import shutil
import statistics
import subprocess

import run_benchmark as b
import run_overlap_o5 as o5
import o5_rank_output
from analyze_overlap_o5 import distribution, paired_ratio, save, write_csv
from cpu_binding import digest


def load(path):
    return json.loads(Path(path).read_text())


def experiment(root):
    e = o5.Experiment(root)
    e.binaries.update(before=root/'dmg-md-before', after=root/'dmg-md-after')
    return e


def binding_identity(bindings):
    """Keep resource constraints/mapping, excluding live observation counters."""
    stable = json.loads(json.dumps(bindings))
    for binding in stable.values():
        capacity = binding['capacity']
        capacity.pop('t_begin_ns', None)
        capacity.pop('t_end_ns', None)
        for ancestor in capacity['ancestors']:
            ancestor.pop('cpu.stat', None)
    return stable


def freeze(root, previous):
    design = load(root/'frozen-design.json')
    if digest(root/'dmg-md-before') != design['baseline_sha256']:
        raise ValueError('baseline changed')
    o5.freeze(root/'design.json', dict(cases=sorted({c for c, p in design['scopes']}),
        ranks=[2, 4, 8], resource=dict(gpu_indices=list(range(8)))))
    alias = root/'dmg-md-o4'
    if not alias.exists():
        alias.symlink_to('dmg-md-after')
    for case in load(root/'design.json')['cases']:
        target = root/'inputs'/case
        target.mkdir(parents=True, exist_ok=True)
        for filename in ('model.xyz', 'nep.txt'):
            source = previous/'inputs'/case/filename
            if not (target/filename).exists():
                shutil.copy2(source, target/filename)
            if digest(source) != digest(target/filename):
                raise ValueError('input differs from accepted fixture')
    e = experiment(root)
    tools = [Path(__file__), b.HERE/'test_overlap_o7.py', b.HERE/'o5_rank_output.py', b.HERE/'analyze_overlap_o5.py',
             b.ROOT/'tests/mpi/run_cache_decision.py', b.ROOT/'tests/cache_decision_wrappers.cpp']
    plan = dict(design_sha256=digest(root/'frozen-design.json'),
                identity=e.identity(('before', 'after')), bindings=e.bindings,
                tool_sha256={str(p.relative_to(b.ROOT)): digest(p) for p in tools},
                gate_sha256={p.name: digest(p) for p in
                    (root/'cache-gates.json', root/'long-nve.json') if p.exists()})
    o5.freeze(root/'frozen-plan.json', plan)
    if not (root/'machine.json').exists():
        b.save(root/'machine.json', b.snapshot())
        for name, command in [('nvcc', ['nvcc', '--version']),
                              ('mpi', ['mpiexec', '--version']), ('ucx', ['ucx_info', '-v'])]:
            (root/f'{name}-version.txt').write_text(b.command_output(command))
        (root/'implementation.patch').write_text(b.command_output(['git', 'diff', '--binary']))
    print('FROZEN O7', flush=True)


def checked(root, offline=False):
    plan = load(root/'frozen-plan.json')
    e = None
    if offline:
        for version, metadata in plan['identity']['binaries'].items():
            if digest(root/f'dmg-md-{version}') != metadata['sha256']:
                raise ValueError('frozen binary changed')
        for case, files in plan['identity']['inputs'].items():
            for name, expected in files.items():
                if digest(root/'inputs'/case/name) != expected:
                    raise ValueError('frozen input changed')
    else:
        e = experiment(root)
        if (e.identity(('before', 'after')) != plan['identity'] or
                binding_identity(e.bindings) != binding_identity(plan['bindings'])):
            raise ValueError('frozen binary/input/environment/binding identity differs')
    if digest(root/'frozen-design.json') != plan['design_sha256']:
        raise ValueError('design changed')
    for name, expected in plan['tool_sha256'].items():
        if digest(b.ROOT/name) != expected:
            raise ValueError('frozen collector changed: '+name)
    for name, expected in plan['gate_sha256'].items():
        if digest(root/name) != expected:
            raise ValueError('frozen acceptance changed: '+name)
    return e, load(root/'frozen-design.json')


def science(root):
    e, design = checked(root)
    # Existing gates ran against the exact frozen candidate. Their logs retain
    # full commands in gate-commands.sh and independent raw work directories.
    required = {'environment-p8.log': 'PASS environment:', 'golden.log': 'PASS: all 4 baseline cases match',
                'neighbor-cuda.log': 'all checks passed',
                'mpi-domain.log': 'PASS: M2a domain matrix'}
    for filename, marker in required.items():
        if marker not in (root/filename).read_text():
            raise ValueError('missing acceptance gate: '+filename)
    cache = load(root/'cache-gates.json')
    if (cache['status'] != 'PASS' or cache['completed_jobs'] != 108 or
            cache['candidate_sha256'] != digest(root/'dmg-md-after')):
        raise ValueError('incomplete cache boundary/count gates')
    for source in cache['sources']:
        if digest(root/source['path']) != source['sha256']:
            raise ValueError('cache acceptance source changed')
    results = []
    for case, ranks in design['scopes']:
        for backend in ('HostStaged', 'CudaAware'):
            reference = None
            for version in ('before', 'after'):
                for mode in ('full', 'split-serial', 'overlap-wait'):
                    job = dict(case=case, ranks=ranks, backend=backend, version=version,
                               mode=mode, steps=2, warmup=2,
                               id=f'{case}-p{ranks}-{backend}-{version}-{mode}')
                    result = e.trial('science', job)
                    validate_result(result)
                    reference = reference or result['scientific_outputs']
                    if result['scientific_outputs'] != reference:
                        raise ValueError('science bytes differ: '+job['id'])
                    results.append(result)
    o5.freeze(root/'science.json', dict(status='PASS', jobs=len(results),
        candidate_sha256=digest(root/'dmg-md-after'), all_scientific_files_identical=True))


def validate_result(result):
    if result['status'] != 'PASS':
        raise ValueError('failed/invalid trial: '+result['id'])
    directory = Path(result['directory'])
    for name, expected in result['scientific_outputs'].items():
        if digest(directory/name) != expected:
            raise ValueError('scientific output changed: '+result['id']+'/'+name)
    if digest(directory/'run.in') != result['input_sha256']['run.in']:
        raise ValueError('trial input changed: '+result['id'])
    requested = o5.MODES[result['mode']][1]
    text = (directory/'stdout.txt').read_text()
    o5.validate_schedule(text, result['ranks'], requested)


def main_trials(root):
    e, design = checked(root)
    long_nve = load(root/'long-nve.json')
    if (load(root/'science.json')['status'] != 'PASS' or
            long_nve['candidate_sha256'] != digest(root/'dmg-md-after') or
            long_nve['completed_configs'] != long_nve['total_configs'] or
            'PASS long-NVE profile=nightly' not in (root/'long-nve.log').read_text()):
        raise ValueError('science and long_nve gates required')
    jobs = list(design['jobs'])
    if (root/'extension-plan.json').exists():
        jobs += load(root/'extension-plan.json')['jobs']
    for job in jobs:
        validate_result(e.trial('main', job, minimum=design['minimum_formal_seconds']))


def analyze(root):
    _, design = checked(root, offline=True)
    identity = load(root/'frozen-plan.json')['identity']
    jobs = list(design['jobs'])
    if (root/'extension-plan.json').exists():
        jobs += load(root/'extension-plan.json')['jobs']
    rows = []
    for job in jobs:
        r = load(root/'main'/job['id']/'result.json')
        if (any(r.get(k) != v for k, v in job.items()) or
                r['binary_sha256'] != identity['binaries'][job['version']]['sha256'] or
                r['input_hashes'] != identity['inputs'][job['case']]):
            raise ValueError('trial differs from frozen configuration: '+job['id'])
        validate_result(r)
        if r['seconds'] < design['minimum_formal_seconds'] or r['formal_cpu'] is not None:
            raise ValueError('formal measurement contract differs')
        rows.append({k: r[k] for k in ('id', 'case', 'ranks', 'backend', 'version', 'repeat',
                                       'steps', 'warmup', 'seconds', 'ms_per_step',
                                       'scientific_outputs', 'binary_sha256', 'formal_cpu')})
    summaries = []
    extensions = []
    for case, ranks in design['scopes']:
        for backend in ('HostStaged', 'CudaAware'):
            selected = [r for r in rows if (r['case'], r['ranks'], r['backend']) == (case, ranks, backend)]
            by_version = {v: {r['repeat']: r for r in selected if r['version'] == v} for v in ('before', 'after')}
            if set(by_version['before']) != set(by_version['after']) or len(by_version['before']) < 5:
                raise ValueError('incomplete paired blocks')
            for repeat, before in by_version['before'].items():
                if before['scientific_outputs'] != by_version['after'][repeat]['scientific_outputs']:
                    raise ValueError('paired science output differs')
            values = {v: {i: r['ms_per_step'] for i, r in by_version[v].items()} for v in by_version}
            ratio = paired_ratio(values['before'], values['after'], seed=design['statistics']['seed'])
            lo, hi = ratio['ci95']
            if (hi-lo)/ratio['estimate'] > .1:
                extensions.append((case, ranks, backend))
            summaries.append(dict(case=case, ranks=ranks, backend=backend,
                before=distribution(list(values['before'].values())),
                after=distribution(list(values['after'].values())), speedup=ratio,
                delta_ms=statistics.median(values['before'].values())-statistics.median(values['after'].values()),
                conclusion='SPEEDUP' if lo > 1 else 'REGRESSION' if hi < 1 else 'INCONCLUSIVE'))
    save(root/'analysis.json', dict(status='PASS', formal_trials=len(rows), summaries=summaries,
        extension_scopes=extensions, minimum_seconds=min(r['seconds'] for r in rows),
        maximum_seconds=max(r['seconds'] for r in rows), formal_cpu=None,
        scope='O7 full only; paired process blocks, no speed sample removed',
        adoption='REVIEW_REGRESSION' if any(r['conclusion']=='REGRESSION' for r in summaries) else 'ADOPT'))
    write_csv(root/'trials.csv', rows)
    print(json.dumps(summaries, indent=2), flush=True)
    if extensions and not (root/'extension-plan.json').exists():
        raise ValueError('frozen width rule requires five additional complete blocks for: '+str(extensions))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=('freeze', 'science', 'main', 'analyze'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--previous', type=Path, default=b.ROOT/'dmgmd-overlap-o6-20261021')
    args = p.parse_args()
    root = args.root.resolve()
    with (root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.phase == 'freeze':
            freeze(root, args.previous.resolve())
        else:
            o5_rank_output.install()
            {'science': science, 'main': main_trials, 'analyze': analyze}[args.phase](root)


if __name__ == '__main__':
    main()
