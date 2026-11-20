#!/usr/bin/env python3
"""Fail-closed local-NPT routing, invalid configuration, and empty-rank cases."""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_domain_npt_contracts as contract
import run_domain_npt as domain
import generate_inputs
import check_environment


def sparse_model(text):
    lines = text.splitlines()
    # Same large box, two nonzero-velocity atoms in one slab: N < P4.
    return '\n'.join(['2', lines[1], lines[2], lines[3]]) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--devices', required=True)
    p.add_argument('--backend', choices=('HostStaged', 'CudaAware'), default='HostStaged')
    p.add_argument('--timeout', type=int, default=900)
    a = p.parse_args()
    candidate, fixture, root = a.candidate.resolve(), a.fixture.resolve(), a.work_root.resolve()
    devices = a.devices.split(',')
    if len(devices) < 4 or len(set(devices)) != len(devices): p.error('four unique devices required')
    mpiexec = check_environment.validate_environment(candidate, 'mpiexec', devices, 4, a.timeout)
    root.mkdir(parents=True, exist_ok=False)
    records = []
    result = {'candidate_sha256': contract.sha(candidate), 'records': records, 'status': 'RUNNING'}
    def save(): (root / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    def run(name, model, text, env_extra=None, mismatch=None, failure=None, route=None):
        stage = root / name; stage.mkdir()
        (stage / 'model.xyz').write_text(model)
        shutil.copyfile(fixture / 'nep.txt', stage / 'nep.txt')
        (stage / 'run.in').write_text(text)
        env = os.environ.copy()
        env.update(CUDA_VISIBLE_DEVICES=','.join(devices[:4]), CUDA_DEVICE_ORDER='PCI_BUS_ID',
                   DMGMD_COMM_BACKEND=a.backend, DMGMD_DOMAIN_NPT='rebuild',
                   DMGMD_DOMAIN_PARTITION='off', DMGMD_DOMAIN_OVERLAP='off',
                   DMGMD_ENSEMBLE_SEED='12345678', DMGMD_DOMAIN_DIAGNOSTICS='1', OMP_NUM_THREADS='1')
        env.update(env_extra or {})
        args = ([sys.executable, str(HERE / 'domain_npt_rank_env.py'), *mismatch, str(candidate)]
                if mismatch else [str(candidate)])
        cmd = [str(mpiexec), '-n', '4', *args]
        rec = {'name': name, 'command': cmd, 'environment': {k: env[k] for k in (
            'CUDA_VISIBLE_DEVICES', 'CUDA_DEVICE_ORDER', 'DMGMD_COMM_BACKEND', 'DMGMD_DOMAIN_NPT',
            'DMGMD_DOMAIN_PARTITION', 'DMGMD_DOMAIN_OVERLAP', 'DMGMD_ENSEMBLE_SEED', 'DMGMD_DOMAIN_DIAGNOSTICS', 'OMP_NUM_THREADS')},
            'inputs': {x: contract.sha(stage / x) for x in ('model.xyz', 'nep.txt', 'run.in')}, 'status': 'RUNNING'}
        records.append(rec); save()
        try:
            with (stage / 'stdout.log').open('w') as out, (stage / 'stderr.log').open('w') as err:
                proc = subprocess.Popen(cmd, cwd=stage, env=env, stdout=out, stderr=err, start_new_session=True)
                try: proc.wait(timeout=a.timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL); proc.wait(); raise
            rec['returncode'] = proc.returncode
            log = (stage / 'stdout.log').read_text(errors='replace') + '\n' + (stage / 'stderr.log').read_text(errors='replace')
            if failure:
                if proc.returncode == 0 or failure.lower() not in log.lower():
                    raise AssertionError(f'expected nonzero exit and diagnostic {failure!r}')
            else:
                if proc.returncode: raise RuntimeError(f'exit {proc.returncode}')
                if route == 'fallback':
                    if 'DMGMD_DOMAIN mode=m1-fallback' not in log or 'DMGMD_DOMAIN_NPT ' in log:
                        raise AssertionError('expected initial fallback without local marker')
                else: domain.check_route(log, 4, route)
            rec['status'] = 'PASS'
        except Exception as e:
            rec.update(status='FAIL', error=str(e)); raise
        finally:
            rec['files_sha256'] = {x.name: contract.sha(x) for x in stage.iterdir() if x.is_file()}; save()
        return stage
    try:
        model = (fixture / 'model.xyz').read_text()
        text = contract.run_text('npt_ber', 'single')
        run('invalid-mode', model, text, {'DMGMD_DOMAIN_NPT': 'bad'}, failure='DMGMD_DOMAIN_NPT must be')
        run('mismatched-mode', model, text, mismatch=['DMGMD_DOMAIN_NPT', 'rebuild', 'affine'],
            failure='DMGMD_DOMAIN_NPT differs')
        run('partition-overlap-invalid', model, text, {'DMGMD_DOMAIN_OVERLAP': 'wait'},
            failure='NPT requires DMGMD_DOMAIN_OVERLAP=off')
        run('partition-mismatch', model, text, mismatch=['DMGMD_DOMAIN_PARTITION', 'off', 'serial'],
            failure='DMGMD_DOMAIN_PARTITION')
        run('partition-npt', model, text, {'DMGMD_DOMAIN_PARTITION': 'serial'},
            failure='NPT requires DMGMD_DOMAIN_PARTITION=off')
        run('initial-fallback', generate_inputs.model_text((3, 3, 3), 301), text, route='fallback')
        # Large negative pressure shrinks the first candidate enough to violate slab eligibility.
        shrink = text.replace('0 425 200', '180000 425 200')
        failed_stage = run('runtime-geometry', model, shrink, failure='domain NPT box preflight failed')
        geometry_log = (failed_stage / 'stdout.log').read_text(errors='replace') + (failed_stage / 'stderr.log').read_text(errors='replace')
        domain.check_route(geometry_log, 4, 'rebuild')
        if not re.search(r'lx=[0-9.eE+-]+ ly=[0-9.eE+-]+ lz=[0-9.eE+-]+ axis=[0-2] slab_width=[0-9.eE+-]+ required=[0-9.eE+-]+', geometry_log):
            raise AssertionError('geometry failure omitted candidate dimensions')
        for output in ('thermo.out', 'trajectory.xyz', 'restart.xyz'):
            path = failed_stage / output
            if path.exists():
                lines = path.read_text().splitlines()
                data = [line for line in lines if line.strip() and not line.lstrip().startswith('#')]
                if data: raise AssertionError(f'first-step failure committed output: {output}')
        rows = model.splitlines()
        fields = rows[2].split()
        fields[5] = '1000000'
        rows[2] = ' '.join(fields)
        displacement = run('multi-box-displacement', '\n'.join(rows) + '\n', text,
            failure='unsupported multi-box displacement')
        displacement_log = (displacement / 'stdout.log').read_text(errors='replace') + (displacement / 'stderr.log').read_text(errors='replace')
        domain.check_route(displacement_log, 4, 'rebuild')
        sparse = sparse_model(model)
        ref = run('empty-off', sparse, text, {'DMGMD_DOMAIN_NPT': 'off'}, route='off')
        records[-1]['outputs'] = contract.validate_outputs(ref, 4)
        tolerance = json.loads((contract.ROOT / 'tests/baseline/manifest.json').read_text())['tolerances']
        for mode in ('rebuild', 'affine'):
            candidate_stage = run('empty-' + mode, sparse, text, {'DMGMD_DOMAIN_NPT': mode}, route=mode)
            records[-1]['outputs'] = contract.validate_outputs(candidate_stage, 4)
            records[-1]['comparison'] = contract.compare(ref, candidate_stage, tolerance)
            log = (candidate_stage / 'stdout.log').read_text(errors='replace')
            layouts = [line for line in log.splitlines() if line.startswith('DMGMD_DOMAIN_LAYOUT ')]
            empty_ranks = {int(m.group(1)) for line in layouts
                           if (m := re.search(r'rank=(\d+).*owned=0(?:\s|$)', line))}
            if not empty_ranks: raise AssertionError('N<P did not observe any empty owned rank')
            records[-1]['empty_owned_ranks'] = sorted(empty_ranks)
        result['status'] = 'PASS'
    except Exception as e:
        result.update(status='FAIL', error=str(e)); raise
    finally: save()


if __name__ == '__main__': main()
