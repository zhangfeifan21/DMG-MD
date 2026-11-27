#!/usr/bin/env python3
"""CPU checks for the operational runner's generated input and fail-closed route."""
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace
import run_domain_npt as domain
import run_domain_npt_contracts as runner
import run_domain_npt_negatives as negatives
import run_domain_npt_pmpi as pmpi
import run_domain_npt_restarts as restarts
import run_domain_npt_stop_boundary as stop_boundary


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

    def test_restart_freeze_checks_registered_metadata_and_input_set(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            candidate.write_bytes(b"candidate")
            freeze_root = root / "freeze"
            restarts.freeze(candidate, freeze_root)
            frozen = restarts.load_freeze(candidate, freeze_root)
            freeze_path = freeze_root / "freeze.json"
            changes = {
                "files": {key: digest for key, digest in frozen["files"].items()
                          if key != restarts.MODEL},
                "seed": "9",
                "steps": 5,
                "tolerances": {**frozen["tolerances"], "velocity": {"atol": 1, "rtol": 1}},
            }
            for key, value in changes.items():
                with self.subTest(key=key):
                    restarts.save(freeze_path, {**frozen, key: value})
                    with self.assertRaisesRegex(ValueError, "freeze metadata"):
                        restarts.load_freeze(candidate, freeze_root)

            restarts.save(freeze_path, frozen)
            model = freeze_root / restarts.MODEL
            model.unlink()
            with self.assertRaisesRegex(ValueError, "file set"):
                restarts.load_freeze(candidate, freeze_root)
            model.write_bytes(b"different snapshot")
            with self.assertRaisesRegex(ValueError, "input bytes"):
                restarts.load_freeze(candidate, freeze_root)

    def test_nvt_template_rejects_missing_or_multiple_ensemble_commands(self):
        expected = restarts.run_text("nvt_nhc")
        self.assertIn("\nensemble nvt_nhc 300 300 40\n", expected)
        for template in ("ensemble npt_ber 300 300 40 0 425 201\nrun 4\n",
                         "ensemble npt_ber 300 300 40 0 425 200\nensemble nve\nrun 4\n"):
            with mock.patch.object(runner, "run_text", return_value=template), \
                 self.assertRaisesRegex(ValueError, "one frozen command"):
                restarts.run_text("nvt_nhc")

    def test_restart_reset_requires_each_controller_field_to_exceed_full_tolerance(self):
        def frame(velocity, lattice):
            return {"fields": {"Lattice": f"{lattice} 0 0 0 1 0 0 0 1"},
                    "properties": [("vel", "R", 3)], "rows": [[str(velocity), "0", "0"]]}

        args = SimpleNamespace(freeze=Path("/fixture"),
                               frozen={"tolerances": {category: {"atol": 0.2, "rtol": 0.1}
                                                      for category in ("velocity", "lattice")}})
        checkpoint_prefix = [frame(1.0, 1.0)] * restarts.STEPS
        continuation = checkpoint_prefix + [frame(1.0, 1.0)]

        def check(ensemble, velocity, lattice):
            with mock.patch.object(restarts, "launch"), mock.patch.object(restarts, "save"), \
                 mock.patch.object(restarts.baseline, "parse_xyz",
                                   side_effect=[checkpoint_prefix, continuation, [frame(velocity, lattice)]]):
                return restarts.reset_control(args, Path("/producer"), Path("/fresh"), ensemble,
                                              1, "HostStaged", Path("/uninterrupted"))

        for ensemble in ("nvt_nhc", "nvt_bdp", "npt_scr"):
            with self.subTest(ensemble=ensemble), self.assertRaisesRegex(ValueError, "vel"):
                check(ensemble, 1.25, 1.4)
        with self.assertRaisesRegex(ValueError, "Lattice"):
            check("npt_scr", 1.4, 1.25)
        self.assertEqual(check("nvt_nhc", 1.4, 1.0)["required_fields"], ("vel",))
        self.assertEqual(check("nvt_bdp", 1.4, 1.0)["comparisons"]["vel"]["above_tolerance_count"], 1)
        result = check("npt_scr", 1.4, 1.4)
        for stats in result["comparisons"].values():
            self.assertEqual(stats["above_tolerance_count"], 1)
            self.assertAlmostEqual(stats["first_above_tolerance"]["allowed_abs_diff"], 0.3)

    def test_preflight_ranks_from_mpi_abort_stderr(self):
        error = 'category=input message="domain NPT box preflight failed step=3"'
        log = "\n".join([f"DMGMD_ERROR rank=0 {error}", "MPI_ABORT was invoked",
                         f"\x00DMGMD_ERROR rank=1 {error}", f"DMGMD_ERROR rank=0 {error}",
                         'DMGMD_ERROR rank=2 category=input message="other failure"'])
        self.assertEqual(stop_boundary.preflight_error_ranks(log), [0, 1])

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
