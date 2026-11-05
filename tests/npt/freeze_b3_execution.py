#!/usr/bin/env python3
"""Freeze an execution-only seed-sharding amendment under the B3 science freeze."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--parent-freeze", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--inflight-results", type=Path, nargs="+", required=True)
    p.add_argument("--scheduled-stop-evidence", type=Path, nargs="+")
    p.add_argument("--environment-proof", type=Path)
    p.add_argument("--schedule", type=Path)
    p.add_argument("--reference-launch-registry", type=Path)
    a = p.parse_args()
    parent_path, output = a.parent_freeze.resolve(), a.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite execution amendment {output}")
    parent = json.loads(parent_path.read_text())
    root_input = Path(parent["generated_input_root"])
    current_inputs = {x.relative_to(root_input).as_posix(): sha(x)
                      for x in sorted(root_input.rglob("*")) if x.is_file()}
    if current_inputs != parent["generated_inputs_sha256"]:
        raise SystemExit("registered input inventory changed since parent B3 freeze")
    payload = dict(parent)
    payload["schema"] = "dmgmd.npt-b3-execution-freeze.v1"
    payload["parent_science_freeze_path"] = str(parent_path)
    payload["parent_science_freeze_sha256"] = sha(parent_path)
    payload["parent_science_freeze_metadata"] = {
        "frozen_at_utc": parent.get("frozen_at_utc"),
        "formal_results_seen_before_freeze": parent.get("formal_results_seen_before_freeze"),
        "scope": "original B3 science/input freeze",
    }
    payload["frozen_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    payload["formal_results_seen_before_freeze"] = True
    payload["formal_results_seen_before_freeze_scope"] = (
        "execution amendment created after formal runs began; this is not a new pre-sampling freeze")
    payload["script_sha256"] = dict(parent["script_sha256"])
    for name in ("tests/npt/run_npt_scr.py", "tests/npt/build_records.py",
                 "tests/npt/freeze_b3_execution.py", "tests/npt/capture_environment_proof.py"):
        payload["script_sha256"][name] = sha(ROOT / name)
    payload["execution_amendment"] = {
        "type": "execution-only seed sharding across assigned GPUs",
        "science_parameters_changed": False,
        "input_inventory_changed": False,
        "registered_seeds": [101, 102, 103, 104, 105, 106, 107, 108],
        "policy": "Each profile/engine/seed is run at most once per selected evidence set; preserve all earlier runs. Separate profile/condition result directories may be merged only after duplicate-key rejection.",
        "inflight_previous_runs": [str(x.resolve()) for x in a.inflight_results],
        "previous_runs_are_preserved": True,
        "formal_runs_had_started_before_amendment": True,
        "amendment_scope": "execution scheduling/gate-proof reuse only; parent science parameters and frozen inputs remain authoritative",
    }
    if a.scheduled_stop_evidence:
        stop_evidence = []
        for stop_file in a.scheduled_stop_evidence:
            stop_path = stop_file.resolve()
            stop_payload = json.loads(stop_path.read_text())
            if stop_payload.get("schema") != "dmgmd.npt-b3-scheduled-stop.v1" or \
                    not stop_payload.get("stop_marker_precreated"):
                raise SystemExit(f"scheduled stop evidence is invalid: {stop_path}")
            stop_evidence.append({"path": str(stop_path), "sha256": sha(stop_path),
                                  "payload": stop_payload})
        payload["execution_amendment"]["scheduled_stop_evidence"] = stop_evidence
    if a.environment_proof:
        proof_path = a.environment_proof.resolve()
        proof_payload = json.loads(proof_path.read_text())
        if proof_payload.get("schema") != "dmgmd.npt-b3-environment-proof.v1" or \
                proof_payload.get("status") != "PASS" or \
                proof_payload.get("candidate_sha256") != parent.get("candidate_sha256"):
            raise SystemExit("environment proof is invalid for the frozen candidate")
        payload["execution_amendment"]["environment_proof"] = {
            "path": str(proof_path), "sha256": sha(proof_path),
            "physical_devices": proof_payload.get("physical_devices"),
            "environment_fingerprint": proof_payload.get("environment_fingerprint"),
            "probed_backend": proof_payload.get("probed_backend"),
        }
    if a.schedule:
        schedule_path = a.schedule.resolve()
        schedule_payload = json.loads(schedule_path.read_text())
        if schedule_payload.get("schema") != "dmgmd.npt-b3-execution-schedule.v1" or \
                schedule_payload.get("science_freeze_sha256") != sha(parent_path):
            raise SystemExit("execution schedule does not match the parent science freeze")
        keys = [job.get("key") for job in schedule_payload.get("jobs", [])]
        if len(keys) != len(set(keys)):
            raise SystemExit("execution schedule has duplicate keys")
        payload["execution_amendment"]["execution_schedule_snapshot"] = {
            "path": str(schedule_path), "sha256": sha(schedule_path),
            "job_count": len(keys),
        }
    if a.reference_launch_registry:
        registry_path = a.reference_launch_registry.resolve()
        registry = json.loads(registry_path.read_text())
        if registry.get("schema") != "dmgmd.npt-reference-launch-registry.v1":
            raise SystemExit("reference launch registry has invalid schema")
        seconds = [x.get("utc_second") for x in registry.get("reservations", [])]
        if len(seconds) != len(set(seconds)):
            raise SystemExit("reference launch registry contains duplicate UTC seconds")
        payload["execution_amendment"]["reference_launch_registry"] = {
            "path": str(registry_path), "sha256": sha(registry_path),
            "seeded_existing_launches": len(seconds),
            "initial_utc_seconds": sorted(seconds),
            "policy": "global fcntl-locked UTC-second reservation before GPUMD Popen; reused by all concurrent runner shards",
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"execution_freeze": str(output), "sha256": sha(output),
                      "parent_science_freeze_sha256": payload["parent_science_freeze_sha256"],
                      "candidate_sha256": payload["candidate_sha256"],
                      "generated_inputs": len(current_inputs)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
