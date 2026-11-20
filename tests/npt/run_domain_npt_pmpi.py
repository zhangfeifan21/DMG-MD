#!/usr/bin/env python3
"""Compare 4 and 6 output-free steps to isolate ordinary-step MPI collectives."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_domain_npt_contracts as contract
import run_domain_npt as domain
import check_environment


def census(log, ranks):
    rows = {}
    for line in log.splitlines():
        if not line.startswith('DN4_PMPI '):
            continue
        fields = dict(re.findall(r'(\w+)=(\d+)', line))
        rank = int(fields.pop('rank'))
        if rank in rows:
            raise ValueError('duplicate PMPI rank')
        rows[rank] = {k: int(v) for k, v in fields.items()}
    if set(rows) != set(range(ranks)):
        raise ValueError('missing PMPI ranks')
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--probe', type=Path, required=True)
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--devices', required=True)
    p.add_argument('--ranks', type=int, choices=(2, 4), required=True)
    p.add_argument('--backend', choices=('HostStaged', 'CudaAware'), required=True)
    p.add_argument('--mode', choices=('rebuild', 'affine'), required=True)
    p.add_argument('--ensemble', choices=('npt_ber', 'npt_scr'), default='npt_ber')
    p.add_argument('--timeout', type=int, default=900)
    a = p.parse_args()
    candidate, probe, fixture, root = [x.resolve() for x in (a.candidate, a.probe, a.fixture, a.work_root)]
    devices = a.devices.split(',')
    if len(devices) < a.ranks or len(set(devices)) != len(devices): p.error('insufficient unique devices')
    mpiexec = check_environment.validate_environment(candidate, 'mpiexec', devices, a.ranks, a.timeout)
    root.mkdir(parents=True, exist_ok=False)
    records = []
    result = {'status': 'RUNNING', 'candidate_sha256': contract.sha(candidate),
              'probe_sha256': contract.sha(probe), 'records': records}
    def save(): (root / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    try:
        for steps in (4, 6):
            stage = root / f'steps-{steps}'
            stage.mkdir()
            for name in ('model.xyz', 'nep.txt'): shutil.copyfile(fixture / name, stage / name)
            (stage / 'run.in').write_text('potential nep.txt\ntime_step 0.25\n'
                f'ensemble {a.ensemble} 300 300 40 0 425 200\n'
                f'dump_thermo 9999\ndump_xyz 9999 trajectory.xyz precision double\nrun {steps}\n')
            env = os.environ.copy()
            env.update(CUDA_VISIBLE_DEVICES=','.join(devices[:a.ranks]), CUDA_DEVICE_ORDER='PCI_BUS_ID',
                       DMGMD_COMM_BACKEND=a.backend, DMGMD_DOMAIN_NPT=a.mode,
                       DMGMD_DOMAIN_PARTITION='off', DMGMD_DOMAIN_OVERLAP='off',
                       DMGMD_ENSEMBLE_SEED='12345678', OMP_NUM_THREADS='1', LD_PRELOAD=str(probe))
            command = [str(mpiexec), '-n', str(a.ranks), str(candidate)]
            rec = {'steps': steps, 'command': command, 'environment': {k: env[k] for k in
                   ('CUDA_VISIBLE_DEVICES', 'CUDA_DEVICE_ORDER', 'DMGMD_COMM_BACKEND', 'DMGMD_DOMAIN_NPT',
                    'DMGMD_DOMAIN_PARTITION', 'DMGMD_DOMAIN_OVERLAP', 'DMGMD_ENSEMBLE_SEED', 'OMP_NUM_THREADS', 'LD_PRELOAD')},
                   'inputs': {x: contract.sha(stage / x) for x in ('run.in', 'model.xyz', 'nep.txt')}, 'status': 'RUNNING'}
            records.append(rec); save()
            try:
                with (stage / 'stdout.log').open('w') as out, (stage / 'stderr.log').open('w') as err:
                    proc = subprocess.Popen(command, cwd=stage, env=env, stdout=out, stderr=err, start_new_session=True)
                    try: proc.wait(timeout=a.timeout)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL); proc.wait(); raise
                rec['returncode'] = proc.returncode
                if proc.returncode: raise RuntimeError(f'exit {proc.returncode}')
                log = (stage / 'stdout.log').read_text(errors='replace') + '\n' + (stage / 'stderr.log').read_text(errors='replace')
                domain.check_route(log, a.ranks, a.mode)
                rec['census'] = census(log, a.ranks)
                rec['status'] = 'PASS'
            except Exception as e:
                rec.update(status='FAIL', error=str(e)); raise
            finally:
                rec['files_sha256'] = {x.name: contract.sha(x) for x in stage.iterdir() if x.is_file()}; save()
        for rank in range(a.ranks):
            low, high = records[0]['census'][rank], records[1]['census'][rank]
            delta = {key: high[key] - low[key] for key in low}
            # Two steps each perform one cache reason OR and one scaled-box preflight OR.
            # SCR additionally broadcasts its thermostat factor. Setup cancels.
            expected = {'thermo': 2, 'reason': 4, 'bcast2': 4 if a.ensemble == 'npt_scr' else 2, 'preflight': 0,
                        'gather': 0, 'gatherv': 0, 'allgather': 0, 'allgatherv': 0}
            for key, value in expected.items():
                if delta[key] != value: raise AssertionError(f'rank {rank}: {key} delta {delta[key]} != {value}')
            records[1].setdefault('delta_by_rank', {})[rank] = delta
        result['status'] = 'PASS'
    except Exception as e:
        result.update(status='FAIL', error=str(e)); raise
    finally: save()


if __name__ == '__main__': main()
