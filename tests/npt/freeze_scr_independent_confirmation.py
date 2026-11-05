#!/usr/bin/env python3
"""Fail-closed outer freeze/preflight for independent SCR confirmation.

This program only inventories identities. It never launches MD or MPI jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLAN_SCHEMA = "dmgmd.npt-scr-independent-confirmation-dry-run.v1"
FREEZE_SCHEMA = "dmgmd.npt-scr-independent-confirmation-execution-freeze.v1"
INPUT_NAMES = ("model.xyz", "run.in", "nep.txt")


class FreezeError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FreezeError(f"expected JSON object: {path}")
    return value


def checked_file(path: Path, expected: str, label: str) -> dict[str, str]:
    if not path.is_file():
        raise FreezeError(f"missing {label}: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise FreezeError(f"{label} SHA256 mismatch: {path}: expected {expected}, got {actual}")
    return {"path": str(path.resolve()), "sha256": actual}


def canonical_registered_keys(manifest: dict[str, Any]) -> set[str]:
    reg = manifest["confirmation_registration"]
    keys: set[str] = set()
    for profile in reg["primary_profiles"]:
        cohort = profile["cohort"]
        for engine in ("candidate", "reference"):
            for seed in profile["seed_set"]:
                keys.add(f"{cohort}|{engine}|P1|HostStaged|seed{seed}|steps{profile['steps']}")
    bridge = reg["bridge_profile"]
    for condition in bridge["candidate_conditions"]:
        for seed in bridge["seed_set"]:
            keys.add(f"bridge_base|candidate|P{condition['ranks']}|{condition['backend']}|seed{seed}|steps{bridge['steps']}")
    return keys


def canonical_job_metadata(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reg = manifest["confirmation_registration"]
    expected: dict[str, dict[str, Any]] = {}
    for profile in reg["primary_profiles"]:
        cohort = profile["cohort"]
        short_profile = "base" if cohort == "primary_base" else "half"
        for engine in ("candidate", "reference"):
            for seed in profile["seed_set"]:
                key = f"{cohort}|{engine}|P1|HostStaged|seed{seed}|steps{profile['steps']}"
                expected[key] = {"cohort": cohort, "profile": short_profile, "engine": engine,
                                 "ranks": 1, "backend": "HostStaged", "seed": int(seed),
                                 "actual_steps": int(profile["steps"])}
    bridge = reg["bridge_profile"]
    for condition in bridge["candidate_conditions"]:
        for seed in bridge["seed_set"]:
            key = f"bridge_base|candidate|P{condition['ranks']}|{condition['backend']}|seed{seed}|steps{bridge['steps']}"
            expected[key] = {"cohort": "bridge_base", "profile": "base", "engine": "candidate",
                             "ranks": int(condition["ranks"]), "backend": condition["backend"],
                             "seed": int(seed), "actual_steps": int(bridge["steps"])}
    return expected


def validate_jobs(manifest: dict[str, Any], plan: dict[str, Any], ledger: dict[str, Any],
                  input_root: Path) -> list[dict[str, Any]]:
    jobs = plan.get("jobs")
    ledger_jobs = ledger.get("jobs")
    if not isinstance(jobs, list) or not isinstance(ledger_jobs, list):
        raise FreezeError("plan and ledger must contain jobs arrays")
    expected = canonical_registered_keys(manifest)
    expected_meta = canonical_job_metadata(manifest)
    keys = [j.get("key") for j in jobs if isinstance(j, dict)]
    if len(keys) != len(jobs) or len(keys) != len(set(keys)):
        raise FreezeError("plan contains malformed or duplicate job keys")
    if set(keys) != expected:
        missing, extra = sorted(expected - set(keys)), sorted(set(keys) - expected)
        raise FreezeError(f"registered job set mismatch; missing={missing[:3]}, extra={extra[:3]}")
    ledger_by_key = {j.get("key"): j for j in ledger_jobs if isinstance(j, dict)}
    if len(ledger_by_key) != len(ledger_jobs) or set(ledger_by_key) != expected:
        raise FreezeError("ledger job keys do not exactly match registered jobs")
    inventory = plan.get("input_inventory_sha256")
    if not isinstance(inventory, dict) or len(inventory) != 192:
        raise FreezeError("input inventory must contain exactly 192 file hashes")
    if plan.get("input_file_count") != 192:
        raise FreezeError("plan input_file_count must equal 192")
    if "fixtures" in manifest:
        expected_inventory_keys: set[str] = set()
        for profile in manifest["confirmation_registration"]["primary_profiles"]:
            profile_name = "base" if profile["cohort"] == "primary_base" else "half"
            for seed in profile["seed_set"]:
                for name in INPUT_NAMES:
                    expected_inventory_keys.add(f"{profile_name}/{profile['steps']}/seed-{seed}/{name}")
        if set(inventory) != expected_inventory_keys:
            raise FreezeError("input inventory paths differ from the registered seed/profile matrix")
        models = manifest["fixtures"]["statistics"]["model_xyz_sha256_by_seed"]
        potential_sha = manifest["source_contract"]["potential_sha256"]
        for profile in manifest["confirmation_registration"]["primary_profiles"]:
            profile_name = "base" if profile["cohort"] == "primary_base" else "half"
            for seed in profile["seed_set"]:
                rel = f"{profile_name}/{profile['steps']}/seed-{seed}"
                run = (input_root / rel / "run.in").read_text().splitlines()
                expected_run = [
                    "potential nep.txt",
                    f"time_step {profile['dt_fs']}",
                    f"ensemble npt_scr 300 300 {profile['tdamp_steps']} 0 425 {profile['tau_p_steps']}",
                    "dump_thermo 1",
                    f"run {profile['steps']}",
                ]
                if run != expected_run:
                    raise FreezeError(f"registered run.in contract mismatch: {rel}/run.in")
                if inventory[f"{rel}/model.xyz"] != models[str(seed)]:
                    raise FreezeError(f"initial model SHA differs from manifest seed fixture: {rel}/model.xyz")
                if inventory[f"{rel}/nep.txt"] != potential_sha:
                    raise FreezeError(f"potential SHA differs from manifest: {rel}/nep.txt")
    actual_input_files = {p.relative_to(input_root.resolve()).as_posix(): p
                          for p in input_root.resolve().rglob("*") if p.is_file()}
    if set(actual_input_files) != set(inventory):
        raise FreezeError("registered input tree contains missing or unregistered files")
    for relative, expected_sha in inventory.items():
        p = actual_input_files[relative]
        if not p.is_file() or sha256_file(p) != expected_sha:
            raise FreezeError(f"input inventory hash mismatch or missing input: {p}")

    normalized: list[dict[str, Any]] = []
    result_roots: set[str] = set()
    result_files: set[str] = set()
    for job in jobs:
        key = job["key"]
        expected_fields = expected_meta[key]
        if any(job.get(name) != value for name, value in expected_fields.items()):
            raise FreezeError(f"job metadata does not match registered key: {key}")
        if ledger_by_key[key] != job:
            raise FreezeError(f"ledger/plan job mismatch: {key}")
        if job.get("status") != "planned":
            raise FreezeError(f"non-planned job in pre-execution plan: {key}")
        source = Path(job.get("input_source_dir", "")).resolve()
        expected_source = (input_root / expected_fields["profile"] / str(expected_fields["actual_steps"])
                           / f"seed-{expected_fields['seed']}").resolve()
        if source != expected_source:
            raise FreezeError(f"job input source does not match registered profile/seed: {key}")
        try:
            rel_dir = source.relative_to(input_root.resolve()).as_posix()
        except ValueError as exc:
            raise FreezeError(f"job input source outside registered input root: {key}") from exc
        declared = job.get("input_sha256")
        if not isinstance(declared, dict) or set(declared) != set(INPUT_NAMES):
            raise FreezeError(f"job input_sha256 must bind all three files: {key}")
        for name in INPUT_NAMES:
            rel = f"{rel_dir}/{name}"
            expected_sha = inventory.get(rel)
            if expected_sha is None or declared[name] != expected_sha:
                raise FreezeError(f"job input hash differs from inventory: {key} {name}")
            if sha256_file(source / name) != expected_sha:
                raise FreezeError(f"job input changed after inventory: {source / name}")
        result_root = Path(job.get("result_root", ""))
        result_file = Path(job.get("result_file", ""))
        expected_result_root = (input_root.parent / "results" / key.replace("|", "__")).resolve()
        if not result_root.is_absolute() or result_root.resolve() != expected_result_root:
            raise FreezeError(f"job result_root differs from its registered unique path: {key}")
        if result_file != result_root / "results.json":
            raise FreezeError(f"result_file is not bound to result_root/results.json: {key}")
        root_key, file_key = str(result_root.resolve()), str(result_file.resolve())
        if root_key in result_roots or file_key in result_files:
            raise FreezeError(f"result paths are not unique: {key}")
        result_roots.add(root_key)
        result_files.add(file_key)
        if result_root.exists() or result_file.exists():
            raise FreezeError(f"refusing result path that already exists: {result_root}")
        normalized.append(job)
    return sorted(normalized, key=lambda item: item["key"])


def validate_registry(manifest_path: Path, protocol_path: Path, proof_path: Path,
                      plan: dict[str, Any], ledger: dict[str, Any],
                      input_root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    manifest = load_json(manifest_path)
    protocol = protocol_path.read_text()
    proof = load_json(proof_path)
    manifest_sha, protocol_sha, proof_sha = (sha256_file(manifest_path), sha256_file(protocol_path),
                                             sha256_file(proof_path))
    if manifest.get("schema") != "dmgmd.npt-scr-independent-confirmation.preregistration.v1":
        raise FreezeError("unexpected manifest schema")
    if plan.get("schema") != PLAN_SCHEMA:
        raise FreezeError("unexpected dry-run plan schema")
    if plan.get("manifest_sha256") != manifest_sha or ledger.get("manifest_sha256") != manifest_sha:
        raise FreezeError("plan/ledger do not bind the current manifest SHA")
    if plan.get("protocol_status") != manifest.get("confirmation_registration", {}).get("status"):
        raise FreezeError("plan protocol status differs from the registered manifest")
    if plan.get("sampling_authorized") is not manifest.get("sampling_authorized"):
        raise FreezeError("plan sampling authorization differs from manifest")
    if (plan.get("candidate_sha256") != ledger.get("candidate_sha256")
            or plan.get("reference_sha256") != ledger.get("reference_sha256")):
        raise FreezeError("ledger candidate/reference binary identity differs from plan")
    if ledger.get("sampling_authorized") is not manifest.get("sampling_authorized"):
        raise FreezeError("ledger sampling authorization differs from manifest")
    if ledger.get("input_inventory_sha256") != plan.get("input_inventory_sha256"):
        raise FreezeError("ledger input inventory differs from plan")
    if plan.get("input_root") != str(input_root.resolve()):
        raise FreezeError("plan input_root differs from supplied registered input root")
    ids = manifest["confirmation_registration"]["analysis_software_identity"]
    if ids["bridge_rng_source_audit"]["sha256"] != proof_sha:
        raise FreezeError("bridge RNG proof SHA differs from manifest")
    if proof.get("status") != "PASS_ROOT_REVIEWED":
        raise FreezeError("bridge RNG proof is not root-reviewed PASS")
    hashes = {
        "manifest": manifest_sha,
        "protocol": protocol_sha,
        "bridge_rng_source_audit": proof_sha,
    }
    if not protocol.strip():
        raise FreezeError("empty protocol")
    status = manifest.get("status")
    if status not in ("DRAFT_FOR_ROOT_REVIEW_NO_SAMPLING_AUTHORIZED",
                      "APPROVED_INDEPENDENT_CONFIRMATION"):
        raise FreezeError(f"unrecognized exact manifest status: {status!r}")
    if manifest.get("confirmation_registration", {}).get("status") != status:
        raise FreezeError("top-level and registration statuses must match exactly")
    expected_status = "DRAFT_FOR_ROOT_REVIEW" if status == "DRAFT_FOR_ROOT_REVIEW_NO_SAMPLING_AUTHORIZED" else "APPROVED_INDEPENDENT_CONFIRMATION"
    status_lines = [line for line in protocol.splitlines() if line.startswith("**状态：")]
    if len(status_lines) != 1 or expected_status not in status_lines[0]:
        raise FreezeError(f"protocol status line does not match registered state {expected_status}")
    if status == "APPROVED_INDEPENDENT_CONFIRMATION" and not status_lines[0].startswith("**状态：APPROVED_INDEPENDENT_CONFIRMATION。**"):
        raise FreezeError("approved protocol status line must use the exact approved enum")
    return manifest, hashes


def validate_identities(manifest: dict[str, Any], source_digest_path: Path,
                        freezer_path: Path, preparer_path: Path, runner_path: Path,
                        freezer_test_path: Path, cpu_log_path: Path,
                        adapter_test_path: Path, adapter_test_log: Path) -> dict[str, Any]:
    ids = manifest["confirmation_registration"]["analysis_software_identity"]
    checked: dict[str, Any] = {}
    for label in ("primary_analyzer", "shared_statistics_primitives", "native_acf_helper",
                  "paired_math_draft", "paired_math_tests_draft", "candidate_binary",
                  "reference_binary", "candidate_source_digest", "potential", "paired_cli",
                  "paired_integration_tests", "native_acf_helper_executable"):
        record = ids[label]
        path = Path(record["path"])
        if not path.is_absolute():
            path = ROOT / path
        checked[label] = checked_file(path, record["sha256"], label)
    checked["freezer_source"] = {"path": str(freezer_path.resolve()), "sha256": sha256_file(freezer_path)}
    preparer_sha = sha256_file(preparer_path)
    checked["preparer_source"] = {"path": str(preparer_path.resolve()), "sha256": preparer_sha}
    runner_sha = sha256_file(runner_path)
    checked["runner_source"] = {"path": str(runner_path.resolve()), "sha256": runner_sha}
    checked["freezer_test_source"] = {"path": str(freezer_test_path.resolve()),
                                      "sha256": sha256_file(freezer_test_path)}
    checked["adapter_test_source"] = {"path": str(adapter_test_path.resolve()),
                                      "sha256": sha256_file(adapter_test_path)}
    checked["adapter_cpu_test_log"] = {"path": str(adapter_test_log.resolve()),
                                        "sha256": sha256_file(adapter_test_log)}
    dependencies = {
        "generate_inputs.py": HERE / "generate_inputs.py",
        "run_npt_ber.py": HERE / "run_npt_ber.py",
        "run_npt_scr.py": HERE / "run_npt_scr.py",
        "capture_environment_proof.py": HERE / "capture_environment_proof.py",
        "check_environment.py": ROOT / "tests/mpi/check_environment.py",
        "run_baselines.py": ROOT / "tests/baseline/run_baselines.py",
    }
    for name, path in dependencies.items():
        checked[f"runner_dependency:{name}"] = {"path": str(path.resolve()), "sha256": sha256_file(path)}
    cpu = ids["cpu_validation"]
    checked["paired_cpu_test_log"] = checked_file(Path(cpu["log_path"]), cpu["log_sha256"], "paired CPU test log")
    checked["freezer_cpu_test_log"] = {"path": str(cpu_log_path.resolve()),
                                       "sha256": sha256_file(cpu_log_path)}
    checked["native_acf_source"] = checked_file(ROOT / ids["native_acf_helper"]["path"],
                                                 ids["native_acf_helper"]["sha256"], "native ACF source")
    if sha256_file(source_digest_path) != ids["candidate_source_digest"]["sha256"]:
        raise FreezeError("candidate source digest file hash mismatch")
    digest_entries: list[dict[str, str]] = []
    source_paths: set[str] = set()
    source_head: str | None = None
    binary_seen = False
    for line in source_digest_path.read_text().splitlines():
        if line.startswith("HEAD="):
            if source_head is not None or not re.fullmatch(r"HEAD=[0-9a-f]{40}", line):
                raise FreezeError("malformed or duplicate HEAD metadata in source digest")
            source_head = line.removeprefix("HEAD=")
            continue
        if line.startswith("BINARY="):
            if binary_seen:
                raise FreezeError("duplicate BINARY metadata in source digest")
            match = re.fullmatch(r"BINARY=([0-9a-f]{64})\s+(.+)", line)
            if not match:
                raise FreezeError("malformed BINARY metadata in source digest")
            binary_sha, binary_path = match.groups()
            candidate = ids["candidate_binary"]
            if binary_sha != candidate["sha256"] or Path(binary_path).resolve() != Path(candidate["path"]).resolve():
                raise FreezeError("source digest BINARY identity differs from manifest candidate")
            checked_file(Path(binary_path), binary_sha, "source digest candidate binary")
            binary_seen = True
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise FreezeError(f"unrecognized source digest line: {line!r}")
        digest, relative = parts
        target = (ROOT / relative).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise FreezeError(f"critical source path escapes repository: {relative}") from exc
        if relative in source_paths:
            raise FreezeError(f"duplicate critical source digest entry: {relative}")
        source_paths.add(relative)
        digest_entries.append(checked_file(target, digest, f"critical source {relative}"))
    if len(digest_entries) != 19:
        raise FreezeError(f"critical source digest must list 19 files, got {len(digest_entries)}")
    if source_head is None or not binary_seen:
        raise FreezeError("source digest is missing HEAD or BINARY identity")
    try:
        current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FreezeError(f"cannot verify source digest HEAD: {exc}") from exc
    if current_head != source_head:
        raise FreezeError(f"source digest HEAD differs from checkout: {source_head} != {current_head}")
    checked["source_digest_head"] = source_head
    checked["critical_19_sources"] = digest_entries
    return checked


def prepare_output(args: argparse.Namespace, *, formal: bool) -> dict[str, Any]:
    manifest_path = args.manifest.resolve()
    protocol_path = args.protocol.resolve()
    proof_path = args.rng_proof.resolve()
    plan_path = args.plan.resolve()
    ledger_path = args.ledger.resolve()
    input_root = args.input_root.resolve()
    plan, ledger = load_json(plan_path), load_json(ledger_path)
    manifest, registry_hashes = validate_registry(manifest_path, protocol_path, proof_path,
                                                  plan, ledger, input_root)
    if formal:
        if (manifest.get("status") != "APPROVED_INDEPENDENT_CONFIRMATION"
                or manifest.get("sampling_authorized") is not True
                or manifest.get("confirmation_registration", {}).get("status") != "APPROVED_INDEPENDENT_CONFIRMATION"):
            raise FreezeError("formal freeze requires exact APPROVED_INDEPENDENT_CONFIRMATION statuses and sampling_authorized is True")
    elif (manifest.get("status") != "DRAFT_FOR_ROOT_REVIEW_NO_SAMPLING_AUTHORIZED"
          or manifest.get("sampling_authorized") is not False):
        raise FreezeError("dry-run requires exact draft status and sampling_authorized is False")
    jobs = validate_jobs(manifest, plan, ledger, input_root)
    if len(jobs) != 160 or plan.get("job_count") != 160:
        raise FreezeError("registered job count must be exactly 160")
    ids = validate_identities(manifest, args.source_digest.resolve(), args.freezer.resolve(),
                              args.preparer.resolve(), args.runner.resolve(), args.freezer_test.resolve(),
                              args.cpu_test_log.resolve(), args.adapter_test.resolve(),
                              args.adapter_test_log.resolve())
    proof_records = {}
    environment_payloads = {}
    for label, path, expected in args.evidence:
        p = Path(path).resolve()
        proof_records[label] = checked_file(p, expected, label)
        if label.startswith("environment_proof"):
            evidence = load_json(p)
            if (evidence.get("schema") != "dmgmd.npt-b3-environment-proof.v1"
                    or evidence.get("status") != "PASS"
                    or evidence.get("candidate_sha256") != ids["candidate_binary"]["sha256"]
                    or evidence.get("supported_runtime_backends") != ["HostStaged", "CudaAware"]
                    or evidence.get("gate_ranks") != 4
                    or not all(token in evidence.get("probe_summary", "")
                               for token in ("cuda_copy", "cuda_ipc", "CudaAware probe ranks=4"))):
                raise FreezeError(f"invalid candidate/environment proof: {p}")
            devices = set(evidence.get("physical_devices", []))
            if devices not in (set("0123"), set("4567")):
                raise FreezeError(f"unexpected physical GPU set in environment proof: {p}")
            identities = evidence.get("gpu_identity", {})
            if set(identities) != devices or any(not item.get("uuid") or not item.get("driver_version")
                                                 for item in identities.values()):
                raise FreezeError(f"incomplete physical GPU UUID/driver proof: {p}")
            environment_payloads[label] = evidence
        elif label == "prior_reference_rng_freeze":
            evidence = load_json(p)
            if (evidence.get("schema") != "dmgmd.npt-b3-execution-freeze.v1"
                    or evidence.get("candidate_sha256") != ids["candidate_binary"]["sha256"]
                    or evidence.get("reference_sha256") != ids["reference_binary"]["sha256"]
                    or evidence.get("reference_scr_rng", {}).get("binary_call_verified") is not True):
                raise FreezeError(f"invalid prior reference RNG freeze: {p}")
    if len(environment_payloads) != 2:
        raise FreezeError("both registered four-GPU environment proofs are required")
    env0 = environment_payloads.get("environment_proof_gpu0_3")
    env4 = environment_payloads.get("environment_proof_gpu4_7")
    if not env0 or not env4 or set(env0["physical_devices"]) != set("0123") or set(env4["physical_devices"]) != set("4567"):
        raise FreezeError("canonical GPU0-3 and GPU4-7 environment proof identities are required")
    if env0["environment_fingerprint"] != env4["environment_fingerprint"]:
        raise FreezeError("GPU0-3 and GPU4-7 environment fingerprints differ")
    for label, expected in (("candidate", ids["candidate_binary"]["sha256"]),
                            ("reference", ids["reference_binary"]["sha256"])):
        if plan.get(f"{label}_sha256") != expected:
            raise FreezeError(f"plan {label} SHA differs from manifest")
    potential_sha = manifest["source_contract"]["potential_sha256"]
    if plan.get("potential_sha256") != potential_sha:
        raise FreezeError("plan potential SHA differs from manifest")
    payload = {
        "schema": FREEZE_SCHEMA,
        "status": "FROZEN_FOR_EXECUTION" if formal else "PREFLIGHT_ONLY",
        "sampling_authorized": formal,
        "formal_execution_freeze": formal,
        "note": ("Identity freeze only; this file does not launch jobs." if formal else
                 "Identity preflight only; this file does not authorize sampling and cannot be passed as a runnable freeze."),
        "registry": {"manifest": {"path": str(manifest_path), "sha256": registry_hashes["manifest"]},
                     "protocol": {"path": str(protocol_path), "sha256": registry_hashes["protocol"]},
                     "bridge_rng_source_audit": {"path": str(proof_path), "sha256": registry_hashes["bridge_rng_source_audit"]}},
        "plan": {"path": str(plan_path), "sha256": sha256_file(plan_path)},
        "ledger": {"path": str(ledger_path), "sha256": sha256_file(ledger_path)},
        "reference_launch_registry": {
            "path": str((plan_path.parent / "reference-launch-utc-registry.json").resolve()),
            "initial_sha256": sha256_bytes((json.dumps(
                {"schema": "dmgmd.npt-reference-launch-registry.v1", "reservations": []},
                indent=2, sort_keys=True) + "\n").encode()),
            "initialization": "runner creates these canonical bytes atomically on first use if absent",
        },
        "input_root": str(input_root),
        "input_file_count": 192,
        "input_inventory_sha256": plan["input_inventory_sha256"],
        "job_count": len(jobs),
        "job_keys": [job["key"] for job in jobs],
        "jobs": jobs,
        "identities": ids,
        "external_evidence": proof_records,
    }
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", "--preflight", dest="dry_run", action="store_true",
                      help="validate draft identities without authorization")
    mode.add_argument("--freeze", dest="formal_freeze", action="store_true",
                      help="write a formal freeze only for the exact root-approved registry")
    p.add_argument("--manifest", type=Path, default=HERE / "independent-confirmation-manifest.json")
    p.add_argument("--protocol", type=Path, default=HERE / "independent-confirmation-protocol.md")
    p.add_argument("--rng-proof", type=Path, default=HERE / "bridge-rng-source-audit.json")
    p.add_argument("--plan", type=Path, default=Path("/tmp/dmgmd-b0-b4-20261103/npt-independent-confirmation-draft-v2/dry-run-plan.json"))
    p.add_argument("--ledger", type=Path, default=Path("/tmp/dmgmd-b0-b4-20261103/npt-independent-confirmation-draft-v2/execution-ledger.draft.json"))
    p.add_argument("--input-root", type=Path, default=Path("/tmp/dmgmd-b0-b4-20261103/npt-independent-confirmation-draft-v2/registered-inputs"))
    p.add_argument("--source-digest", type=Path, default=Path("/tmp/dmgmd-b0-b4-20261103/b3-source-digest.sha256"))
    p.add_argument("--freezer", type=Path, default=Path(__file__).resolve())
    p.add_argument("--preparer", type=Path, default=HERE / "prepare_scr_independent_confirmation.py")
    p.add_argument("--runner", type=Path, default=HERE / "run_scr_independent_confirmation.py")
    p.add_argument("--cpu-test-log", type=Path, default=Path("/tmp/dmgmd-b0-b4-20261103/paired-confirmation-cpu-tests.log"))
    p.add_argument("--freezer-test", type=Path, default=HERE / "test_freeze_scr_independent_confirmation.py")
    p.add_argument("--adapter-test", type=Path, default=HERE / "test_run_scr_independent_confirmation.py")
    p.add_argument("--adapter-test-log", type=Path, default=Path("/tmp/dmgmd-b0-b4-20261103/scr-independent-runner-cpu-tests.log"))
    p.add_argument("--out", type=Path, required=True, help="fresh output path; existing files are never overwritten")
    p.add_argument("--evidence", action="append", default=[
        "environment_proof_gpu0_3=b94ca00e60e327188757d1fa760e4401a4fd2d80869fd9d7e744e83f4f4a2733=/tmp/dmgmd-b0-b4-20261103/npt-b3/environment-proof-gpu0-3.json",
        "environment_proof_gpu4_7=2d24ed18fc6a884a9a0bba6610bb9bf0a1256e2672ee196d0b3f4aad3676df8a=/tmp/dmgmd-b0-b4-20261103/npt-b3/environment-proof-gpu4-7.json",
        "prior_reference_rng_freeze=099e5ead7a5303b77db093df1802dea7ce72af8d2f57daa1994fed9e17b9be9f=/tmp/dmgmd-b0-b4-20261103/npt-b3/freeze-b3-execution-v13.json",
    ], metavar="NAME=SHA256=PATH",
                   help="additional prior GPU/MPI validation artifact and locked SHA (repeatable)")
    a = p.parse_args()
    try:
        evidence = []
        for item in a.evidence:
            parts = item.split("=", 2)
            if len(parts) != 3:
                raise FreezeError(f"invalid --evidence {item!r}; expected NAME=SHA256=PATH")
            evidence.append((parts[0], parts[2], parts[1]))
        a.evidence = evidence
        payload = prepare_output(a, formal=a.formal_freeze)
        out = a.out.resolve()
        if out.exists():
            raise FreezeError(f"refusing to overwrite preflight output: {out}")
        if Path(payload["reference_launch_registry"]["path"]).exists():
            raise FreezeError("reference UTC-second registry path must be fresh and absent before freeze")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"{payload['status']} jobs={payload['job_count']} inputs={payload['input_file_count']} output={out} sha256={sha256_file(out)}")
        return 0
    except (FreezeError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"freeze preflight rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
