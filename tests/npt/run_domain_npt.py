#!/usr/bin/env python3
"""Short domain-NPT differential runner; accepts a prepared, immutable fixture.

This is a deterministic smoke gate, not the complete DN4 or statistics suite.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / 'tests/baseline'), str(ROOT / 'tests/mpi')]
import run_baselines as baseline
import check_environment


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_route(log, ranks, mode):
    domain = [line for line in log.splitlines() if line.startswith('DMGMD_DOMAIN ')]
    npt = [line for line in log.splitlines() if line.startswith('DMGMD_DOMAIN_NPT ')]
    if len(domain) != 1:
        raise ValueError('expected exactly one DMGMD_DOMAIN route')
    fields = dict(item.split('=', 1) for item in domain[0].split()[1:] if '=' in item)
    if ranks > 1 and mode != 'off':
        if fields.get('mode') != 'm2a' or npt != [f'DMGMD_DOMAIN_NPT mode={mode}']:
            raise ValueError('local NPT route missing or mismatched')
    elif npt:
        raise ValueError('unexpected local NPT route')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--fixture', type=Path, required=True, help='directory with model.xyz, nep.txt and run.in')
    p.add_argument('--work-root', type=Path, required=True, help='must not exist')
    p.add_argument('--devices', required=True, help='comma-separated physical GPU IDs')
    p.add_argument('--ranks', default='2,4')
    p.add_argument('--backends', default='HostStaged,CudaAware')
    p.add_argument('--timeout', type=int, default=900)
    a = p.parse_args()
    candidate, fixture, root = a.candidate.resolve(), a.fixture.resolve(), a.work_root.resolve()
    ranks, backends, devices = [int(x) for x in a.ranks.split(',')], a.backends.split(','), a.devices.split(',')
    if any(x not in (2, 4) for x in ranks) or any(x not in ('HostStaged', 'CudaAware') for x in backends):
        p.error('only registered P2/P4 and backends are supported')
    inventory = {name: sha(fixture / name) for name in ('model.xyz', 'nep.txt', 'run.in')}
    mpiexec = check_environment.validate_environment(candidate, 'mpiexec', devices, max(ranks), a.timeout)
    root.mkdir(parents=True, exist_ok=False)
    tolerances = json.loads((ROOT / 'tests/baseline/manifest.json').read_text())['tolerances']
    records = []
    results = root / 'results.json'
    def save():
        results.write_text(json.dumps({'scope': 'short_differential_only', 'candidate_sha256': sha(candidate),
                                       'fixture_sha256': inventory, 'records': records}, indent=2) + '\n')
    save()
    for rank in ranks:
        for backend in backends:
            stages = {}
            for mode in ('off', 'rebuild', 'affine'):
                stage = root / f'p{rank}-{backend}-{mode}'
                stage.mkdir()
                for name in inventory:
                    shutil.copyfile(fixture / name, stage / name)
                env = os.environ.copy()
                env.update(CUDA_VISIBLE_DEVICES=','.join(devices[:rank]), CUDA_DEVICE_ORDER='PCI_BUS_ID',
                           DMGMD_COMM_BACKEND=backend, DMGMD_DOMAIN_NPT=mode,
                           DMGMD_DOMAIN_PARTITION='off', DMGMD_DOMAIN_OVERLAP='off',
                           DMGMD_DOMAIN_DIAGNOSTICS='1', DMGMD_COMM_LOG_INTERVAL='1',
                           DMGMD_ENSEMBLE_SEED='12345678', OMP_NUM_THREADS='1')
                command = [str(mpiexec), '-n', str(rank), str(candidate)]
                rec = {'ranks': rank, 'backend': backend, 'mode': mode, 'command': command, 'status': 'RUNNING'}
                records.append(rec); save()
                try:
                    with (stage / 'stdout.log').open('w') as out, (stage / 'stderr.log').open('w') as err:
                        proc = subprocess.Popen(command, cwd=stage, env=env, stdout=out, stderr=err,
                                                start_new_session=True)
                        try:
                            proc.wait(timeout=a.timeout)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait()
                            raise
                    log = (stage / 'stdout.log').read_text(errors='replace') + '\n' + (stage / 'stderr.log').read_text(errors='replace')
                    if proc.returncode: raise RuntimeError(f'exit {proc.returncode}')
                    check_route(log, rank, mode)
                    for name in ('thermo.out', 'trajectory.xyz'):
                        if not (stage / name).is_file(): raise RuntimeError(f'missing {name}')
                    if mode != 'off':
                        collector = baseline.DiffCollector(tolerances, enforce=True)
                        baseline.compare_thermo(stages['off'] / 'thermo.out', stage / 'thermo.out', collector, mode)
                        baseline.compare_xyz(stages['off'] / 'trajectory.xyz', stage / 'trajectory.xyz',
                                             {'kind': 'xyz'}, collector, mode)
                        rec['comparison'] = collector.stats
                    rec['status'] = 'PASS'
                    rec['output_sha256'] = {x.name: sha(x) for x in stage.iterdir() if x.is_file()}
                    stages[mode] = stage
                except Exception as error:
                    rec.update(status='FAIL', error=str(error)); save(); raise
                save()
    return 0


if __name__ == '__main__':
    sys.exit(main())
