#!/usr/bin/env python3
"""CPU checks for the operational runner's generated input and fail-closed route."""
import unittest
from unittest import mock
from pathlib import Path
import run_domain_npt as domain
import run_domain_npt_contracts as runner
import run_domain_npt_negatives as negatives
import run_domain_npt_pmpi as pmpi


class ContractInputTests(unittest.TestCase):
    def test_segments_have_explicit_transitions_and_output(self):
        text = runner.run_text('npt_ber', 'segments')
        self.assertEqual(text.count('dump_thermo 1'), 4)
        self.assertEqual(text.count('dump_xyz 1 '), 4)
        self.assertEqual(text.count('dump_restart 1'), 4)
        self.assertEqual([int(x.split()[1]) for x in text.splitlines() if x.startswith('run ')], [1, 2, 2, 2])
        self.assertIn('run 1\nensemble nve\n', text)
        self.assertTrue(text.endswith('run 2\n'))
        self.assertIn('velocity force potential virial', text)

    def test_fixed_box_only_changes_pressure_parameter(self):
        normal = runner.run_text('npt_scr', 'single')
        fixed = runner.run_text('npt_scr', 'fixed-box')
        self.assertEqual(normal.replace('0 425 200', '0 2001 200'), fixed)

    def test_late_unwrapped_starts_after_first_segment(self):
        text = runner.run_text('npt_ber', 'late-unwrapped')
        first, rest = text.split('run 1\n', 1)
        self.assertNotIn('unwrapped_position', first)
        self.assertEqual(rest.count('unwrapped_position'), 2)
        self.assertIn('ensemble nve\n', rest)

    def test_missing_output_frames_cannot_pass(self):
        frame = {'natoms': 2}
        with mock.patch.object(runner.baseline, 'parse_thermo', return_value=[{'rows': [[0]] * 7}]), \
             mock.patch.object(runner.baseline, 'parse_xyz', side_effect=[[frame], [frame]]):
            with self.assertRaisesRegex(ValueError, 'expected 7 thermo rows/frames'):
                runner.validate_outputs(Path('/fake'), 7)

    def test_output_validator_rejects_stale_restart_and_bad_time(self):
        props = [('species', 'S', 1), ('pos', 'R', 3), ('vel', 'R', 3),
                 ('forces', 'R', 3), ('energy_atom', 'R', 1), ('virial', 'R', 9)]
        frame = {'natoms': 1, 'fields': {'Time': '0.25', 'Lattice': '1 0 0 0 1 0 0 0 1'},
                 'properties': props, 'rows': [['C'] + ['1'] * 19]}
        restart = {'natoms': 1, 'fields': {'Lattice': '1 0 0 0 1 0 0 0 1'},
                   'properties': props[:3], 'rows': [['C'] + ['1'] * 6]}
        def check(end, saved):
            with mock.patch.object(runner.baseline, 'parse_thermo', return_value=[{'rows': [[0]]}]), \
                 mock.patch.object(runner.baseline, 'parse_xyz', side_effect=[[end], [saved], [{'natoms': 1}]]):
                return runner.validate_outputs(Path('/fake'), 1)
        self.assertTrue(check(frame, restart)['restart_matches_final'])
        stale = dict(restart, rows=[['C'] + ['2'] * 6])
        with self.assertRaisesRegex(ValueError, 'restart pos'): check(frame, stale)
        wrong = dict(frame, fields=dict(frame['fields'], Time='0.5'))
        with self.assertRaisesRegex(ValueError, 'wrong trajectory time'): check(wrong, restart)

    def test_restart_uses_restart_tolerance(self):
        calls = []
        with mock.patch.object(runner.baseline, 'DiffCollector') as collector, \
             mock.patch.object(runner.baseline, 'compare_thermo'), \
             mock.patch.object(runner.baseline, 'parse_xyz', return_value=[]), \
             mock.patch.object(runner.baseline, 'compare_xyz', side_effect=lambda *args: calls.append(args)):
            collector.return_value.stats = {}
            runner.compare(Path('/left'), Path('/right'), {})
        self.assertEqual([call[2]['kind'] for call in calls], ['xyz', 'restart'])

    def test_sparse_fixture_preserves_box_and_two_atoms(self):
        model = '3\nbox header\nC 1 2 3 12 1 0 0\nC 2 3 4 12 0 1 0\nC 3 4 5 12 0 0 1\n'
        self.assertEqual(negatives.sparse_model(model).splitlines(),
                         ['2', 'box header', 'C 1 2 3 12 1 0 0', 'C 2 3 4 12 0 1 0'])

    def test_census_rejects_missing_rank(self):
        line = 'DN4_PMPI rank=0 thermo=8 reason=9 bcast2=5 preflight=2 other_allreduce=3 gather=0 gatherv=0 allgather=0 allgatherv=0'
        self.assertEqual(pmpi.census(line, 1)[0]['thermo'], 8)
        with self.assertRaises(ValueError): pmpi.census(line, 2)
        with self.assertRaises(ValueError): pmpi.census(line + '\n' + line, 1)

    def test_fallback_never_counts_as_local(self):
        fallback = 'DMGMD_DOMAIN mode=m1-fallback\nDMGMD_DOMAIN_NPT mode=rebuild\n'
        with self.assertRaises(ValueError):
            domain.check_route(fallback, 2, 'rebuild')
        with self.assertRaises(ValueError):
            domain.check_route('DMGMD_DOMAIN mode=m2a\n', 2, 'rebuild')
        domain.check_route('DMGMD_DOMAIN mode=m2a\nDMGMD_DOMAIN_NPT mode=rebuild\n', 2, 'rebuild')


if __name__ == '__main__':
    unittest.main()
