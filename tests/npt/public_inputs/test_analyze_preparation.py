"""Behavior tests for the public-input preparation command-line tools."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
COLUMNS = "T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz"


def run_command(script, *args):
    return subprocess.run([sys.executable, "-B", str(HERE / script), *map(str, args)],
                          capture_output=True, text=True, check=False)


def write_thermo(path, intervals, volume_at, temperature=300, pressure=0, dt_fs=1):
    start = 0
    with path.open("w") as stream:
        for steps, stride in intervals:
            stream.write(
                f"# dump_thermo {stride}\n# format_version 1\n# num_atoms 216\n"
                f"# dt_output {dt_fs * stride:.10e} fs\n# columns {COLUMNS}\n"
            )
            for step in range(start + stride, start + steps + 1, stride):
                values = (
                    temperature, 1, -1, pressure + 1, pressure, pressure - 1, 0, 0, 0,
                    volume_at(step), 0, 0, 0, 1, 0, 0, 0, 1,
                )
                stream.write(" ".join(map(str, values)) + "\n")
            start += steps


class PreparationCLI(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="test-public-preparation-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.thermo = self.root / "thermo.out"
        self.report = self.root / "report"

    def run_stage(self, stage, dt_fs=1):
        return run_command(
            "analyze_preparation.py", stage, "--thermo", self.thermo,
            "--time-step-fs", dt_fs, "--output-dir", self.report,
        )

    def summary(self):
        return json.loads((self.report / "summary.json").read_text())

    def test_berendsen_uses_registered_points_and_writes_time_series(self):
        def box_volume(step):
            if step > 95000 and step % 1000:
                return 1000.2
            return 1000

        write_thermo(self.thermo, ((90000, 1000), (10000, 100)), box_volume, dt_fs=0.5)
        run = self.run_stage("berendsen", 0.5)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = self.summary()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["observed_samples"], 190)
        self.assertEqual(report["statistics_samples"], 100)
        self.assertEqual(report["mean_volume_A3"], 1000)
        self.assertEqual(report["sample_relative_sd_volume"], 0)
        terminal = report["final_5000_steps"]
        self.assertEqual(terminal["samples"], 50)
        self.assertEqual(terminal["first_step"], 95100)
        self.assertAlmostEqual(terminal["mean_volume_A3"], 1000.18)

        with (self.report / "thermo.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([int(row["step"]) for row in rows[89:91]], [90000, 90100])
        self.assertEqual(float(rows[-1]["time_ps"]), 50.0)
        self.assertEqual(len(ET.parse(self.report / "thermo.svg").findall(
            ".//{http://www.w3.org/2000/svg}polyline")), 3)

    def test_berendsen_rejects_drift_even_with_small_relative_sd(self):
        write_thermo(
            self.thermo, ((90000, 1000), (10000, 100)),
            lambda step: 1000 + max(0, step - 95000) * 0.001,
        )
        run = self.run_stage("berendsen")
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertEqual(self.summary()["checks"], {
            "relative_volume_sd_lt_0.01": True,
            "relative_terminal_drift_le_0.0025": False,
        })

    def test_scr_uncorrelated_volume_and_registered_targets_pass(self):
        write_thermo(
            self.thermo, ((20000, 100),),
            lambda step: 1000 + step // 100 % 2,
            temperature=299, pressure=0.1,
        )
        run = self.run_stage("scr")
        self.assertEqual(run.returncode, 0, run.stderr)
        result = self.summary()
        self.assertAlmostEqual(result["mean_pressure_GPa"], 0.1)
        self.assertEqual(result["effective_samples"]["volume_A3"]["effective_samples"], 200)

    def test_scr_correlated_volume_reports_ess_without_gating_preparation(self):
        write_thermo(
            self.thermo, ((20000, 100),),
            lambda step: 1000 + math.sin(step / 2500),
        )
        run = self.run_stage("scr")
        self.assertEqual(run.returncode, 0, run.stderr)
        result = self.summary()
        self.assertEqual(result["checks"], {
            "relative_temperature_offset_lt_0.03": True,
            "absolute_pressure_lt_0.5_GPa": True,
            "relative_volume_sd_lt_0.02": True,
        })
        self.assertGreater(result["effective_samples"]["volume_A3"]["effective_samples"], 0)
        self.assertLess(result["effective_samples"]["volume_A3"]["effective_samples"], 100)

    def test_scr_constant_trace_records_zero_ess(self):
        write_thermo(self.thermo, ((20000, 100),), lambda _: 1000)
        run = self.run_stage("scr")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.summary()["effective_samples"]["volume_A3"], {
            "tau_samples": None,
            "effective_samples": 0.0,
        })

    def test_scr_extension_checks_registered_50_ps_schedule_and_same_physical_bounds(self):
        write_thermo(
            self.thermo, ((100000, 100),),
            lambda step: 1000 + 100 * (step // 100 % 2),
            temperature=300, pressure=0, dt_fs=0.5,
        )
        run = self.run_stage("scr-extension", 0.5)
        self.assertEqual(run.returncode, 1, run.stderr)
        result = self.summary()
        self.assertEqual(result["stage"], "scr-extension")
        self.assertEqual(result["observed_samples"], 1000)
        self.assertEqual(result["schedule_steps_stride"], [[100000, 100]])
        self.assertEqual(result["checks"], {
            "relative_temperature_offset_lt_0.03": True,
            "absolute_pressure_lt_0.5_GPa": True,
            "relative_volume_sd_lt_0.02": False,
        })
        with (self.report / "thermo.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(float(rows[-1]["time_ps"]), 50.0)

    def test_scr_target_limits_are_strict(self):
        write_thermo(
            self.thermo, ((20000, 100),),
            lambda step: 1000 + step // 100 % 2,
            temperature=309, pressure=0.5,
        )
        run = self.run_stage("scr")
        self.assertEqual(run.returncode, 1, run.stderr)
        checks = self.summary()["checks"]
        self.assertFalse(checks["relative_temperature_offset_lt_0.03"])
        self.assertFalse(checks["absolute_pressure_lt_0.5_GPa"])

    def test_repeated_identical_analysis_reuses_complete_evidence(self):
        write_thermo(self.thermo, ((20000, 100),), lambda step: 1000 + step // 100 % 2)
        first = self.run_stage("scr")
        self.assertEqual(first.returncode, 0, first.stderr)
        timestamps = {path: path.stat().st_mtime_ns for path in self.report.iterdir()}

        second = self.run_stage("scr")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(
            {path: path.stat().st_mtime_ns for path in self.report.iterdir()}, timestamps,
        )

    def test_distinct_registered_checks_preserve_old_evidence(self):
        write_thermo(self.thermo, ((20000, 100),), lambda step: 1000 + math.sin(step / 2500))
        first = self.run_stage("scr")
        self.assertEqual(first.returncode, 0, first.stderr)
        historical_report = self.summary()
        historical_report["checks"]["volume_effective_samples_gt_100"] = False
        historical_report["status"] = "FAIL"
        (self.report / "summary.json").write_text(json.dumps(historical_report))
        original = self.report
        original_evidence = {path: path.read_bytes() for path in original.iterdir()}

        redo = self.run_stage("scr")
        self.assertEqual(redo.returncode, 2)
        self.assertIn("--output-dir", redo.stderr)
        self.assertIn("report-recheck", redo.stderr)
        self.assertEqual(
            {path: path.read_bytes() for path in original.iterdir()}, original_evidence,
        )

        self.report = self.root / "analysis-ess-diagnostic"
        redo = self.run_stage("scr")
        self.assertEqual(redo.returncode, 0, redo.stderr)
        self.assertEqual(self.summary()["status"], "PASS")

    def test_partial_evidence_is_preserved(self):
        write_thermo(self.thermo, ((20000, 100),), lambda step: 1000 + step // 100 % 2)
        self.report.mkdir()
        (self.report / "thermo.svg").write_text("prior curves")
        run = self.run_stage("scr")
        self.assertEqual(run.returncode, 2)
        self.assertIn("existing or incomplete analysis", run.stderr)
        self.assertEqual((self.report / "thermo.svg").read_text(), "prior curves")
        self.assertEqual(list(self.report.iterdir()), [self.report / "thermo.svg"])

    def test_incomplete_thermo_returns_error_without_publishing_report(self):
        write_thermo(self.thermo, ((19900, 100),), lambda step: 1000)
        run = self.run_stage("scr")
        self.assertEqual(run.returncode, 2)
        self.assertIn("step 20000", run.stderr)
        self.assertFalse(self.report.exists())

    def write_pressure_job(self, direction, pressure):
        directory = self.root / direction
        directory.mkdir()
        scale = 1.001 if direction == "expand" else 0.999
        model = directory / "model.xyz"
        restart = directory / "restart.xyz"
        box_format = '216\npbc="T T T" Lattice="{} 0 0 0 1 0 0 0 1"\n'
        # Use isotropic axis lengths, all multiplied by the known scaling factor.
        model.write_text(box_format.format(1))
        restart.write_text(
            f'216\npbc="T T T" Lattice="{scale} 0 0 0 {scale} 0 0 0 {scale}"\n'
        )
        (directory / "run.in").write_text("run 1\n")
        (directory / "nep.txt").write_text("potential\n")
        write_thermo(directory / "thermo.out", ((1, 1),), lambda _: scale ** 3, pressure=pressure)
        identity = {
            "expected_direction": direction,
            "atoms": 216,
            "bulk_modulus_GPa": 1,
            "time_step_fs": 1,
            "target_pressure_GPa": 0,
            "pressure_damping_steps": 1000,
        }
        for asset, key in (("model.xyz", "model_sha256"), ("run.in", "run_input_sha256"),
                           ("nep.txt", "potential_sha256")):
            identity[key] = hashlib.sha256((directory / asset).read_bytes()).hexdigest()
        (directory / "input.json").write_text(json.dumps(identity))
        (directory / "execution.json").write_text('{"exit_code": 0}')

    def test_direction_check_uses_measured_pressure_and_rejects_wrong_sign(self):
        self.write_pressure_job("expand", -3)
        self.write_pressure_job("contract", -3)
        run = run_command("analyze_pressure_direction.py", "check", "--output-dir", self.root)
        self.assertEqual(run.returncode, 1, run.stderr)
        result = json.loads((self.root / "pressure-direction.json").read_text())
        self.assertEqual(result["cases"]["contract"]["status"], "PASS")
        expansion = result["cases"]["expand"]
        self.assertTrue(expansion["checks"]["volume_has_expected_change"])
        self.assertFalse(expansion["checks"]["pressure_has_expected_sign"])
        self.assertFalse(expansion["checks"]["restart_axes_match_pressure_scaling"])


if __name__ == "__main__":
    unittest.main()
