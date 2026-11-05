#!/usr/bin/env python3
"""Execute one frozen independent SCR job, or assemble its immutable records.

The execution path is intentionally fail-closed: a PREFLIGHT_ONLY freeze or a
non-approved registration is rejected before result directories, staging, or
subprocesses are created. This script never changes scientific parameters.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import capture_environment_proof
import run_npt_ber
import run_npt_scr

UNITS = {"temperature": "K", "pressure": "GPa", "volume": "angstrom^3",
         "kinetic_energy": "eV"}
FREEZE_SCHEMA = "dmgmd.npt-scr-independent-confirmation-execution-freeze.v1"
INPUT_SCHEMA = "dmgmd.npt-scr-independent-confirmation-input.v1"
INPUT_NAMES = ("model.xyz", "run.in", "nep.txt")


class RunnerError(RuntimeError):
    pass


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RunnerError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def registered_jobs(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reg = manifest["confirmation_registration"]
    jobs: dict[str, dict[str, Any]] = {}
    for profile in reg["primary_profiles"]:
        cohort = profile["cohort"]
        short_profile = "base" if cohort == "primary_base" else "half"
        steps = int(profile["steps"])
        for engine in ("candidate", "reference"):
            for seed in profile["seed_set"]:
                key = f"{cohort}|{engine}|P1|HostStaged|seed{seed}|steps{steps}"
                jobs[key] = {"key": key, "cohort": cohort, "profile": short_profile,
                             "engine": engine, "seed": int(seed), "steps": steps,
                             "dt_fs": float(profile["dt_fs"]), "tdamp_steps": int(profile["tdamp_steps"]),
                             "tau_p_steps": int(profile["tau_p_steps"]), "ranks": 1,
                             "backend": "HostStaged"}
    bridge = reg["bridge_profile"]
    for condition in bridge["candidate_conditions"]:
        ranks, backend = int(condition["ranks"]), condition["backend"]
        for seed in bridge["seed_set"]:
            key = f"bridge_base|candidate|P{ranks}|{backend}|seed{seed}|steps{bridge['steps']}"
            jobs[key] = {"key": key, "cohort": "bridge_base", "profile": "base",
                         "engine": "candidate", "seed": int(seed), "steps": int(bridge["steps"]),
                         "dt_fs": float(bridge["dt_fs"]), "tdamp_steps": int(bridge["tdamp_steps"]),
                         "tau_p_steps": int(bridge["tau_p_steps"]), "ranks": ranks,
                         "backend": backend}
    expected = int(manifest["budget"]["total_independent_full_chains"])
    if len(jobs) != expected or expected != 160:
        raise RunnerError(f"registered job count mismatch: {len(jobs)} != {expected}")
    return jobs


def approved_gate(manifest: dict[str, Any], protocol_text: str,
                  freeze: dict[str, Any], freeze_path: Path) -> None:
    # This check deliberately precedes output path checks, directories, input staging,
    # or any external process creation.
    if manifest.get("sampling_authorized") is not True or \
            manifest.get("status") != "APPROVED_INDEPENDENT_CONFIRMATION" or \
            manifest.get("confirmation_registration", {}).get("status") != "APPROVED_INDEPENDENT_CONFIRMATION":
        raise RunnerError("registration is not approved; refusing before staging or subprocess")
    if not protocol_text.strip() or "APPROVED_INDEPENDENT_CONFIRMATION" not in protocol_text:
        raise RunnerError("protocol does not explicitly carry the approved status")
    if freeze.get("schema") != FREEZE_SCHEMA or \
            freeze.get("status") != "FROZEN_FOR_EXECUTION" or \
            freeze.get("sampling_authorized") is not True or \
            freeze.get("formal_execution_freeze") is not True:
        raise RunnerError("outer freeze is preflight-only or not formally authorized")
    registry = freeze.get("registry", {})
    for name, path_key in (("manifest", "manifest_path"), ("protocol", "protocol_path"),
                           ("bridge_rng_source_audit", "bridge_rng_source_audit_path")):
        value = registry.get(name, {})
        target = Path(value.get("path", path_key if path_key else "")).resolve()
        if not target.is_file() or sha(target) != value.get("sha256"):
            raise RunnerError(f"frozen {name} file/hash mismatch: {target}")
    freeze_sha = sha(freeze_path)
    if len(freeze_sha) != 64:
        raise RunnerError("cannot hash outer execution freeze")


def validate_frozen_identities(freeze: dict[str, Any]) -> None:
    """Recheck every path/hash leaf captured by the outer preflight."""
    checked: list[tuple[str, dict[str, Any]]] = []

    def walk(label: str, value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
                checked.append((label, value))
            else:
                for key, child in value.items():
                    walk(f"{label}.{key}" if label else key, child)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(f"{label}[{index}]", child)

    walk("identities", freeze.get("identities", {}))
    walk("external_evidence", freeze.get("external_evidence", {}))
    if not checked:
        raise RunnerError("outer freeze contains no verifiable source/binary identities")
    for label, item in checked:
        path = Path(item["path"]).resolve()
        if not path.is_file() or sha(path) != item["sha256"]:
            raise RunnerError(f"frozen identity changed: {label} ({path})")
    self_records = [item for label, item in checked if label.endswith("runner_source")]
    if len(self_records) != 1 or Path(self_records[0]["path"]).resolve() != Path(__file__).resolve():
        raise RunnerError("outer freeze does not bind this exact runner source")


def update_ledger(path: Path, key: str, updates: dict[str, Any], *, expected: str) -> dict[str, Any]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = read_json(path)
        matches = [job for job in data.get("jobs", []) if job.get("key") == key]
        if len(matches) != 1:
            raise RunnerError(f"ledger key missing or duplicated: {key}")
        job = matches[0]
        if job.get("status") != expected:
            raise RunnerError(f"job {key} is {job.get('status')}, expected {expected}; duplicate launch refused")
        job.update(updates)
        data["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        atomic_json(path, data)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return job


def numeric_thermo_rows(path: Path) -> int:
    count = 0
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) != 18:
            continue
        try:
            [float(value) for value in fields]
        except ValueError:
            continue
        count += 1
    return count


def validate_or_initialize_reference_registry(path: Path, freeze: dict[str, Any],
                                              jobs: dict[str, dict[str, Any]]) -> None:
    identity = freeze.get("reference_launch_registry", {})
    expected_path = Path(identity.get("path", "")).resolve()
    expected_sha = identity.get("initial_sha256")
    if not identity.get("path") or path.resolve() != expected_path or not isinstance(expected_sha, str):
        raise RunnerError("reference launch registry path/initial SHA is not freeze-bound")
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not path.exists():
            initial = {"schema": "dmgmd.npt-reference-launch-registry.v1", "reservations": []}
            encoded = json.dumps(initial, indent=2, sort_keys=True) + "\n"
            if hashlib.sha256(encoded.encode()).hexdigest() != expected_sha:
                raise RunnerError("canonical empty reference registry SHA differs from outer freeze")
            temp = path.with_name(path.name + f".{os.getpid()}.tmp")
            temp.write_text(encoded)
            temp.replace(path)
        data = read_json(path)
        if data.get("schema") != "dmgmd.npt-reference-launch-registry.v1" or \
                not isinstance(data.get("reservations"), list):
            raise RunnerError("reference launch registry schema mismatch")
        if not data["reservations"] and sha(path) != expected_sha:
            raise RunnerError("empty reference launch registry differs from freeze-bound initial bytes")
        seconds, labels = [], []
        registered_refs = {key for key, job in jobs.items() if job["engine"] == "reference"}
        for reservation in data["reservations"]:
            second, label = reservation.get("utc_second"), reservation.get("label")
            if not isinstance(second, str) or label not in registered_refs:
                raise RunnerError("reference launch registry has malformed or unregistered reservation")
            seconds.append(second)
            labels.append(label)
        if len(seconds) != len(set(seconds)) or len(labels) != len(set(labels)):
            raise RunnerError("reference launch registry contains duplicate seconds or jobs")
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def environment_proof(freeze: dict[str, Any], candidate_sha: str,
                      devices: list[str]) -> tuple[dict[str, Any], Path]:
    if len(devices) != 4 or len(set(devices)) != 4:
        raise RunnerError("each execution group must name four distinct physical GPU IDs")
    matches = []
    for item in freeze.get("external_evidence", {}).values():
        path = Path(item.get("path", ""))
        if not path.is_file() or sha(path) != item.get("sha256"):
            continue
        evidence = read_json(path)
        if evidence.get("schema") != "dmgmd.npt-b3-environment-proof.v1" or \
                evidence.get("status") != "PASS" or \
                evidence.get("candidate_sha256") != candidate_sha or \
                evidence.get("gate_ranks") != 4 or \
                evidence.get("supported_runtime_backends") != ["HostStaged", "CudaAware"]:
            continue
        if set(evidence.get("physical_devices", [])) == set(devices):
            matches.append((evidence, path))
    if len(matches) != 1:
        raise RunnerError(f"expected one frozen four-GPU proof for {devices}, found {len(matches)}")
    proof, path = matches[0]
    if proof.get("environment_fingerprint") != capture_environment_proof.environment_fingerprint():
        raise RunnerError("GPU/MPI environment fingerprint differs from frozen proof")
    current = capture_environment_proof.gpu_inventory()
    if proof.get("gpu_identity") != {device: current.get(device) for device in proof["physical_devices"]}:
        raise RunnerError("physical GPU UUID/driver identity differs from frozen proof")
    return proof, path


def run_one(args: argparse.Namespace) -> int:
    manifest_path, protocol_path = args.manifest.resolve(), args.protocol.resolve()
    freeze_path, static_ledger_path = args.freeze.resolve(), args.static_ledger.resolve()
    manifest, freeze = read_json(manifest_path), read_json(freeze_path)
    protocol_text = protocol_path.read_text()
    approved_gate(manifest, protocol_text, freeze, freeze_path)
    validate_frozen_identities(freeze)

    if freeze.get("registry", {}).get("manifest", {}).get("path") != str(manifest_path) or \
            freeze["registry"]["manifest"]["sha256"] != sha(manifest_path) or \
            freeze["registry"].get("protocol", {}).get("path") != str(protocol_path) or \
            freeze["registry"]["protocol"]["sha256"] != sha(protocol_path):
        raise RunnerError("manifest/protocol path or hash differs from frozen registry")
    if freeze.get("plan", {}).get("sha256") != sha(Path(freeze["plan"]["path"])) or \
            freeze.get("ledger", {}).get("path") != str(static_ledger_path) or \
            freeze.get("ledger", {}).get("sha256") != sha(static_ledger_path):
        raise RunnerError("frozen plan or initial ledger identity mismatch")
    static_ledger = read_json(static_ledger_path)
    jobs_by_key = registered_jobs(manifest)
    frozen_jobs = freeze.get("jobs", [])
    if len(frozen_jobs) != 160 or {x.get("key") for x in frozen_jobs} != set(jobs_by_key):
        raise RunnerError("outer freeze does not bind the exact 160 registered keys")
    frozen_by_key = {item["key"]: item for item in frozen_jobs}
    static_by_key = {item.get("key"): item for item in static_ledger.get("jobs", [])}
    if len(static_by_key) != 160 or static_by_key != frozen_by_key:
        raise RunnerError("frozen jobs differ from the immutable initial ledger")
    job_key = args.job_key
    if job_key not in jobs_by_key:
        raise RunnerError(f"unregistered job key: {job_key}")
    job = jobs_by_key[job_key]
    frozen = [x for x in frozen_jobs if x.get("key") == job_key]
    if len(frozen) != 1:
        raise RunnerError(f"frozen job key missing or duplicated: {job_key}")
    frozen = frozen[0]
    static_jobs = [x for x in static_ledger.get("jobs", []) if x.get("key") == job_key]
    if len(static_jobs) != 1 or static_jobs[0] != frozen:
        raise RunnerError("static ledger job differs from frozen job entry")

    candidate_info = manifest["confirmation_registration"]["analysis_software_identity"]["candidate_binary"]
    reference_info = manifest["confirmation_registration"]["analysis_software_identity"]["reference_binary"]
    candidate, reference = Path(candidate_info["path"]).resolve(), Path(reference_info["path"]).resolve()
    if sha(candidate) != candidate_info["sha256"] or sha(reference) != reference_info["sha256"]:
        raise RunnerError("candidate/reference executable identity mismatch")
    devices = args.devices.split(",")
    proof, proof_path = environment_proof(freeze, candidate_info["sha256"], devices)
    ranks = job["ranks"]
    if len(devices) < ranks:
        raise RunnerError("selected physical GPU group is smaller than job rank count")
    if not args.mpiexec or Path(args.mpiexec).resolve() != Path(proof["mpiexec_path"]).resolve():
        raise RunnerError("--mpiexec must match the frozen environment proof")
    validate_or_initialize_reference_registry(args.reference_launch_registry.resolve(), freeze,
                                              jobs_by_key)

    input_root = Path(freeze["input_root"]).resolve()
    inventory = freeze.get("input_inventory_sha256", {})
    if len(inventory) != 192:
        raise RunnerError("frozen inventory must have exactly 192 registered files")
    actual_inventory = {p.relative_to(input_root).as_posix(): sha(p)
                        for p in sorted(input_root.rglob("*")) if p.is_file()}
    if actual_inventory != inventory:
        raise RunnerError("registered input tree changed after outer freeze")
    source = (input_root / job["profile"] / str(job["steps"]) /
              f"seed-{job['seed']}").resolve()
    try:
        source.relative_to(input_root)
    except ValueError as error:
        raise RunnerError("canonical job input source escapes frozen input root") from error
    if source != Path(frozen["input_source_dir"]).resolve():
        raise RunnerError("frozen job input source differs from registered profile/seed")
    expected_inputs = frozen["input_sha256"]
    if set(expected_inputs) != set(INPUT_NAMES) or \
            {name: sha(source / name) for name in INPUT_NAMES} != expected_inputs:
        raise RunnerError("job input hashes differ from frozen inputs")

    output_root = Path(frozen["result_root"]).resolve()
    if str(output_root) != frozen["result_root"] or output_root.exists():
        raise RunnerError("job result path is noncanonical or already exists; refusing duplicate launch")
    execution_ledger = args.execution_ledger.resolve()
    if not execution_ledger.exists():
        # Copy the immutable approved initial ledger once, under a lock. Each
        # independent job then updates its own unique key atomically.
        lock_path = execution_ledger.with_suffix(execution_ledger.suffix + ".init.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if not execution_ledger.exists():
                atomic_json(execution_ledger, static_ledger)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    run_ledger = read_json(execution_ledger)
    if run_ledger.get("manifest_sha256") != sha(manifest_path) or \
            run_ledger.get("input_inventory_sha256") != inventory:
        raise RunnerError("mutable execution ledger is bound to different frozen inputs")
    ledger_job = [x for x in run_ledger.get("jobs", []) if x.get("key") == job_key]
    if len(ledger_job) != 1 or ledger_job[0].get("status") != "planned":
        raise RunnerError("job is not uniquely planned in the execution ledger")

    freeze_sha = sha(freeze_path)
    update_ledger(execution_ledger, job_key,
                  {"status": "running", "execution_freeze_sha256": freeze_sha,
                   "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "gpu_ids": devices[:ranks], "environment_proof_path": str(proof_path),
                   "environment_proof_sha256": sha(proof_path)}, expected="planned")

    profile = job["profile"]
    results_root = output_root.parent
    args_obj = argparse.Namespace(
        work_root=output_root, freeze=freeze_path, devices=args.devices,
        schedule=None, candidate=candidate, reference=reference,
        dt_fs=job["dt_fs"], tdamp_steps=job["tdamp_steps"], tau_p_steps=job["tau_p_steps"],
        reference_launch_registry=args.reference_launch_registry,
        timeout=args.timeout)
    # ScrRunner validates the entire frozen input inventory and writes the
    # normal raw run record; this adapter adds only registered metadata.
    bridge_freeze = {"generated_input_root": str(input_root),
                     "generated_inputs_sha256": inventory,
                     "candidate_sha256": candidate_info["sha256"],
                     "reference_sha256": reference_info["sha256"],
                     "manifest_sha256": sha(manifest_path),
                     "reference_status_before": run_npt_ber.reference_status_snapshot()}
    runner = run_npt_scr.ScrRunner(args_obj, manifest, bridge_freeze)
    runner.mpiexec = Path(args.mpiexec).resolve()
    # The shared UTC registry requires canonical registered job keys, so use
    # the job key as the stage label. It is also a valid single directory name.
    try:
        run_record = runner.run_one(job["engine"], profile, job["steps"], job["seed"],
                                    job["steps"], job["ranks"], job["backend"], job_key,
                                    ensemble_seed="registered" if job["engine"] == "candidate" else None)
    except BaseException as error:
        update_ledger(execution_ledger, job_key,
                      {"status": "failed", "ended_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "failure": f"{type(error).__name__}: {error}",
                       "execution_freeze_sha256": freeze_sha}, expected="running")
        raise
    try:
        if job["engine"] == "reference":
            actual_start = dt.datetime.fromisoformat(
                run_record["started_at_utc"].replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            if run_record.get("reserved_utc_second") != actual_start:
                raise RunnerError("GPUMD actual start second differs from its globally reserved UTC second")
        thermo_path = Path(run_record["thermo_path"])
        if numeric_thermo_rows(thermo_path) != job["steps"]:
            raise RunnerError(f"thermo rows do not equal the complete registered length: {job_key}")
        run_record.update({"label": "run", "job_key": job_key,
                           "cohort": job["cohort"], "dt_fs": job["dt_fs"],
                           "units": dict(UNITS), "executable_path": str(
                               (candidate if job["engine"] == "candidate" else reference).resolve()),
                           "execution_freeze_sha256": freeze_sha,
                           "environment_proof_path": str(proof_path),
                           "environment_proof_sha256": sha(proof_path)})
        if job["cohort"] == "bridge_base":
            audit = manifest["confirmation_registration"]["analysis_software_identity"]["bridge_rng_source_audit"]
            run_record["rng_draw_evidence"] = {"status": "PASS",
                                                  "source_audit_sha256": audit["sha256"]}
        runner.payload["execution_freeze_sha256"] = freeze_sha
        runner.save()
        result_file = output_root / "results.json"
        if str(result_file) != frozen["result_file"]:
            raise RunnerError("runner result path differs from frozen result_file")
        update_ledger(execution_ledger, job_key,
                      {"status": "completed", "ended_at_utc": run_record["ended_at_utc"],
                       "result_file": str(result_file), "result_file_sha256": sha(result_file),
                       "thermo_sha256": run_record["thermo_sha256"],
                       "execution_freeze_sha256": freeze_sha}, expected="running")
    except BaseException as error:
        current = read_json(execution_ledger)
        state = next((item.get("status") for item in current.get("jobs", [])
                      if item.get("key") == job_key), None)
        if state == "running":
            update_ledger(execution_ledger, job_key,
                          {"status": "failed", "ended_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                           "failure": f"{type(error).__name__}: {error}",
                           "execution_freeze_sha256": freeze_sha}, expected="running")
        raise
    print(json.dumps({"job_key": job_key, "status": "PASS", "result_file": str(result_file),
                      "result_file_sha256": sha(result_file), "thermo_sha256": run_record["thermo_sha256"],
                      "execution_freeze_sha256": freeze_sha}, indent=2))
    return 0


def assemble_records(args: argparse.Namespace) -> int:
    manifest_path, protocol_path = args.manifest.resolve(), args.protocol.resolve()
    manifest = read_json(manifest_path)
    jobs = registered_jobs(manifest)
    ledger = read_json(args.execution_ledger.resolve())
    if len(ledger.get("jobs", [])) != 160:
        raise RunnerError("execution ledger does not have exactly 160 jobs")
    records = []
    for key, job in sorted(jobs.items()):
        status = next((x for x in ledger["jobs"] if x.get("key") == key), None)
        if not status or status.get("status") != "completed":
            raise RunnerError(f"job is not complete: {key}")
        path = Path(status["result_file"])
        if sha(path) != status.get("result_file_sha256"):
            raise RunnerError(f"result file hash mismatch: {key}")
        payload = read_json(path)
        if payload.get("execution_freeze_sha256") != status.get("execution_freeze_sha256"):
            raise RunnerError(f"result/freeze identity mismatch: {key}")
        matches = [x for x in payload.get("records", []) if x.get("label") == "run"]
        if len(matches) != 1:
            raise RunnerError(f"expected one raw run record: {key}")
        record = matches[0]
        for field in ("cohort", "dt_fs", "units", "executable_path"):
            if field not in record:
                raise RunnerError(f"record missing {field}: {key}")
        records.append(record)
    ids = manifest["confirmation_registration"]["analysis_software_identity"]
    proof = ids["bridge_rng_source_audit"]
    bundle = {"schema": INPUT_SCHEMA,
              "manifest_sha256": sha(manifest_path),
              "protocol_sha256": sha(protocol_path),
              "candidate_executable_sha256": ids["candidate_binary"]["sha256"],
              "reference_executable_sha256": ids["reference_binary"]["sha256"],
              "candidate_source_digest_sha256": ids["candidate_source_digest"]["sha256"],
              "potential_sha256": manifest["source_contract"]["potential_sha256"],
              # The analyzer resolves this bundle path relative to the manifest
              # directory, so use the actual manifest-relative path here.
              "bridge_rng_source_audit": {"status": "PASS", "path": "bridge-rng-source-audit.json",
                                            "sha256": proof["sha256"]},
              "runs": records}
    output = args.output.resolve()
    if output.exists():
        raise RunnerError(f"refusing to overwrite records bundle: {output}")
    atomic_json(output, bundle)
    print(json.dumps({"records": str(output), "sha256": sha(output), "runs": len(records)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run-one")
    run.add_argument("--manifest", type=Path, default=HERE / "independent-confirmation-manifest.json")
    run.add_argument("--protocol", type=Path, default=HERE / "independent-confirmation-protocol.md")
    run.add_argument("--freeze", type=Path, required=True)
    run.add_argument("--static-ledger", type=Path, required=True)
    run.add_argument("--execution-ledger", type=Path, required=True)
    run.add_argument("--reference-launch-registry", type=Path, required=True)
    run.add_argument("--job-key", required=True)
    run.add_argument("--devices", required=True)
    run.add_argument("--mpiexec", required=True)
    run.add_argument("--timeout", type=int, default=3600)
    assemble = sub.add_parser("assemble-records")
    assemble.add_argument("--manifest", type=Path, default=HERE / "independent-confirmation-manifest.json")
    assemble.add_argument("--protocol", type=Path, default=HERE / "independent-confirmation-protocol.md")
    assemble.add_argument("--execution-ledger", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run_one(args) if args.command == "run-one" else assemble_records(args)
    except (RunnerError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"independent SCR runner rejected/failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
