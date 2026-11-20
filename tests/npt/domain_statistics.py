#!/usr/bin/env python3
"""Prepare and execute preregistered large-box NPT statistics or isolated pilots.

Pilots are diagnostics and cannot enter formal records. Formal execution refuses
DRAFT registrations and requires an immutable candidate/input execution freeze.
"""
import argparse
import hashlib
import itertools
import json
import math
import os
import re
import secrets
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CLAIM_ROOT = ROOT / 'artifacts/domain-npt/statistics-claims'
sys.path.insert(0, str(ROOT / 'tests/mpi'))
import check_environment
from generate_inputs import model_text, scr_run
from run_domain_npt import check_route
import capture_environment_proof
import analyze_statistics
import run_domain_pressure_oracle as pressure_oracle


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def exclusive_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w') as out:
        json.dump(value, out, indent=2, sort_keys=True, allow_nan=False)
        out.write('\n')


def replace_json(path, value):
    tmp = path.with_name(path.name + '.tmp')
    write_json(tmp, value)
    os.replace(tmp, path)


def linked_library_hashes(executable, mpiexec):
    """Record resolved linked libraries as well as the MPI launcher binary."""
    output = subprocess.run(['ldd', str(executable)], check=True, capture_output=True, text=True).stdout
    if 'not found' in output: raise ValueError('unresolved linked library')
    paths = {str(Path(mpiexec).resolve())}
    for line in output.splitlines():
        match = re.search(r'(?:=>\s*)?(/[^\s]+)', line)
        if match: paths.add(str(Path(match.group(1)).resolve()))
    if len(paths) < 2: raise ValueError('no resolved candidate libraries')
    return {path: sha(Path(path)) for path in sorted(paths)}


def prepare(args):
    registration = args.registration.resolve()
    reg = json.loads(registration.read_text())
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    potential = ROOT / reg['potential']
    if sha(potential) != reg['potential_sha256']: raise ValueError('registered potential changed')
    shutil.copyfile(potential, out / 'nep.txt')
    inventory = {'nep.txt': sha(out / 'nep.txt')}
    for seed in reg['seeds']:
        path = out / f'model-{seed}.xyz'
        path.write_text(model_text((20, 6, 6), seed))
        inventory[path.name] = sha(path)
    for seed in reg['independent_scientific_reference']['seeds']:
        for volume in ('0.99', '1.00', '1.01'):
            path = out / f'nvt-model-{seed}-{volume}.xyz'
            path.write_text(model_text((20, 6, 6), seed, scale=1.0023 * float(volume)**(1/3)))
            inventory[path.name] = sha(path)
    for profile in reg['profiles']:
        path = out / f"scr-{profile['name']}.in"
        path.write_text(scr_run(profile['steps'], profile['dt_fs'], profile['tdamp_steps'], profile['tau_p_steps']))
        inventory[path.name] = sha(path)
    nvt = reg['independent_scientific_reference']
    path = out / 'nvt.in'
    path.write_text('potential nep.txt\n' + f"time_step {nvt['dt_fs']}\n" +
                    f"ensemble nvt_bdp 300 300 {nvt['tdamp_steps']}\n" +
                    f"dump_thermo 1\nrun {nvt['steps']}\n")
    inventory[path.name] = sha(path)
    write_json(out / 'inventory.json', {'registration_sha256': sha(registration), 'files': inventory})
    print(json.dumps({'input_root': str(out), 'files': len(inventory), 'inventory_sha256': sha(out / 'inventory.json')}))


def validate_inventory(root, registration):
    inventory = json.loads((root / 'inventory.json').read_text())
    if inventory['registration_sha256'] != sha(registration):
        raise ValueError('registration changed')
    for name, digest in inventory['files'].items():
        if sha(root / name) != digest:
            raise ValueError(f'input changed: {name}')
    return inventory


def formal_jobs(reg):
    jobs = {}
    for profile in reg['profiles']:
        for condition in (reg['conditions'] if profile['conditions'] == 'all' else profile['conditions']):
            for seed in reg['seeds']:
                key = f"scr-{profile['name']}-p{condition['ranks']}-{condition['backend']}-{condition['mode']}-seed{seed}"
                if key in jobs: raise ValueError('duplicate job')
                jobs[key] = {'kind': 'scr', 'profile': profile['name'], 'seed': seed, **condition,
                             'model': f'model-{seed}.xyz', 'run': f"scr-{profile['name']}.in",
                             'steps': profile['steps']}
    for seed in reg['independent_scientific_reference']['seeds']:
        for volume in ('0.99', '1.00', '1.01'):
            key = f'nvt-vol{volume}-seed{seed}'
            jobs[key] = {'kind': 'nvt', 'seed': seed, 'volume': volume, 'ranks': 1,
                         'backend': 'HostStaged', 'mode': 'off', 'model': f'nvt-model-{seed}-{volume}.xyz',
                         'run': 'nvt.in', 'steps': reg['independent_scientific_reference']['steps']}
    return jobs


def validate_report_artifacts(descriptor, executable, kind='stage', expected_status='PASS'):
    """Verify a runner report and each recorded stage, without trusting paths in records."""
    path = Path(descriptor['path'])
    if not path.is_absolute() or sha(path) != descriptor['sha256']:
        raise ValueError('runner report identity mismatch')
    report = json.loads(path.read_text())
    if ((kind != 'differential' and report.get('status') != expected_status)
            or report.get('candidate_sha256') != sha(executable)):
        raise ValueError('runner report did not pass for candidate')
    records = report.get('records', [])
    if not records: raise ValueError('runner report has no records')
    names = set()
    for rec in records:
        if rec.get('status') != 'PASS': raise ValueError('runner record did not pass')
        command = rec.get('command', [])
        if not command or sha(Path(command[-1])) != sha(executable):
            raise ValueError('runner command candidate mismatch')
        if kind == 'differential':
            name = f"p{rec['ranks']}-{rec['backend']}-{rec['mode']}"
        elif kind == 'pmpi':
            name = f"steps-{rec['steps']}"
        else:
            name = rec.get('stage', rec.get('name'))
        if not isinstance(name, str) or Path(name).name != name or name in names:
            raise ValueError('invalid or duplicate runner stage')
        names.add(name)
        stage = path.parent / name
        files = rec.get('files_sha256', rec.get('output_sha256', {}))
        if not {'model.xyz', 'nep.txt', 'run.in', 'stdout.log', 'stderr.log'} <= set(files):
            raise ValueError('runner record lacks input or log hashes')
        if any(Path(n).name != n or sha(stage / n) != digest for n, digest in files.items()):
            raise ValueError('runner stage artifact changed')
        inputs = rec.get('input_sha256', rec.get('inputs', report.get('fixture_sha256', {})))
        if any(inputs.get(n) != files[n] for n in ('model.xyz', 'nep.txt', 'run.in')):
            raise ValueError('runner input metadata mismatch')
    return report, names


def validate_scalar_proof(proof, candidate):
    if (proof.get('schema') != 'dmgmd.domain-npt-rng-proof.v2'
            or proof.get('candidate_sha256') != sha(candidate)):
        raise ValueError('invalid RNG proof identity')
    item = proof['scalar']; path = Path(item['path'])
    if not path.is_absolute() or sha(path) != item['sha256']:
        raise ValueError('scalar report identity mismatch')
    report = json.loads(path.read_text())
    if report.get('status') != 'PASS' or len(report.get('records', [])) != 3:
        raise ValueError('incomplete scalar proof')
    expected = {f'p{n}.{s}' for n in (1, 2, 4) for s in ('stdout', 'stderr')}
    files = item['files_sha256']
    if set(files) != expected or any(sha(path.parent / name) != digest for name, digest in files.items()):
        raise ValueError('scalar logs changed or missing')
    streams = []
    for ranks, record in zip((1, 2, 4), report['records']):
        command = record.get('command', [])
        if (record.get('status') != 'PASS' or record.get('returncode') != 0
                or len(command) != 4 or command[1:3] != ['-n', str(ranks)]
                or sha(Path(command[3])) != report.get('scalar_sha256')):
            raise ValueError('scalar command or binary mismatch')
        lines = [line for line in (path.parent / f'p{ranks}.stdout').read_text().splitlines()
                 if re.fullmatch(r'\d+ (?:-?0x[0-9a-f.]+p[+-]\d+ ){2}-?0x[0-9a-f.]+p[+-]\d+', line)]
        if len(lines) != 12 or [int(line.split()[0]) for line in lines] != list(range(12)):
            raise ValueError('invalid scalar stream')
        streams.append(lines)
    if streams[1:] != streams[:1] * 2: raise ValueError('scalar streams differ')


def validate_gate_proofs(rng_path, deterministic_path, executable, deep=True):
    rng = json.loads(rng_path.read_text())
    validate_scalar_proof(rng, executable)
    gate = json.loads(deterministic_path.read_text())
    if (gate.get('schema') != 'dmgmd.domain-npt-deterministic-proof.v2'
            or gate.get('candidate_sha256') != sha(executable)):
        raise ValueError('invalid deterministic proof identity')
    reports = gate['reports']
    expected_reports = {'pressure', 'pressure_reference', 'differential_ber', 'differential_scr', 'affine_cache', 'contracts',
                        'negatives_HostStaged', 'negatives_CudaAware', 'variants_ber', 'variants_scr', 'pmpi'}
    if set(reports) != expected_reports: raise ValueError('incomplete required runner reports')
    reference, reference_names = validate_report_artifacts(reports['pressure_reference'], executable, expected_status='REFERENCE_PASS')
    pressure, names = validate_report_artifacts(reports['pressure'], executable)
    expected_reference = {f's{i}-p1-HostStaged-off-r{repeat}' for i in range(15) for repeat in (0, 1)}
    if reference_names != expected_reference: raise ValueError('incomplete independent pressure reference matrix')
    fixture = gate['pressure_fixture']
    fixture_path = Path(fixture['path'])
    if not fixture_path.is_absolute(): raise ValueError('invalid pressure fixture path')
    inventory = {name: sha(fixture_path / name) for name in ('model.xyz', 'nep.txt', 'run.in')}
    if inventory != fixture['files_sha256'] or inventory != pressure.get('fixture_sha256') or inventory != reference.get('fixture_sha256'):
        raise ValueError('pressure fixture identity mismatch')
    expected = set(expected_reference)
    expected |= {f's{i}-p{p}-{b}-{m}-r0' for i, p, b, m in itertools.product(
        range(15), (2, 4), ('HostStaged', 'CudaAware'), ('rebuild', 'affine'))}
    if names != expected: raise ValueError('incomplete pressure oracle matrix')
    for item in (reference, pressure):
        for rec in item['records']:
            match = re.fullmatch(r's(\d+)-p(1|2|4)-(HostStaged|CudaAware)-(off|rebuild|affine)-r([01])', rec['stage'])
            if not match: raise ValueError('invalid pressure stage metadata')
            index, rank, backend, mode, repeat = match.groups()
            if (int(index) >= len(pressure_oracle.JITTER_SCALES) or rec.get('ranks') != int(rank)
                    or rec.get('backend') != backend or rec.get('mode') != mode or rec.get('repeat') != int(repeat)
                    or rec.get('scale') != pressure_oracle.JITTER_SCALES[int(index)]):
                raise ValueError('pressure stage metadata mismatch')
    settings = pressure.get('settings', {})
    if (pressure.get('schema') != 'dmgmd.domain-pressure-oracle.v1'
            or settings.get('scales') != list(pressure_oracle.JITTER_SCALES)
            or settings.get('dt_fs') != 1e-8 or settings.get('bulk_modulus_GPa') != 2001
            or settings.get('reference_only') is not False or settings.get('temperature_damping_steps') != 1e30
            or settings.get('jitter_full') is not True or settings.get('jitter_reference') is not False
            or settings.get('calibration') is not False
            or settings.get('jitter') != {'seed': 20261106, 'distribution': 'independent uniform per atom and axis',
                'amplitude_A': .01, 'centers': [-.008, 0., .008], 'h': [.001, .0005]}
            or settings.get('finite_difference', {}).get('atol_GPa') != .01
            or settings.get('finite_difference', {}).get('rtol') != .002):
        raise ValueError('pressure oracle settings differ')
    checks = pressure.get('checks', {})
    fd = checks.get('finite_difference', {})
    if (checks.get('reference_repeat', {}).get('status') != 'PASS'
            or checks['reference_repeat'].get('count') != 15 or fd.get('pass') is not True
            or fd.get('direction', {}).get('pass') is not True
            or len(fd.get('derivatives', [])) != 6
            or {(x.get('center_log_scale'), x.get('h_log_scale')) for x in fd['derivatives']}
               != set(itertools.product((-.008, 0., .008), (.001, .0005)))
            or any(x.get('pass') is not True for x in fd['derivatives'])):
        raise ValueError('pressure oracle checks incomplete')
    if deep:
        points = {r['scale']: r['metrics'] for r in pressure['records'] if r['ranks'] == 1 and r['repeat'] == 0}
        if pressure_oracle.jitter_derivative_checks(points) != fd:
            raise ValueError('pressure finite difference recomputation mismatch')
    local_checks = checks.get('local_finite_difference', [])
    required_local = set(itertools.product((2, 4), ('HostStaged', 'CudaAware'), ('rebuild', 'affine')))
    if (len(local_checks) != 8
            or {(x.get('ranks'), x.get('backend'), x.get('mode')) for x in local_checks} != required_local):
        raise ValueError('incomplete local pressure finite differences')
    for item in local_checks:
        if (item.get('pass') is not True or item.get('direction', {}).get('pass') is not True
                or len(item.get('derivatives', [])) != 6
                or {(x.get('center_log_scale'), x.get('h_log_scale')) for x in item['derivatives']}
                   != set(itertools.product((-.008, 0., .008), (.001, .0005)))
                or any(x.get('pass') is not True for x in item['derivatives'])):
            raise ValueError('local pressure finite difference failed or incomplete')
        if deep:
            points = {r['scale']: r['metrics'] for r in pressure['records']
                      if (r['ranks'], r['backend'], r['mode']) == (item['ranks'], item['backend'], item['mode'])}
            expected_check = pressure_oracle.jitter_derivative_checks(points)
            actual_check = {k: item[k] for k in ('derivatives', 'direction', 'pass')}
            if actual_check != expected_check:
                raise ValueError('local pressure finite difference recomputation mismatch')
    reference_proof = pressure.get('reference_proof', {})
    if (reference_proof.get('path') != reports['pressure_reference']['path']
            or reference_proof.get('sha256') != reports['pressure_reference']['sha256']
            or reference_proof.get('status') != 'VERIFIED' or reference_proof.get('records') != 30
            or reference_proof.get('finite_difference') != reference.get('checks', {}).get('finite_difference')):
        raise ValueError('pressure reference proof mismatch')
    if deep:
        tolerances = json.loads((ROOT / 'tests/baseline/manifest.json').read_text())['tolerances']
        recomputed = pressure_oracle.validate_jitter_proof(Path(reports['pressure_reference']['path']), executable,
            inventory, pressure_oracle.jitter_model((fixture_path / 'model.xyz').read_text()), tolerances)
        if recomputed != reference_proof: raise ValueError('pressure reference recomputation mismatch')
    if any(r.get('metrics', {}).get('thermostat_branch_skipped') is not True
           or r['metrics'].get('thermostat_formula_factor') != 1.0
           or r['metrics'].get('thermostat_coupling') != 1.0 / 1e30 for r in pressure['records']):
        raise ValueError('pressure oracle thermostat settings mismatch')
    if any('-r1' in r['stage'] or '-p1-' not in r['stage'] for r in pressure['records'] if not r.get('comparison')):
        raise ValueError('missing pressure comparison')
    ranks, backends, modes = (2, 4), ('HostStaged', 'CudaAware'), ('off', 'rebuild', 'affine')
    for ensemble in ('ber', 'scr'):
        report, names = validate_report_artifacts(reports['differential_' + ensemble], executable, 'differential')
        if names != {f'p{p}-{b}-{m}' for p, b, m in itertools.product(ranks, backends, modes)}:
            raise ValueError('incomplete differential matrix')
        for rec in report['records']:
            stage = Path(reports['differential_' + ensemble]['path']).parent / f"p{rec['ranks']}-{rec['backend']}-{rec['mode']}"
            if f'ensemble npt_{ensemble} ' not in (stage / 'run.in').read_text():
                raise ValueError('differential ensemble mismatch')
            if rec['mode'] != 'off' and not rec.get('comparison'): raise ValueError('missing differential comparison')
    report, names = validate_report_artifacts(reports['affine_cache'], executable)
    expected = {f'p{p}-{b}-{scenario}-{m}' for p, b, scenario, m in
                itertools.product(ranks, backends, ('weak', 'expansion', 'compression'), modes)}
    if names != expected: raise ValueError('incomplete affine cache matrix')
    if any(not r.get('comparison') or not r.get('cache_summary') for r in report['records'] if not r['stage'].endswith('-off')):
        raise ValueError('missing affine cache comparison')
    report, names = validate_report_artifacts(reports['contracts'], executable)
    expected = set()
    for p, b, ensemble, scenario in itertools.product(ranks, backends, ('npt_ber', 'npt_scr'), ('single', 'segments', 'late-unwrapped', 'fixed-box')):
        if ensemble == 'npt_ber' and scenario == 'fixed-box': continue
        prefix = f'p{p}-{b}-{ensemble}-{scenario}'
        expected |= {prefix + '-' + m for m in modes}
        if ensemble == 'npt_scr': expected |= {prefix + '-' + m + '-repeat' for m in ('rebuild', 'affine')}
    for b in backends:
        for source, destination in itertools.product((1, 2, 4), ranks):
            prefix = f'restart-{b}-from{source}-to{destination}'
            expected |= {prefix + '-' + m for m in ('source', *modes)}
        for source in ranks:
            prefix = f'restart-{b}-from{source}-to1'
            expected |= {prefix + '-' + m for m in ('source', *modes)}
    if names != expected: raise ValueError('incomplete operational contracts matrix')
    if any(r.get('unwrapped_semantics', {}).get('coordinates_checked', 0) <= 0
           or not r['unwrapped_semantics'].get('comparison') for r in report['records'] if '-late-unwrapped-' in r['stage']):
        raise ValueError('missing late unwrapped semantics evidence')
    if any(not r.get('comparison') for r in report['records'] if
           (r['stage'].endswith('-rebuild') or r['stage'].endswith('-affine')) and
           not r['stage'].endswith('-source')):
        raise ValueError('missing operational comparison')
    for backend in backends:
        report, names = validate_report_artifacts(reports['negatives_' + backend], executable)
        expected = {'invalid-mode', 'mismatched-mode', 'partition-overlap-invalid', 'partition-mismatch',
                    'partition-npt', 'initial-fallback', 'runtime-geometry', 'multi-box-displacement',
                    'empty-off', 'empty-rebuild', 'empty-affine'}
        if names != expected or any(r.get('environment', {}).get('DMGMD_COMM_BACKEND') != backend for r in report['records']):
            raise ValueError('incomplete negative matrix')
    for ensemble in ('ber', 'scr'):
        report, names = validate_report_artifacts(reports['variants_' + ensemble], executable)
        expected = {f'{fixture}-p{p}-{b}-{m}' for fixture, p, b, m in itertools.product(
            ('nep5', 'typewise', 'zbl', 'flexible-zbl', 'typewise-zbl'), ranks, backends, modes)}
        if names != expected: raise ValueError('incomplete potential variants matrix')
        for rec in report['records']:
            stage = Path(reports['variants_' + ensemble]['path']).parent / rec['stage']
            if f'ensemble npt_{ensemble} ' not in (stage / 'run.in').read_text():
                raise ValueError('variants ensemble mismatch')
            if not rec['stage'].endswith('-off') and not rec.get('comparison'):
                raise ValueError('missing variants comparison')
    pmpi = reports['pmpi']
    if not isinstance(pmpi, list) or len(pmpi) != 16: raise ValueError('incomplete PMPI reports')
    observed = set()
    for descriptor in pmpi:
        report, names = validate_report_artifacts(descriptor, executable, 'pmpi')
        if names != {'steps-4', 'steps-6'}: raise ValueError('incomplete PMPI steps')
        records = sorted(report['records'], key=lambda x: x['steps'])
        env = records[0]['environment']
        rank = int(records[0]['command'][2])
        backend, mode = env['DMGMD_COMM_BACKEND'], env['DMGMD_DOMAIN_NPT']
        stage = Path(descriptor['path']).parent / 'steps-4'
        lines = (stage / 'run.in').read_text()
        ensemble = next((x for x in ('npt_ber', 'npt_scr') if f'ensemble {x} ' in lines), None)
        key = (rank, backend, mode, ensemble)
        if key in observed: raise ValueError('duplicate PMPI configuration')
        observed.add(key)
        expected_delta = {'thermo': 2, 'reason': 4, 'bcast2': 4 if ensemble == 'npt_scr' else 2,
                          'preflight': 0, 'other_allreduce': 0, 'gather': 0, 'gatherv': 0, 'allgather': 0, 'allgatherv': 0}
        delta = records[1].get('delta_by_rank', {})
        if set(delta) != {str(i) for i in range(rank)} or any(x != expected_delta for x in delta.values()):
            raise ValueError('PMPI counts mismatch')
    if observed != set(itertools.product(ranks, backends, ('rebuild', 'affine'), ('npt_ber', 'npt_scr'))):
        raise ValueError('incomplete PMPI configuration matrix')
    return None


def generate_gate_proofs(args):
    root, executable = args.evidence.resolve(), args.candidate.resolve()
    def descriptor(path):
        return {'path': str(path.resolve()), 'sha256': sha(path)}
    names = ('pressure', 'differential_ber', 'differential_scr', 'affine_cache', 'contracts',
             'negatives_HostStaged', 'negatives_CudaAware', 'variants_ber', 'variants_scr')
    reports = {name: descriptor(root / name.replace('_', '-') / 'results.json') for name in names}
    reports['pressure_reference'] = descriptor(root / 'pressure-jitter-reference' / 'results.json')
    reports['pmpi'] = [descriptor(p) for p in sorted((root / 'pmpi').glob('*/results.json'))]
    scalar_path = root / 'scalar' / 'results.json'
    scalar = descriptor(scalar_path)
    scalar['files_sha256'] = {f'p{p}.{suffix}': sha(scalar_path.parent / f'p{p}.{suffix}')
                              for p in (1, 2, 4) for suffix in ('stdout', 'stderr')}
    rng = {'schema': 'dmgmd.domain-npt-rng-proof.v2', 'candidate_sha256': sha(executable), 'scalar': scalar}
    deterministic = {'schema': 'dmgmd.domain-npt-deterministic-proof.v2',
                     'candidate_sha256': sha(executable), 'reports': reports,
                     'pressure_fixture': {'path': str(args.pressure_fixture.resolve()), 'files_sha256':
                         {n: sha(args.pressure_fixture / n) for n in ('model.xyz', 'nep.txt', 'run.in')}}}
    # Validate before publishing either proof. Temporary names also prevent a
    # partially written proof from being mistaken for an approved result.
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rng_path, gate_path = output / 'rng-proof.json', output / 'deterministic-proof.json'
    try:
        write_json(rng_path, rng); write_json(gate_path, deterministic)
        validate_gate_proofs(rng_path, gate_path, executable)
    except Exception:
        shutil.rmtree(output)
        raise
    print(json.dumps({'rng_proof': str(rng_path), 'deterministic_proof': str(gate_path)}))


def freeze(args):
    regpath, root, executable = args.registration.resolve(), args.inputs.resolve(), args.candidate.resolve()
    reg = json.loads(regpath.read_text())
    if reg.get('status') != 'APPROVED': raise ValueError('formal freeze requires APPROVED registration')
    inventory = validate_inventory(root, regpath)
    expected = {'nep.txt', 'nvt.in'} | {f'model-{seed}.xyz' for seed in reg['seeds']}
    expected |= {f"scr-{profile['name']}.in" for profile in reg['profiles']}
    expected |= {f'nvt-model-{seed}-{volume}.xyz' for seed in reg['independent_scientific_reference']['seeds'] for volume in ('0.99','1.00','1.01')}
    if set(inventory['files']) != expected or inventory['files']['nep.txt'] != reg['potential_sha256']:
        raise ValueError('input inventory does not match registration')
    output = args.output.resolve()
    if output.exists(): raise ValueError('freeze already exists')
    proof = json.loads(args.environment_proof.read_text())
    if (proof.get('schema') != 'dmgmd.npt-b3-environment-proof.v1' or proof.get('status') != 'PASS'
            or proof.get('candidate_sha256') != sha(executable) or proof.get('gate_ranks') != 4
            or proof.get('supported_runtime_backends') != ['HostStaged', 'CudaAware']):
        raise ValueError('invalid environment proof')
    validate_gate_proofs(args.rng_proof.resolve(), args.deterministic_proof.resolve(), executable)
    files = (HERE / 'domain_statistics.py', HERE / 'run_domain_npt.py',
             HERE / 'generate_inputs.py', HERE / 'analyze_statistics.py',
             HERE / 'paired_confirmation_math.py', HERE / 'analyze_domain_statistics.py',
             HERE / 'run_domain_pressure_oracle.py', HERE / 'run_domain_npt_contracts.py',
             HERE / 'run_domain_npt_negatives.py', HERE / 'run_domain_npt_pmpi.py',
             HERE / 'run_domain_npt_variants.py', HERE / 'run_domain_affine_cache.py',
             HERE / 'run_domain_scr_scalar.py', HERE / 'check_migration_numerics.py',
             HERE / 'domain-manifest.json', ROOT / 'tests/baseline/run_baselines.py',
             ROOT / 'tests/baseline/manifest.json',
             HERE / 'capture_environment_proof.py', ROOT / 'tests/thermostats/sample_statistics.py',
             ROOT / 'tests/thermostats/autocorrelation.cpp', ROOT / 'tests/mpi/check_environment.py')
    registration_sha = sha(regpath)
    ledger = (CLAIM_ROOT / registration_sha).resolve()
    data = {'schema': 'dmgmd.domain-npt.statistics-freeze.v1', 'registration_sha256': registration_sha,
            'inventory_sha256': sha(root / 'inventory.json'), 'candidate_sha256': sha(executable),
            'candidate': str(executable), 'inputs': str(root),
            'acf_helper_sha256': sha(args.acf_helper.resolve()),
            'linked_library_sha256': linked_library_hashes(executable, proof['mpiexec_path']),
            'software_sha256': {str(path.relative_to(ROOT)): sha(path) for path in files},
            'jobs': formal_jobs(reg), 'environment_proof_sha256': sha(args.environment_proof.resolve()),
            'rng_proof_sha256': sha(args.rng_proof.resolve()),
            'deterministic_proof_sha256': sha(args.deterministic_proof.resolve()),
            'ledger': str(ledger), 'ledger_token': secrets.token_hex(32), 'freeze_path': str(output)}
    CLAIM_ROOT.mkdir(parents=True, exist_ok=True)
    ledger.mkdir(exist_ok=False)
    (ledger / 'jobs').mkdir()
    exclusive_json(ledger / 'identity.json', {'schema': 'dmgmd.domain-npt.statistics-ledger.v1',
                   'registration_sha256': registration_sha, 'token': data['ledger_token'],
                   'freeze_path': str(output), 'status': 'CREATING'})
    # A failed or interrupted freeze leaves the ledger claimed and cannot be
    # silently repeated under the same registration.
    exclusive_json(output, data)
    replace_json(ledger / 'identity.json', {'schema': 'dmgmd.domain-npt.statistics-ledger.v1',
                 'registration_sha256': registration_sha, 'token': data['ledger_token'],
                 'freeze_path': str(output), 'freeze_sha256': sha(output), 'status': 'READY'})
    print(json.dumps({'freeze': str(output), 'sha256': sha(output), 'jobs': len(data['jobs'])}))


def validate_freeze(path, registration, inputs, candidate, environment_proof, rng_proof, deterministic_proof, acf_helper):
    data = json.loads(path.read_text())
    if data.get('schema') != 'dmgmd.domain-npt.statistics-freeze.v1': raise ValueError('bad freeze schema')
    ledger = (CLAIM_ROOT / data['registration_sha256']).resolve()
    if (data.get('ledger') != str(ledger) or data.get('freeze_path') != str(path.resolve())):
        raise ValueError('freeze ledger identity mismatch')
    identity = json.loads((ledger / 'identity.json').read_text())
    if (identity.get('schema') != 'dmgmd.domain-npt.statistics-ledger.v1' or identity.get('status') != 'READY'
            or identity.get('registration_sha256') != data['registration_sha256']
            or identity.get('token') != data.get('ledger_token') or identity.get('freeze_path') != str(path.resolve())
            or identity.get('freeze_sha256') != sha(path)):
        raise ValueError('freeze ledger identity mismatch')
    reg = json.loads(registration.read_text())
    if reg.get('status') != 'APPROVED': raise ValueError('registration not approved')
    if data['registration_sha256'] != sha(registration): raise ValueError('registration identity mismatch')
    validate_inventory(inputs, registration)
    if data['inventory_sha256'] != sha(inputs / 'inventory.json'): raise ValueError('inventory identity mismatch')
    if data['candidate_sha256'] != sha(candidate): raise ValueError('candidate identity mismatch')
    if data['acf_helper_sha256'] != sha(acf_helper) or not os.access(acf_helper, os.X_OK): raise ValueError('ACF helper identity mismatch')
    if data['environment_proof_sha256'] != sha(environment_proof): raise ValueError('environment proof identity mismatch')
    proof = json.loads(environment_proof.read_text())
    if data['linked_library_sha256'] != linked_library_hashes(candidate, proof['mpiexec_path']):
        raise ValueError('linked library identity mismatch')
    if data['rng_proof_sha256'] != sha(rng_proof): raise ValueError('RNG proof identity mismatch')
    if data['deterministic_proof_sha256'] != sha(deterministic_proof): raise ValueError('deterministic proof identity mismatch')
    validate_gate_proofs(rng_proof, deterministic_proof, candidate, deep=False)
    if data['jobs'] != formal_jobs(reg): raise ValueError('job matrix mismatch')
    for name, digest in data['software_sha256'].items():
        if sha(ROOT / name) != digest: raise ValueError(f'software changed: {name}')
    return data


def formal_run(args):
    registration, inputs, executable, proof_path = (args.registration.resolve(), args.inputs.resolve(),
                                                  args.candidate.resolve(), args.environment_proof.resolve())
    frozen = validate_freeze(args.freeze.resolve(), registration, inputs, executable, proof_path,
                             args.rng_proof.resolve(), args.deterministic_proof.resolve(), args.acf_helper.resolve())
    if args.job not in frozen['jobs']: raise ValueError('unregistered job')
    job = frozen['jobs'][args.job]
    proof = json.loads(proof_path.read_text())
    devices = args.devices.split(',')
    if devices != proof['physical_devices'] or len(devices) != 4 or len(set(devices)) != 4:
        raise ValueError('devices differ from proof')
    if proof['environment_fingerprint'] != capture_environment_proof.environment_fingerprint():
        raise ValueError('environment fingerprint changed')
    actual_devices = capture_environment_proof.gpu_inventory()
    if proof['gpu_identity'] != {device: actual_devices.get(device) for device in devices}:
        raise ValueError('GPU identity changed')
    out = args.output.resolve()
    claim_path = Path(frozen['ledger']) / 'jobs' / (args.job + '.json')
    claim = {'status': 'RUNNING', 'job': args.job, 'output': str(out), 'freeze_sha256': sha(args.freeze)}
    exclusive_json(claim_path, claim)
    out.mkdir(parents=True, exist_ok=False)
    for source, dest in ((job['model'], 'model.xyz'), (job['run'], 'run.in'), ('nep.txt', 'nep.txt')):
        shutil.copyfile(inputs / source, out / dest)
    env = os.environ.copy()
    env.update(CUDA_VISIBLE_DEVICES=','.join(devices[:job['ranks']]), CUDA_DEVICE_ORDER='PCI_BUS_ID',
               DMGMD_COMM_BACKEND=job['backend'], DMGMD_DOMAIN_NPT=job['mode'],
               DMGMD_DOMAIN_PARTITION='off', DMGMD_DOMAIN_OVERLAP='off',
               DMGMD_ENSEMBLE_SEED=str(job['seed']), OMP_NUM_THREADS='1')
    command = [proof['mpiexec_path'], '-n', str(job['ranks']), str(executable)]
    record = {'job': args.job, 'metadata': job, 'status': 'RUNNING', 'command': command,
              'freeze_sha256': sha(args.freeze), 'input_sha256': {x: sha(out / x) for x in ('model.xyz','nep.txt','run.in')}}
    write_json(out / 'record.json', record)
    start = time.monotonic()
    previous_term = signal.getsignal(signal.SIGTERM)
    def stop_on_term(signum, frame):
        raise InterruptedError(f'terminated by signal {signum}')
    signal.signal(signal.SIGTERM, stop_on_term)
    try:
        with (out / 'stdout.log').open('w') as stdout, (out / 'stderr.log').open('w') as stderr:
            process = subprocess.Popen(command, cwd=out, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
            try: process.wait(timeout=args.timeout)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL); process.wait(); raise
        if process.returncode: raise ValueError(f'exit {process.returncode}')
        log = (out / 'stdout.log').read_text(errors='replace') + '\n' + (out / 'stderr.log').read_text(errors='replace')
        if job['kind'] == 'scr': check_route(log, job['ranks'], job['mode'])
        reg = json.loads(registration.read_text())
        dt = (next(x['dt_fs'] for x in reg['profiles'] if x['name'] == job['profile'])
              if job['kind'] == 'scr' else reg['independent_scientific_reference']['dt_fs'])
        analyze_statistics._thermo_rows(out / 'thermo.out', job['steps'], 5760, dt)
        record.update(status='PASS', elapsed_seconds=time.monotonic()-start,
                      thermo_sha256=sha(out / 'thermo.out'),
                      log_sha256={name: sha(out / name) for name in ('stdout.log', 'stderr.log')})
    except BaseException as error:
        record.update(status='FAIL', error=str(error)); write_json(out / 'record.json', record)
        claim.update(status='FAIL', error=str(error), record_sha256=sha(out / 'record.json'))
        replace_json(claim_path, claim)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_term)
    write_json(out / 'record.json', record)
    claim.update(status='PASS', record_sha256=sha(out / 'record.json'))
    replace_json(claim_path, claim)


def validate_batch_record(path, key, job, freeze_path, inventory):
    record = json.loads((path / 'record.json').read_text())
    if (record.get('status') != 'PASS' or record.get('job') != key or record.get('metadata') != job
            or record.get('freeze_sha256') != sha(freeze_path)):
        raise ValueError(f'invalid completed job record: {key}')
    expected = {'model.xyz': inventory[job['model']], 'run.in': inventory[job['run']], 'nep.txt': inventory['nep.txt']}
    if (record.get('input_sha256') != expected or any(sha(path / n) != digest for n, digest in expected.items())
            or sha(path / 'thermo.out') != record.get('thermo_sha256')):
        raise ValueError(f'completed job input or thermo changed: {key}')
    logs = record.get('log_sha256', {})
    if set(logs) != {'stdout.log', 'stderr.log'} or any(sha(path / n) != digest for n, digest in logs.items()):
        raise ValueError(f'completed job logs changed: {key}')


def formal_batch(args):
    registration, inputs, candidate, freeze_path = (args.registration.resolve(), args.inputs.resolve(),
                                                     args.candidate.resolve(), args.freeze.resolve())
    envproof, rngproof, gateproof, helper = (args.environment_proof.resolve(), args.rng_proof.resolve(),
                                            args.deterministic_proof.resolve(), args.acf_helper.resolve())
    frozen = validate_freeze(freeze_path, registration, inputs, candidate, envproof, rngproof, gateproof, helper)
    inventory = json.loads((inputs / 'inventory.json').read_text())['files']
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    evidence = output / 'evidence'; evidence.mkdir()
    logs = output / 'batch-logs'; logs.mkdir()
    progress_path = output / 'progress.json'
    progress = {'schema': 'dmgmd.domain-npt.statistics-batch.v1', 'status': 'RUNNING',
                'freeze_sha256': sha(freeze_path), 'jobs': {}, 'analysis': None}
    def save_progress():
        temporary = output / '.progress.tmp'
        write_json(temporary, progress)
        os.replace(temporary, progress_path)
    save_progress()
    common = ['--freeze', str(freeze_path), '--registration', str(registration), '--inputs', str(inputs),
              '--candidate', str(candidate), '--environment-proof', str(envproof), '--rng-proof', str(rngproof),
              '--deterministic-proof', str(gateproof), '--acf-helper', str(helper)]
    def launch(command, label, entry):
        with (logs / f'{label}.stdout').open('x') as out, (logs / f'{label}.stderr').open('x') as err:
            process = subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True)
            try:
                entry['pid'] = process.pid
                save_progress()
                return process.wait()
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait()
                raise
    previous_term = signal.getsignal(signal.SIGTERM)
    def stop_batch(signum, frame):
        raise InterruptedError(f'terminated by signal {signum}')
    signal.signal(signal.SIGTERM, stop_batch)
    try:
        for key, job in frozen['jobs'].items():
            stage = evidence / key
            command = [sys.executable, str(HERE / 'domain_statistics.py'), 'run', *common,
                       '--job', key, '--output', str(stage), '--devices', args.devices, '--timeout', str(args.timeout)]
            entry = {'status': 'RUNNING', 'command': command}; progress['jobs'][key] = entry
            save_progress()
            code = launch(command, key, entry); entry['returncode'] = code
            if code != 0: raise RuntimeError(f'job failed: {key}, exit {code}')
            validate_batch_record(stage, key, job, freeze_path, inventory)
            entry['status'] = 'PASS'; entry['record_sha256'] = sha(stage / 'record.json')
            save_progress()
        analysis_path = output / 'analysis.json'
        command = [sys.executable, str(HERE / 'analyze_domain_statistics.py'), *common,
                   '--evidence', str(evidence), '--output', str(analysis_path)]
        progress['analysis'] = {'status': 'RUNNING', 'command': command}
        save_progress()
        code = launch(command, 'analysis', progress['analysis']); progress['analysis']['returncode'] = code
        if code != 0: raise RuntimeError(f'analysis failed: exit {code}')
        result = json.loads(analysis_path.read_text())
        if result.get('status') not in ('PASS', 'FAIL', 'INCONCLUSIVE'):
            raise ValueError('invalid final analysis status')
        progress['analysis'].update(status=result['status'], sha256=sha(analysis_path))
        progress['status'] = 'COMPLETE'
    except BaseException as error:
        if progress['analysis'] and progress['analysis']['status'] == 'RUNNING':
            progress['analysis']['status'] = 'FAIL'
        elif progress['jobs']:
            last = next(reversed(progress['jobs'].values()))
            if last['status'] == 'RUNNING': last['status'] = 'FAIL'
        progress.update(status='STOPPED', error=str(error))
        save_progress()
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_term)
    save_progress()


def run_pilot(args):
    root = args.inputs.resolve()
    inventory = json.loads((root / 'inventory.json').read_text())
    registration = args.registration.resolve()
    if inventory['registration_sha256'] != sha(registration): raise ValueError('registration changed')
    for name, digest in inventory['files'].items():
        if sha(root / name) != digest: raise ValueError(f'input changed: {name}')
    if args.steps < 1 or args.ranks not in (1, 2, 4): raise ValueError('invalid pilot steps/ranks')
    if args.ranks == 1 and args.mode != 'off': raise ValueError('P1 pilot uses off')
    devices = args.devices.split(',')
    exe = args.candidate.resolve()
    mpiexec = check_environment.validate_environment(exe, 'mpiexec', devices, args.ranks, args.timeout)
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(root / 'model-301.xyz', out / 'model.xyz')
    shutil.copyfile(root / 'nep.txt', out / 'nep.txt')
    (out / 'run.in').write_text(scr_run(args.steps))
    env = os.environ.copy(); env.update(CUDA_VISIBLE_DEVICES=','.join(devices[:args.ranks]),
        CUDA_DEVICE_ORDER='PCI_BUS_ID', DMGMD_COMM_BACKEND=args.backend, DMGMD_DOMAIN_NPT=args.mode,
        DMGMD_DOMAIN_PARTITION='off', DMGMD_DOMAIN_OVERLAP='off', DMGMD_ENSEMBLE_SEED='301', OMP_NUM_THREADS='1')
    command = [str(mpiexec), '-n', str(args.ranks), str(exe)]
    record = {'status': 'RUNNING', 'pilot_only': True, 'candidate_sha256': sha(exe),
              'inventory_sha256': sha(root / 'inventory.json'), 'command': command,
              'ranks': args.ranks, 'backend': args.backend, 'mode': args.mode, 'steps': args.steps}
    write_json(out / 'pilot.json', record)
    start = time.monotonic()
    try:
        with (out / 'stdout.log').open('w') as stdout, (out / 'stderr.log').open('w') as stderr:
            process = subprocess.Popen(command, cwd=out, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
            try: process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait(); raise
        if process.returncode: raise RuntimeError(f'exit {process.returncode}')
        log = (out / 'stdout.log').read_text(errors='replace') + '\n' + (out / 'stderr.log').read_text(errors='replace')
        check_route(log, args.ranks, args.mode)
        rows = [x for x in (out / 'thermo.out').read_text().splitlines() if x.strip() and not x.lstrip().startswith('#')]
        if len(rows) != args.steps: raise ValueError('unexpected thermo row count')
        if any(not math.isfinite(float(v)) for row in rows for v in row.split()): raise ValueError('nonfinite thermo')
        timing = [line for line in log.splitlines() if line.startswith('DMGMD_TIMING phase=run ')]
        if len(timing) != 1: raise ValueError('missing unique run timing')
        timing_fields = dict(part.split('=', 1) for part in timing[0].split()[1:] if '=' in part)
        if int(timing_fields['steps']) != args.steps: raise ValueError('timing steps mismatch')
        loop_seconds = float(timing_fields['seconds_max'])
        if not math.isfinite(loop_seconds) or loop_seconds <= 0: raise ValueError('invalid run timing')
        elapsed = time.monotonic() - start
        record.update(status='PASS', elapsed_seconds=elapsed, run_loop_seconds_max=loop_seconds,
                      run_loop_steps_per_second=args.steps / loop_seconds,
                      output_sha256={p.name: sha(p) for p in out.iterdir() if p.is_file() and p.name != 'pilot.json'})
    except Exception as error:
        record.update(status='FAIL', error=str(error)); write_json(out / 'pilot.json', record); raise
    write_json(out / 'pilot.json', record)
    print(json.dumps(record, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('prepare'); q.add_argument('--output', required=True, type=Path)
    q.add_argument('--registration', type=Path, default=HERE / 'domain-statistics-registration.json')
    q.set_defaults(func=prepare)
    q = sub.add_parser('pilot'); q.add_argument('--inputs', required=True, type=Path)
    q.add_argument('--registration', type=Path, default=HERE / 'domain-statistics-registration.json')
    q.add_argument('--output', required=True, type=Path); q.add_argument('--candidate', required=True, type=Path)
    q.add_argument('--steps', type=int, default=100); q.add_argument('--ranks', type=int, default=2)
    q.add_argument('--mode', choices=('off','rebuild','affine'), default='rebuild')
    q.add_argument('--backend', choices=('HostStaged','CudaAware'), default='HostStaged')
    q.add_argument('--devices', required=True); q.add_argument('--timeout', type=int, default=900)
    q.set_defaults(func=run_pilot)
    q = sub.add_parser('freeze'); q.add_argument('--inputs', required=True, type=Path)
    q.add_argument('--registration', type=Path, default=HERE / 'domain-statistics-registration.json')
    q.add_argument('--candidate', required=True, type=Path); q.add_argument('--environment-proof', required=True, type=Path)
    q.add_argument('--rng-proof', required=True, type=Path); q.add_argument('--deterministic-proof', required=True, type=Path)
    q.add_argument('--acf-helper', required=True, type=Path)
    q.add_argument('--output', required=True, type=Path); q.set_defaults(func=freeze)
    q = sub.add_parser('proof'); q.add_argument('--evidence', required=True, type=Path)
    q.add_argument('--candidate', required=True, type=Path); q.add_argument('--output', required=True, type=Path)
    q.add_argument('--pressure-fixture', required=True, type=Path)
    q.set_defaults(func=generate_gate_proofs)
    q = sub.add_parser('run'); q.add_argument('--inputs', required=True, type=Path)
    q.add_argument('--registration', type=Path, default=HERE / 'domain-statistics-registration.json')
    q.add_argument('--candidate', required=True, type=Path); q.add_argument('--environment-proof', required=True, type=Path)
    q.add_argument('--freeze', required=True, type=Path); q.add_argument('--job', required=True)
    q.add_argument('--rng-proof', required=True, type=Path); q.add_argument('--deterministic-proof', required=True, type=Path)
    q.add_argument('--acf-helper', required=True, type=Path)
    q.add_argument('--output', required=True, type=Path); q.add_argument('--devices', required=True)
    q.add_argument('--timeout', type=int, default=86400); q.set_defaults(func=formal_run)
    q = sub.add_parser('batch'); q.add_argument('--inputs', required=True, type=Path)
    q.add_argument('--registration', type=Path, default=HERE / 'domain-statistics-registration.json')
    q.add_argument('--candidate', required=True, type=Path); q.add_argument('--environment-proof', required=True, type=Path)
    q.add_argument('--freeze', required=True, type=Path); q.add_argument('--rng-proof', required=True, type=Path)
    q.add_argument('--deterministic-proof', required=True, type=Path); q.add_argument('--acf-helper', required=True, type=Path)
    q.add_argument('--output', required=True, type=Path); q.add_argument('--devices', required=True)
    q.add_argument('--timeout', type=int, default=86400); q.set_defaults(func=formal_batch)
    args = p.parse_args(); args.func(args)


if __name__ == '__main__': main()
