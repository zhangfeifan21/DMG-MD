#!/usr/bin/env python3
"""Offline checks for locked NPT fixture and preregistration contracts."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "manifest.json").read_text())
SPEC = importlib.util.spec_from_file_location("npt_inputs", HERE / "generate_inputs.py")
assert SPEC and SPEC.loader
INPUTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INPUTS)


class NptManifestTests(unittest.TestCase):
    def test_216_seed_models_match_registered_hashes(self) -> None:
        fixture = MANIFEST["fixtures"]["statistics"]
        for seed in fixture["seeds"]:
            actual = INPUTS.sha256_bytes(INPUTS.model_text((3, 3, 3), seed).encode())
            self.assertEqual(actual, fixture["model_xyz_sha256_by_seed"][str(seed)])

    def test_large_and_m2a_models_match_registered_hashes(self) -> None:
        large = INPUTS.sha256_bytes(INPUTS.model_text((7, 6, 6), 101).encode())
        self.assertEqual(large, MANIFEST["fixtures"]["ber_golden_large"]["model_xyz_sha256_by_seed"]["101"])
        domain = INPUTS.sha256_bytes(
            INPUTS.model_text((18, 6, 6), 101, scale=1.0,
                              exact_lengths=(64.26, 21.42, 21.42)).encode()
        )
        self.assertEqual(domain, MANIFEST["fixtures"]["m2a_eligible"]["model_xyz_sha256_by_seed"]["101"])

    def test_pressure_direction_models_match_registered_hashes(self) -> None:
        reference_scale = 1.0023
        cases = {"compressed": reference_scale * 0.995, "expanded": reference_scale * 1.005}
        for key, scale in cases.items():
            actual = INPUTS.sha256_bytes(INPUTS.model_text((3, 3, 3), 101, scale).encode())
            self.assertEqual(actual, MANIFEST["fixtures"][key]["model_xyz_sha256_by_seed"]["101"])

    def test_negative_models_match_registered_hashes(self) -> None:
        base = INPUTS.model_text((3, 3, 3), 101)
        import re
        variants = {
            "negative-nonperiodic": base.replace('pbc="T T T"', 'pbc="T T F"', 1),
            "negative-triclinic": re.sub(r'Lattice="[^"]+"',
                'Lattice="10.736433 0 0 0.1 10.736433 0 0 0 10.736433"', base, count=1),
            "zero-ke": INPUTS.zero_ke_model(),
        }
        for name, model in variants.items():
            actual = INPUTS.sha256_bytes(model.encode())
            self.assertEqual(actual, MANIFEST["fixtures"]["negative_models"][name]["model_xyz_sha256"])

    def test_scr_manifest_has_no_per_step_xyz(self) -> None:
        config = MANIFEST["b3_scr_statistics"]["configuration"]
        self.assertEqual(config["thermo_interval_steps"], 1)
        self.assertIsNone(config["trajectory_interval_steps"])
        self.assertEqual(MANIFEST["fixtures"]["statistics"]["run_input"]["dump_xyz"], False)
        self.assertEqual(config["dt_fs"], 0.25)
        self.assertEqual(config["Tdamp_steps"], 40)
        self.assertEqual(config["tau_p_steps"], 200)
        half = MANIFEST["b3_scr_statistics"]["half_dt"]
        self.assertEqual((half["dt_fs"], half["Tdamp_steps"], half["tau_p_steps"]), (0.125, 80, 400))
        self.assertEqual(MANIFEST["b3_scr_statistics"]["candidate_conditions_base_dt"], [
            {"ranks": 1, "backend": "HostStaged"},
            {"ranks": 2, "backend": "HostStaged"},
            {"ranks": 2, "backend": "CudaAware"},
            {"ranks": 4, "backend": "HostStaged"},
            {"ranks": 4, "backend": "CudaAware"},
        ])
        self.assertEqual(MANIFEST["b3_scr_statistics"]["candidate_conditions_half_dt"],
                         [{"ranks": 1, "backend": "HostStaged"}])
        self.assertIn("system_clock", MANIFEST["b3_scr_statistics"]["reference_rng_source"])

    def test_scr_run_input_contract(self) -> None:
        profiles = ((20000, .25, 40, 200), (40000, .25, 40, 200),
                    (80000, .25, 40, 200), (40000, .125, 80, 400),
                    (80000, .125, 80, 400), (160000, .125, 80, 400))
        for steps, dt, tdamp, tau in profiles:
            text = INPUTS.scr_run(steps, dt, tdamp, tau)
            self.assertEqual(text, ("potential nep.txt\n"
                                    f"time_step {dt}\n"
                                    f"ensemble npt_scr 300 300 {tdamp} 0 425 {tau}\n"
                                    "dump_thermo 1\n"
                                    f"run {steps}\n"))
            self.assertNotIn("dump_xyz", text)
            self.assertNotIn("dump_restart", text)

    def test_run_input_hashes_and_tokens(self) -> None:
        locked = MANIFEST["input_locking"]["run_in_sha256"]
        expected = {
            "ber-golden-200": INPUTS.ber_run(200, trajectory_interval=20, restart_interval=200),
            "ber-pressure-track-20": INPUTS.ber_run(20, trajectory_interval=1, restart_interval=20),
            "ber-route-20-target5": INPUTS.ber_run(20, target_pressure_gpa=5.0,
                                                    trajectory_interval=5, restart_interval=20),
            "ber-negative-pressure-20-target-minus1": INPUTS.ber_run(
                20, target_pressure_gpa=-1.0, trajectory_interval=5),
            "ber-fixed-box-20-B2001": INPUTS.ber_run(
                20, target_pressure_gpa=-1.0, bulk_modulus_gpa=2001.0,
                trajectory_interval=5),
            "ber-restart-resume-3": INPUTS.ber_run(3, target_pressure_gpa=5.0,
                                                    trajectory_interval=1, restart_interval=3),
            "ber-unsafe-scale-1": INPUTS.ber_run(1, target_pressure_gpa=10000.0,
                                                  bulk_modulus_gpa=1.0, tau_p_steps=1),
            "ber-invalid-t-nan": INPUTS.ber_run(1, ensemble_line="ensemble npt_ber nan 300 40 0 425 200"),
            "ber-invalid-b-zero": INPUTS.ber_run(1, ensemble_line="ensemble npt_ber 300 300 40 0 0 200"),
            "ber-invalid-tau-zero": INPUTS.ber_run(1, ensemble_line="ensemble npt_ber 300 300 40 0 425 0"),
            "ber-invalid-token-12": INPUTS.ber_run(1, ensemble_line="ensemble npt_ber 300 300 40 0 425 200 0 0 0 200"),
            "ber-invalid-token-18": INPUTS.ber_run(1, ensemble_line="ensemble npt_ber 300 300 40 0 425 200 0 0 0 200 0 0 0 200 0 0"),
            "ber-zero-ke-1": INPUTS.ber_run(1),
        }
        self.assertEqual(set(locked), set(expected))
        for name, text in expected.items():
            self.assertEqual(INPUTS.sha256_bytes(text.encode()), locked[name], name)
        self.assertEqual(len(MANIFEST["b2_berendsen"]["run_line"].split()), 8)

    def test_reference_acceptance_uses_frozen_baseline_tolerances(self) -> None:
        self.assertEqual(MANIFEST["input_locking"]["baseline_tolerances_source"],
                         "tests/baseline/manifest.json; immutable for this comparison")
        self.assertTrue(MANIFEST["sampling_authorized"])


if __name__ == "__main__":
    unittest.main()
