#!/usr/bin/env python3
"""Independently re-read DN4 full-jitter artifacts; no GPU execution."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'tests/baseline')]
import run_baselines as baseline
import run_domain_npt as domain
import run_domain_npt_contracts as contracts
import run_domain_pressure_oracle as oracle


def audit(path, fixture):
    data = json.loads(path.read_text())
    if data['status'] != 'PASS' or not data['settings'].get('jitter_full'):
        raise ValueError('full jitter report did not pass')
    candidate = Path(data['candidate'])
    inventory = {x: contracts.sha(fixture / x) for x in ('model.xyz', 'nep.txt', 'run.in')}
    if contracts.sha(candidate) != data['candidate_sha256'] or data['fixture_sha256'] != inventory:
        raise ValueError('candidate or fixture identity changed')
    tol = json.loads((ROOT / 'tests/baseline/manifest.json').read_text())['tolerances']
    model = oracle.jitter_model((fixture / 'model.xyz').read_text())
    proof_path = Path(data['reference_proof']['path'])
    proof = oracle.validate_jitter_proof(proof_path, candidate, inventory, model, tol)
    if proof != data['reference_proof']:
        raise ValueError('reference proof changed')
    if data['settings']['scales'] != list(oracle.JITTER_SCALES):
        raise ValueError('invalid strain registration')
    records = data['records']
    if len(records) != 150:
        raise ValueError('expected exactly 150 jobs')
    index = {(r['ranks'], r['backend'], r['mode'], r['scale'], r['repeat']): r for r in records}
    if len(index) != 150:
        raise ValueError('duplicate jobs')
    configs = [(1, 'HostStaged', 'off')] + [(rank, backend, mode) for rank in (2, 4)
                for backend in ('HostStaged', 'CudaAware') for mode in ('rebuild', 'affine')]
    points, max_pressure_error, max_pressure_fraction, files = {}, 0.0, 0.0, 0
    for rank, backend, mode in configs:
        values = {}
        for si, strain in enumerate(oracle.JITTER_SCALES):
            for repeat in ((0, 1) if rank == 1 else (0,)):
                rec = index[(rank, backend, mode, strain, repeat)]
                stage_name = f's{si}-p{rank}-{backend}-{mode}-r{repeat}'
                if rec['status'] != 'PASS' or rec['stage'] != stage_name:
                    raise ValueError('invalid stage status or identity')
                stage = path.parent / stage_name
                required = {'model.xyz', 'nep.txt', 'run.in', 'trajectory.xyz', 'thermo.out',
                            'restart.xyz', 'stdout.log', 'stderr.log'}
                if not required <= rec['files_sha256'].keys():
                    raise ValueError('missing output hash')
                for name, digest in rec['files_sha256'].items():
                    if Path(name).name != name or contracts.sha(stage / name) != digest:
                        raise ValueError('output hash mismatch')
                    files += 1
                if (stage/'model.xyz').read_text() != oracle.scaled_model(model, strain):
                    raise ValueError('fixture mismatch')
                if (stage/'run.in').read_text() != oracle.RUN or contracts.sha(stage/'nep.txt') != inventory['nep.txt']:
                    raise ValueError('input mismatch')
                log = (stage/'stdout.log').read_text(errors='replace')+'\n'+(stage/'stderr.log').read_text(errors='replace')
                domain.check_route(log, rank, mode)
                frame, model_frame = baseline.parse_xyz(stage/'trajectory.xyz'), baseline.parse_xyz(stage/'model.xyz')
                thermo = baseline.parse_thermo(stage/'thermo.out')
                if len(frame) != 1 or len(model_frame) != 1 or len(thermo) != 1 or len(thermo[0]['rows']) != 1:
                    raise ValueError('output shape mismatch')
                metrics = oracle.snapshot(frame[0], model_frame[0], thermo[0]['rows'][0])
                if metrics != rec['metrics']:
                    raise ValueError('metrics do not match output')
                max_pressure_error = max(max_pressure_error, metrics['pressure_error_GPa'])
                max_pressure_fraction = max(max_pressure_fraction, metrics['pressure_error_GPa']/metrics['pressure_limit_GPa'])
                if rank != 1 or repeat == 1:
                    ref = path.parent / f's{si}-p1-HostStaged-off-r0'
                    if contracts.compare(ref, stage, tol) != rec['comparison']:
                        raise ValueError('per-field comparison mismatch')
                if repeat == 0:
                    values[strain] = metrics
        check = oracle.jitter_derivative_checks(values)
        if not check['pass']:
            raise ValueError('recomputed finite difference failed')
        points[(rank, backend, mode)] = check
    if points[configs[0]] != data['checks']['finite_difference']:
        raise ValueError('P1 FD mismatch')
    actual_local = data['checks']['local_finite_difference']
    expected_local = [{'ranks': rank, 'backend': backend, 'mode': mode, **points[(rank, backend, mode)]}
                      for rank, backend, mode in configs[1:]]
    if actual_local != expected_local:
        raise ValueError('local FD mismatch')
    if data['checks']['reference_repeat'] != {'status': 'PASS', 'count': 15}:
        raise ValueError('repeat summary mismatch')
    summaries = []
    for rank, backend, mode in configs:
        checks = points[(rank, backend, mode)]['derivatives']
        worst = max(checks, key=lambda item: item['absolute_error_GPa']/item['limit_GPa'])
        summaries.append({'ranks': rank, 'backend': backend, 'mode': mode,
                          'max_absolute_error_GPa': max(x['absolute_error_GPa'] for x in checks),
                          'max_fraction_of_limit': worst['absolute_error_GPa']/worst['limit_GPa'],
                          'worst_fraction_check': worst})
    return {'schema': 'dmgmd.domain-pressure-full-audit.v1', 'status': 'PASS',
            'report_sha256': contracts.sha(path), 'candidate_sha256': data['candidate_sha256'],
            'fixture_sha256': inventory, 'reference_report_sha256': proof['sha256'],
            'verified_jobs': len(records), 'verified_files': files, 'direct_pressure_checks': 150,
            'max_pressure_error_GPa': max_pressure_error, 'max_pressure_fraction_of_limit': max_pressure_fraction,
            'finite_difference_checks': 54, 'finite_difference_by_configuration': summaries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.report.resolve(), args.fixture.resolve())
    with args.output.open('x') as f:
        json.dump(result, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
