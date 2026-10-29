#!/usr/bin/env python3
"""Run O9 independent confirmation (default); --legacy-probe replays historical O9.

Historical probe only:

Reuse the accepted O6 binary/window for a counter-availability probe. A missing
or failed experiment is PARTIAL; only an actual counter-permission rejection
can close this no-candidate branch. Available counters require a new candidate
design and experiment, never automatic adoption. All output goes to --root.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys

import run_benchmark as b
import run_overlap_o5 as o5
from run_overlap_o7 import binding_identity
from cpu_binding import Audit, digest
from profile_overlap_o6_ncu import attempt


REFERENCES = ('gpumd-reference', 'lammps-reference', 'lammps-matpl-reference')
SECTIONS = ['LaunchStats', 'Occupancy', 'SchedulerStats', 'WarpStateStats',
            'MemoryWorkloadAnalysis', 'SourceCounters']


def load(path):
    return json.loads(Path(path).read_text())


def git(*args, cwd=b.ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True)


def references():
    return {name: dict(head=git('rev-parse', 'HEAD', cwd=b.ROOT.parent/name).strip(),
                      status=git('status', '--short', '--untracked-files=all', cwd=b.ROOT.parent/name),
                      ignored=git('ls-files', '--others', '--ignored', '--exclude-standard',
                                  cwd=b.ROOT.parent/name).splitlines()) for name in REFERENCES}


def copy_verified(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    if digest(source) != digest(target):
        raise ValueError(f'preserved file differs: {target}')


def preserve(root, previous, accepted):
    if not (root/'initial-state.json').exists():
        changed = set(git('diff', '--name-only').splitlines()) | set(
            git('ls-files', '--others', '--exclude-standard').splitlines())
        files = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split('\0')
        production = {n: digest(b.ROOT/n) for n in files if n and
                      (n.startswith(('src/', 'include/')) or n == 'CMakeLists.txt')}
        for name in sorted(changed):
            if (b.ROOT/name).is_file():
                copy_verified(b.ROOT/name, root/'initial-source'/name)
        o5.freeze(root/'initial-state.json', dict(head=git('rev-parse', 'HEAD').strip(),
            dirty=git('status', '--short'), production_sha256=production,
            current_binary_sha256=digest(b.ROOT/'build/dmg-md')))
        (root/'initial.patch').write_text(git('diff', '--binary', 'HEAD'))
        o5.freeze(root/'reference-before.json', references())
    copy_verified(previous/'dmg-md-o4', root/'dmg-md-o4')
    copy_verified(accepted/'dmg-md-o8', root/'dmg-md-o8')
    if digest(root/'dmg-md-o8') != load(accepted/'science.json')['candidate_sha256']:
        raise ValueError('current control differs from accepted O8 binary')
    for case in ('carbon_200k', 'water_400k'):
        for name in ('model.xyz', 'nep.txt'):
            copy_verified(previous/'inputs'/case/name, root/'inputs'/case/name)


def experiment(root):
    result = o5.Experiment(root)
    result.binaries['o8'] = root/'dmg-md-o8'
    return result


def freeze(root, previous, accepted):
    preserve(root, previous, accepted)
    o5.freeze(root/'design.json', dict(stage='O9', cases=['carbon_200k', 'water_400k'],
        ranks=[2, 8], resource=dict(gpu_indices=list(range(8)))))
    current = experiment(root)
    old = load(previous/'frozen-plan.json')
    if current.devices != old['identity']['devices']:
        raise ValueError('GPU UUID order differs from O6')
    for ranks, binding in binding_identity(current.bindings).items():
        if binding['mapping'] != old['bindings'][str(ranks)]['mapping']:
            raise ValueError('K=4 CPU mapping differs from O6')
    if digest(root/'dmg-md-o4') != old['identity']['binaries']['o4']['sha256']:
        raise ValueError('O6 binary differs')
    for case, files in current.identity()['inputs'].items():
        if files != old['identity']['inputs'][case]:
            raise ValueError('O6 input differs')
    window = next(w for w in load(previous/'ncu-windows.json') if
                  (w['case'], w['ranks'], w['mode']) == ('carbon_200k', 2, 'full'))
    if not window['ordinary'] or window['launch_count'] != 5:
        raise ValueError('expected an O6 ordinary five-kernel full window')
    # The existing collector checks successful output against this exact window.
    copy_verified(previous/'profiles'/window['source_profile']/'thermo.out',
                  root/'profiles'/window['source_profile']/'thermo.out')
    inherited = {}
    for name in ('frozen-plan.json', 'kernel-summary.json', 'kernel-trials.json',
                 'wall-analysis.json', 'ncu-analysis.json', 'ncu-windows.json',
                 'raw-evidence-inventory.json'):
        target = root/'provenance/o6'/name
        copy_verified(previous/name, target)
        inherited[str(target.relative_to(root))] = digest(target)
    compatible = {}
    for path in sorted((b.ROOT/'src/gpumd_compat').rglob('*')):
        if path.is_file() and path.suffix in ('.cu', '.cuh', '.cpp', '.hpp'):
            name = str(path.relative_to(b.ROOT))
            if digest(path) != digest(previous/'source-files'/name):
                raise ValueError('NEP source differs from the O6 diagnostic: '+name)
            compatible[name] = digest(path)
    sources = [Path(__file__).resolve(), b.HERE/'profile_overlap_o6_ncu.py',
               b.HERE/'run_overlap_o7.py', b.ROOT/'tests/mpi/check_environment.py',
               b.ROOT/'tests/baseline/run_baselines.py']
    plan = dict(schema_version=1, stage='O9', scope='evidence gate; no execution candidate',
        previous=str(previous), accepted=str(accepted), identity=current.identity(('o4', 'o8')),
        bindings=binding_identity(current.bindings), inherited_sha256=inherited,
        unchanged_nep_sha256=compatible,
        sources={str(p.relative_to(b.ROOT)): digest(p) for p in sources},
        probe=dict(window, id='carbon_200k-p2-full-cache-none', cache_control='none', sections=SECTIONS),
        policy=dict(permission='one real steady-window attempt; after ERR_NVGPUCTRPERM stop; no driver changes',
            available='PARTIAL: freeze a single-factor candidate design before any kernel change',
            unavailable='NO_ADOPTION: no causal hardware or single-factor candidate evidence',
            other_failure='PARTIAL: preserve and diagnose; never count as NO_ADOPTION',
            main_trials=0, statistics=None, formal_cpu=None,
            reason='No credible candidate has passed the O6 gate; diagnostic time is not a performance sample.',
            conditions=dict(exclusive=False, clocks_locked=False, memory_residency='UNKNOWN')))
    o5.freeze(root/'frozen-plan.json', plan)
    for name, command in [('ncu', ['ncu', '--version']), ('cuda', ['nvcc', '--version']),
                          ('mpi', ['mpiexec', '--version']), ('ucx', ['ucx_info', '-v'])]:
        path = root/f'{name}-version.txt'
        if not path.exists():
            path.write_text(b.command_output(command))
    if not (root/'machine.json').exists():
        b.save(root/'machine.json', b.snapshot())
        (root/'driver-policy.txt').write_text(Path('/proc/driver/nvidia/params').read_text())
    print('FROZEN O9 evidence gate', flush=True)


def checked(root, offline=False):
    plan = load(root/'frozen-plan.json')
    for name, expected in {**plan['sources'], **plan['unchanged_nep_sha256'],
                           **plan['identity']['sources']}.items():
        if digest(b.ROOT/name) != expected:
            raise ValueError('source changed: '+name)
    for name, expected in plan['inherited_sha256'].items():
        if digest(root/name) != expected:
            raise ValueError('inherited evidence changed: '+name)
    for version, spec in plan['identity']['binaries'].items():
        if digest(root/f'dmg-md-{version}') != spec['sha256']:
            raise ValueError('control binary changed: '+version)
    for case, files in plan['identity']['inputs'].items():
        for name, expected in files.items():
            if digest(root/'inputs'/case/name) != expected:
                raise ValueError('input changed')
    current = None if offline else experiment(root)
    if current and (current.identity(('o4', 'o8')) != plan['identity'] or
                    binding_identity(current.bindings) != plan['bindings']):
        raise ValueError('environment or binding differs from frozen plan')
    return current, plan


def gates(root):
    current, plan = checked(root)
    directory = root/'gates'
    directory.mkdir(exist_ok=True)
    results = []
    for version in ('o4', 'o8'):
        audit = Audit(directory/f'binding-{version}', current.bindings[8], current.binaries[version])
        audit.close()
        commands = [
            ('environment', [sys.executable, str(b.ROOT/'tests/mpi/check_environment.py'),
                '--candidate', str(current.binaries[version]), '--devices', ','.join(current.devices),
                '--ranks', '8', *['--mpiexec-arg='+v for v in audit.mpi_args()]]),
            ('golden', [sys.executable, str(b.ROOT/'tests/baseline/run_baselines.py'),
                '--candidate', str(current.binaries[version]), '--device', current.devices[0]])]
        for label, command in commands:
            stem = f'{label}-{version}'
            o5.freeze(directory/f'{stem}-command.json', dict(command=command,
                environment=o5.controlled_environment(), binary_sha256=plan['identity']['binaries'][version]['sha256']))
            with (directory/f'{stem}.log').open('x') as output:
                subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True,
                               env=o5.controlled_environment(), timeout=600)
            results.append(dict(id=stem, status='PASS', log_sha256=digest(directory/f'{stem}.log')))
            print('GATE', stem, 'PASS', flush=True)
    o5.freeze(root/'gates.json', dict(status='PASS', results=results))


def probe(root):
    current, plan = checked(root)
    if load(root/'gates.json')['status'] != 'PASS':
        raise ValueError('environment and golden gates required')
    attempt(current, plan['probe'])


def decision(result, gates_passed, production_unchanged):
    """Missing evidence and unrelated failures cannot close the O9 branch."""
    if not gates_passed or not production_unchanged or result is None:
        return 'PARTIAL'
    if result.get('status') == 'PASS':
        return 'CANDIDATE_REVIEW_REQUIRED'
    if (result.get('status') == 'INCONCLUSIVE' and
            'ERR_NVGPUCTRPERM' in result.get('error', '') and
            not result.get('binding_error') and not result.get('timeout') and
            result.get('binding', {}).get('status') == 'PASS'):
        return 'NO_ADOPTION'
    return 'PARTIAL'


def verify_evidence(root):
    inventory = load(root/'raw-evidence-inventory.json')
    for name, spec in inventory.items():
        path = root/name
        if not path.is_file() or path.stat().st_size != spec['bytes'] or digest(path) != spec['sha256']:
            raise ValueError('raw evidence differs: '+name)
    print('VERIFIED O9 raw evidence', len(inventory), flush=True)


def seal(root):
    checked(root, offline=True)
    files = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split('\0')
    for name in files:
        if name and (name.startswith(('src/', 'include/', 'scripts/', 'tests/', 'docs/')) or
                     name in ('CMakeLists.txt', 'AGENTS.md')):
            copy_verified(b.ROOT/name, root/'source-files'/name)
    (root/'final.patch').write_text(git('diff', '--binary', 'HEAD'))
    excluded = {'raw-evidence-inventory.json', 'analysis.json', 'scheduler.lock', 'reference-after.json'}
    inventory = {str(path.relative_to(root)): dict(sha256=digest(path), bytes=path.stat().st_size)
                 for path in sorted(root.rglob('*')) if path.is_file() and
                 str(path.relative_to(root)) not in excluded}
    o5.freeze(root/'raw-evidence-inventory.json', inventory)
    verify_evidence(root)


def analyze(root):
    if (root/'raw-evidence-inventory.json').exists():
        verify_evidence(root)
    _, plan = checked(root, offline=True)
    initial = load(root/'initial-state.json')
    unchanged = all(digest(b.ROOT/n) == h for n, h in initial['production_sha256'].items())
    unchanged = unchanged and digest(b.ROOT/'build/dmg-md') == initial['current_binary_sha256']
    before, after = load(root/'reference-before.json'), references()
    b.save(root/'reference-after.json', after)
    if before != after:
        raise ValueError('reference status or ignored inventory changed')
    gate_path = root/'gates.json'
    gate = load(gate_path) if gate_path.exists() else {}
    passed = gate.get('status') == 'PASS' and len(gate.get('results', [])) == 4
    for row in gate.get('results', []):
        if row['status'] != 'PASS' or digest(root/'gates'/(row['id']+'.log')) != row['log_sha256']:
            raise ValueError('gate evidence changed')
    path = root/'ncu'/plan['probe']['id']/'result.json'
    result = load(path) if path.exists() else None
    if result:
        if any(result.get(k) != v for k, v in plan['probe'].items()):
            raise ValueError('probe configuration differs from frozen window')
        if result['binary_sha256'] != plan['identity']['binaries']['o4']['sha256']:
            raise ValueError('probe binary differs')
        raw = root/'ncu'/plan['probe']['id']/'combined-rank-output.txt'
        if 'ERR_NVGPUCTRPERM' in result.get('error', '') and 'ERR_NVGPUCTRPERM' not in raw.read_text():
            raise ValueError('permission conclusion lacks raw profiler evidence')
    outcome = decision(result, passed, unchanged)
    summary = dict(stage='O9', status='COMPLETE' if outcome == 'NO_ADOPTION' else 'PARTIAL',
        decision=outcome, implementation='NO_KERNEL_CHANGE', correctness='UNCHANGED_BINARY_GATES_PASS' if passed else 'PARTIAL',
        performance='NOT_MEASURED_NO_CANDIDATE', new_performance_trials=0,
        hardware_cause='INCONCLUSIVE', candidate=None, production_unchanged=unchanged,
        references_unchanged=True, formal_cpu=None,
        limitation='No candidate speed or regression claim. Old O6 measurements are inherited evidence, not new O9 trials.')
    b.save(root/'analysis.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def main():
    if "--legacy-probe" not in sys.argv:
        from overlap_o9_confirmation import main as confirm
        return confirm()
    sys.argv.remove("--legacy-probe")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('freeze', 'gates', 'probe', 'analyze', 'seal'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--previous', type=Path, default=b.ROOT/'dmgmd-overlap-o6-20261021')
    parser.add_argument('--accepted', type=Path, default=b.ROOT/'dmgmd-overlap-o8-20261023')
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.phase == 'freeze':
            freeze(root, args.previous.resolve(), args.accepted.resolve())
        else:
            globals()[args.phase](root)


if __name__ == '__main__':
    main()
