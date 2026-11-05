#!/usr/bin/env python3
"""Freeze B2 code/binary/reference/input identity before any formal result is opened."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import generate_inputs
import run_npt_ber


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path,
                        default=ROOT.parent / "gpumd-reference" / "src" / "gpumd")
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--build-log", type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    reference = args.reference.resolve()
    if not candidate.is_file() or not candidate.stat().st_mode & 0o111:
        raise SystemExit(f"candidate is missing or not executable: {candidate}")
    if not reference.is_file() or sha(reference) != json.loads(
            (ROOT / "tests/baseline/manifest.json").read_text())["reference"]["executable_sha256"]:
        raise SystemExit("GPUMD path/hash does not match the pinned baseline manifest")
    if args.input_root.exists():
        raise SystemExit(f"refusing to overwrite existing registered-input directory {args.input_root}")

    manifest_path = HERE / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    args.input_root.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(HERE / "generate_inputs.py"),
                    "--output", str(args.input_root.resolve()), "--write"], check=True)
    input_hashes = {p.relative_to(args.input_root).as_posix(): sha(p)
                    for p in sorted(args.input_root.rglob("*")) if p.is_file()}

    reference_status_before = run_npt_ber.reference_status_snapshot()
    expected_heads = {
        "gpumd-reference": "9d23496e41319b9e2af5221a7df6285387401d1e",
        "lammps-reference": "611ca3b7f8525ba802373b04f9e3d632d515f7e3",
        "lammps-matpl-reference": "21fabf34eadc57a717f4c7fbce9bb80aad5fd991",
    }
    for name, expected in expected_heads.items():
        if reference_status_before[name]["head"] != expected:
            raise SystemExit(f"{name} HEAD changed: {reference_status_before[name]['head']} != {expected}")
        if reference_status_before[name]["tracked_status"]:
            raise SystemExit(f"{name} has tracked/untracked changes before B2 sampling")

    tracked_scripts = [
        "tests/npt/freeze_b2.py",
        "tests/npt/generate_inputs.py",
        "tests/npt/run_npt_ber.py",
        "tests/npt/test_manifest.py",
        "tests/baseline/run_baselines.py",
        "tests/baseline/manifest.json",
        "tests/mpi/check_environment.py",
    ]
    payload = {
        "schema": "dmgmd.npt-b2-freeze.v1",
        "frozen_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "manifest_sha256": sha(manifest_path),
        "candidate_path": str(candidate),
        "candidate_sha256": sha(candidate),
        "reference_path": str(reference),
        "reference_sha256": sha(reference),
        "baseline_manifest_sha256": sha(ROOT / "tests/baseline/manifest.json"),
        "potential_path": str(ROOT / manifest["source_contract"]["potential_path"]),
        "potential_sha256": sha(ROOT / manifest["source_contract"]["potential_path"]),
        "generated_input_dir": str(args.input_root.resolve()),
        "generated_input_sha256": input_hashes,
        "script_sha256": {name: sha(ROOT / name) for name in tracked_scripts},
        "reference_status_before": reference_status_before,
        "reference_status_before_sha256": hashlib.sha256(
            json.dumps(reference_status_before, sort_keys=True).encode()).hexdigest(),
        "build_log": str(args.build_log.resolve()) if args.build_log else None,
        "build_log_sha256": sha(args.build_log.resolve()) if args.build_log else None,
        "tolerance_source": "tests/baseline/manifest.json (read-only for B2)",
        "scientific_parameters_frozen": True,
        "formal_results_seen_before_freeze": False,
    }
    out = args.freeze.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"freeze": str(out), "freeze_sha256": sha(out),
                      "candidate_sha256": payload["candidate_sha256"],
                      "reference_sha256": payload["reference_sha256"],
                      "manifest_sha256": payload["manifest_sha256"],
                      "registered_input_files": len(input_hashes)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
