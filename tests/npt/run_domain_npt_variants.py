#!/usr/bin/env python3
"""Run prepared potential variants against off using the frozen baseline tolerances."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_domain_npt_contracts as contract
import check_environment


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--fixtures', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--devices', required=True)
    p.add_argument('--ranks', default='2,4')
    p.add_argument('--backends', default='HostStaged,CudaAware')
    p.add_argument('--timeout', type=int, default=900)
    a = p.parse_args()
    candidate, fixtures, root = a.candidate.resolve(), a.fixtures.resolve(), a.work_root.resolve()
    ranks, backends, devices = list(map(int, a.ranks.split(','))), a.backends.split(','), a.devices.split(',')
    if not ranks or any(x not in (2, 4) for x in ranks) or len(devices) < max(ranks) or len(set(devices)) != len(devices):
        p.error('invalid ranks or insufficient unique devices')
    if not backends or any(x not in ('HostStaged', 'CudaAware') for x in backends): p.error('invalid backend')
    inventory = json.loads((fixtures / 'inventory.json').read_text())
    for name, files in inventory['fixtures'].items():
        for filename, digest in files.items():
            if contract.sha(fixtures / name / filename) != digest: raise ValueError(f'fixture changed: {name}/{filename}')
    mpiexec = check_environment.validate_environment(candidate, 'mpiexec', devices, max(ranks), a.timeout)
    root.mkdir(parents=True, exist_ok=False)
    tolerances = json.loads((contract.ROOT / 'tests/baseline/manifest.json').read_text())['tolerances']
    records = []
    result = {'status': 'RUNNING', 'candidate_sha256': contract.sha(candidate),
              'fixture_inventory_sha256': contract.sha(fixtures / 'inventory.json'), 'records': records}
    def save(): (root / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    try:
        for name in inventory['fixtures']:
            fixture = fixtures / name
            run = (fixture / 'run.in').read_text()
            for rank in ranks:
                for backend in backends:
                    previous = None
                    for mode in ('off', 'rebuild', 'affine'):
                        stage = root / f'{name}-p{rank}-{backend}-{mode}'
                        try:
                            contract.execute(stage, candidate, mpiexec, rank, backend, mode, devices,
                                a.timeout, fixture / 'model.xyz', fixture / 'nep.txt', run, records)
                            if mode == 'off': previous = stage
                            else: records[-1]['comparison'] = contract.compare(previous, stage, tolerances)
                        except Exception as e:
                            if records: records[-1].update(status='FAIL', error=str(e))
                            raise
                        finally: save()
        result['status'] = 'PASS'
    except Exception as e:
        result.update(status='FAIL', error=str(e)); raise
    finally: save()


if __name__ == '__main__': main()
