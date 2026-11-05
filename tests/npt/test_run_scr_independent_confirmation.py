#!/usr/bin/env python3
"""CPU-only safety and registry tests for the independent SCR runner."""
from __future__ import annotations

import json
import hashlib
import datetime as dt
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from argparse import Namespace
from unittest import mock

import run_scr_independent_confirmation as runner
import run_npt_scr

HERE = Path(__file__).resolve().parent


class IndependentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((HERE / "independent-confirmation-manifest.json").read_text())

    def test_registered_job_set_is_exact_and_unique(self):
        jobs = runner.registered_jobs(self.manifest)
        self.assertEqual(len(jobs), 160)
        self.assertEqual(sum(j["cohort"] == "primary_base" for j in jobs.values()), 64)
        self.assertEqual(sum(j["cohort"] == "primary_half" for j in jobs.values()), 64)
        self.assertEqual(sum(j["cohort"] == "bridge_base" for j in jobs.values()), 32)
        self.assertEqual(len(set(jobs)), 160)

    def test_draft_rejected_before_any_freeze_or_filesystem_gate(self):
        draft = json.loads(json.dumps(self.manifest))
        draft["status"] = "DRAFT"
        draft["confirmation_registration"]["status"] = "DRAFT"
        draft["sampling_authorized"] = False
        # The supplied empty freeze proves manifest auth is checked first.
        with self.assertRaisesRegex(runner.RunnerError, "not approved"):
            runner.approved_gate(draft, "", {}, Path("/must/not/be/read"))

    def test_run_one_draft_refusal_creates_no_execution_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            draft = json.loads(json.dumps(self.manifest))
            draft["status"] = "DRAFT"
            draft["confirmation_registration"]["status"] = "DRAFT"
            draft["sampling_authorized"] = False
            manifest_path = root / "draft-manifest.json"
            manifest_path.write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n")
            freeze = root / "freeze.json"
            freeze.write_text("{}\n")
            ledger = root / "ledger.json"
            registry = root / "reference-registry.json"
            args = Namespace(
                manifest=manifest_path,
                protocol=HERE / "independent-confirmation-protocol.md",
                freeze=freeze, static_ledger=root / "static-ledger.json",
                execution_ledger=ledger, reference_launch_registry=registry,
                job_key="primary_base|candidate|P1|HostStaged|seed201|steps160000",
                devices="0,1,2,3", mpiexec="/usr/bin/mpiexec", timeout=10)
            with self.assertRaisesRegex(runner.RunnerError, "not approved"):
                runner.run_one(args)
            self.assertFalse(ledger.exists())
            self.assertFalse(registry.exists())

    def test_bridge_keys_bind_only_registered_conditions_and_seeds(self):
        jobs = runner.registered_jobs(self.manifest)
        bridge = [item for item in jobs.values() if item["cohort"] == "bridge_base"]
        self.assertEqual({item["ranks"] for item in bridge}, {2, 4})
        self.assertEqual({item["backend"] for item in bridge}, {"HostStaged", "CudaAware"})
        self.assertEqual({item["seed"] for item in bridge}, set(range(201, 209)))

    def test_reference_registry_is_created_and_duplicate_launch_seconds_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reference-launch-utc-registry.json"
            initial = json.dumps({"schema": "dmgmd.npt-reference-launch-registry.v1",
                                  "reservations": []}, indent=2, sort_keys=True) + "\n"
            freeze = {"reference_launch_registry": {
                "path": str(path), "initial_sha256": hashlib.sha256(initial.encode()).hexdigest()}}
            jobs = runner.registered_jobs(self.manifest)
            runner.validate_or_initialize_reference_registry(path, freeze, jobs)
            self.assertEqual(path.read_text(), initial)
            ref_key = next(key for key, job in jobs.items() if job["engine"] == "reference")
            populated = {"schema": "dmgmd.npt-reference-launch-registry.v1", "reservations": [
                {"utc_second": "2026-11-03T00:00:01+00:00", "label": ref_key}]}
            path.write_text(json.dumps(populated, indent=2, sort_keys=True) + "\n")
            runner.validate_or_initialize_reference_registry(path, freeze, jobs)
            populated["reservations"].append({"utc_second": "2026-11-03T00:00:01+00:00",
                                              "label": ref_key})
            path.write_text(json.dumps(populated, indent=2, sort_keys=True) + "\n")
            with self.assertRaisesRegex(runner.RunnerError, "duplicate seconds"):
                runner.validate_or_initialize_reference_registry(path, freeze, jobs)

    def test_mocked_candidate_and_two_references_cross_adapter_save_assembly_and_cli_identity(self):
        """Exercise the actual ScrRunner stage/save path while mocking only GPU/Popen."""
        from analyze_paired_confirmation import load_bundle

        source_plan = json.loads(Path(
            "/tmp/dmgmd-b0-b4-20261103/npt-independent-confirmation-draft-v2/dry-run-plan.json"
        ).read_text())
        input_root = Path(source_plan["input_root"]).resolve()
        inventory = source_plan["input_inventory_sha256"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = json.loads((HERE / "independent-confirmation-manifest.json").read_text())
            manifest["status"] = "APPROVED_INDEPENDENT_CONFIRMATION"
            manifest["sampling_authorized"] = True
            manifest["confirmation_registration"]["status"] = "APPROVED_INDEPENDENT_CONFIRMATION"
            manifest_path = root / "independent-confirmation-manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            protocol_path = root / "independent-confirmation-protocol.md"
            protocol_path.write_text("status: APPROVED_INDEPENDENT_CONFIRMATION\n")
            bridge_path = root / "bridge-rng-source-audit.json"
            shutil.copy2(HERE / "bridge-rng-source-audit.json", bridge_path)

            candidate_id = manifest["confirmation_registration"]["analysis_software_identity"]["candidate_binary"]
            reference_id = manifest["confirmation_registration"]["analysis_software_identity"]["reference_binary"]
            registered = runner.registered_jobs(manifest)
            jobs = []
            for key, job in sorted(registered.items()):
                source = input_root / job["profile"] / str(job["steps"]) / f"seed-{job['seed']}"
                result_root = root / "results" / key.replace("|", "__")
                hashes = {name: runner.sha(source / name) for name in runner.INPUT_NAMES}
                jobs.append({"key": key, "cohort": job["cohort"], "profile": job["profile"],
                             "engine": job["engine"], "seed": job["seed"],
                             "actual_steps": job["steps"], "ranks": job["ranks"],
                             "backend": job["backend"], "status": "planned",
                             "input_source_dir": str(source), "input_sha256": hashes,
                             "result_root": str(result_root),
                             "result_file": str(result_root / "results.json")})
            static_ledger = {"schema": "test", "manifest_sha256": runner.sha(manifest_path),
                             "candidate_sha256": candidate_id["sha256"],
                             "reference_sha256": reference_id["sha256"],
                             "input_root": str(input_root),
                             "input_inventory_sha256": inventory, "jobs": jobs}
            static_ledger_path = root / "static-ledger.json"
            static_ledger_path.write_text(json.dumps(static_ledger, indent=2, sort_keys=True) + "\n")
            plan_path = root / "plan.json"
            plan_path.write_text("{}\n")
            proof_path = root / "mock-environment-proof.json"
            proof_path.write_text('{"mock":true}\n')
            registry_path = root / "reference-launch-utc-registry.json"
            empty_registry = json.dumps({"schema": "dmgmd.npt-reference-launch-registry.v1",
                                         "reservations": []}, indent=2, sort_keys=True) + "\n"
            freeze = {"schema": runner.FREEZE_SCHEMA, "status": "FROZEN_FOR_EXECUTION",
                      "sampling_authorized": True, "formal_execution_freeze": True,
                      "registry": {"manifest": {"path": str(manifest_path), "sha256": runner.sha(manifest_path)},
                                   "protocol": {"path": str(protocol_path), "sha256": runner.sha(protocol_path)},
                                   "bridge_rng_source_audit": {"path": str(bridge_path), "sha256": runner.sha(bridge_path)}},
                      "plan": {"path": str(plan_path), "sha256": runner.sha(plan_path)},
                      "ledger": {"path": str(static_ledger_path), "sha256": runner.sha(static_ledger_path)},
                      "input_root": str(input_root), "input_inventory_sha256": inventory,
                      "jobs": jobs,
                      "identities": {"runner_source": {"path": str(Path(runner.__file__).resolve()),
                                                           "sha256": runner.sha(Path(runner.__file__))}},
                      "external_evidence": {"mock_environment": {"path": str(proof_path),
                                                                   "sha256": runner.sha(proof_path)}},
                      "reference_launch_registry": {"path": str(registry_path),
                                                    "initial_sha256": hashlib.sha256(empty_registry.encode()).hexdigest()}}
            freeze_path = root / "freeze.json"
            freeze_path.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n")
            runtime_ledger = root / "execution-ledger.json"
            common = {"manifest": manifest_path, "protocol": protocol_path, "freeze": freeze_path,
                      "static_ledger": static_ledger_path, "execution_ledger": runtime_ledger,
                      "reference_launch_registry": registry_path, "devices": "0,1,2,3",
                      "mpiexec": "/mock/mpiexec", "timeout": 60}
            selected = [
                "primary_base|candidate|P1|HostStaged|seed201|steps160000",
                "primary_base|reference|P1|HostStaged|seed201|steps160000",
                "primary_base|reference|P1|HostStaged|seed202|steps160000",
            ]
            failure_key = "primary_base|candidate|P1|HostStaged|seed203|steps160000"

            class FakeProc:
                next_pid = 700000
                def __init__(self, command, **kwargs):
                    type(self).next_pid += 1
                    self.pid = type(self).next_pid
                    self.cwd = Path(kwargs["cwd"])
                    kwargs["stdout"].write("DMGMD_DOMAIN mode=m1 reason=npt-m1-only\n")
                    kwargs["stderr"].write("")
                    kwargs["stdout"].flush(); kwargs["stderr"].flush()
                def poll(self): return 0
                def wait(self, timeout=None):
                    (self.cwd / "thermo.out").write_text("0 0 0 0 0 0 0 0 0 10 0 0 0 10 0 0 0 10\n")
                    return 0

            with mock.patch.object(runner, "environment_proof",
                                   return_value=({"mpiexec_path": "/mock/mpiexec"}, proof_path)), \
                 mock.patch.object(runner.run_npt_ber, "reference_status_snapshot",
                                   return_value={"checkout": "mocked read-only snapshot"}), \
                 mock.patch.object(run_npt_scr.subprocess, "Popen", FakeProc), \
                 mock.patch.object(runner, "numeric_thermo_rows", return_value=160000):
                for key in selected:
                    self.assertEqual(runner.run_one(Namespace(**common, job_key=key)), 0)
                # The process succeeds, but a post-launch output-contract check
                # fails. The mutable ledger must retain this as failed.
                with mock.patch.object(runner, "numeric_thermo_rows", return_value=0):
                    with self.assertRaisesRegex(runner.RunnerError, "thermo rows"):
                        runner.run_one(Namespace(**common, job_key=failure_key))

            failed_ledger = json.loads(runtime_ledger.read_text())
            self.assertEqual(next(item["status"] for item in failed_ledger["jobs"]
                                  if item["key"] == failure_key), "failed")

            registry = json.loads(registry_path.read_text())
            self.assertEqual(len(registry["reservations"]), 2)
            self.assertEqual({item["label"] for item in registry["reservations"]}, set(selected[1:]))
            self.assertEqual(len({item["utc_second"] for item in registry["reservations"]}), 2)
            freeze_sha = runner.sha(freeze_path)

            # Supply deterministic mock completions for the other registered
            # keys so the full strict records assembler can validate cardinality.
            runtime = json.loads(runtime_ledger.read_text())
            # Complete the unrelated strict-assembler fixture synthetically;
            # the failed post-launch case above was already asserted separately.
            for item in runtime["jobs"]:
                if item["key"] == failure_key:
                    item["status"] = "planned"
                    shutil.rmtree(Path(item["result_root"]))
            thermo = root / "synthetic-thermo.out"
            thermo.write_text("0 0 0 0 0 0 0 0 0 10 0 0 0 10 0 0 0 10\n")
            ref_clock = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc)
            ref_index = 0
            for item in runtime["jobs"]:
                if item["status"] == "completed":
                    continue
                j = registered[item["key"]]
                source = Path(item["input_source_dir"])
                executable = Path(candidate_id["path"] if j["engine"] == "candidate" else reference_id["path"])
                start = ref_clock + dt.timedelta(seconds=ref_index) if j["engine"] == "reference" else \
                    dt.datetime(2026, 11, 4, 12, 0, ref_index % 60, tzinfo=dt.timezone.utc)
                if j["engine"] == "reference": ref_index += 1
                end = start + dt.timedelta(seconds=1)
                record = {"label": "run", "job_key": j["key"], "cohort": j["cohort"],
                          "engine": j["engine"], "seed": j["seed"], "steps": j["steps"],
                          "dt_fs": j["dt_fs"], "ranks": j["ranks"], "backend": j["backend"],
                          "status": "PASS", "units": dict(runner.UNITS),
                          "executable_path": str(executable), "executable_sha256": runner.sha(executable),
                          "ensemble_seed": str(j["seed"]) if j["engine"] == "candidate" else None,
                          "started_at_utc": start.isoformat(), "ended_at_utc": end.isoformat(),
                          "pid": 900000 + ref_index,
                          "input_paths": {name: str((source / name).resolve()) for name in runner.INPUT_NAMES},
                          "input_sha256": {name: runner.sha(source / name) for name in runner.INPUT_NAMES},
                          "thermo_path": str(thermo.resolve()), "thermo_sha256": runner.sha(thermo),
                          "route_lines": ["mode=m1 reason=npt-m1-only"] if j["engine"] == "candidate" else [],
                          "rng_initialization_evidence": "release initialize_rng system_clock epoch count"}
                if j["cohort"] == "bridge_base":
                    proof_id = manifest["confirmation_registration"]["analysis_software_identity"]["bridge_rng_source_audit"]
                    record["rng_draw_evidence"] = {"status": "PASS", "source_audit_sha256": proof_id["sha256"]}
                result_root = Path(item["result_root"]); result_root.mkdir(parents=True)
                result_file = Path(item["result_file"])
                payload = {"execution_freeze_sha256": freeze_sha, "records": [record]}
                result_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
                item.update(status="completed", execution_freeze_sha256=freeze_sha,
                            ended_at_utc=end.isoformat(), result_file_sha256=runner.sha(result_file))
            runtime_ledger.write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n")

            bundle_path = root / "records.json"
            assemble_args = Namespace(manifest=manifest_path, protocol=protocol_path,
                                      execution_ledger=runtime_ledger, output=bundle_path)
            runner.assemble_records(assemble_args)
            bundle = json.loads(bundle_path.read_text())
            self.assertEqual(len(bundle["runs"]), 160)
            self.assertEqual(bundle["bridge_rng_source_audit"]["path"], "bridge-rng-source-audit.json")

            # Exercise the analyzer's strict software identity validation and
            # full record-contract loader without fabricating multi-GB thermo.
            import analyze_paired_confirmation as analyzer
            helper = manifest["confirmation_registration"]["analysis_software_identity"]["native_acf_helper_executable"]["path"]
            os.environ["DMGMD_ACF_HELPER"] = helper
            class LazyChain:
                def __init__(self, size): self.size = size
                def __len__(self): return self.size
                def __getitem__(self, item):
                    return LazyChain(len(range(*item.indices(self.size)))) if isinstance(item, slice) else 0.0
            original_rows = analyzer._PRIMARY._thermo_rows
            analyzer._PRIMARY._thermo_rows = lambda path, steps, atoms, dt_fs: {
                name: LazyChain(steps) for name in ("temperature_K", "kinetic_energy_eV",
                                                   "potential_energy_eV", "pressure_GPa", "volume_A3")}
            try:
                loaded, _, _, _ = analyzer.load_bundle(bundle_path, manifest_path, protocol_path)
                self.assertEqual(len(loaded["runs"]), 160)
            finally:
                analyzer._PRIMARY._thermo_rows = original_rows


if __name__ == "__main__":
    unittest.main(verbosity=2)
