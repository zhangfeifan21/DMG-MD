"""CPU tests for the recorded 60 ps and 110 ps water selections and its inputs."""

from __future__ import annotations

from hashlib import sha256 as hash_bytes
import json
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import unittest

from analyze_preparation import SCHEDULE, THERMO_COLUMNS
from prepare import FIXTURES, input_script, sha256, write_json
from water_selection import (
    DIAGNOSTICS_FILENAME, SECOND_EXTENSION, SELECTION_FILENAME, build_diagnostics,
    check_selection, record_decision,
)


class WaterSelectionTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name) / "water"
        self.diagnostics = self.directory / DIAGNOSTICS_FILENAME
        self.selection = self.directory / SELECTION_FILENAME
        self.write_stage("scr")
        self.frame_sha = self.write_stage("scr-extension")
        write_json(self.diagnostics, build_diagnostics(self.directory))

    def write_stage(self, stage, *, temperature=300.0):
        atoms = 3
        job = self.directory / stage
        job.mkdir(parents=True, exist_ok=True)
        restart = job / "restart.xyz"
        restart.write_text(f"Synthetic {stage} endpoint\n")
        digest = sha256(restart)
        profile = "scr" if stage == "scr" else "scr-extension"
        steps, stride = SCHEDULE[profile][0]
        header = [
            f"# dump_thermo {stride}", "# format_version 1", f"# num_atoms {atoms}",
            f"# dt_output {stride * 0.5:g} fs", f"# columns {THERMO_COLUMNS}",
        ]
        rows = []
        for i in range(steps // stride):
            start_block = {"scr": 0, "scr-extension": 1, SECOND_EXTENSION: 6}[stage]
            block = start_block + i // 200
            jitter = -1 if i % 2 == 0 else 1
            rows.append((temperature + jitter / 10, 0.1, -6 + block * 0.03,
                         0.1, -0.1, 0.0, 0, 0, 0,
                         30 + block / 100 + jitter / 1000, 0, 0, 0, 45, 0, 0, 0, 30))
        thermo = job / "thermo.out"
        thermo.write_text("\n".join(header + [" ".join(map(str, row)) for row in rows]) + "\n")
        fixture = FIXTURES["water"]
        record = {
            "status": "PASS",
            "exit_code": 0,
            "gpumd_binary_sha256": "b" * 64,
            "run_in_sha256": hash_bytes(input_script(fixture, profile, None).encode("ascii")).hexdigest(),
            "potential_sha256": fixture["potential_sha256"],
            "velocity_seed": None,
            "snapshot": {"atoms": atoms, "sha256": digest},
            "thermo_sha256": sha256(thermo),
        }
        if stage != "scr":
            predecessor = "scr" if stage == "scr-extension" else "scr-extension"
            record.update({
                "model_in_sha256": sha256(self.directory / predecessor / "restart.xyz"),
                "resumes_execution_sha256": sha256(self.directory / predecessor / "execution.json"),
            })
        write_json(job / "execution.json", record)
        return digest

    def test_thermo_diagnostics_recover_physical_windows_and_normalize_energy(self):
        result = build_diagnostics(self.directory)
        self.assertEqual(result["endpoint_step"], 120_000)
        self.assertEqual(result["endpoint_time_ps"], 60)
        windows = result["consecutive_10_ps_windows"]
        self.assertEqual(len(windows), 6)
        for i, window in enumerate(windows):
            self.assertEqual(window["sample_count"], 200)
            self.assertEqual(window["first_sample_step"], i * 20_000 + 100)
            self.assertEqual(window["last_sample_step"], (i + 1) * 20_000)
            self.assertAlmostEqual(window["metrics"]["potential_energy_eV_per_atom"]["mean"],
                                   -2 + i * 0.01)
            self.assertAlmostEqual(window["metrics"]["volume_A3"]["mean"], (30 + i / 100) * 1350)
            self.assertAlmostEqual(window["metrics"]["temperature_K"]["mean"], 300)
            self.assertAlmostEqual(window["metrics"]["pressure_GPa"]["mean"], 0)
        halves = result["continuation_halves"]
        self.assertEqual(halves["first"]["last_sample_step"], 70_000)
        self.assertEqual(halves["second"]["first_sample_step"], 70_100)
        self.assertEqual([half["sample_count"] for half in halves.values()], [500, 500])
        deltas = result["mean_differences"]
        self.assertAlmostEqual(deltas["second_half_minus_first_half"]["potential_energy_eV_per_atom"],
                               0.024)
        self.assertAlmostEqual(deltas["last_window_minus_original_scr"]["volume_A3"], 67.5)

    def test_second_extension_keeps_first_six_windows_and_uses_its_own_halves(self):
        original = build_diagnostics(self.directory)
        self.write_stage(SECOND_EXTENSION)
        result = build_diagnostics(self.directory)
        self.assertEqual(result["endpoint_step"], 220_000)
        self.assertEqual(result["endpoint_time_ps"], 110)
        windows = result["consecutive_10_ps_windows"]
        self.assertEqual(len(windows), 11)
        self.assertEqual(windows[:6], original["consecutive_10_ps_windows"])
        for i, window in enumerate(windows[6:], start=6):
            self.assertEqual(window["first_sample_step"], i * 20_000 + 100)
            self.assertEqual(window["last_sample_step"], (i + 1) * 20_000)
            self.assertEqual(window["sample_count"], 200)
            self.assertAlmostEqual(window["metrics"]["potential_energy_eV_per_atom"]["mean"],
                                   -2 + i * 0.01)
        halves = result["continuation_halves"]
        self.assertEqual([half["sample_count"] for half in halves.values()], [500, 500])
        self.assertEqual(halves["first"]["start_ps_exclusive"], 60)
        self.assertEqual(halves["first"]["last_sample_step"], 170_000)
        self.assertEqual(halves["second"]["first_sample_step"], 170_100)
        self.assertEqual(halves["second"]["end_ps_inclusive"], 110)
        self.assertEqual(set(result["sources"]), {"scr", "scr-extension", SECOND_EXTENSION})
        self.assertEqual(result["sources"]["scr-extension"], original["sources"]["scr-extension"])

    def test_second_extension_requires_both_predecessor_hashes(self):
        self.write_stage(SECOND_EXTENSION)
        execution = self.directory / SECOND_EXTENSION / "execution.json"
        valid = json.loads(execution.read_text())
        for field in ("model_in_sha256", "resumes_execution_sha256"):
            with self.subTest(field=field):
                write_json(execution, {**valid, field: "d" * 64})
                with self.assertRaisesRegex(ValueError, "does not resume the original scr-extension"):
                    build_diagnostics(self.directory)

    def test_second_extension_selects_actual_110_ps_frame_and_packages_evidence(self):
        frame_sha = self.write_stage(SECOND_EXTENSION)
        write_json(self.diagnostics, build_diagnostics(self.directory))
        restart = self.directory / SECOND_EXTENSION / "restart.xyz"
        restart.write_text("Unregistered frame")
        with self.assertRaisesRegex(ValueError, "registered 110 ps endpoint"):
            record_decision(self.directory, "select", "Reviewed.")
        restart.write_text(f"Synthetic {SECOND_EXTENSION} endpoint\n")
        recorded = record_decision(self.directory, "select", "Reviewed all eleven windows.")
        self.assertEqual(recorded["frame_sha256"], frame_sha)
        self.assertEqual(recorded["selected_time_ps"], 110)
        packaged = self.directory.parent / "package"
        copytree(self.directory, packaged, ignore=lambda _, names: {"restart.xyz"}.intersection(names))
        arguments = (packaged / SELECTION_FILENAME, packaged / DIAGNOSTICS_FILENAME)
        self.assertEqual(check_selection(*arguments, frame_sha), recorded)
        thermo = packaged / SECOND_EXTENSION / "thermo.out"
        thermo.write_bytes(thermo.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, f"{SECOND_EXTENSION} thermo differs"):
            check_selection(*arguments, frame_sha)

    def test_60_ps_selection_becomes_stale_when_second_extension_exists(self):
        prior = record_decision(self.directory, "select", "Reviewed the first extension.")
        frame_sha = self.write_stage(SECOND_EXTENSION)
        with self.assertRaisesRegex(ValueError, "differ from recomputation"):
            check_selection(self.selection, self.diagnostics, self.frame_sha)
        write_json(self.diagnostics, build_diagnostics(self.directory))
        forged = {**prior, "frame_sha256": frame_sha, "diagnostics_sha256": sha256(self.diagnostics)}
        write_json(self.selection, forged)
        with self.assertRaisesRegex(ValueError, "does not identify the registered endpoint time"):
            check_selection(self.selection, self.diagnostics, frame_sha)

    def test_broken_second_extension_link_does_not_return_60_ps(self):
        (self.directory / SECOND_EXTENSION).symlink_to("missing-stage", target_is_directory=True)
        with self.assertRaises(FileNotFoundError):
            build_diagnostics(self.directory)

    def test_verified_selection_works_in_portable_evidence_directory(self):
        recorded = record_decision(self.directory, "select", "Reviewed all six windows.")
        packaged = self.directory.parent / "package"
        copytree(self.directory, packaged, ignore=lambda _, names: {"restart.xyz"}.intersection(names))
        self.assertEqual(
            check_selection(packaged / SELECTION_FILENAME, packaged / DIAGNOSTICS_FILENAME,
                            self.frame_sha),
            recorded,
        )
        with self.assertRaises(FileExistsError):
            record_decision(self.directory, "select", "A different assessment.")

    def test_reject_and_missing_reason_cannot_establish_a_selection(self):
        with self.assertRaisesRegex(ValueError, "with a reason"):
            record_decision(self.directory, "select", " ")
        record_decision(self.directory, "reject", "Review declined this endpoint.")
        with self.assertRaisesRegex(ValueError, "no affirmative selection"):
            check_selection(self.selection, self.diagnostics, self.frame_sha)

    def test_changed_restart_cannot_be_recorded_or_substituted(self):
        record_decision(self.directory, "select", "Reviewed.")
        restart = self.directory / "scr-extension/restart.xyz"
        restart.write_text("Unregistered frame")
        with self.assertRaisesRegex(ValueError, "registered 60 ps endpoint"):
            record_decision(self.directory, "select", "Reviewed.")
        with self.assertRaisesRegex(ValueError, "does not match the selection"):
            check_selection(self.selection, self.diagnostics, sha256(restart))

    def test_thermo_bytes_are_bound_for_both_stages(self):
        record_decision(self.directory, "select", "Reviewed.")
        for stage in ("scr", "scr-extension"):
            with self.subTest(stage=stage):
                thermo = self.directory / stage / "thermo.out"
                original = thermo.read_bytes()
                thermo.write_bytes(original + b"\n")
                with self.assertRaisesRegex(ValueError, f"{stage} thermo differs"):
                    check_selection(self.selection, self.diagnostics, self.frame_sha)
                thermo.write_bytes(original)

    def test_execution_change_and_predecessor_mismatch_are_rejected(self):
        record_decision(self.directory, "select", "Reviewed.")
        execution = self.directory / "scr-extension/execution.json"
        original = json.loads(execution.read_text())
        write_json(execution, {**original, "note": "Unregistered execution edit"})
        with self.assertRaisesRegex(ValueError, "differ from recomputation"):
            check_selection(self.selection, self.diagnostics, self.frame_sha)
        write_json(execution, {**original, "resumes_execution_sha256": "c" * 64})
        with self.assertRaisesRegex(ValueError, "does not resume the original"):
            check_selection(self.selection, self.diagnostics, self.frame_sha)

    def test_diagnostics_are_recomputed_even_if_saved_hash_is_changed(self):
        choice = record_decision(self.directory, "select", "Reviewed.")
        altered = json.loads(self.diagnostics.read_text())
        altered["consecutive_10_ps_windows"][-1]["metrics"]["volume_A3"]["mean"] += 1
        write_json(self.diagnostics, altered)
        write_json(self.selection, {**choice, "diagnostics_sha256": sha256(self.diagnostics)})
        with self.assertRaisesRegex(ValueError, "differ from recomputation"):
            check_selection(self.selection, self.diagnostics, self.frame_sha)

    def test_existing_temperature_check_still_applies_to_affirmative_decision(self):
        self.write_stage("scr-extension", temperature=320)
        write_json(self.diagnostics, build_diagnostics(self.directory))
        with self.assertRaisesRegex(ValueError, "registered preparation check failed"):
            record_decision(self.directory, "select", "Reviewed.")
        choice = record_decision(self.directory, "reject", "The temperature criterion failed.")
        self.assertEqual(choice["decision"], "reject")


if __name__ == "__main__":
    unittest.main()
