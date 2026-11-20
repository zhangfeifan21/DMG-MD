#!/usr/bin/env python3
"""Exercise affine reuse and compression rebuild against paired NPT paths.

This deterministic sparse fixture tests cache mechanics, not NPT sampling.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'tests/mpi')]
import check_environment
import run_domain_npt_contracts as contracts


def model_text():
    rows = []
    for x in (3.0, 30.0, 61.0, 94.0):
        for j, dx in enumerate((0.0, 1.5, 3.0)):
            rows.append(f'C {x + dx} 16 16 {0.003 * (j - 1):.5f} 0.0004 -0.0002')
    return (f'{len(rows)}\n'
            'pbc="T T T" Lattice="128 0 0 0 64 0 0 0 64" '
            'Properties=species:S:1:pos:R:3:vel:R:3\n' + '\n'.join(rows) + '\n')


def input_text(pressure):
    return ('potential nep.txt\ntime_step 0.25\n'
            f'ensemble npt_ber 300 300 40 {pressure} 425 200\n'
            'dump_thermo 1\n'
            'dump_xyz 1 trajectory.xyz precision double velocity force potential virial\n'
            'dump_restart 1\nrun 20\n')


def cache_summary(stage, rank):
    pattern = re.compile(r'^DMGMD_DOMAIN_SUMMARY\s+(.*)$', re.MULTILINE)
    rows = []
    for line in pattern.findall((stage / 'stdout.log').read_text(errors='replace')):
        fields = dict(token.split('=', 1) for token in line.split() if '=' in token)
        if fields.get('sequence') == '0':
            rows.append({k: int(fields[k]) for k in ('rank', 'cache_reuses', 'cache_rebuilds',
                'affine_invalid_rebuilds', 'affine_budget_rebuilds', 'box_rebuilds')})
    if sorted(row['rank'] for row in rows) != list(range(rank)):
        raise AssertionError(f'expected one summary for each rank, got {rows}')
    keys = ('cache_reuses', 'cache_rebuilds', 'affine_invalid_rebuilds',
            'affine_budget_rebuilds', 'box_rebuilds')
    if any(tuple(row[k] for k in keys) != tuple(rows[0][k] for k in keys) for row in rows):
        raise AssertionError(f'inconsistent global decisions: {rows}')
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--devices', required=True)
    p.add_argument('--ranks', default='2,4')
    p.add_argument('--backends', default='HostStaged,CudaAware')
    p.add_argument('--timeout', type=int, default=900)
    a = p.parse_args()
    ranks, backends, devices = list(map(int, a.ranks.split(','))), a.backends.split(','), a.devices.split(',')
    if not ranks or any(x not in (2, 4) for x in ranks) or len(devices) < max(ranks) or len(set(devices)) != len(devices):
        p.error('ranks must be 2 or 4 and devices must be unique and sufficient')
    if not backends or any(x not in ('HostStaged', 'CudaAware') for x in backends):
        p.error('invalid backend')
    candidate, root = a.candidate.resolve(), a.work_root.resolve()
    mpiexec = check_environment.validate_environment(candidate, 'mpiexec', devices, max(ranks), a.timeout)
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'source'; source.mkdir()
    (source / 'model.xyz').write_text(model_text())
    shutil.copyfile(ROOT / 'tests/baseline/inputs/potentials/nep_C.txt', source / 'nep.txt')
    tolerances = json.loads((ROOT / 'tests/baseline/manifest.json').read_text())['tolerances']
    records = []
    result = {'scope': 'deterministic affine cache mechanics', 'status': 'RUNNING',
              'candidate_sha256': contracts.sha(candidate), 'records': records}
    def save():
        (root / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    try:
        for rank in ranks:
            for backend in backends:
                for name, pressure in (('weak', 0), ('expansion', -100), ('compression', 5100)):
                    run = input_text(pressure)
                    stages = {}
                    for mode in ('off', 'rebuild', 'affine'):
                        stage = contracts.execute(root / f'p{rank}-{backend}-{name}-{mode}', candidate,
                            mpiexec, rank, backend, mode, devices, a.timeout,
                            source / 'model.xyz', source / 'nep.txt', run, records)
                        stages[mode] = stage
                        if mode != 'off':
                            records[-1]['comparison'] = contracts.compare(stages['off'], stage, tolerances)
                            records[-1]['cache_summary'] = cache_summary(stage, rank)
                        save()
                    summary = cache_summary(stages['affine'], rank)[0]
                    if name in ('weak', 'expansion') and not summary['cache_reuses']:
                        raise AssertionError(f'{name} did not reuse: {summary}')
                    if name == 'compression' and not summary['affine_budget_rebuilds']:
                        raise AssertionError(f'compression did not exhaust the affine budget: {summary}')
        result['status'] = 'PASS'
    except Exception as error:
        result.update(status='FAIL', error=str(error))
        raise
    finally:
        save()
    return 0


if __name__ == '__main__':
    sys.exit(main())
