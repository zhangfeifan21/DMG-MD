#!/usr/bin/env python3
"""Capture a reusable, auditable B3 four-GPU CUDA-aware environment gate."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tests/mpi"))
import check_environment


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def environment_fingerprint() -> dict:
    names = ("OMPI_HOME", "UCX_HOME", "CUDA_HOME", "OMPI_MCA_pml",
             "OMPI_MCA_coll", "UCX_WARN_UNUSED_ENV_VARS",
             "OMPI_MCA_mca_base_component_path")
    values = {name: os.environ.get(name) for name in names}
    mpiexec = shutil.which("mpiexec")
    values["mpiexec"] = str(Path(mpiexec).resolve()) if mpiexec else None
    return values


def gpu_inventory() -> dict[str, dict[str, str]]:
    output = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,uuid,driver_version",
         "--format=csv,noheader,nounits"], check=True, capture_output=True,
        text=True).stdout
    result = {}
    for line in output.splitlines():
        fields = [x.strip() for x in line.split(",")]
        if len(fields) == 3:
            result[fields[0]] = {"uuid": fields[1], "driver_version": fields[2]}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--devices", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    devices = [x.strip() for x in args.devices.split(",") if x.strip()]
    if len(devices) != 4 or len(set(devices)) != 4:
        raise SystemExit("environment proof requires four unique physical GPU ids")
    before = gpu_inventory()
    transcript = io.StringIO()
    with contextlib.redirect_stdout(transcript):
        mpiexec = check_environment.validate_environment(
            candidate, "mpiexec", devices, 4, args.timeout)
    after = gpu_inventory()
    if any(before.get(device) != after.get(device) for device in devices):
        raise SystemExit("GPU identity/driver changed during environment probe")
    proof = {
        "schema": "dmgmd.npt-b3-environment-proof.v1",
        "status": "PASS",
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidate_path": str(candidate),
        "candidate_sha256": sha(candidate),
        "gate_ranks": 4,
        "probed_backend": "CudaAware",
        "supported_runtime_backends": ["HostStaged", "CudaAware"],
        "physical_devices": devices,
        "gpu_identity": {device: after[device] for device in devices},
        "environment_fingerprint": environment_fingerprint(),
        "mpiexec_path": str(mpiexec.resolve()),
        "probe_summary": transcript.getvalue().strip(),
        "scope": "same candidate binary, MPI/UCX/CUDA environment, and physical GPU identities; internal NPT science results are not inferred",
    }
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite environment proof: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"proof_path": str(output), "proof_sha256": sha(output),
                      "devices": devices, "candidate_sha256": proof["candidate_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
