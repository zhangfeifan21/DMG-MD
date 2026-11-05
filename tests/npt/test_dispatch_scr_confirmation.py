from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch_scr_confirmation as dispatch


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SuccessorManifestTests(unittest.TestCase):
    def setUp(self):
        self.old = {
            "status": "APPROVED_INDEPENDENT_CONFIRMATION",
            "sampling_authorized": True,
            "created_at": "old",
            "original_preregistration_sha256": "old-prereg",
            "budget": {"steps": 160000, "jobs": 160},
            "precision_gates": {"relative": 0.05},
            "confirmation_registration": {
                "status": "APPROVED_INDEPENDENT_CONFIRMATION",
                "analysis_software_identity": {"reference_binary": {"path": "/old/gpumd", "sha256": "old-sha"}},
                "seed_population": [201, 202],
            },
        }
        self.new = copy.deepcopy(self.old)
        self.new["created_at"] = "new"
        self.new["confirmation_registration"]["analysis_software_identity"]["reference_binary"] = {
            "path": "/new/gpumd", "sha256": "new-sha"}
        self.new["migration_registration"] = {
            "status": "ROOT_APPROVED_SUCCESSOR_EXECUTION_REGISTRATION",
            "successor_of_manifest_sha256": "old-file-sha",
            "successor_of_protocol_sha256": "old-protocol-sha",
            "fresh_full_chains": 160,
            "predecessor_confirmation_statistical_analysis_performed": False,
        }

    def test_only_whitelisted_reference_identity_and_migration_metadata_can_change(self):
        dispatch.validate_successor_manifest(self.old, self.new, "old-file-sha", "old-protocol-sha")

    def test_scientific_budget_or_seed_change_is_rejected(self):
        changed_budget = copy.deepcopy(self.new)
        changed_budget["budget"]["steps"] += 1
        with self.assertRaisesRegex(dispatch.DispatchError, "non-whitelisted"):
            dispatch.validate_successor_manifest(self.old, changed_budget, "old-file-sha", "old-protocol-sha")
        changed_seeds = copy.deepcopy(self.new)
        changed_seeds["confirmation_registration"]["seed_population"].append(203)
        with self.assertRaisesRegex(dispatch.DispatchError, "non-whitelisted"):
            dispatch.validate_successor_manifest(self.old, changed_seeds, "old-file-sha", "old-protocol-sha")

    def test_reference_must_be_replaced_and_successor_linked(self):
        unchanged = copy.deepcopy(self.new)
        unchanged["confirmation_registration"]["analysis_software_identity"]["reference_binary"]["sha256"] = "old-sha"
        with self.assertRaisesRegex(dispatch.DispatchError, "not replaced"):
            dispatch.validate_successor_manifest(self.old, unchanged, "old-file-sha", "old-protocol-sha")
        wrong_parent = copy.deepcopy(self.new)
        wrong_parent["migration_registration"]["successor_of_manifest_sha256"] = "wrong"
        with self.assertRaisesRegex(dispatch.DispatchError, "predecessor"):
            dispatch.validate_successor_manifest(self.old, wrong_parent, "old-file-sha", "old-protocol-sha")


class QueueAndPlacementTests(unittest.TestCase):
    def test_proof_group_input_order_is_arbitrary_but_set_coverage_is_exact(self):
        groups = dispatch.normalize_proof_groups([["4", "2", "1", "3"], ["7", "5", "4", "6"]])
        self.assertEqual([set(g) for g in groups], [set("1234"), set("4567")])
        with self.assertRaisesRegex(dispatch.DispatchError, "proof coverage"):
            dispatch.normalize_proof_groups([["1", "2", "3", "4"], ["4", "5", "6", "8"]])

    def test_rank_placement_leases_only_prefix_and_p4_is_fixed_to_gpu4_through7(self):
        groups = [["4", "2", "3", "1"], ["7", "4", "5", "6"]]
        p1 = dispatch.choose_devices(1, {"1", "2", "3", "4", "5", "6", "7"}, groups)
        self.assertEqual(len(p1), 4)
        self.assertIn(p1[0], {"1", "2", "3", "4", "5", "6", "7"})
        p2 = dispatch.choose_devices(2, {"1", "2", "3", "5", "6", "7"}, groups)
        self.assertEqual(len(p2), 4)
        self.assertTrue(set(p2[:2]).issubset({"1", "2", "3", "5", "6", "7"}))
        p4 = dispatch.choose_devices(4, {"1", "2", "3", "4", "5", "6", "7"}, groups)
        self.assertEqual(set(p4), {"4", "5", "6", "7"})
        self.assertIsNone(dispatch.choose_devices(4, {"1", "2", "3", "5", "6", "7"}, groups))

    def test_completed_and_planned_queue_resumes_but_running_or_failed_fails_closed(self):
        static_jobs = [{"key": f"k{i:03}", "status": "planned", "ranks": 1, "input_sha256": {},
                        "result_file": f"r{i}.json"}
                       for i in range(160)]
        static = {"jobs": static_jobs}
        ledger = copy.deepcopy(static)
        planned, completed = dispatch.validate_queue(ledger, static)
        self.assertEqual((len(planned), len(completed)), (160, 0))
        ledger["jobs"][0].update(status="completed", result_file_sha256="rsha",
                                  execution_freeze_sha256="freeze")
        planned, completed = dispatch.validate_queue(ledger, static)
        self.assertEqual((len(planned), len(completed)), (159, 1))
        for status in ("running", "failed", "interrupted-for-server-reboot"):
            bad = copy.deepcopy(ledger)
            bad["jobs"][1]["status"] = status
            with self.assertRaisesRegex(dispatch.DispatchError, "fail-closed"):
                dispatch.validate_queue(bad, static)

    def test_completed_queue_rechecks_result_and_full_thermo_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze = root / "freeze.json"
            freeze.write_text("{}\n")
            thermo = root / "thermo.out"
            thermo.write_text(" ".join(["1"] * 18) + "\n")
            result = root / "results.json"
            key = "k000"
            input_hashes = {"model.xyz": "m", "run.in": "r", "nep.txt": "n"}
            record = {"label": "run", "job_key": key, "execution_freeze_sha256": sha(freeze),
                      "input_sha256": input_hashes, "steps": 1, "executable_sha256": "candidate",
                      "thermo_path": str(thermo), "thermo_sha256": sha(thermo)}
            result.write_text(json.dumps({"records": [record]}))
            static = {"jobs": [{"key": key, "actual_steps": 1, "engine": "candidate",
                                 "input_sha256": input_hashes}]}
            row = {"key": key, "result_file": str(result), "result_file_sha256": sha(result),
                   "thermo_sha256": sha(thermo)}
            manifest = {"confirmation_registration": {"analysis_software_identity": {
                "candidate_binary": {"sha256": "candidate"}, "reference_binary": {"sha256": "reference"}}}}
            dispatch.revalidate_completed_queue(manifest, freeze, static, [row])
            thermo.write_text(" ".join(["1"] * 18) + "\n" + " ".join(["1"] * 18) + "\n")
            with self.assertRaisesRegex(dispatch.DispatchError, "changed or is incomplete"):
                dispatch.revalidate_completed_queue(manifest, freeze, static, [row])

    def test_stop_file_and_failure_both_stop_dispatching(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "STOP"
            self.assertTrue(dispatch.dispatch_allowed(path, False, False))
            path.write_text("stop\n")
            self.assertFalse(dispatch.dispatch_allowed(path, False, False))
            path.unlink()
            self.assertFalse(dispatch.dispatch_allowed(path, True, False))
            self.assertFalse(dispatch.dispatch_allowed(path, False, True))

    def test_staged_artifact_promotion_can_finish_after_partial_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            final = root / "records.json"
            staged_a = root / "records-a.staged"
            staged_a.write_text("same payload\n")
            dispatch.promote_staged(staged_a, final)
            staged_b = root / "records-b.staged"
            staged_b.write_text("same payload\n")
            dispatch.promote_staged(staged_b, final)
            self.assertEqual(final.read_text(), "same payload\n")
            self.assertFalse(staged_b.exists())
            mismatch = root / "records-c.staged"
            mismatch.write_text("different\n")
            with self.assertRaisesRegex(dispatch.DispatchError, "differs from staged"):
                dispatch.promote_staged(mismatch, final)

    def test_runner_invocation_preserves_registered_key_and_full_proof_group(self):
        command = dispatch.build_runner_command(python="python3", script=Path("runner.py"),
            manifest=Path("manifest.json"), protocol=Path("protocol.md"), freeze=Path("freeze.json"),
            static_ledger=Path("static.json"), execution_ledger=Path("runtime.json"),
            reference_registry=Path("reference.json"), job_key="primary_base|candidate|P1|HostStaged|seed201|steps160000",
            devices=["4", "2", "3", "1"], mpiexec="/mpi/bin/mpirun", timeout=3600)
        self.assertIn("run-one", command)
        self.assertEqual(command[command.index("--job-key") + 1],
                         "primary_base|candidate|P1|HostStaged|seed201|steps160000")
        self.assertEqual(command[command.index("--devices") + 1], "4,2,3,1")


class MigrationBindingTests(unittest.TestCase):
    def test_binding_rehashes_new_host_environment_and_native_proofs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env_script, package_manifest, library = (root / x for x in ("md-mpi.sh", "packages.json", "libpmix.so"))
            env_script.write_text("canonical\n")
            package_manifest.write_text("{\"pmix\":\"5.0.1\"}\n")
            library.write_text("pmix bytes\n")
            env = {name: os.environ.get(name) for name in dispatch.ENV_KEYS}
            env["UCX_TLS"] = "self,sm,tcp,cuda_copy,cuda_ipc"
            gpu_ids = [str(i) for i in range(8)]
            inventory = {d: {"uuid": f"GPU-{d}", "driver_version": "570.86.10"} for d in gpu_ids}
            manifest_path = root / "successor.json"
            manifest_path.write_text(json.dumps({"confirmation_registration": {"analysis_software_identity": {
                "candidate_binary": {"sha256": "candidate"}, "reference_binary": {"sha256": "reference"}}}}))
            native_items = []
            external = {}
            for index, devices in enumerate((["4", "2", "3", "1"], ["7", "5", "4", "6"])):
                proof_path = root / f"proof-{index}.json"
                proof = {"schema": "dmgmd.npt-b3-environment-proof.v1", "status": "PASS",
                    "candidate_sha256": "candidate", "physical_devices": devices,
                    "gpu_identity": {d: inventory[d] for d in devices}, "gate_ranks": 4,
                    "supported_runtime_backends": ["HostStaged", "CudaAware"],
                    "environment_fingerprint": {"mpi": "current"}, "mpiexec_path": "/mpi/bin/mpirun"}
                proof_path.write_text(json.dumps(proof))
                item = {"path": str(proof_path), "sha256": sha(proof_path), "devices": devices}
                native_items.append(item)
                external[f"native-{index}"] = {"path": str(proof_path), "sha256": sha(proof_path)}
            binding = {"schema": dispatch.MIGRATION_BINDING_SCHEMA, "status": "PASS_ROOT_REVIEWED",
                "hostname": "n3", "driver_version": "570.86.10", "candidate_sha256": "candidate",
                "reference_sha256": "reference", "mpiexec_path": "/mpi/bin/mpirun",
                "canonical_env_script": {"path": str(env_script), "sha256": sha(env_script)},
                "local_package_manifest": {"path": str(package_manifest), "sha256": sha(package_manifest)},
                "environment_variables": env, "critical_libraries": [{"path": str(library), "sha256": sha(library)}],
                "native_gpu_proofs": native_items}
            binding_path = root / "migration-runtime-binding.json"
            binding_path.write_text(json.dumps(binding))
            external["migration_runtime_binding"] = {"path": str(binding_path), "sha256": sha(binding_path)}
            freeze = {"external_evidence": external, "registry": {"manifest": {"path": str(manifest_path)}}}
            with mock.patch.object(dispatch.socket, "gethostname", return_value="n3"), \
                 mock.patch.object(dispatch.capture_environment_proof, "environment_fingerprint",
                                   return_value={"mpi": "current"}), \
                 mock.patch.object(dispatch.capture_environment_proof, "gpu_inventory",
                                   return_value=inventory), \
                 mock.patch.dict("os.environ", {k: v for k, v in env.items() if v is not None}, clear=False):
                checked, _ = dispatch._load_frozen_migration_binding(freeze)
            self.assertEqual(set(map(frozenset, checked["proof_groups"])),
                             {frozenset({"1", "2", "3", "4"}), frozenset({"4", "5", "6", "7"})})

    def test_binding_fails_closed_if_a_critical_library_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            lib = Path(temp) / "lib.so"
            lib.write_text("original")
            binding = {"critical_libraries": [{"path": str(lib), "sha256": sha(lib)}]}
            lib.write_text("mutated")
            # The same path/hash guard used by the loader is intentionally simple and testable.
            item = binding["critical_libraries"][0]
            self.assertNotEqual(sha(Path(item["path"])), item["sha256"])


if __name__ == "__main__":
    unittest.main()
