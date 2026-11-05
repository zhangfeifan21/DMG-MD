#!/usr/bin/env python3
"""Dispatch a root-approved successor SCR queue through the frozen original runner.

The dispatcher does not change inputs or scientific parameters. A failed or
stale-running job is left in the ledger for root review and is never retried.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import capture_environment_proof
import run_scr_independent_confirmation as runner

MIGRATION_BINDING_SCHEMA = "dmgmd.npt-scr-migration-runtime-binding.v1"
EVENT_SCHEMA = "dmgmd.npt-scr-successor-dispatch-event.v1"
ENV_KEYS = ("UCX_TLS", "LD_LIBRARY_PATH", "OMPI_HOME", "UCX_HOME", "CUDA_HOME",
            "PMIX_MCA_mca_base_component_path", "OMPI_MCA_pml", "OMPI_MCA_coll",
            "OMPI_MCA_mca_base_component_path", "CUDA_DEVICE_ORDER", "OMP_NUM_THREADS",
            "UCX_WARN_UNUSED_ENV_VARS")
ALLOWED_MANIFEST_DIFFS = {
    ("created_at",),
    ("confirmation_registration", "analysis_software_identity", "reference_binary", "path"),
    ("confirmation_registration", "analysis_software_identity", "reference_binary", "sha256"),
    ("migration_registration",),
}
PROOF_GROUP_SETS = (frozenset({"1", "2", "3", "4"}), frozenset({"4", "5", "6", "7"}))


class DispatchError(RuntimeError):
    pass


def sha(path: Path) -> str:
    return runner.sha(path)


def read_json(path: Path) -> dict[str, Any]:
    return runner.read_json(path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    runner.atomic_json(path, value)


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"schema": EVENT_SCHEMA, "at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), **event}
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def promote_staged(staged: Path, destination: Path) -> None:
    """Idempotently finish a two-artifact audit commit after a process stop."""
    if not staged.is_file():
        raise DispatchError(f"staged audit artifact is missing: {staged}")
    if destination.exists():
        if not destination.is_file() or sha(destination) != sha(staged):
            raise DispatchError(f"existing final artifact differs from staged output: {destination}")
        staged.unlink()
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged.replace(destination)


def _flatten(value: Any, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    result: dict[tuple[str, ...], Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            result.update(_flatten(child, path + (str(key),)))
    elif isinstance(value, list):
        result[path] = value
    else:
        result[path] = value
    return result


def validate_successor_manifest(baseline: dict[str, Any], successor: dict[str, Any],
                                baseline_sha256: str | None = None,
                                baseline_protocol_sha256: str | None = None) -> None:
    """Require byte-value equality of all scientific fields outside the small identity whitelist."""
    if successor.get("sampling_authorized") is not True or \
            successor.get("status") != "APPROVED_INDEPENDENT_CONFIRMATION" or \
            successor.get("confirmation_registration", {}).get("status") != "APPROVED_INDEPENDENT_CONFIRMATION":
        raise DispatchError("successor registration is not sampling-authorized")
    old = _flatten(baseline)
    new = _flatten(successor)
    all_paths = set(old) | set(new)
    differences = {path for path in all_paths if old.get(path, object()) != new.get(path, object())}
    rejected = [path for path in differences if path not in ALLOWED_MANIFEST_DIFFS and
                not any(path[:len(allowed)] == allowed and allowed == ("migration_registration",)
                        for allowed in ALLOWED_MANIFEST_DIFFS)]
    if rejected:
        labels = [".".join(path) for path in sorted(rejected)]
        raise DispatchError(f"successor changed non-whitelisted manifest fields: {labels[:8]}")
    migration = successor.get("migration_registration")
    expected_predecessor = baseline_sha256 or baseline.get("original_preregistration_sha256")
    if not isinstance(migration, dict) or migration.get("successor_of_manifest_sha256") != expected_predecessor:
        raise DispatchError("migration registration does not identify the approved predecessor")
    expected_protocol = baseline_protocol_sha256 or migration.get("successor_of_protocol_sha256")
    if migration.get("successor_of_protocol_sha256") != expected_protocol:
        raise DispatchError("migration registration does not identify the predecessor protocol")
    if migration.get("status") != "ROOT_APPROVED_SUCCESSOR_EXECUTION_REGISTRATION" or \
            migration.get("fresh_full_chains") != 160 or \
            migration.get("predecessor_confirmation_statistical_analysis_performed") is not False:
        raise DispatchError("successor registration is not approved for a fresh 160-chain dataset")
    old_ref = baseline.get("confirmation_registration", {}).get("analysis_software_identity", {}).get("reference_binary", {})
    new_ref = successor.get("confirmation_registration", {}).get("analysis_software_identity", {}).get("reference_binary", {})
    if old_ref.get("sha256") == new_ref.get("sha256") or not new_ref.get("path") or not new_ref.get("sha256"):
        raise DispatchError("successor reference binary identity was not replaced")


def normalize_proof_groups(devices_by_proof: Iterable[Iterable[str]]) -> list[list[str]]:
    groups = [[str(x) for x in group] for group in devices_by_proof]
    group_sets = [frozenset(group) for group in groups]
    if len(groups) != 2 or any(len(group) != 4 for group in groups) or \
            set(group_sets) != set(PROOF_GROUP_SETS):
        raise DispatchError("proof coverage must be groups {1,2,3,4} and {4,5,6,7}; order is free")
    return groups


def dispatch_allowed(stop_file: Path, failure_seen: bool, signal_seen: bool) -> bool:
    return not stop_file.exists() and not failure_seen and not signal_seen


def validate_frozen_registration(manifest_path: Path, protocol_path: Path, freeze_path: Path,
                                 static_ledger_path: Path) -> tuple[dict, dict, dict]:
    manifest, freeze, static = read_json(manifest_path), read_json(freeze_path), read_json(static_ledger_path)
    runner.approved_gate(manifest, protocol_path.read_text(), freeze, freeze_path)
    runner.validate_frozen_identities(freeze)
    registry = freeze.get("registry", {})
    for name, path in (("manifest", manifest_path), ("protocol", protocol_path)):
        binding = registry.get(name, {})
        if binding.get("path") != str(path.resolve()) or binding.get("sha256") != sha(path):
            raise DispatchError(f"successor freeze does not bind {name} path/hash")
    plan = freeze.get("plan", {})
    if not Path(plan.get("path", "")).is_file() or sha(Path(plan["path"])) != plan.get("sha256"):
        raise DispatchError("successor dry-run plan path/hash mismatch")
    ledger_binding = freeze.get("ledger", {})
    if ledger_binding.get("path") != str(static_ledger_path.resolve()) or \
            ledger_binding.get("sha256") != sha(static_ledger_path):
        raise DispatchError("successor static ledger path/hash mismatch")
    registered = runner.registered_jobs(manifest)
    frozen = {j.get("key"): j for j in freeze.get("jobs", [])}
    static_jobs = {j.get("key"): j for j in static.get("jobs", [])}
    if len(registered) != 160 or set(frozen) != set(registered) or frozen != static_jobs:
        raise DispatchError("successor freeze/static ledger do not bind exactly 160 original job keys")
    if any(j.get("status") != "planned" for j in static.get("jobs", [])):
        raise DispatchError("successor static ledger must begin with all 160 jobs planned")
    return manifest, freeze, static


def _load_frozen_migration_binding(freeze: dict) -> tuple[dict[str, Any], Path]:
    entry = freeze.get("external_evidence", {}).get("migration_runtime_binding", {})
    path = Path(entry.get("path", "")).resolve()
    if not path.is_file() or sha(path) != entry.get("sha256"):
        raise DispatchError("freeze does not bind the migration runtime evidence path/hash")
    binding = read_json(path)
    if binding.get("schema") != MIGRATION_BINDING_SCHEMA or binding.get("status") != "PASS_ROOT_REVIEWED":
        raise DispatchError("migration runtime evidence is not reviewed PASS")
    if binding.get("hostname") != socket.gethostname() or binding.get("driver_version") != "570.86.10":
        raise DispatchError("migration runtime evidence does not match current host/driver")
    for field in ("canonical_env_script", "local_package_manifest"):
        identity = binding.get(field, {})
        target = Path(identity.get("path", "")).resolve()
        if not target.is_file() or sha(target) != identity.get("sha256"):
            raise DispatchError(f"migration {field} path/hash mismatch")
    env_values = binding.get("environment_variables", {})
    if set(ENV_KEYS) - set(env_values) or any(os.environ.get(k) != v for k, v in env_values.items()):
        raise DispatchError("current environment variables differ from frozen migration evidence")
    if env_values.get("UCX_TLS") != "self,sm,tcp,cuda_copy,cuda_ipc":
        raise DispatchError("migration evidence does not bind approved UCX_TLS transport set")
    libraries = binding.get("critical_libraries")
    if not isinstance(libraries, list) or not libraries:
        raise DispatchError("migration evidence has no critical MPI/UCX/PMIx library identities")
    for item in libraries:
        target = Path(item.get("path", "")).resolve()
        if not target.is_file() or sha(target) != item.get("sha256"):
            raise DispatchError(f"critical runtime library changed: {target}")
    candidate_sha = binding.get("candidate_sha256")
    ref_sha = binding.get("reference_sha256")
    groups: list[list[str]] = []
    native_proofs = binding.get("native_gpu_proofs", [])
    if not isinstance(native_proofs, list) or len(native_proofs) != 2:
        raise DispatchError("migration runtime evidence must contain exactly two native GPU proofs")
    seen_groups = set()
    inventory = capture_environment_proof.gpu_inventory()
    for item in native_proofs:
        proof_path = Path(item.get("path", "")).resolve()
        if not proof_path.is_file() or sha(proof_path) != item.get("sha256"):
            raise DispatchError("native post-reboot GPU proof path/hash mismatch")
        proof = read_json(proof_path)
        devices = [str(x) for x in item.get("devices", [])]
        group = frozenset(devices)
        if group not in PROOF_GROUP_SETS or group in seen_groups:
            raise DispatchError("native GPU proof groups must be exactly {1,2,3,4} and {4,5,6,7}")
        seen_groups.add(group)
        if proof.get("schema") != "dmgmd.npt-b3-environment-proof.v1" or proof.get("status") != "PASS" or \
                proof.get("candidate_sha256") != candidate_sha or proof.get("physical_devices") is None or \
                set(str(x) for x in proof.get("physical_devices", [])) != group or \
                proof.get("environment_fingerprint") != capture_environment_proof.environment_fingerprint():
            raise DispatchError("native proof content/environment fingerprint mismatch")
        expected_gpu = {d: inventory.get(d) for d in devices}
        if proof.get("gpu_identity") != expected_gpu or proof.get("gate_ranks") != 4:
            raise DispatchError("current GPU UUID/driver identity differs from native proof")
        if proof.get("supported_runtime_backends") != ["HostStaged", "CudaAware"]:
            raise DispatchError("native proof does not cover the frozen formal runtime backends")
        if proof.get("mpiexec_path") != binding.get("mpiexec_path"):
            raise DispatchError("native proof mpiexec differs from migration environment binding")
        frozen_native = [entry for entry in freeze.get("external_evidence", {}).values()
                         if isinstance(entry, dict) and
                         Path(entry.get("path", "")).resolve() == proof_path and
                         entry.get("sha256") == sha(proof_path)]
        if len(frozen_native) != 1:
            raise DispatchError("native proof must also be directly frozen for the original runner adapter")
        groups.append(devices)
    if seen_groups != set(PROOF_GROUP_SETS):
        raise DispatchError("native proof group coverage is incomplete")
    groups = normalize_proof_groups([item["devices"] for item in native_proofs])
    if candidate_sha != read_json(Path(freeze["registry"]["manifest"]["path"]))[
            "confirmation_registration"]["analysis_software_identity"]["candidate_binary"]["sha256"]:
        raise DispatchError("migration evidence candidate SHA differs from successor manifest")
    ref_binary = read_json(Path(freeze["registry"]["manifest"]["path"]))[
        "confirmation_registration"]["analysis_software_identity"]["reference_binary"]
    if ref_sha != ref_binary.get("sha256"):
        raise DispatchError("migration evidence reference SHA differs from successor manifest")
    return {**binding, "proof_groups": groups}, path


def choose_devices(ranks: int, free_ids: Iterable[str], proof_groups: list[list[str]],
                   load_counts: dict[str, int] | None = None) -> list[str] | None:
    """Choose a runner-compatible four-ID ordering; only its first `ranks` IDs are leased."""
    free = set(map(str, free_ids))
    loads = load_counts or {}
    candidates = []
    for group in proof_groups:
        group = [str(x) for x in group]
        if ranks == 4 and frozenset(group) != frozenset({"4", "5", "6", "7"}):
            continue
        eligible = sorted(free.intersection(group), key=lambda d: (loads.get(d, 0), int(d)))
        for used in itertools.combinations(eligible, ranks):
            remainder = [d for d in group if d not in used]
            ordering = [*used, *remainder]
            score = (sum(loads.get(d, 0) for d in used), tuple(int(d) for d in used), tuple(int(d) for d in group))
            candidates.append((score, ordering))
    if not candidates:
        return None
    return min(candidates, key=lambda x: x[0])[1]


def validate_queue(ledger: dict, static: dict) -> tuple[list[dict], list[dict]]:
    jobs, initial = ledger.get("jobs"), static.get("jobs")
    if not isinstance(jobs, list) or not isinstance(initial, list) or len(jobs) != 160 or len(initial) != 160:
        raise DispatchError("runtime/static ledgers must each contain exactly 160 jobs")
    current = {x.get("key"): x for x in jobs}
    frozen = {x.get("key"): x for x in initial}
    if len(current) != 160 or current.keys() != frozen.keys():
        raise DispatchError("runtime ledger keys differ from frozen static keys")
    invalid = {x.get("status") for x in jobs} - {"planned", "completed"}
    if invalid:
        raise DispatchError(f"runtime ledger has fail-closed statuses: {sorted(invalid)}")
    planned = []
    for key, row in current.items():
        if row.get("status") == "planned":
            if any(row.get(k) != frozen[key].get(k) for k in frozen[key]):
                raise DispatchError(f"planned runtime job changed from static freeze: {key}")
            planned.append(row)
        else:
            if any(row.get(k) != frozen[key].get(k) for k in frozen[key] if k != "status"):
                raise DispatchError(f"completed runtime job changed frozen fields: {key}")
            if row.get("result_file") is None or row.get("execution_freeze_sha256") is None:
                raise DispatchError(f"completed runtime row lacks completion evidence: {key}")
            if row.get("result_file") != frozen[key].get("result_file"):
                raise DispatchError(f"completed result path differs from its frozen path: {key}")
    return sorted(planned, key=lambda x: x["key"]), [current[k] for k in sorted(current) if current[k]["status"] == "completed"]


def revalidate_completed_queue(manifest: dict, freeze_path: Path, static: dict,
                               completed: list[dict]) -> None:
    """Check completed records and full-chain row counts without reading scientific values."""
    expected_inputs = {x["key"]: x["input_sha256"] for x in static["jobs"]}
    expected_steps = {x["key"]: x["actual_steps"] for x in static["jobs"]}
    expected_engine = {x["key"]: x["engine"] for x in static["jobs"]}
    identities = manifest["confirmation_registration"]["analysis_software_identity"]
    for row in completed:
        result_path = Path(row["result_file"])
        if not result_path.is_file() or sha(result_path) != row.get("result_file_sha256"):
            raise DispatchError(f"previously completed result changed: {row['key']}")
        payload = read_json(result_path)
        raw = [x for x in payload.get("records", []) if x.get("label") == "run"]
        if len(raw) != 1:
            raise DispatchError(f"completed result must contain exactly one run record: {row['key']}")
        record = raw[0]
        if record.get("job_key") != row["key"] or record.get("execution_freeze_sha256") != sha(freeze_path) or \
                record.get("input_sha256") != expected_inputs[row["key"]] or \
                record.get("steps") != expected_steps[row["key"]]:
            raise DispatchError(f"completed result is not bound to the frozen full chain: {row['key']}")
        executable = identities["candidate_binary" if expected_engine[row["key"]] == "candidate" else "reference_binary"]
        if record.get("executable_sha256") != executable["sha256"]:
            raise DispatchError(f"completed result executable identity changed: {row['key']}")
        thermo = Path(record.get("thermo_path", ""))
        if not thermo.is_file() or sha(thermo) != row.get("thermo_sha256") or \
                runner.numeric_thermo_rows(thermo) != expected_steps[row["key"]]:
            raise DispatchError(f"completed full-chain thermo changed or is incomplete: {row['key']}")


def build_runner_command(*, python: str, script: Path, manifest: Path, protocol: Path, freeze: Path,
                         static_ledger: Path, execution_ledger: Path, reference_registry: Path,
                         job_key: str, devices: list[str], mpiexec: str, timeout: int) -> list[str]:
    return [python, str(script), "run-one", "--manifest", str(manifest), "--protocol", str(protocol),
            "--freeze", str(freeze), "--static-ledger", str(static_ledger),
            "--execution-ledger", str(execution_ledger), "--reference-launch-registry", str(reference_registry),
            "--job-key", job_key, "--devices", ",".join(devices), "--mpiexec", mpiexec,
            "--timeout", str(timeout)]


def audit_complete_queue(manifest: dict, freeze_path: Path, static: dict, ledger: dict,
                         migration_binding: dict, audit_path: Path) -> dict[str, Any]:
    planned, completed = validate_queue(ledger, static)
    if planned or len(completed) != 160:
        raise DispatchError("all 160 successor jobs must be complete before final assembly")
    freeze_sha = sha(freeze_path)
    records = []
    expected_input_by_key = {x["key"]: x["input_sha256"] for x in static["jobs"]}
    proof_by_group = {}
    for item in migration_binding["native_gpu_proofs"]:
        proof = read_json(Path(item["path"]))
        proof_by_group[frozenset(str(x) for x in proof["physical_devices"])] = (proof, item["sha256"])
    for row in completed:
        result_path = Path(row["result_file"])
        if not result_path.is_file() or sha(result_path) != row["result_file_sha256"]:
            raise DispatchError(f"completed results hash mismatch: {row['key']}")
        payload = read_json(result_path)
        runs = [x for x in payload.get("records", []) if x.get("label") == "run"]
        if len(runs) != 1:
            raise DispatchError(f"expected one raw runner record: {row['key']}")
        record = runs[0]
        if record.get("job_key") != row["key"] or record.get("execution_freeze_sha256") != freeze_sha or \
                record.get("input_sha256") != expected_input_by_key[row["key"]]:
            raise DispatchError(f"result record/freeze/input identity mismatch: {row['key']}")
        engine = next(x["engine"] for x in static["jobs"] if x["key"] == row["key"])
        static_job = next(x for x in static["jobs"] if x["key"] == row["key"])
        if any(record.get(record_field) != static_job.get(job_field) for record_field, job_field in
               (("cohort", "cohort"), ("engine", "engine"), ("seed", "seed"),
                ("ranks", "ranks"), ("backend", "backend"), ("steps", "actual_steps"))):
            raise DispatchError(f"raw record metadata differs from frozen job: {row['key']}")
        executable = manifest["confirmation_registration"]["analysis_software_identity"][
            "candidate_binary" if engine == "candidate" else "reference_binary"]
        if record.get("executable_sha256") != executable.get("sha256"):
            raise DispatchError(f"raw record executable differs from successor identity: {row['key']}")
        if row.get("execution_freeze_sha256") != freeze_sha or row.get("thermo_sha256") != record.get("thermo_sha256"):
            raise DispatchError(f"ledger/result identity mismatch: {row['key']}")
        thermo = Path(record.get("thermo_path", ""))
        steps = next(x["actual_steps"] for x in static["jobs"] if x["key"] == row["key"])
        if not thermo.is_file() or sha(thermo) != row["thermo_sha256"] or \
                runner.numeric_thermo_rows(thermo) != steps:
            raise DispatchError(f"thermo output is missing, changed, or not a full chain: {row['key']}")
        source = Path(static_job["input_source_dir"])
        for name, expected_sha in expected_input_by_key[row["key"]].items():
            source_file = source / name
            staged_file = Path(record.get("input_paths", {}).get(name, ""))
            if not source_file.is_file() or sha(source_file) != expected_sha or \
                    not staged_file.is_file() or sha(staged_file) != expected_sha:
                raise DispatchError(f"registered/staged input hash mismatch: {row['key']} {name}")
        proof_path = Path(record.get("environment_proof_path", "")).resolve()
        if not proof_path.is_file() or sha(proof_path) != record.get("environment_proof_sha256"):
            raise DispatchError(f"raw runner record environment proof mismatch: {row['key']}")
        proof = read_json(proof_path)
        used = [str(x) for x in row.get("gpu_ids", [])]
        group = frozenset(str(x) for x in proof.get("physical_devices", []))
        expected_proof = proof_by_group.get(group)
        ranks = next(int(x["ranks"]) for x in static["jobs"] if x["key"] == row["key"])
        if group not in PROOF_GROUP_SETS or expected_proof is None or \
                record.get("environment_proof_sha256") != expected_proof[1] or \
                record.get("cuda_visible_devices") != ",".join(used) or \
                len(used) != ranks or len(set(used)) != ranks or any(x not in expected_proof[0]["physical_devices"] for x in used):
            raise DispatchError(f"GPU assignment is not covered by a frozen migration proof: {row['key']}")
        records.append({"job_key": row["key"], "gpu_ids": used,
                        "environment_proof_path": str(proof_path),
                        "environment_proof_sha256": sha(proof_path),
                        "proof_group": sorted(group), "thermo_rows": steps,
                        "result_file_sha256": row["result_file_sha256"],
                        "thermo_sha256": row["thermo_sha256"]})
    audit = {"schema": "dmgmd.npt-scr-successor-environment-audit.v1",
             "freeze_sha256": freeze_sha, "migration_binding_path": migration_binding["path"],
             "migration_binding_sha256": migration_binding["sha256"],
             "hostname": migration_binding["hostname"], "driver_version": migration_binding["driver_version"],
             "canonical_env_script": migration_binding["canonical_env_script"],
             "local_package_manifest": migration_binding["local_package_manifest"],
             "environment_variables": migration_binding["environment_variables"],
             "critical_libraries": migration_binding["critical_libraries"], "jobs": records}
    if audit_path.exists():
        raise DispatchError(f"refusing to overwrite environment audit: {audit_path}")
    atomic_json(audit_path, audit)
    return audit


def dispatch(args: argparse.Namespace) -> int:
    manifest_path, protocol_path = args.manifest.resolve(), args.protocol.resolve()
    freeze_path, static_path = args.freeze.resolve(), args.static_ledger.resolve()
    manifest, freeze, static = validate_frozen_registration(manifest_path, protocol_path, freeze_path, static_path)
    validate_successor_manifest(read_json(args.baseline_manifest), manifest, sha(args.baseline_manifest),
                                sha(args.baseline_protocol))
    migration_binding, migration_binding_path = _load_frozen_migration_binding(freeze)
    migration_binding["path"] = str(migration_binding_path)
    migration_binding["sha256"] = sha(migration_binding_path)
    registry = Path(freeze.get("reference_launch_registry", {}).get("path", "")).resolve()
    if not registry.is_absolute():
        raise DispatchError("freeze lacks absolute reference launch registry path")
    execution_path, log_dir = args.execution_ledger.resolve(), args.log_dir.resolve()
    event_path = args.events.resolve()
    lock_path = execution_path.with_suffix(execution_path.suffix + ".dispatch.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_stream = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DispatchError("another successor dispatcher holds the queue lock") from exc
        if not execution_path.exists():
            atomic_json(execution_path, static)
        ledger = read_json(execution_path)
        planned, completed = validate_queue(ledger, static)
        revalidate_completed_queue(manifest, freeze_path, static, completed)
        log_dir.mkdir(parents=True, exist_ok=True)
        append_event(event_path, {"event": "dispatch-started", "freeze_sha256": sha(freeze_path),
                                  "planned": len(planned), "already_completed": len(completed)})
        if not planned:
            _assemble_and_audit(args, manifest, freeze, static, ledger, migration_binding)
            return 0

        all_gpu_ids = sorted({d for group in migration_binding["proof_groups"] for d in group}, key=int)
        running: dict[int, dict[str, Any]] = {}
        used: set[str] = set()
        launch_counts = {d: 0 for d in all_gpu_ids}
        queue = sorted(planned, key=lambda x: (-int(x["ranks"]), x["key"]))
        stop_dispatch = False
        failure: str | None = None
        original_handlers = {}
        stop_signal = {"value": None}
        def request_stop(signum, _frame):
            stop_signal["value"] = signum
        for sig in (signal.SIGINT, signal.SIGTERM):
            original_handlers[sig] = signal.signal(sig, request_stop)
        try:
            while queue or running:
                stop_requested = not dispatch_allowed(args.stop_file, failure is not None,
                                                      stop_signal["value"] is not None)
                if stop_requested:
                    stop_dispatch = True
                launched = False
                if not stop_dispatch and failure is None:
                    for job in list(queue):
                        ranks = int(job["ranks"])
                        devices = choose_devices(ranks, set(all_gpu_ids) - used,
                                                 migration_binding["proof_groups"], launch_counts)
                        if devices is None:
                            continue
                        job_key = job["key"]
                        used_ids = devices[:ranks]
                        stem = hashlib.sha256(job_key.encode()).hexdigest()[:16]
                        stdout_path, stderr_path = log_dir / f"{stem}.stdout.log", log_dir / f"{stem}.stderr.log"
                        if stdout_path.exists() or stderr_path.exists():
                            raise DispatchError(f"per-job dispatcher logs already exist: {job_key}")
                        command = build_runner_command(python=sys.executable,
                            script=HERE / "run_scr_independent_confirmation.py", manifest=manifest_path,
                            protocol=protocol_path, freeze=freeze_path, static_ledger=static_path,
                            execution_ledger=execution_path, reference_registry=registry,
                            job_key=job_key, devices=devices, mpiexec=migration_binding["mpiexec_path"],
                            timeout=args.timeout)
                        out = stdout_path.open("wb")
                        err = stderr_path.open("wb")
                        try:
                            proc = subprocess.Popen(command, cwd=ROOT, env=os.environ.copy(),
                                                    stdout=out, stderr=err, start_new_session=True)
                        except BaseException:
                            out.close()
                            err.close()
                            raise
                        running[proc.pid] = {"process": proc, "job": job, "devices": used_ids,
                                             "stdout": out, "stderr": err,
                                             "stdout_path": str(stdout_path), "stderr_path": str(stderr_path)}
                        used.update(used_ids)
                        for device in used_ids:
                            launch_counts[device] += 1
                        queue.remove(job)
                        append_event(event_path, {"event": "runner-started", "job_key": job_key,
                            "pid": proc.pid, "ranks": ranks, "runner_devices": devices,
                            "leased_devices": used_ids, "stdout": str(stdout_path), "stderr": str(stderr_path)})
                        launched = True
                    if queue and not running and not launched:
                        raise DispatchError("no planned job can be placed on the currently available proof groups")
                finished = []
                for pid, item in list(running.items()):
                    rc = item["process"].poll()
                    if rc is None:
                        continue
                    item["stdout"].close()
                    item["stderr"].close()
                    used.difference_update(item["devices"])
                    finished.append(pid)
                    append_event(event_path, {"event": "runner-finished", "job_key": item["job"]["key"],
                                              "pid": pid, "returncode": rc})
                    if rc != 0 and failure is None:
                        failure = f"runner exited {rc} for {item['job']['key']}"
                        stop_dispatch = True
                for pid in finished:
                    del running[pid]
                if (not launched and not finished) or running:
                    time.sleep(.1)
                if stop_dispatch and not running:
                    break
        except BaseException as exc:
            failure = failure or f"dispatcher exception: {type(exc).__name__}: {exc}"
            stop_dispatch = True
            while running:
                for pid, item in list(running.items()):
                    rc = item["process"].poll()
                    if rc is None:
                        continue
                    item["stdout"].close()
                    item["stderr"].close()
                    used.difference_update(item["devices"])
                    append_event(event_path, {"event": "runner-finished-after-dispatch-error",
                        "job_key": item["job"]["key"], "pid": pid, "returncode": rc})
                    del running[pid]
                if running:
                    time.sleep(.1)
        finally:
            for sig, handler in original_handlers.items():
                signal.signal(sig, handler)
        if stop_dispatch or failure:
            append_event(event_path, {"event": "dispatch-stopped", "reason": failure or
                (f"signal-{stop_signal['value']}" if stop_signal["value"] else "stop-file"),
                "remaining_planned": len(queue)})
            return 1 if failure else 0
        ledger = read_json(execution_path)
        _assemble_and_audit(args, manifest, freeze, static, ledger, migration_binding)
        append_event(event_path, {"event": "dispatch-complete", "job_count": 160})
        return 0
    finally:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        lock_stream.close()


def _assemble_and_audit(args, manifest: dict, freeze: dict, static: dict, ledger: dict,
                        migration_binding: dict) -> None:
    execution_path = args.execution_ledger.resolve()
    records_path, audit_path = args.records.resolve(), args.environment_audit.resolve()
    suffix = f"{os.getpid()}-{time.time_ns()}"
    staged_records = records_path.with_name(f".{records_path.name}.{suffix}.staged")
    staged_audit = audit_path.with_name(f".{audit_path.name}.{suffix}.staged")
    audit_complete_queue(manifest, args.freeze.resolve(), static, ledger, migration_binding, staged_audit)
    runner.assemble_records(argparse.Namespace(manifest=args.manifest.resolve(),
        protocol=args.protocol.resolve(), execution_ledger=execution_path, output=staged_records))
    promote_staged(staged_audit, audit_path)
    promote_staged(staged_records, records_path)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-manifest", type=Path, required=True)
    p.add_argument("--baseline-protocol", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--protocol", type=Path, required=True)
    p.add_argument("--freeze", type=Path, required=True)
    p.add_argument("--static-ledger", type=Path, required=True)
    p.add_argument("--execution-ledger", type=Path, required=True)
    p.add_argument("--log-dir", type=Path, required=True)
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--records", type=Path, required=True)
    p.add_argument("--environment-audit", type=Path, required=True)
    p.add_argument("--stop-file", type=Path, required=True)
    p.add_argument("--timeout", type=int, default=3600)
    args = p.parse_args()
    try:
        return dispatch(args)
    except (DispatchError, runner.RunnerError, OSError, KeyError, TypeError, ValueError,
            json.JSONDecodeError, RuntimeError) as exc:
        print(f"successor SCR dispatcher rejected/failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
