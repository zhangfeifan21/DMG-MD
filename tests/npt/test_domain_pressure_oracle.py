#!/usr/bin/env python3
"""CPU checks for units, signs, finite difference and fixture scaling."""
import math
import json
import tempfile
from pathlib import Path
import unittest
import run_domain_pressure_oracle as oracle
import audit_domain_pressure_calibration as audit


class PressureOracleTest(unittest.TestCase):
    def test_pressure_from_owned_atoms(self):
        model = {'natoms': 2, 'properties': [('species','S',1), ('pos','R',3), ('mass','R',1)],
                 'fields': {'Lattice':'2 0 0 0 3 0 0 0 4'},
                 'rows': [['C','0','0','0','2'], ['C','0','0','0','3']]}
        frame = {'natoms': 2, 'properties': [('species','S',1), ('pos','R',3), ('vel','R',3),
                 ('forces','R',3), ('energy_atom','R',1), ('virial','R',9)],
                 'fields': {'Lattice':'2 0 0 0 3 0 0 0 4'},
                 'rows': [['C','0','0','0','1','0','0','0','0','0','-1','1','0','0','0','2','0','0','0','3'],
                          ['C','0','0','0','0','2','0','0','0','0','-2','-1','0','0','0','-2','0','0','0','-3']]}
        two_k = 14 * oracle.TIME_CONVERSION**2
        p = two_k / (3 * 24) * oracle.PRESSURE_CONVERSION
        result = oracle.snapshot(frame, model, [300,0,0,p,p,p])
        self.assertAlmostEqual(result['pressure_GPa'], p)
        self.assertEqual(result['trace_virial_eV'], 0)
        self.assertEqual(result['energy_eV'], -3)
        self.assertTrue(result['thermostat_branch_skipped'])
        self.assertEqual(result['thermostat_formula_factor'], 1.0)
        with self.assertRaises(ValueError):
            oracle.snapshot(frame, model, [300,0,0,-p,-p,-p])

    def test_derivative_and_direction(self):
        # E(x)=a*x^2: pressure at x=0 is zero, slopes have opposite sign.
        # Positive a produces compression pressure > expansion pressure.
        points = {}
        for x in oracle.SCALES:
            volume = 100 * math.exp(3*x)
            points[x] = {'energy_eV': 100*x*x, 'config_pressure_GPa': -200*x/(3*volume)*oracle.PRESSURE_CONVERSION,
                         'volume_A3': volume}
        self.assertTrue(oracle.derivative_check(points)['pass'])
        points[0.0]['config_pressure_GPa'] = 2
        self.assertFalse(oracle.derivative_check(points)['pass'])

    def test_calibration_richardson_and_repeats(self):
        previous = oracle.SCALES
        try:
            oracle.SCALES = oracle.CALIBRATION_SCALES
            volume = 100.0
            factor = oracle.PRESSURE_CONVERSION / (3 * volume)
            points = {x: {'energy_eV': 7 + 2*x + 5*x**3,
                          'volume_A3': volume,
                          'config_pressure_GPa': -2*factor}
                      for x in oracle.SCALES}
            result = oracle.calibration_analysis(points, points)
            self.assertEqual(len(result['raw_central_differences']), 4)
            self.assertEqual(len(result['repeats']), 9)
            for estimate in result['richardson']:
                self.assertAlmostEqual(estimate['pressure_GPa'], -2*factor, places=9)
            self.assertTrue(all(row['energy_difference_eV'] == 0 for row in result['repeats']))
        finally:
            oracle.SCALES = previous

    def test_reference_proof_rejects_wrong_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            binary = root / 'binary'
            binary.write_bytes(b'candidate')
            report = {'schema': 'dmgmd.domain-pressure-oracle.v1', 'status': 'REFERENCE_PASS',
                      'candidate_sha256': 'wrong', 'fixture_sha256': {}, 'settings': {}}
            proof = root / 'results.json'
            proof.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                oracle.validate_jitter_proof(proof, binary, {}, '', {})

    def test_virial_work_integral_has_correct_sign(self):
        points = {x: {'energy_eV': 4 + 3*x + 2*x*x,
                      'trace_virial_eV': -3 - 4*x}
                  for x in (0.0, -0.008, -0.016, 0.008, 0.016)}
        for x in (-0.016, 0.016):
            result = audit.integrate_pair(points, x)
            self.assertAlmostEqual(result['residual_eV'], 0.0)

    def test_jitter_reference_preserves_nonposition_fields(self):
        rows = ['C 1 2 3 12 0.1 0.2 0.3'] * 5760
        source = '\n'.join(['5760', 'header', *rows]) + '\n'
        first = oracle.jitter_model(source)
        self.assertEqual(first, oracle.jitter_model(source))
        values = first.splitlines()[2].split()
        self.assertEqual(values[4:], ['12', '0.1', '0.2', '0.3'])
        self.assertTrue(all(abs(float(x) - y) <= .01 for x,y in zip(values[1:4], (1,2,3))))
        self.assertNotEqual(first.splitlines()[2], first.splitlines()[3])

    def test_jitter_three_centers_derivative(self):
        points = {}
        for x in oracle.JITTER_SCALES:
            volume = 100 * math.exp(3*x)
            points[x] = {'energy_eV': 100*x*x, 'config_pressure_GPa': -200*x/(3*volume)*oracle.PRESSURE_CONVERSION,
                         'volume_A3': volume}
        check = oracle.jitter_derivative_checks(points)
        self.assertEqual(len(check['derivatives']), 6)
        self.assertTrue(check['pass'])
        points[0.008]['config_pressure_GPa'] = 100
        self.assertFalse(oracle.jitter_derivative_checks(points)['pass'])

    def test_scales_positions_and_lattice_only(self):
        rows = ['C 1 2 3 12 0.1 0.2 0.3'] * 5760
        source = '\n'.join(['5760', 'pbc="T T T" Lattice="10 0 0 0 20 0 0 0 30" Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3', *rows]) + '\n'
        lines = oracle.scaled_model(source, math.log(2)).splitlines()
        self.assertIn('Lattice="20 0 0 0 40 0 0 0 60"', lines[1])
        self.assertEqual(lines[2], 'C 2 4 6 12 0.1 0.2 0.3')


if __name__ == '__main__':
    unittest.main()
