#!/usr/bin/env python3
"""Independent DN4 large-box pressure gate. No GPU work occurs on import."""
import argparse
import datetime
import json
import math
import os
from pathlib import Path
import platform
import re
import random
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'tests/baseline'), str(ROOT / 'tests/mpi')]
import run_baselines as baseline
import run_domain_npt_contracts as contracts
import check_environment

SCALES = (-0.001, -0.0005, 0.0, 0.0005, 0.001)  # log(linear scale)
CALIBRATION_SCALES = (-0.016, -0.008, -0.004, -0.002, 0.0, 0.002, 0.004, 0.008, 0.016)
JITTER_CENTERS = (-0.008, 0.0, 0.008)
JITTER_H = (0.001, 0.0005)
JITTER_SCALES = tuple(sorted({round(c + shift, 7) for c in JITTER_CENTERS
                              for shift in (0.0, -0.001, 0.001, -0.0005, 0.0005)}))
JITTER_SEED = 20261106
JITTER_A = 0.01
DT = 1e-8  # fs; negligible position drift, NPT requires a positive time step
TIME_CONVERSION = 10.18051
PRESSURE_CONVERSION = 160.2177
FD_ATOL_GPA, FD_RTOL = 0.01, 0.002
RUN = ('potential nep.txt\ntime_step 1e-8\nensemble npt_ber 300 300 1e30 0 2001 200\n'
       'dump_thermo 1\ndump_xyz 1 trajectory.xyz precision double velocity force potential virial\n'
       'dump_restart 1\nrun 1\n')


def jitter_model(text):
    lines = text.splitlines()
    if int(lines[0]) != 5760 or len(lines) != 5762:
        raise ValueError('expected 5760-atom fixture')
    rng = random.Random(JITTER_SEED)
    for i in range(2, len(lines)):
        fields = lines[i].split()
        if len(fields) != 8 or fields[0] != 'C':
            raise ValueError('expected C pos mass vel fixture')
        fields[1:4] = [format(float(x) + rng.uniform(-JITTER_A, JITTER_A), '.17g') for x in fields[1:4]]
        lines[i] = ' '.join(fields)
    return '\n'.join(lines) + '\n'


def offsets(frame):
    result, start = {}, 0
    for name, _, width in frame['properties']:
        result[name] = (start, width)
        start += width
    return result


def numbers(row, spec, name):
    start, width = spec[name]
    return [float(x) for x in row[start:start + width]]


def scaled_model(text, strain):
    lines = text.splitlines()
    if int(lines[0]) != 5760 or len(lines) != 5762:
        raise ValueError('expected registered 5760-atom fixture')
    match = re.search(r'Lattice="([^"]+)"', lines[1])
    if not match:
        raise ValueError('missing lattice')
    lattice = [float(x) for x in match.group(1).split()]
    if len(lattice) != 9 or any(lattice[i] != 0 for i in (1, 2, 3, 5, 6, 7)):
        raise ValueError('fixture must be orthogonal')
    scale = math.exp(strain)
    lines[1] = lines[1][:match.start(1)] + ' '.join(format(x * scale, '.17g') for x in lattice) + lines[1][match.end(1):]
    for i in range(2, len(lines)):
        fields = lines[i].split()
        if len(fields) != 8 or fields[0] != 'C':
            raise ValueError('expected C pos mass vel fixture')
        fields[1:4] = [format(float(x) * scale, '.17g') for x in fields[1:4]]
        lines[i] = ' '.join(fields)
    return '\n'.join(lines) + '\n'


def snapshot(frame, model, thermo):
    out, inp = offsets(frame), offsets(model)
    if frame['natoms'] != model['natoms'] or not {'vel', 'forces', 'energy_atom', 'virial'} <= out.keys() or 'mass' not in inp:
        raise ValueError('missing owned output or mass')
    lattice = [float(x) for x in frame['fields']['Lattice'].split()]
    if len(lattice) != 9 or any(lattice[i] != 0 for i in (1, 2, 3, 5, 6, 7)):
        raise ValueError('non-orthogonal output')
    model_lattice = [float(x) for x in model['fields']['Lattice'].split()]
    if len(model_lattice) != 9 or any(abs(a - b) > 1e-10 for a, b in zip(lattice, model_lattice)):
        raise ValueError('fixed-box snapshot changed lattice')
    if 'pos' not in out or 'pos' not in inp:
        raise ValueError('missing snapshot positions')
    volume = lattice[0] * lattice[4] * lattice[8]
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError('invalid volume')
    energies, traces, kinetic = [], [], []
    for row, original in zip(frame['rows'], model['rows']):
        if row[0] != original[0]:
            raise ValueError('atom ordering/species mismatch')
        pos, initial = numbers(row, out, 'pos'), numbers(original, inp, 'pos')
        lengths = (lattice[0], lattice[4], lattice[8])
        if any(abs((a - b) - round((a - b) / length) * length) > 1e-7
               for a, b, length in zip(pos, initial, lengths)):
            raise ValueError('microstep moved atoms beyond the static snapshot budget')
        mass = numbers(original, inp, 'mass')[0]
        v = numbers(row, out, 'vel')
        vir = numbers(row, out, 'virial')
        if mass <= 0 or len(v) != 3 or len(vir) != 9:
            raise ValueError('invalid mass, velocity or virial')
        energies.append(numbers(row, out, 'energy_atom')[0])
        traces.append(vir[0] + vir[4] + vir[8])
        kinetic.append(mass * sum((x * TIME_CONVERSION)**2 for x in v))
    energy, trace, two_k = math.fsum(energies), math.fsum(traces), math.fsum(kinetic)
    pressure = (two_k + trace) / (3 * volume) * PRESSURE_CONVERSION
    temperature = thermo[0]
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError('invalid pre-feedback temperature')
    coupling = 1.0 / 1e30
    calculated_factor = math.sqrt(1.0 + coupling * (300.0 / temperature - 1.0))
    if coupling > 1e-5 or calculated_factor != 1.0:
        raise ValueError('thermostat must be an exact no-op at this snapshot')
    measured = sum(thermo[i] for i in (3, 4, 5)) / 3
    allowed = 1e-5 + 1e-4 * abs(pressure)
    if not all(math.isfinite(x) for x in (energy, trace, two_k, pressure, measured)) or abs(pressure - measured) > allowed:
        raise ValueError(f'independent pressure mismatch: {pressure} vs {measured}, limit {allowed}')
    return {'energy_eV': energy, 'trace_virial_eV': trace, 'two_kinetic_eV': two_k,
            'volume_A3': volume, 'pressure_GPa': pressure, 'thermo_pressure_GPa': measured,
            'pressure_error_GPa': abs(pressure - measured), 'pressure_limit_GPa': allowed,
            'pre_feedback_temperature_K': temperature, 'thermostat_coupling': coupling,
            'thermostat_formula_factor': calculated_factor, 'thermostat_branch_skipped': coupling <= 1e-5,
            'config_pressure_GPa': trace / (3 * volume) * PRESSURE_CONVERSION}


def derivative_check(points):
    center = points[0.0]
    checks = []
    for h in (0.001, 0.0005):
        # ln(V) = 3 ln(scale); trace(W) = -dE/dln(scale).
        deriv = -(points[h]['energy_eV'] - points[-h]['energy_eV']) / (6 * h * center['volume_A3']) * PRESSURE_CONVERSION
        expected = center['config_pressure_GPa']
        error = abs(deriv - expected)
        limit = FD_ATOL_GPA + FD_RTOL * abs(expected)
        checks.append({'h_log_scale': h, 'energy_derivative_pressure_GPa': deriv,
                       'virial_pressure_GPa': expected, 'absolute_error_GPa': error,
                       'limit_GPa': limit, 'pass': math.isfinite(deriv) and error <= limit})
    compressed, expanded = points[-0.001]['config_pressure_GPa'], points[0.001]['config_pressure_GPa']
    direction = {'compressed_GPa': compressed, 'center_GPa': center['config_pressure_GPa'],
                 'expanded_GPa': expanded, 'pass': compressed > center['config_pressure_GPa'] > expanded}
    return {'derivatives': checks, 'direction': direction,
            'pass': all(c['pass'] for c in checks) and direction['pass']}


def jitter_derivative_checks(points):
    checks = []
    for center in JITTER_CENTERS:
        snapshot = points[center]
        for h in JITTER_H:
            plus, minus = points[round(center+h, 7)], points[round(center-h, 7)]
            value = -(plus['energy_eV'] - minus['energy_eV']) / (6*h*snapshot['volume_A3']) * PRESSURE_CONVERSION
            expected = snapshot['config_pressure_GPa']
            error, limit = abs(value-expected), FD_ATOL_GPA + FD_RTOL*abs(expected)
            checks.append({'center_log_scale': center, 'h_log_scale': h,
                           'energy_derivative_pressure_GPa': value, 'virial_pressure_GPa': expected,
                           'absolute_error_GPa': error, 'limit_GPa': limit,
                           'pass': math.isfinite(value) and error <= limit})
    pressures = [points[x]['config_pressure_GPa'] for x in JITTER_CENTERS]
    direction = {'compressed_GPa': pressures[0], 'center_GPa': pressures[1],
                 'expanded_GPa': pressures[2], 'pass': pressures[0] > pressures[1] > pressures[2]}
    return {'derivatives': checks, 'direction': direction,
            'pass': all(x['pass'] for x in checks) and direction['pass']}


def calibration_analysis(points, repeats):
    """Report P1 measurements only; no candidate tolerance is inferred here."""
    center = points[0.0]
    volume = center['volume_A3']
    convert = PRESSURE_CONVERSION / (3 * volume)
    raw = []
    for h in (0.002, 0.004, 0.008, 0.016):
        plus, minus = points[h]['energy_eV'], points[-h]['energy_eV']
        delta = plus - minus
        derivative = -delta / (2 * h) * convert
        raw.append({'h_log_scale': h, 'energy_plus_eV': plus, 'energy_minus_eV': minus,
                    'energy_difference_eV': delta, 'pressure_GPa': derivative,
                    'virial_pressure_GPa': center['config_pressure_GPa'],
                    'error_GPa': derivative - center['config_pressure_GPa'],
                    'binary64_energy_ulp_plus_eV': math.ulp(plus),
                    'binary64_energy_ulp_minus_eV': math.ulp(minus)})
    richardson = []
    for i in range(3):
        h, d1, d2 = raw[i]['h_log_scale'], raw[i]['pressure_GPa'], raw[i + 1]['pressure_GPa']
        estimate = (4 * d1 - d2) / 3
        richardson.append({'h_log_scale': h, 'pressure_GPa': estimate,
                           'error_GPa': estimate - center['config_pressure_GPa'],
                           'correction_GPa': estimate - d1})
    repeated = [{'log_scale': x, 'first_energy_eV': points[x]['energy_eV'],
                 'repeat_energy_eV': repeats[x]['energy_eV'],
                 'energy_difference_eV': repeats[x]['energy_eV'] - points[x]['energy_eV'],
                 'config_pressure_difference_GPa': repeats[x]['config_pressure_GPa'] - points[x]['config_pressure_GPa']}
                for x in SCALES]
    return {'raw_central_differences': raw, 'richardson': richardson,
            'repeats': repeated, 'center_virial_pressure_GPa': center['config_pressure_GPa'],
            'center_volume_A3': volume,
            'interpretation': 'diagnostic only; binary64 ulp is serialization precision, not NEP kernel precision; no stability criterion or acceptance threshold is inferred'}


def validate_jitter_proof(path, candidate, inventory, model_text, tolerances):
    """Recompute and verify every P1 proof before any local-domain GPU run."""
    report = json.loads(path.read_text())
    settings = report['settings']
    expected_jitter = {'seed': JITTER_SEED, 'distribution': 'independent uniform per atom and axis',
                       'amplitude_A': JITTER_A, 'centers': list(JITTER_CENTERS), 'h': list(JITTER_H)}
    if (report['schema'] != 'dmgmd.domain-pressure-oracle.v1' or report['status'] != 'REFERENCE_PASS'
        or report['candidate_sha256'] != contracts.sha(candidate) or report['fixture_sha256'] != inventory
        or settings.get('jitter_reference') is not True or settings['jitter'] != expected_jitter
        or settings['scales'] != list(JITTER_SCALES)
        or settings['finite_difference']['atol_GPa'] != FD_ATOL_GPA
        or settings['finite_difference']['rtol'] != FD_RTOL):
        raise ValueError('P1 proof identity or fixed registration mismatch')
    records = report['records']
    if len(records) != 2 * len(JITTER_SCALES):
        raise ValueError('P1 proof has incomplete record count')
    indexed = {(r['scale'], r['repeat']): r for r in records}
    if len(indexed) != len(records):
        raise ValueError('P1 proof has duplicate records')
    points = {}
    for i, strain in enumerate(JITTER_SCALES):
        for repeat in (0, 1):
            rec = indexed[(strain, repeat)]
            stage_name = f's{i}-p1-HostStaged-off-r{repeat}'
            if (rec['stage'] != stage_name or rec['status'] != 'PASS' or rec['ranks'] != 1
                or rec['backend'] != 'HostStaged' or rec['mode'] != 'off'):
                raise ValueError('invalid P1 proof matrix')
            stage = path.parent / stage_name
            for name, digest in rec['files_sha256'].items():
                if Path(name).name != name or contracts.sha(stage / name) != digest:
                    raise ValueError('P1 proof output hash mismatch')
            if not {'model.xyz', 'nep.txt', 'run.in', 'trajectory.xyz', 'thermo.out',
                    'restart.xyz', 'stdout.log', 'stderr.log'} <= rec['files_sha256'].keys():
                raise ValueError('P1 proof missing required output hashes')
            if (stage / 'model.xyz').read_text() != scaled_model(model_text, strain):
                raise ValueError('P1 proof model differs from registered fixture')
            if ((stage / 'run.in').read_text() != RUN or contracts.sha(stage / 'nep.txt') != inventory['nep.txt']):
                raise ValueError('P1 proof input changed')
            frames = baseline.parse_xyz(stage / 'trajectory.xyz')
            models = baseline.parse_xyz(stage / 'model.xyz')
            thermo = baseline.parse_thermo(stage / 'thermo.out')
            if len(frames) != 1 or len(models) != 1 or len(thermo) != 1 or len(thermo[0]['rows']) != 1:
                raise ValueError('invalid P1 proof output shape')
            metrics = snapshot(frames[0], models[0], thermo[0]['rows'][0])
            if metrics != rec['metrics']:
                raise ValueError('P1 proof metrics mismatch')
            if repeat == 0:
                points[strain] = metrics
            else:
                first = path.parent / f's{i}-p1-HostStaged-off-r0'
                contracts.compare(first, stage, tolerances)
                limit = FD_ATOL_GPA + FD_RTOL * abs(points[strain]['config_pressure_GPa'])
                if abs(points[strain]['config_pressure_GPa'] - metrics['config_pressure_GPa']) > limit:
                    raise ValueError('P1 proof repeat pressure mismatch')
    check = jitter_derivative_checks(points)
    if not check['pass'] or check != report['checks']['finite_difference']:
        raise ValueError('P1 proof finite difference mismatch')
    return {'path': str(path), 'sha256': contracts.sha(path), 'status': 'VERIFIED',
            'records': len(records), 'finite_difference': check}


def main():
    global SCALES
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--devices', required=True)
    p.add_argument('--timeout', type=int, default=900)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--reference-only', action='store_true', help='run only P1 repeats with the original fixed gate')
    group.add_argument('--calibration', action='store_true', help='P1-only diagnostic strain scan; never yields a gate PASS')
    group.add_argument('--jitter-reference', action='store_true', help='P1-only pre-registered jittered reference scan')
    group.add_argument('--jitter-full', action='store_true', help='full pre-registered jittered local-domain matrix')
    p.add_argument('--reference-proof', type=Path, help='completed --jitter-reference results.json, required by --jitter-full')
    a = p.parse_args()
    if a.calibration:
        SCALES = CALIBRATION_SCALES
    elif a.jitter_reference or a.jitter_full:
        SCALES = JITTER_SCALES
    if bool(a.reference_proof) != a.jitter_full:
        p.error('--reference-proof is required only with --jitter-full')
    candidate, fixture, root = a.candidate.resolve(), a.fixture.resolve(), a.work_root.resolve()
    devices = a.devices.split(',')
    needed = 1 if a.reference_only or a.calibration or a.jitter_reference else 4
    if len(devices) < needed or len(set(devices)) != len(devices) or any(not x for x in devices):
        p.error(f'{needed} unique devices required')
    inventory = {x: contracts.sha(fixture / x) for x in ('model.xyz', 'nep.txt', 'run.in')}
    model_text = (fixture / 'model.xyz').read_text()
    if a.jitter_reference or a.jitter_full:
        model_text = jitter_model(model_text)
    mpiexec = check_environment.validate_environment(candidate, 'mpiexec', devices, needed, a.timeout)
    root.mkdir(parents=True, exist_ok=False)
    tolerances = json.loads((ROOT / 'tests/baseline/manifest.json').read_text())['tolerances']
    report = {'schema': 'dmgmd.domain-pressure-oracle.v1', 'status': 'RUNNING',
              'candidate_sha256': contracts.sha(candidate), 'candidate': str(candidate),
              'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'platform': platform.platform(), 'fixture_sha256': inventory,
              'settings': {'scales': list(SCALES), 'ranks': [1, 2, 4],
                           'backends': ['HostStaged', 'CudaAware'], 'modes': ['rebuild', 'affine'],
                           'reference_repeats': 2, 'reference_only': a.reference_only, 'calibration': a.calibration,
                           'jitter_reference': a.jitter_reference, 'jitter_full': a.jitter_full,
                           'jitter': {'seed': JITTER_SEED, 'distribution': 'independent uniform per atom and axis',
                                      'amplitude_A': JITTER_A, 'centers': list(JITTER_CENTERS),
                                      'h': list(JITTER_H)} if a.jitter_reference or a.jitter_full else None, 'temperature_damping_steps': 1e30, 'dt_fs': DT, 'bulk_modulus_GPa': 2001,
                           'finite_difference': {'atol_GPa': FD_ATOL_GPA, 'rtol': FD_RTOL,
                              'formula': '-dE/dln(scale)/(3V) = trace(W)/(3V)',
                              'policy': ('diagnostic only; no acceptance threshold applied' if a.calibration else
                                         'fixed before candidate comparisons; reference repeats and two step sizes must independently pass')}},
              'records': [], 'checks': {}}
    def save():
        (root / 'results.json').write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n')
    save()
    def run(strain, rank, backend, mode, repeat):
        name = f's{SCALES.index(strain)}-p{rank}-{backend}-{mode}-r{repeat}'
        model_file = root / (name + '-model.xyz')
        model_file.write_text(scaled_model(model_text, strain))
        try:
            stage = contracts.execute(root / name, candidate, mpiexec, rank, backend, mode, devices,
                                      a.timeout, model_file, fixture / 'nep.txt', RUN, report['records'])
            rec = report['records'][-1]
            if any(contracts.sha(stage / name) != digest for name, digest in rec['input_sha256'].items()):
                raise ValueError('runtime modified an input')
            rec.update(ranks=rank, backend=backend, mode=mode, scale=strain, repeat=repeat)
            frames = baseline.parse_xyz(stage / 'trajectory.xyz')
            models = baseline.parse_xyz(stage / 'model.xyz')
            thermos = baseline.parse_thermo(stage / 'thermo.out')
            if len(frames) != 1 or len(models) != 1 or len(thermos) != 1 or len(thermos[0]['rows']) != 1:
                raise ValueError('expected one snapshot and thermo row')
            rec['metrics'] = snapshot(frames[0], models[0], thermos[0]['rows'][0])
            return stage, rec['metrics']
        except Exception:
            if report['records']:
                report['records'][-1].update(ranks=rank, backend=backend, mode=mode, scale=strain, repeat=repeat, status='FAIL')
            raise
        finally:
            save()
    try:
        if a.jitter_full:
            report['reference_proof'] = validate_jitter_proof(a.reference_proof.resolve(), candidate, inventory, model_text, tolerances)
            save()
        refs, points, repeats = {}, {}, {}
        for strain in SCALES:
            first, points[strain] = run(strain, 1, 'HostStaged', 'off', 0)
            second, repeated = run(strain, 1, 'HostStaged', 'off', 1)
            repeats[strain] = repeated
            try:
                report['records'][-1]['comparison'] = contracts.compare(first, second, tolerances)
            except Exception as error:
                report['records'][-1].update(status='FAIL', error=str(error))
                raise
            refs[strain] = first
            # Compare repeated energy and pressure using the same fixed FD budget.
            limit = FD_ATOL_GPA + FD_RTOL * abs(points[strain]['config_pressure_GPa'])
            delta = abs(points[strain]['config_pressure_GPa'] - repeated['config_pressure_GPa'])
            if not a.calibration and delta > limit:
                raise ValueError('reference repeat exceeds fixed finite-difference budget')
        if a.calibration:
            report['checks']['reference_repeat'] = {'status': 'PASS', 'count': len(SCALES)}
            report['calibration'] = calibration_analysis(points, repeats)
            report['status'] = 'CALIBRATION_COMPLETE'
            return 0
        check = jitter_derivative_checks(points) if a.jitter_reference or a.jitter_full else derivative_check(points)
        report['checks']['reference_repeat'] = {'status': 'PASS', 'count': len(SCALES)}
        report['checks']['finite_difference'] = check
        if not check['pass']:
            raise ValueError('reference finite-difference or pressure direction failed')
        save()
        if a.reference_only or a.jitter_reference:
            report['status'] = 'REFERENCE_PASS'
            return 0
        for rank in (2, 4):
            for backend in ('HostStaged', 'CudaAware'):
                for mode in ('rebuild', 'affine'):
                    local_points = {}
                    for strain in SCALES:
                        stage, local_points[strain] = run(strain, rank, backend, mode, 0)
                        try:
                            report['records'][-1]['comparison'] = contracts.compare(refs[strain], stage, tolerances)
                        except Exception as error:
                            report['records'][-1].update(status='FAIL', error=str(error))
                            raise
                        save()
                    if a.jitter_full:
                        local_check = jitter_derivative_checks(local_points)
                        report['checks'].setdefault('local_finite_difference', []).append(
                            {'ranks': rank, 'backend': backend, 'mode': mode, **local_check})
                        save()
                        if not local_check['pass']:
                            raise ValueError(f'local finite difference or pressure direction failed: P{rank}/{backend}/{mode}')
        report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error=str(error))
        raise
    finally:
        save()
    return 0


if __name__ == '__main__':
    sys.exit(main())
