#!/usr/bin/env python3
"""Synthetic metadata/identity tests for the independent SCR analyzer CLI."""
from __future__ import annotations

import importlib.util
import copy
import datetime as dt
import json
import math
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "dmgmd_paired_confirmation_cli", HERE / "analyze_paired_confirmation.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load paired confirmation CLI")
cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli
SPEC.loader.exec_module(cli)


class PairedConfirmationIntegrationTests(unittest.TestCase):
    def test_draft_registration_fails_closed_until_sampling_authorized(self) -> None:
        draft = {"status": "DRAFT_FOR_ROOT_REVIEW_NO_SAMPLING_AUTHORIZED",
                 "sampling_authorized": False,
                 "confirmation_registration": {"status": "DRAFT"}}
        with self.assertRaisesRegex(ValueError, "not approved"):
            cli.require_approved_registration(draft)
        for bad in ("false", 1, "APPROVED_BUT_WRONG"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "not approved"):
                cli.require_approved_registration({
                    "status": bad, "sampling_authorized": bad,
                    "confirmation_registration": {"status": bad}})
        approved = {"status": "APPROVED_INDEPENDENT_CONFIRMATION",
                    "sampling_authorized": True,
                    "confirmation_registration": {"status": "APPROVED_INDEPENDENT_CONFIRMATION"}}
        cli.require_approved_registration(approved)

    def test_reference_launch_seconds_are_unique_across_both_profiles(self) -> None:
        seconds = list(range(64))
        cli.require_global_reference_seconds(seconds)
        # The duplicate is deliberately across the 32/32 profile boundary.
        seconds[32] = seconds[0]
        with self.assertRaisesRegex(ValueError, "globally distinct"):
            cli.require_global_reference_seconds(seconds)

    def test_registered_software_hashes_and_acf_executable_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "acf-helper"
            helper.write_bytes(b"synthetic executable\n")
            helper.chmod(0o755)
            import os
            old = os.environ.get("DMGMD_ACF_HELPER")
            os.environ["DMGMD_ACF_HELPER"] = str(helper)
            try:
                source_files = {
                    "primary_analyzer": cli.ROOT / "tests/npt/analyze_statistics.py",
                    "shared_statistics_primitives": cli.ROOT / "tests/thermostats/sample_statistics.py",
                    "paired_math_draft": HERE / "paired_confirmation_math.py",
                    "paired_math_tests_draft": HERE / "test_paired_confirmation_math.py",
                }
                identities = {key: {"path": path.relative_to(cli.ROOT).as_posix(),
                                    "sha256": cli.sha256(path)}
                              for key, path in source_files.items()}
                identities["paired_cli"] = {
                    "path": Path(cli.__file__).resolve().relative_to(cli.ROOT).as_posix(),
                    "sha256": cli.sha256(Path(cli.__file__).resolve()),
                }
                integration = HERE / "test_paired_confirmation_integration.py"
                identities["paired_integration_tests"] = {
                    "path": integration.relative_to(cli.ROOT).as_posix(),
                    "sha256": cli.sha256(integration),
                }
                identities["native_acf_helper_executable"] = {
                    "path": str(helper), "sha256": cli.sha256(helper)}
                identities["native_acf_helper"] = {
                    "path": "tests/thermostats/autocorrelation.cpp",
                    "sha256": cli.sha256(cli.ROOT / "tests/thermostats/autocorrelation.cpp")}
                manifest = {"confirmation_registration": {
                    "analysis_software_identity": identities}}
                cli.validate_software_identity(manifest)

                wrong_acf = {"confirmation_registration": {
                    "analysis_software_identity": {**identities,
                        "native_acf_helper_executable": {
                            "path": str(helper), "sha256": "0" * 64}}}}
                with self.assertRaisesRegex(ValueError, "ACF executable hash"):
                    cli.validate_software_identity(wrong_acf)

                wrong_primary = {"confirmation_registration": {
                    "analysis_software_identity": {**identities,
                        "primary_analyzer": {"path": "tests/npt/analyze_statistics.py",
                                              "sha256": "0" * 64}}}}
                with self.assertRaisesRegex(ValueError, "dependency hash mismatch"):
                    cli.validate_software_identity(wrong_primary)
            finally:
                if old is None:
                    os.environ.pop("DMGMD_ACF_HELPER", None)
                else:
                    os.environ["DMGMD_ACF_HELPER"] = old

    @staticmethod
    def _synthetic_bundle(root: Path) -> tuple[Path, Path, Path, dict]:
        manifest = json.loads(cli.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        manifest["status"] = "APPROVED_INDEPENDENT_CONFIRMATION"
        manifest["sampling_authorized"] = True
        registration = manifest["confirmation_registration"]
        registration["status"] = "APPROVED_INDEPENDENT_CONFIRMATION"
        for profile in registration["primary_profiles"]:
            profile["steps"], profile["block_steps"] = 40, 1
            profile["block_fs"] = profile["dt_fs"]
        bridge = registration["bridge_profile"]
        bridge["steps"], bridge["block_steps"] = 40, 1
        bridge["block_fs"] = bridge["dt_fs"]

        potential = root / "nep.txt"
        potential.write_text("synthetic potential\n", encoding="utf-8")
        potential_hash = cli.sha256(potential)
        manifest["source_contract"]["potential_sha256"] = potential_hash
        model_hashes, model_paths = {}, {}
        for seed in registration["seed_population"]["seeds"]:
            path = root / f"model-{seed}.xyz"
            path.write_text(f"synthetic model for {seed}\n", encoding="utf-8")
            model_paths[str(seed)] = str(path)
            model_hashes[str(seed)] = cli.sha256(path)
        manifest["input_locking"]["confirmation_model_xyz_sha256_by_seed"] = model_hashes
        run_hashes, run_paths = {}, {}
        for label in ("base_dt_0.25_160000", "half_dt_0.125_320000"):
            path = root / f"{label}.in"
            path.write_text(f"synthetic {label}\n", encoding="utf-8")
            run_paths[label] = str(path)
            run_hashes[label] = cli.sha256(path)
        manifest["input_locking"]["confirmation_run_in_sha256"] = run_hashes

        candidate = root / "dmg-md"
        reference = root / "gpumd"
        candidate.write_bytes(b"synthetic candidate executable")
        reference.write_bytes(b"synthetic reference executable")
        candidate_sha, reference_sha = cli.sha256(candidate), cli.sha256(reference)
        identities = registration["analysis_software_identity"]
        identities["candidate_binary"]["sha256"] = candidate_sha
        identities["reference_binary"]["sha256"] = reference_sha
        identities["candidate_source_digest"]["sha256"] = "1" * 64
        proof = root / "rng-audit.json"
        proof.write_text('{"synthetic":true}\n', encoding="utf-8")
        proof_sha = cli.sha256(proof)
        identities["bridge_rng_source_audit"] = {"path": "rng-audit.json", "sha256": proof_sha}

        def write_thermo(path: Path, dt_fs: float, *, nonfinite: bool = False) -> None:
            with path.open("w", encoding="utf-8") as stream:
                stream.write("# dump_thermo 1\n# format_version 1\n")
                stream.write("# num_atoms 216\n")
                stream.write(f"# dt_output {dt_fs:.10e} fs\n")
                stream.write("# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz\n")
                length = 11.0
                for index in range(40):
                    temp = "nan" if nonfinite and index == 0 else "300"
                    row = [temp, "8", "-100", "0", "0", "0", "0", "0", "0",
                           str(length), "0", "0", "0", str(length), "0", "0", "0", str(length)]
                    stream.write(" ".join(row) + "\n")

        runs, output_paths = [], []
        ref_second = 0
        for cohort in ("primary_base", "primary_half", "bridge_base"):
            profile = next((item for item in registration["primary_profiles"]
                            if item["cohort"] == cohort), bridge if cohort == "bridge_base" else None)
            seeds = profile["seed_set"]
            conditions = ([{"ranks": profile["candidate"]["ranks"],
                            "backend": profile["candidate"]["backend"]}]
                          if cohort != "bridge_base" else profile["candidate_conditions"])
            engines = ("candidate", "reference") if cohort != "bridge_base" else ("candidate",)
            for engine in engines:
                selected = conditions if engine == "candidate" else [{"ranks": 1, "backend": "HostStaged"}]
                for condition in selected:
                    for seed in seeds:
                        base_label = "half_dt_0.125_320000" if cohort == "primary_half" else "base_dt_0.25_160000"
                        input_paths = {"model.xyz": model_paths[str(seed)],
                                       "run.in": run_paths[base_label], "nep.txt": str(potential)}
                        input_sha = {name: cli.sha256(Path(path)) for name, path in input_paths.items()}
                        executable = candidate if engine == "candidate" else reference
                        thermo = root / f"thermo-{len(runs)}.out"
                        write_thermo(thermo, profile["dt_fs"])
                        path = profile["candidate"] if engine == "candidate" and cohort != "bridge_base" else None
                        ranks, backend = condition["ranks"], condition["backend"]
                        second = len(runs) + 1
                        started = dt.datetime(2026, 11, 3, 0, 0, 0, tzinfo=dt.timezone.utc) + \
                            dt.timedelta(seconds=second)
                        ended = started + dt.timedelta(seconds=1)
                        run = {"cohort": cohort, "engine": engine, "seed": seed,
                               "ranks": ranks, "backend": backend, "steps": 40,
                               "dt_fs": profile["dt_fs"], "status": "PASS",
                               "units": cli.UNITS, "executable_sha256": cli.sha256(executable),
                               "executable_path": str(executable), "input_paths": input_paths,
                               "input_sha256": input_sha, "thermo_path": str(thermo),
                               "thermo_sha256": cli.sha256(thermo), "pid": len(runs) + 1000,
                               "started_at_utc": started.isoformat().replace("+00:00", "Z"),
                               "ended_at_utc": ended.isoformat().replace("+00:00", "Z"),
                               "route_lines": ["mode=m1", "reason=npt-m1-only"]}
                        if engine == "candidate":
                            run["ensemble_seed"] = str(seed)
                            if cohort == "bridge_base":
                                run["rng_draw_evidence"] = {
                                    "status": "PASS", "source_audit_sha256": proof_sha}
                        else:
                            ref_second += 1
                            ref_started = dt.datetime(2026, 11, 3, 1, 0, 0,
                                                      tzinfo=dt.timezone.utc) + \
                                dt.timedelta(seconds=ref_second)
                            run["started_at_utc"] = ref_started.isoformat().replace("+00:00", "Z")
                            run["ended_at_utc"] = (ref_started + dt.timedelta(minutes=1)).isoformat().replace(
                                "+00:00", "Z")
                            run["rng_initialization_evidence"] = "system_clock synthetic fixture"
                        runs.append(run)
                        output_paths.append(thermo)
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        protocol_path = root / "protocol.md"
        protocol_path.write_text("synthetic protocol\n", encoding="utf-8")
        bundle = {"schema": cli.INPUT_SCHEMA, "manifest_sha256": cli.sha256(manifest_path),
                  "protocol_sha256": cli.sha256(protocol_path),
                  "candidate_executable_sha256": candidate_sha,
                  "reference_executable_sha256": reference_sha,
                  "candidate_source_digest_sha256": "1" * 64,
                  "potential_sha256": potential_hash,
                  "bridge_rng_source_audit": {"status": "PASS", "path": "rng-audit.json",
                                               "sha256": proof_sha},
                  "runs": runs}
        records_path = root / "records.json"
        records_path.write_text(json.dumps(bundle), encoding="utf-8")
        return records_path, manifest_path, protocol_path, {
            "bundle": bundle, "manifest": manifest, "output_paths": output_paths,
            "model_paths": model_paths, "run_paths": run_paths,
        }

    def test_load_bundle_accepts_synthetic_full_cohorts_and_rejects_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records_path, manifest_path, protocol_path, fixture = self._synthetic_bundle(root)
            original_validate = cli.validate_software_identity
            cli.validate_software_identity = lambda manifest: {"synthetic": "identity"}
            try:
                _, _, _, loaded = cli.load_bundle(records_path, manifest_path, protocol_path)
                self.assertEqual(len(loaded["runs"]), 160)
                for label, mutate, pattern in (
                    ("missing seed", lambda b: b["runs"].pop(), "exactly 160"),
                    ("duplicate identity", lambda b: b["runs"].__setitem__(159, copy.deepcopy(b["runs"][0])),
                     "duplicate or unregistered"),
                    ("extra record", lambda b: b["runs"].append(copy.deepcopy(b["runs"][0])),
                     "exactly 160"),
                    ("wrong input hash", lambda b: b["runs"][0]["input_sha256"].update({"model.xyz": "0" * 64}),
                     "input SHA map"),
                    ("wrong output hash", lambda b: b["runs"][0].update({"thermo_sha256": "0" * 64}),
                     "thermo output"),
                ):
                    with self.subTest(case=label):
                        changed = copy.deepcopy(fixture["bundle"])
                        mutate(changed)
                        altered_path = root / f"records-{label.replace(' ', '-')}.json"
                        altered_path.write_text(json.dumps(changed), encoding="utf-8")
                        with self.assertRaisesRegex(ValueError, pattern):
                            cli.load_bundle(altered_path, manifest_path, protocol_path)

                bad_thermo = fixture["output_paths"][0]
                bad_thermo.write_text(bad_thermo.read_text().replace("300 8 ", "nan 8 ", 1),
                                       encoding="utf-8")
                changed = copy.deepcopy(fixture["bundle"])
                changed["runs"][0]["thermo_sha256"] = cli.sha256(bad_thermo)
                altered_path = root / "records-nan-thermo.json"
                altered_path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "non-finite"):
                    cli.load_bundle(altered_path, manifest_path, protocol_path)
            finally:
                cli.validate_software_identity = original_validate

    def test_primary_volume_cdf_failure_contributes_to_final_status(self) -> None:
        manifest = json.loads(cli.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        registration = manifest["confirmation_registration"]
        base = copy.deepcopy(registration["primary_profiles"][0])
        half = copy.deepcopy(registration["primary_profiles"][1])
        bridge = copy.deepcopy(registration["bridge_profile"])
        seeds = [201, 202]
        for profile in (base, half, bridge):
            profile["seed_set"] = seeds
            profile["steps"] = 40
            profile["block_steps"] = 1
            profile["block_fs"] = profile["dt_fs"]
        profiles = {base["cohort"]: base, half["cohort"]: half, bridge["cohort"]: bridge}
        series = {}
        for cohort, profile in profiles.items():
            engines = ("candidate", "reference") if cohort != "bridge_base" else ("candidate",)
            conditions = ([{"ranks": 1, "backend": "HostStaged"}]
                          if cohort != "bridge_base" else bridge["candidate_conditions"])
            for engine in engines:
                selected = conditions if engine == "candidate" else [{"ranks": 1, "backend": "HostStaged"}]
                for condition in selected:
                    for seed in seeds:
                        key = (cohort, engine, seed, condition["ranks"], condition["backend"])
                        series[key] = {metric: [float(i + seed) for i in range(40)]
                                       for metric in (*cli.METRICS, "kinetic_energy_eV")}
        loaded = {"series": series, "runs": {}, "input_sha256": "e" * 64,
                  "software_identity": {}}
        energy_stats = {"estimate": 1.0, "sample_variance": 1.0}
        ess = {"effective_samples_mean_pooled": 10000.0,
               "effective_samples_squared_deviation_pooled": 10000.0}

        def primary_metric(_loaded, _profile, _cohort, metric, _z):
            estimate = 1000.0 if metric == "volume_A3" else 300.0 if metric == "temperature_K" else 0.0
            return {
                "candidate_chains": {seed: [float(i) for i in range(40)] for seed in seeds},
                "reference_chains": {seed: [float(i) for i in range(40)] for seed in seeds},
                "candidate_stats": {**energy_stats, "estimate": estimate},
                "reference_stats": {**energy_stats, "estimate": estimate},
                "mean_difference": {"estimate": 0.0, "confidence_interval": [0.0, 0.0]},
                "variance_ratio": {"estimate": 1.0, "confidence_interval": [1.0, 1.0]},
                "cdf_distance": 0.2 if metric == "volume_A3" else 0.0,
                "candidate_ess": ess, "reference_ess": ess,
            }

        paired_mean = {"estimate": 0.0, "confidence_interval": [0.0, 0.0]}
        paired_ratio = {"estimate": 1.0, "confidence_interval": [1.0, 1.0]}
        triangle = {"components": {"a": 0.0, "b": 0.0, "c": 0.0}, "triangle_sum": 0.0}
        with mock.patch.object(cli, "_primary_metric", side_effect=primary_metric), \
                mock.patch.object(cli, "_has_precision", return_value=True), \
                mock.patch.object(cli, "_ess_summary", return_value=ess), \
                mock.patch.object(cli._MATH, "paired_mean_difference_ci", return_value=paired_mean), \
                mock.patch.object(cli._MATH, "paired_global_variance_ratio_ci", return_value=paired_ratio), \
                mock.patch.object(cli._MATH, "cdf_triangle_bound", return_value=triangle):
            bundle = {"manifest_sha256": "a" * 64, "protocol_sha256": "b" * 64}
            report = cli.analyze_data(bundle, manifest, profiles, loaded,
                                      workers=1, bootstrap_replicates=20)
            parallel_report = cli.analyze_data(bundle, manifest, profiles, loaded,
                                               workers=2, bootstrap_replicates=20)
        serial_semantics, parallel_semantics = copy.deepcopy(report), copy.deepcopy(parallel_report)
        serial_semantics["execution"]["variance_bootstrap_workers"] = None
        parallel_semantics["execution"]["variance_bootstrap_workers"] = None
        self.assertEqual(serial_semantics, parallel_semantics)
        self.assertEqual(report["profiles"]["primary_base"]["primary"]["volume_A3"]
                         ["empirical_cdf_decision"], "FAIL")
        self.assertEqual(report["profiles"]["primary_half"]["primary"]["volume_A3"]
                         ["empirical_cdf_decision"], "FAIL")
        self.assertEqual(report["status"], "FAIL")

    def test_run_identity_rejects_wrong_profile_seed_rank_backend_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            executable = base / "dmg-md"
            executable.write_bytes(b"synthetic executable")
            inputs = {name: base / name for name in ("model.xyz", "run.in", "nep.txt")}
            for name, path in inputs.items():
                path.write_text(name + "\n", encoding="utf-8")
            thermo = base / "thermo.out"
            thermo.write_text("synthetic thermo is intentionally not parsed here\n", encoding="utf-8")
            hashes = {name: cli.sha256(path) for name, path in inputs.items()}
            expected = {"cohort": "primary_base", "engine": "candidate", "seed": 201,
                        "ranks": 1, "backend": "HostStaged", "steps": 160000,
                        "dt_fs": 0.25, "executable_sha256": cli.sha256(executable),
                        "model_sha256": hashes["model.xyz"], "run_sha256": hashes["run.in"]}
            run = {**expected, "status": "PASS",
                   "units": cli.UNITS, "ensemble_seed": "201",
                   "dt_fs": 0.25, "executable_path": str(executable),
                   "input_sha256": hashes,
                   "input_paths": {name: str(path) for name, path in inputs.items()},
                   "thermo_path": str(thermo), "thermo_sha256": cli.sha256(thermo)}
            manifest = {"source_contract": {"potential_sha256": hashes["nep.txt"]}}
            cli.validate_run_identity(run, expected=expected, base=base,
                                      manifest=manifest, context="synthetic")
            for field, wrong in (("cohort", "primary_half"), ("seed", 202),
                                 ("ranks", 2), ("backend", "CudaAware")):
                changed = {**run, field: wrong}
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                    cli.validate_run_identity(changed, expected=expected, base=base,
                                              manifest=manifest, context="synthetic")
            changed_hash = {**run, "input_sha256": {**hashes, "model.xyz": "0" * 64}}
            with self.assertRaisesRegex(ValueError, "input SHA map"):
                cli.validate_run_identity(changed_hash, expected=expected, base=base,
                                          manifest=manifest, context="synthetic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
