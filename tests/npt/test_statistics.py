#!/usr/bin/env python3
"""CPU tests for the registered NPT statistics analyzer."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tests/thermostats"))
import generate_inputs
import analyze_statistics as stats

MANIFEST = json.loads((HERE / "manifest.json").read_text())


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _thermo(path: Path, steps: int, dt: float, atoms: int = 216, offset: float = 0.0) -> None:
    with path.open("w") as out:
        out.write("# dump_thermo 1\n# format_version 1\n")
        out.write(f"# num_atoms {atoms}\n# dt_output {dt:.10e} fs\n")
        out.write("# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz\n")
        for i in range(steps):
            t = 300.0 + offset + 0.2 * math.sin(i * 0.17)
            k = 1.5 * atoms * 8.617343e-5 * t
            p = 0.01 * math.cos(i * 0.11)
            volume = 1300.0 + 0.4 * math.sin(i * 0.07)
            length = volume ** (1 / 3)
            row = [t, k, -100.0, p, p, p, 0.0, 0.0, 0.0,
                   length, 0.0, 0.0, 0.0, length, 0.0, 0.0, 0.0, length]
            out.write(" ".join(f"{x:.12g}" for x in row) + "\n")


class StatisticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_bootstrap = stats.BOOTSTRAP_REPLICATES
        stats.BOOTSTRAP_REPLICATES = 250

    def tearDown(self) -> None:
        stats.BOOTSTRAP_REPLICATES = self.old_bootstrap

    @staticmethod
    def _ar1(seed: int, length: int, phi: float) -> list[float]:
        rng = random.Random(seed)
        values, state = [], 0.0
        innovation = math.sqrt(1.0 - phi * phi)
        for _ in range(length):
            state = phi * state + innovation * rng.gauss(0.0, 1.0)
            values.append(state)
        return values

    def test_same_and_different_variance_ratio_coverage(self) -> None:
        # Same stationary marginal variance, deliberately different IATs.
        reference = {seed: self._ar1(seed, 5000, 0.80) for seed in range(8)}
        candidate = {seed: self._ar1(seed + 100, 5000, 0.20) for seed in range(8)}
        result = stats._variance_ratio_ci(candidate, reference, 100, 25.0, 2.394)
        self.assertLess(abs(result["estimate"] - 1.0), 0.12)
        self.assertLessEqual(result["confidence_interval"][0], 1.0)
        self.assertGreaterEqual(result["confidence_interval"][1], 1.0)
        self.assertEqual(result["bootstrap_replicates"], 250)
        changed = {seed: [1.4 * x for x in values] for seed, values in candidate.items()}
        shifted = stats._variance_ratio_ci(changed, reference, 100, 25.0, 2.394)
        self.assertGreater(shifted["estimate"], 1.15)
        self.assertEqual(stats._decision(shifted["confidence_interval"], .85, 1.15, True), "FAIL")

    def test_low_ess_and_inconclusive_precision(self) -> None:
        chains = [self._ar1(seed, 5000, 0.98) for seed in range(8)]
        summary = stats._mean_ci(chains, 100, 2.394)
        self.assertLess(summary["effective_samples_mean"], 1000)
        self.assertEqual(stats._decision([-.1, .1], -.2, .2, False), "INCONCLUSIVE")
        self.assertEqual(stats._decision([-.3, -.25], -.2, .2, True), "FAIL")

    def test_ambiguous_40000_look_uses_explicit_timestep_profile(self) -> None:
        protocol = MANIFEST["b3_scr_statistics"]
        base_half, base = stats._profile_for_dt(protocol, 0.25)
        half_half, half = stats._profile_for_dt(protocol, 0.125)
        self.assertFalse(base_half)
        self.assertTrue(half_half)
        self.assertIn(40000, protocol["budget_steps_per_seed"])
        self.assertIn(40000, half["budget_steps_per_seed"])
        self.assertEqual(base["dt_fs"], 0.25)
        self.assertEqual(half["dt_fs"], 0.125)

    def _small_dataset(self, root: Path, dt_fs: float, look: int = 40) -> tuple[dict, dict]:
        manifest = copy.deepcopy(MANIFEST)
        protocol = manifest["b3_scr_statistics"]
        manifest["b3_scr_statistics"]["seeds"] = [101, 102]
        manifest["b3_scr_statistics"]["minimum_complete_blocks_per_seed"] = 1
        manifest["b3_scr_statistics"]["minimum_pooled_effective_samples_per_engine_and_metric"] = 1
        manifest["fixtures"]["statistics"]["atoms"] = 216
        model_hashes = manifest["fixtures"]["statistics"]["model_xyz_sha256_by_seed"]
        selected = {str(seed): model_hashes[str(seed)] for seed in (101, 102)}
        manifest["fixtures"]["statistics"]["model_xyz_sha256_by_seed"] = selected
        if dt_fs == protocol["configuration"]["dt_fs"]:
            profile = protocol["configuration"]
            protocol["budget_steps_per_seed"] = [look]
            protocol["block_length_steps"]["dt_0.25_fs"] = 1
        else:
            profile = protocol["half_dt"]
            profile["budget_steps_per_seed"] = [look]
            protocol["block_length_steps"]["dt_0.125_fs"] = 1
        profile["dt_fs"] = dt_fs
        records = {
            "schema": "dmgmd.npt-scr-statistics-input.v1",
            "look_steps": look,
            "dt_fs": dt_fs,
            "units": dict(stats.UNITS),
            "ranks": 1,
            "backend": "HostStaged",
            "runs": [],
        }
        pot = ROOT / manifest["source_contract"]["potential_path"]
        potential_hash = _hash(pot)
        for engine in ("candidate", "reference"):
            for seed in (101, 102):
                folder = root / engine / str(seed)
                folder.mkdir(parents=True)
                model = generate_inputs.model_text((3, 3, 3), seed)
                model_path = folder / "model.xyz"
                model_path.write_text(model)
                run_steps = look
                run_hash = stats._expected_run_hash(run_steps, dt_fs, manifest)
                if dt_fs == protocol["configuration"]["dt_fs"]:
                    tdamp, tau = protocol["configuration"]["Tdamp_steps"], protocol["configuration"]["tau_p_steps"]
                else:
                    tdamp, tau = protocol["half_dt"]["Tdamp_steps"], protocol["half_dt"]["tau_p_steps"]
                run_text = ("potential nep.txt\n" f"time_step {dt_fs:g}\n"
                            f"ensemble npt_scr 300 300 {tdamp} 0 425 {tau}\n"
                            "dump_thermo 1\n" f"run {run_steps}\n")
                run_path = folder / "run.in"
                run_path.write_text(run_text)
                potential_path = folder / "nep.txt"
                potential_path.write_bytes(pot.read_bytes())
                thermo_path = folder / "thermo.out"
                _thermo(thermo_path, run_steps, dt_fs)
                record = {
                    "engine": engine, "seed": seed, "status": "PASS", "steps": run_steps,
                    "ranks": 1, "backend": "HostStaged", "pid": seed + (1000 if engine == "reference" else 2000),
                    "started_at_utc": f"2026-11-03T00:00:{(seed % 40) + (1 if engine == 'reference' else 11):02d}Z",
                    "ended_at_utc": f"2026-11-03T00:01:{(seed % 40) + (1 if engine == 'reference' else 11):02d}Z",
                    "rng_initialization_evidence": "reference release uses system_clock epoch count" if engine == "reference" else None,
                    "input_sha256": {"model.xyz": _hash(model_path), "run.in": run_hash,
                                     "nep.txt": potential_hash},
                    "input_paths": {"model.xyz": str(model_path), "run.in": str(run_path),
                                    "nep.txt": str(potential_path)},
                    "executable_sha256": ("a" if engine == "candidate" else "b") * 64,
                    "thermo_path": str(thermo_path), "thermo_sha256": _hash(thermo_path),
                }
                records["runs"].append(record)
        # A second independent chain cannot share a reference launch second.
        (root / "records.json").write_text(json.dumps(records))
        return manifest, records

    def test_records_end_to_end_and_base_half_dt_disambiguation(self) -> None:
        for dt_fs in (0.25, 0.125):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                manifest, _ = self._small_dataset(root, dt_fs)
                manifest_path = root / "manifest.json"
                manifest_path.write_text(json.dumps(manifest))
                records_path = root / "records.json"
                report = stats.analyze(records_path, 40, 1, "HostStaged", manifest_path)
                self.assertEqual(report["dt_fs"], dt_fs)
                # The deliberately tiny 40-row fixture is too imprecise for
                # the variance equivalence CI; it must parse and report it as
                # inconclusive rather than manufacture a PASS.
                self.assertEqual(report["same_condition_reference_gate"]["status"], "INCONCLUSIVE")
                self.assertEqual(report["condition"]["ranks"], 1)
                self.assertTrue(report["condition"]["reference_prefix_reused"])
                self.assertEqual(report["condition"]["candidate_actual_steps"], [40])
                self.assertEqual(report["condition"]["reference_actual_steps"], 40)
                self.assertIn("kinetic_energy_theory_diagnostic", report)

    def test_records_reject_missing_seed_wrong_units_nonfinite_and_short_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, records = self._small_dataset(root, 0.25)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            records_path = root / "records.json"
            records["runs"].pop()
            records_path.write_text(json.dumps(records))
            with self.assertRaisesRegex(ValueError, "exactly one"):
                stats.analyze(records_path, 40, 1, "HostStaged", manifest_path)

            manifest, records = self._small_dataset(root / "again", 0.25)
            manifest_path.write_text(json.dumps(manifest))
            records["units"]["pressure"] = "bar"
            records_path.write_text(json.dumps(records))
            with self.assertRaisesRegex(ValueError, "units"):
                stats.analyze(records_path, 40, 1, "HostStaged", manifest_path)

            manifest, records = self._small_dataset(root / "third", 0.25)
            manifest_path.write_text(json.dumps(manifest))
            thermo_path = Path(records["runs"][0]["thermo_path"])
            thermo = thermo_path.read_text().replace("300", "nan", 1)
            thermo_path.write_text(thermo)
            records["runs"][0]["thermo_sha256"] = _hash(thermo_path)
            records_path.write_text(json.dumps(records))
            with self.assertRaisesRegex(ValueError, "non-finite"):
                stats.analyze(records_path, 40, 1, "HostStaged", manifest_path)

            manifest, records = self._small_dataset(root / "fourth", 0.25)
            manifest_path.write_text(json.dumps(manifest))
            thermo_path = Path(records["runs"][0]["thermo_path"])
            thermo_path.write_text("\n".join(thermo_path.read_text().splitlines()[:-1]) + "\n")
            records["runs"][0]["thermo_sha256"] = _hash(thermo_path)
            records_path.write_text(json.dumps(records))
            with self.assertRaisesRegex(ValueError, "expected 40 rows"):
                stats.analyze(records_path, 40, 1, "HostStaged", manifest_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
