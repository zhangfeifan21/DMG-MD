"""CPU-only unit tests for the non-formal SCR migration prefix diagnostic."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "dmgmd_migration_numerics", HERE / "check_migration_numerics.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load migration diagnostic module")
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class MigrationNumericsTests(unittest.TestCase):
    def test_profile_controls_ignore_only_the_budget_for_normalized_text(self) -> None:
        base = "potential nep.txt\ntime_step 0.25\nensemble npt_scr 300 300 40 0 425 200\ndump_thermo 1\nrun 80000\n"
        half = base.replace("0.25", "0.125").replace("40 0 425 200", "80 0 425 400").replace("80000", "160000")
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.in"
            half_path = Path(directory) / "half.in"
            base_path.write_text(base)
            half_path.write_text(half)
            base_controls = migration._read_run_controls(base_path)
            half_controls = migration._read_run_controls(half_path)
        self.assertEqual(base_controls["profile"], "base")
        self.assertEqual(half_controls["profile"], "half")
        self.assertNotEqual(base_controls["normalized_text"], half_controls["normalized_text"])
        self.assertEqual(base_controls["steps"], 80000)
        self.assertEqual(half_controls["steps"], 160000)

    def test_budget_only_edit_normalizes_to_identical_run_contract(self) -> None:
        original = "potential nep.txt\ntime_step 0.125\nensemble npt_scr 300 300 80 0 425 400\ndump_thermo 1\nrun 160000\n"
        short = original.replace("run 160000", "run 200")
        with tempfile.TemporaryDirectory() as directory:
            left = Path(directory) / "old.in"
            right = Path(directory) / "new.in"
            left.write_text(original)
            right.write_text(short)
            self.assertEqual(migration._normalized_run_text(left),
                             migration._normalized_run_text(right))

    def test_all_18_columns_use_existing_baseline_categories_and_report_derived_pv(self) -> None:
        tolerances = {
            "temperature": {"rtol": 0.0, "atol": 2e-5},
            "energy": {"rtol": 0.0, "atol": 1e-8},
            "thermo_stress": {"rtol": 0.0, "atol": 1e-5},
            "lattice": {"rtol": 0.0, "atol": 1e-12},
        }
        row = [300.0, 1.0, -2.0, 0.1, 0.2, 0.3, 0.01, 0.02, 0.03,
               10.0, 0.0, 0.0, 0.0, 11.0, 0.0, 0.0, 0.0, 12.0]
        actual = row.copy()
        actual[0] += 1e-5
        actual[3] += 5e-6
        actual[9] += 2e-13
        result = migration._compare_rows([row], [actual], tolerances, 1)
        self.assertEqual(len(result["columns"]), 18)
        self.assertEqual(result["columns"][0]["category"], "temperature")
        self.assertEqual(result["columns"][1]["category"], "energy")
        self.assertEqual(result["columns"][3]["category"], "thermo_stress")
        self.assertEqual(result["columns"][9]["category"], "lattice")
        self.assertTrue(result["within_existing_manifest_tolerances"])
        self.assertAlmostEqual(result["derived"]["pressure_GPa"]["max_abs_diff"], 5e-6 / 3.0)
        self.assertAlmostEqual(result["derived"]["volume_A3"]["max_abs_diff"], 2e-13 * 11.0 * 12.0)
        self.assertEqual(result["derived"]["volume_A3"]["gate"],
                         "descriptive only; no new tolerance")

    def test_tolerance_exceedance_is_reported_per_column_without_changing_threshold(self) -> None:
        tolerances = {"temperature": {"rtol": 0.0, "atol": 1e-4},
                      "energy": {"rtol": 0.0, "atol": 1e-4},
                      "thermo_stress": {"rtol": 0.0, "atol": 1e-4},
                      "lattice": {"rtol": 0.0, "atol": 1e-4}}
        row = [1.0] * 18
        actual = row.copy()
        actual[12] += 1e-3
        result = migration._compare_rows([row], [actual], tolerances, 1)
        self.assertFalse(result["within_existing_manifest_tolerances"])
        self.assertEqual(result["columns"][12]["exceeded_tolerance"], 1)
        self.assertEqual(result["columns"][12]["first_exceedance"]["row"], 1)
        self.assertTrue(all(item["exceeded_tolerance"] == 0
                            for index, item in enumerate(result["columns"]) if index != 12))

    def test_numeric_thermo_parser_rejects_nonfinite_or_wrong_width_numeric_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "thermo.out"
            path.write_text("header text\n" + " ".join(["1"] * 17) + "\n")
            with self.assertRaisesRegex(ValueError, "17 columns"):
                migration.numeric_thermo_rows(path)
            path.write_text(" ".join(["1"] * 17 + ["nan"]) + "\n")
            with self.assertRaisesRegex(ValueError, "non-finite"):
                migration.numeric_thermo_rows(path)

    def test_thermo_prefix_requires_registered_row_count(self) -> None:
        row = [0.0] * 18
        with self.assertRaisesRegex(ValueError, "at least 2"):
            migration._compare_rows([row], [row], {
                "temperature": {"rtol": 0, "atol": 0},
                "energy": {"rtol": 0, "atol": 0},
                "thermo_stress": {"rtol": 0, "atol": 0},
                "lattice": {"rtol": 0, "atol": 0}}, 2)


if __name__ == "__main__":
    unittest.main()
