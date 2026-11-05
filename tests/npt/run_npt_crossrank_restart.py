#!/usr/bin/env python3
"""Freeze and verify short P1<->P2 NPT restart jobs from prior B4 checkpoints."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tests/mpi"))
sys.path.insert(0, str(ROOT / "tests/baseline"))
sys.path.insert(0, str(HERE))
import check_environment
import run_baselines as baseline
import run_npt_contracts as contracts

CANDIDATE_DEFAULT = Path("/tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md")
SOURCE_FREEZE_DEFAULT = Path("/tmp/dmgmd-b0-b4-20261103/npt-b4/freeze-v7/freeze.json")
P1_RESULTS_DEFAULT = Path("/tmp/dmgmd-b0-b4-20261103/npt-b4/p1-contracts-v6/results.json")
P2_RESULTS_DEFAULT = Path("/tmp/dmgmd-b0-b4-20261103/npt-b4/p2-p4-output-contracts-v7/results.json")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def make_run(ensemble: str) -> str:
    return ("potential nep.txt\ntime_step 0.1\n"
            f"ensemble {ensemble} 300 300 40 0 425 200\n"
            "dump_thermo 1\n"
            "dump_xyz 1 trajectory.xyz precision double velocity unwrapped_position\n"
            "dump_restart 5\nrun 5\n")


def source_record(results_path: Path, label: str) -> dict:
    data = json.loads(results_path.read_text())
    record = next(item for item in data["records"] if item["label"] == label)
    if record.get("status") != "PASS":
        raise RuntimeError(f"source record did not pass: {label}")
    if data["candidate_sha256"] != sha(CANDIDATE_DEFAULT):
        raise RuntimeError(f"source result candidate SHA differs for {label}")
    return record


def prepare(args: argparse.Namespace) -> None:
    out = args.freeze_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_freeze_path = args.source_freeze.resolve()
    source_freeze = json.loads(source_freeze_path.read_text())
    fixture = source_freeze_path.parent / "fixtures/dense_water"
    model_source, potential_source = fixture / "model.xyz", fixture / "nep.txt"
    expected_fixture = source_freeze["fixtures"]["dense_water"]
    if sha(model_source) != expected_fixture["model_sha256"] or \
            sha(potential_source) != expected_fixture["potential_sha256"]:
        raise RuntimeError("dense-water source fixture differs from its B4 freeze")
    p1_results, p2_results = args.p1_results.resolve(), args.p2_results.resolve()
    p1_json, p2_json = json.loads(p1_results.read_text()), json.loads(p2_results.read_text())
    if p1_json["candidate_sha256"] != p2_json["candidate_sha256"] or \
            p1_json["candidate_sha256"] != sha(args.candidate.resolve()):
        raise RuntimeError("P1/P2 source results use inconsistent candidate binaries")

    fixture_out = out / "fixtures/crossrank"
    fixture_out.mkdir(parents=True)
    shutil.copyfile(model_source, fixture_out / "source-model.xyz")
    shutil.copyfile(potential_source, fixture_out / "nep.txt")
    source_records = {}
    for ensemble in ("npt_ber", "npt_scr"):
        for source_rank, results_path, rank_tag in (
            (1, p1_results, "p1"), (2, p2_results, "p2")):
            label = f"dense_water-{ensemble}-p{source_rank}-HostStaged"
            record = source_record(results_path, label)
            if record["inputs_sha256"]["model.xyz"] != sha(model_source) or \
                    record["inputs_sha256"]["nep.txt"] != sha(potential_source):
                raise RuntimeError(f"source checkpoint fixture mismatch for {label}")
            original_dir = Path(record["cwd"])
            checkpoint = original_dir / "restart.xyz"
            checkpoint_hash = sha(checkpoint)
            if checkpoint_hash != record["output_sha256"]["restart.xyz"]:
                raise RuntimeError(f"source checkpoint hash mismatch for {label}")
            frozen_name = f"{rank_tag}-{ensemble}-restart.xyz"
            shutil.copyfile(checkpoint, fixture_out / frozen_name)
            source_records[f"{rank_tag}-{ensemble}"] = {
                "record_label": label, "results_path": str(results_path),
                "results_sha256": sha(results_path), "checkpoint_source": str(checkpoint),
                "checkpoint_sha256": checkpoint_hash, "frozen_checkpoint": frozen_name,
                "frozen_checkpoint_sha256": sha(fixture_out / frozen_name),
                "source_rank_count": source_rank,
                "source_run_input_sha256": record["inputs_sha256"]["run.in"],
            }
    for ensemble in ("npt_ber", "npt_scr"):
        (fixture_out / f"{ensemble}-resume.in").write_text(make_run(ensemble))
    files = {p.relative_to(out).as_posix(): sha(p)
             for p in sorted(out.rglob("*")) if p.is_file()}
    payload = {
        "schema": "dmgmd.npt-crossrank-restart-freeze.v1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidate": str(args.candidate.resolve()), "candidate_sha256": sha(args.candidate.resolve()),
        "runner_sha256": sha(Path(__file__).resolve()),
        "contract_helper_sha256": sha(HERE / "run_npt_contracts.py"),
        "source_fixture_freeze": str(source_freeze_path),
        "source_fixture_freeze_sha256": sha(source_freeze_path),
        "p1_results_sha256": sha(p1_results), "p2_results_sha256": sha(p2_results),
        "reference_heads": contracts.reference_heads(), "sources": source_records,
        "fixture_model_sha256": sha(fixture_out / "source-model.xyz"),
        "potential_sha256": sha(fixture_out / "nep.txt"), "files_sha256": files,
        "contracts": {"pairs": ["P1->P2", "P2->P1"],
                      "ensembles": ["npt_ber", "npt_scr"], "resume_steps": 5,
                      "continuity_promised": False},
    }
    write_json(out / "freeze.json", payload)
    print(json.dumps({"freeze": str(out / "freeze.json"), "sources": source_records,
                      "files_sha256": files}, indent=2, sort_keys=True))


def run(args: argparse.Namespace) -> None:
    freeze_path = args.freeze.resolve()
    freeze = json.loads(freeze_path.read_text())
    if sha(args.candidate.resolve()) != freeze["candidate_sha256"]:
        raise RuntimeError("candidate binary differs from freeze")
    if sha(Path(__file__).resolve()) != freeze["runner_sha256"] or \
            sha(HERE / "run_npt_contracts.py") != freeze["contract_helper_sha256"]:
        raise RuntimeError("restart runner/helper differs from freeze")
    if freeze["reference_heads"] != contracts.reference_heads():
        raise RuntimeError("reference identity/status/ignored inventory changed")
    input_root = freeze_path.parent
    files = {p.relative_to(input_root).as_posix(): sha(p)
             for p in sorted(input_root.rglob("*")) if p.is_file() and p.name != "freeze.json"}
    if files != freeze["files_sha256"]:
        raise RuntimeError("cross-rank frozen inputs changed")
    root = args.work_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    devices = args.devices.split(",")
    mpiexec = check_environment.validate_environment(args.candidate.resolve(), "mpiexec",
                                                      devices, 2, args.timeout)
    fixture = input_root / "fixtures/crossrank"
    records = []
    for ensemble in ("npt_ber", "npt_scr"):
        for source_rank, target_rank in ((1, 2), (2, 1)):
            source_key = f"p{source_rank}-{ensemble}"
            source = freeze["sources"][source_key]
            checkpoint = fixture / source["frozen_checkpoint"]
            label = f"{ensemble}-restart-p{source_rank}-to-p{target_rank}"
            stage = root / label
            stage.mkdir()
            shutil.copyfile(checkpoint, stage / "model.xyz")
            shutil.copyfile(fixture / "nep.txt", stage / "nep.txt")
            shutil.copyfile(fixture / f"{ensemble}-resume.in", stage / "run.in")
            env = os.environ.copy()
            env.update(CUDA_VISIBLE_DEVICES=",".join(devices[:target_rank]),
                       CUDA_DEVICE_ORDER="PCI_BUS_ID", DMGMD_COMM_BACKEND="HostStaged",
                       DMGMD_DOMAIN_DIAGNOSTICS="1", DMGMD_COMM_LOG_INTERVAL="1",
                       OMP_NUM_THREADS="1", DMGMD_ENSEMBLE_SEED="12345678")
            command = [str(mpiexec), "-n", str(target_rank), str(args.candidate.resolve())]
            rec = {"label": label, "source_rank_count": source_rank,
                   "target_rank_count": target_rank, "source_checkpoint_sha256": sha(checkpoint),
                   "candidate_sha256": freeze["candidate_sha256"], "command": command,
                   "inputs_sha256": {name: sha(stage / name)
                                     for name in ("model.xyz", "nep.txt", "run.in")}}
            records.append(rec)
            started = time.monotonic()
            with (stage / "stdout.log").open("w") as stdout, (stage / "stderr.log").open("w") as stderr:
                try:
                    proc = subprocess.run(command, cwd=stage, env=env, stdout=stdout, stderr=stderr,
                                          timeout=args.timeout, check=False)
                    rc = proc.returncode
                except subprocess.TimeoutExpired:
                    rc = None
            logs = (stage / "stdout.log").read_text(errors="replace") + "\n" + \
                   (stage / "stderr.log").read_text(errors="replace")
            rec["returncode"] = rc
            rec["duration_seconds"] = time.monotonic() - started
            rec["route_lines"] = [line for line in logs.splitlines() if "DMGMD_DOMAIN" in line]
            if rc != 0:
                rec["status"] = "FAIL"
                write_json(root / "results.json", {"schema": "dmgmd.npt-crossrank-restart-results.v1",
                           "freeze_sha256": sha(freeze_path), "candidate_sha256": freeze["candidate_sha256"],
                           "records": records})
                raise RuntimeError(f"{label}: exit {rc}; inspect {stage}")
            rec["contract"] = contracts.ContractRunner.check_outputs(stage, 12288, 5)
            thermo = [row for section in baseline.parse_thermo(stage / "thermo.out")
                      for row in section["rows"]]
            if any(not math.isfinite(value) for row in thermo for value in row):
                raise RuntimeError(f"{label}: non-finite thermo")
            if not any("mode=m1" in line and "reason=npt-m1-only" in line
                       for line in rec["route_lines"]):
                raise RuntimeError(f"{label}: missing M1 NPT route diagnostic")
            rec["restart_classification"] = "NEW_JOB_CROSS_RANK; continuity not promised"
            rec["output_sha256"] = {p.name: sha(p) for p in stage.iterdir()
                                     if p.is_file() and p.name not in ("model.xyz", "nep.txt", "run.in")}
            rec["status"] = "PASS"
            write_json(root / "results.json", {"schema": "dmgmd.npt-crossrank-restart-results.v1",
                       "freeze_sha256": sha(freeze_path), "candidate_sha256": freeze["candidate_sha256"],
                       "records": records})
    print(f"PASS {len(records)} cross-rank restart contracts; evidence {root}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--candidate", type=Path, default=CANDIDATE_DEFAULT)
    parser.add_argument("--source-freeze", type=Path, default=SOURCE_FREEZE_DEFAULT)
    parser.add_argument("--p1-results", type=Path, default=P1_RESULTS_DEFAULT)
    parser.add_argument("--p2-results", type=Path, default=P2_RESULTS_DEFAULT)
    parser.add_argument("--freeze-dir", type=Path)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--devices", default="0,1")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if args.prepare == args.run:
        parser.error("select exactly one of --prepare or --run")
    if args.prepare:
        if not args.freeze_dir:
            parser.error("--prepare requires --freeze-dir")
        prepare(args)
    else:
        if not args.freeze or not args.work_root:
            parser.error("--run requires --freeze and --work-root")
        run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
