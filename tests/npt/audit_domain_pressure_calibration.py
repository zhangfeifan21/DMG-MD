#!/usr/bin/env python3
"""Read-only P1 calibration audit; provides diagnostics, never an acceptance gate."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'tests/baseline')]
import generate_inputs
import run_baselines as baseline
import run_domain_pressure_oracle as oracle


def integrate_pair(points, endpoint):
    mid = endpoint / 2
    a, b, c = points[0.0], points[mid], points[endpoint]
    observed = c['energy_eV'] - a['energy_eV']
    predicted = -endpoint * (a['trace_virial_eV'] + 4*b['trace_virial_eV'] + c['trace_virial_eV']) / 6
    return {'endpoint_log_scale': endpoint, 'energy_change_eV': observed,
            'simpson_integral_eV': predicted, 'residual_eV': observed - predicted,
            'abs_residual_per_atom_eV': abs(observed - predicted) / 5760}


def topology(model):
    lines = model.splitlines()
    box = list(map(float, re.search(r'Lattice="([^"]+)"', lines[1]).group(1).split()))
    lengths = (box[0], box[4], box[8])
    coords = [tuple(map(float, line.split()[1:4])) for line in lines[2:]]
    results = []
    # Eight basis atoms cover all translational orbits of the registered crystal.
    for center in coords[:8]:
        distances = []
        for other in coords:
            d = [(a-b) - round((a-b)/length)*length for a, b, length in zip(center, other, lengths)]
            distance = math.sqrt(sum(v*v for v in d))
            if distance > 1e-9 and distance < 8.0:
                distances.append(distance)
        results.append({str(c): {'count': sum(d < c for d in distances),
                                 'minimum_cutoff_gap_A': min(abs(d-c) for d in distances)} for c in (4, 7)})
    return {str(c): {'counts': sorted(set(x[str(c)]['count'] for x in results)),
                     'minimum_cutoff_gap_A': min(x[str(c)]['minimum_cutoff_gap_A'] for x in results)} for c in (4, 7)}


def site_float_metrics(path):
    frames = baseline.parse_xyz(path)
    if len(frames) != 1:
        raise ValueError('expected one frame')
    frame = frames[0]
    o = oracle.offsets(frame)
    values = [oracle.numbers(row, o, 'energy_atom')[0] for row in frame['rows']]
    if not all(math.isfinite(x) for x in values):
        raise ValueError('non-finite site energy')
    exact = sum(struct.unpack('f', struct.pack('f', x))[0] == x for x in values)
    return {'site_count': len(values), 'exact_binary32_count': exact,
            'unique_site_energies': len(set(values)), 'min_eV': min(values), 'max_eV': max(values)}


def audit(directory):
    report_path = directory / 'results.json'
    report = json.loads(report_path.read_text())
    scales = list(oracle.CALIBRATION_SCALES)
    if report['status'] != 'CALIBRATION_COMPLETE' or report['settings']['scales'] != scales:
        raise ValueError('not a complete registered calibration')
    records = report['records']
    if len(records) != 18:
        raise ValueError('wrong record count')
    points, geometry, precision = {}, {}, {}
    expected_model = generate_inputs.model_text((20, 6, 6), 301)
    for i, strain in enumerate(scales):
        pair = [r for r in records if r['scale'] == strain]
        if len(pair) != 2 or {r['repeat'] for r in pair} != {0, 1}:
            raise ValueError('wrong repeat matrix')
        for rec in pair:
            if (rec['status'], rec['ranks'], rec['mode'], rec['backend']) != ('PASS', 1, 'off', 'HostStaged'):
                raise ValueError('bad P1 record')
            stage = directory / rec['stage']
            for name in ('model.xyz', 'trajectory.xyz', 'thermo.out'):
                digest = hashlib.sha256((stage/name).read_bytes()).hexdigest()
                if digest != rec['files_sha256'][name]:
                    raise ValueError('artifact hash mismatch')
        rec = next(r for r in pair if r['repeat'] == 0)
        stage = directory / rec['stage']
        model = (stage/'model.xyz').read_text()
        if model != oracle.scaled_model(expected_model, strain):
            raise ValueError('model differs from scaled registered crystal')
        points[strain] = rec['metrics']
        geometry[str(strain)] = topology(model)
        precision[str(strain)] = site_float_metrics(stage/'trajectory.xyz')
    return {'schema': 'dmgmd.domain-pressure-calibration-audit.v1', 'status': 'DIAGNOSTIC_ONLY',
            'source_report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
            'geometry_basis_count': 8, 'geometry': geometry, 'site_energy_binary32': precision,
            'energy_virial_integral': [integrate_pair(points, x) for x in (-0.016, 0.016)],
            'interpretation': ['Counts are from eight translational basis centers of the exact generated crystal, using double geometry; the microstep is bounded separately by the runner.',
                               'Exact binary32 site energies identify the stored output representation, not a rigorous bound on preceding floating-point error.',
                               'Simpson integrals are retrospective diagnostics, not a frozen acceptance criterion.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--calibration', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    result = audit(a.calibration.resolve())
    with a.output.open('x') as f:
        json.dump(result, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')
    print(a.output)


if __name__ == '__main__':
    main()
