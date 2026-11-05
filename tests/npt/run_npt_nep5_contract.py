#!/usr/bin/env python3
"""Freeze and run short NPT contracts using the registered equivalent NEP5 potential."""
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
sys.path.insert(0, str(ROOT / "tests/long_nve"))
sys.path.insert(0, str(HERE))
import check_environment
import long_nve_common as long_nve
import run_baselines as baseline
import run_npt_contracts as npt_contracts

CANDIDATE_DEFAULT = Path("/tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md")
REFERENCE_DEFAULT = ROOT.parent / "gpumd-reference/src/gpumd"
SOURCE_FREEZE_DEFAULT = Path("/tmp/dmgmd-b0-b4-20261103/npt-b4/freeze-v7/freeze.json")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def prepare(args: argparse.Namespace) -> None:
    out = args.freeze_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_freeze_path = args.source_freeze.resolve()
    source_freeze = json.loads(source_freeze_path.read_text())
    model_src = source_freeze_path.parent / "fixtures/output-timepoint/model.xyz"
    manifest = long_nve.load_manifest()
    source_base_case = long_nve.profile_case(manifest, "smoke", "carbon_crystal")
    source_case = long_nve.profile_case(manifest, "smoke", "carbon_nep5")
    original_potential = ROOT / source_base_case["potential"]
    if sha(model_src) != "127124305704235915fb9953c51feb71323136a3304e6eb617972c255e735b82":
        raise RuntimeError("source carbon model does not match the B4 216-atom frozen fixture")
    if sha(original_potential) != source_base_case["potential_sha256"]:
        raise RuntimeError("NEP4 source potential differs from the long-NVE manifest lock")
    transformed_text = long_nve.generate_potential(source_case)
    long_nve.validate_generated_potential(source_case, transformed_text)
    fixtures = out / "fixtures" / "carbon_nep5"
    fixtures.mkdir(parents=True)
    shutil.copyfile(model_src, fixtures / "model.xyz")
    shutil.copyfile(original_potential, fixtures / "nep4-source.txt")
    (fixtures / "nep.txt").write_text(transformed_text)
    for kind, steps, dump_interval, restart_interval in (
        ("npt_ber", 100, 50, 100), ("npt_scr", 20, 10, 20)):
        run_text = npt_contracts.run_text(kind, steps, 0.25, dump_interval, restart_interval)
        (fixtures / f"{kind}.in").write_text(run_text)
    files = {path.relative_to(out).as_posix(): sha(path)
             for path in sorted(out.rglob("*")) if path.is_file()}
    candidate, reference = args.candidate.resolve(), args.reference.resolve()
    freeze = {
        "schema": "dmgmd.npt-nep5-contract-freeze.v1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidate": str(candidate), "candidate_sha256": sha(candidate),
        "reference": str(reference), "reference_sha256": sha(reference),
        "runner_sha256": sha(Path(__file__).resolve()),
        "long_nve_common_sha256": sha(ROOT / "tests/long_nve/long_nve_common.py"),
        "long_nve_manifest_sha256": sha(ROOT / "tests/long_nve/manifest.json"),
        "npt_contract_helper_sha256": sha(HERE / "run_npt_contracts.py"),
        "baseline_manifest_sha256": sha(ROOT / "tests/baseline/manifest.json"),
        "source_fixture_freeze": str(source_freeze_path),
        "source_fixture_freeze_sha256": sha(source_freeze_path),
        "source_model_sha256": sha(fixtures / "model.xyz"),
        "nep4_source_potential_sha256": sha(fixtures / "nep4-source.txt"),
        "nep5_transformed_potential_sha256": sha(fixtures / "nep.txt"),
        "nep5_manifest_sha256": source_case["potential_sha256"],
        "files_sha256": files,
        "reference_heads": npt_contracts.reference_heads(),
        "contract": {"atoms": 216, "npt_ber_steps": 100, "npt_scr_steps": 20,
                     "ranks": [1, 2, 4], "backends": ["HostStaged", "CudaAware"],
                     "seed": 12345678, "scr_repeat": True},
    }
    write_json(out / "freeze.json", freeze)
    print(json.dumps({"freeze": str(out / "freeze.json"),
                      "candidate_sha256": freeze["candidate_sha256"],
                      "nep4_source_sha256": freeze["nep4_source_potential_sha256"],
                      "nep5_sha256": freeze["nep5_transformed_potential_sha256"],
                      "files_sha256": files}, indent=2, sort_keys=True))


def finite_outputs(stage: Path, steps: int) -> dict:
    result = npt_contracts.ContractRunner.check_outputs(stage, 216, steps)
    thermo = [row for section in baseline.parse_thermo(stage / "thermo.out")
              for row in section["rows"]]
    if any(not math.isfinite(value) for row in thermo for value in row):
        raise RuntimeError(f"{stage}: non-finite thermo value")
    return result


def run(args: argparse.Namespace) -> None:
    freeze_path = args.freeze.resolve()
    freeze = json.loads(freeze_path.read_text())
    if sha(args.candidate.resolve()) != freeze["candidate_sha256"]:
        raise RuntimeError("candidate SHA differs from the freeze")
    if sha(args.reference.resolve()) != freeze["reference_sha256"]:
        raise RuntimeError("reference executable SHA differs from the freeze")
    if sha(Path(__file__).resolve()) != freeze["runner_sha256"]:
        raise RuntimeError("NEP5 runner changed after freeze")
    if sha(ROOT / "tests/long_nve/long_nve_common.py") != freeze["long_nve_common_sha256"] or \
            sha(ROOT / "tests/long_nve/manifest.json") != freeze["long_nve_manifest_sha256"] or \
            sha(HERE / "run_npt_contracts.py") != freeze["npt_contract_helper_sha256"] or \
            sha(ROOT / "tests/baseline/manifest.json") != freeze["baseline_manifest_sha256"]:
        raise RuntimeError("a frozen input/helper manifest changed")
    input_root = freeze_path.parent
    files = {p.relative_to(input_root).as_posix(): sha(p)
             for p in sorted(input_root.rglob("*")) if p.is_file() and p.name != "freeze.json"}
    if files != freeze["files_sha256"]:
        raise RuntimeError("NEP5 input inventory differs from the freeze")
    if freeze["reference_heads"] != npt_contracts.reference_heads():
        raise RuntimeError("reference checkout identity/status/ignored inventory changed")

    devices = args.devices.split(",")
    ranks = [int(item) for item in args.ranks.split(",")]
    backends = args.backends.split(",")
    mpiexec = check_environment.validate_environment(args.candidate.resolve(), "mpiexec",
                                                      devices, max(ranks), args.timeout)
    root = args.work_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    fixture = input_root / "fixtures/carbon_nep5"
    records: list[dict] = []

    def execute(label: str, kind: str, engine: str, rank_count: int, backend: str,
                seed: str | None) -> Path:
        stage = root / label
        stage.mkdir()
        for name in ("model.xyz", "nep.txt", f"{kind}.in"):
            shutil.copyfile(fixture / name, stage / ("run.in" if name.endswith(".in") else name))
        env = os.environ.copy()
        env.update(CUDA_VISIBLE_DEVICES=",".join(devices[:rank_count]),
                   CUDA_DEVICE_ORDER="PCI_BUS_ID", DMGMD_COMM_BACKEND=backend,
                   DMGMD_DOMAIN_DIAGNOSTICS="1", DMGMD_COMM_LOG_INTERVAL="1",
                   OMP_NUM_THREADS="1")
        if seed is not None:
            env["DMGMD_ENSEMBLE_SEED"] = seed
        else:
            env.pop("DMGMD_ENSEMBLE_SEED", None)
        candidate_run = engine == "candidate"
        command = ([str(mpiexec), "-n", str(rank_count), str(args.candidate.resolve())]
                   if candidate_run else [str(args.reference.resolve())])
        record = {"label": label, "kind": kind, "engine": engine, "ranks": rank_count,
                  "backend": backend, "seed": seed, "command": command,
                  "inputs_sha256": {name: sha(stage / name)
                                    for name in ("model.xyz", "nep.txt", "run.in")},
                  "candidate_sha256": freeze["candidate_sha256" if candidate_run else "reference_sha256"]}
        records.append(record)
        started = time.monotonic()
        with (stage / "stdout.log").open("w") as stdout, (stage / "stderr.log").open("w") as stderr:
            try:
                proc = subprocess.run(command, cwd=stage, env=env, stdout=stdout, stderr=stderr,
                                      timeout=args.timeout, check=False)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                rc = None
        record["returncode"] = rc
        record["duration_seconds"] = time.monotonic() - started
        logs = (stage / "stdout.log").read_text(errors="replace") + "\n" + \
               (stage / "stderr.log").read_text(errors="replace")
        record["route_lines"] = [line for line in logs.splitlines()
                                 if "DMGMD_DOMAIN" in line]
        if rc != 0:
            record["status"] = "FAIL"
            write_json(root / "results.json", {"schema": "dmgmd.npt-nep5-contract-results.v1",
                       "freeze_sha256": sha(freeze_path), "candidate_sha256": freeze["candidate_sha256"],
                       "records": records})
            raise RuntimeError(f"{label}: exit {rc}; inspect {stage}")
        if candidate_run:
            steps = 100 if kind == "npt_ber" else 20
            record["contract"] = finite_outputs(stage, steps)
            if not any("mode=m1" in line and "reason=npt-m1-only" in line
                       for line in record["route_lines"]):
                raise RuntimeError(f"{label}: missing M1 NPT route diagnostic")
        record["output_sha256"] = {p.name: sha(p) for p in stage.iterdir()
                                    if p.is_file() and p.name not in ("model.xyz", "nep.txt", "run.in")}
        record["status"] = "PASS"
        write_json(root / "results.json", {"schema": "dmgmd.npt-nep5-contract-results.v1",
                   "freeze_sha256": sha(freeze_path), "candidate_sha256": freeze["candidate_sha256"],
                   "records": records})
        return stage

    for rank_count in ranks:
        for backend in backends:
            ber = execute(f"npt_ber-p{rank_count}-{backend}", "npt_ber", "candidate",
                          rank_count, backend, "12345678")
            if rank_count == 1 and backend == backends[0]:
                ref = execute("npt_ber-gpumd-p1", "npt_ber", "reference", 1,
                              "HostStaged", None)
                metrics = baseline.DiffCollector(
                    json.loads((ROOT / "tests/baseline/manifest.json").read_text())["tolerances"],
                    enforce=True)
                baseline.compare_thermo(ref / "thermo.out", ber / "thermo.out", metrics,
                                         "carbon_nep5/npt_ber")
                ref_xyz = root / "npt_ber-gpumd-compatible.xyz"
                candidate_xyz = root / "npt_ber-candidate-compatible.xyz"
                npt_contracts.trajectory_without_unwrapped(ref / "trajectory.xyz", ref_xyz)
                npt_contracts.trajectory_without_unwrapped(ber / "trajectory.xyz", candidate_xyz)
                baseline.compare_xyz(ref_xyz, candidate_xyz, {"kind": "xyz"}, metrics,
                                     "carbon_nep5/npt_ber/trajectory")
                baseline.compare_xyz(ref / "restart.xyz", ber / "restart.xyz",
                                     {"kind": "restart"}, metrics,
                                     "carbon_nep5/npt_ber/restart")
                records[-2]["gpumd_metrics"] = metrics.stats
                write_json(root / "results.json", {"schema": "dmgmd.npt-nep5-contract-results.v1",
                           "freeze_sha256": sha(freeze_path),
                           "candidate_sha256": freeze["candidate_sha256"], "records": records})
            scr = execute(f"npt_scr-p{rank_count}-{backend}", "npt_scr", "candidate",
                          rank_count, backend, "12345678")
            repeat = execute(f"npt_scr-repeat-p{rank_count}-{backend}", "npt_scr", "candidate",
                             rank_count, backend, "12345678")
            if sha(scr / "thermo.out") != sha(repeat / "thermo.out"):
                raise RuntimeError(f"SCR fixed-seed thermo differs for P{rank_count}/{backend}")
            records[-1]["repeat_of"] = records[-2]["label"]
            write_json(root / "results.json", {"schema": "dmgmd.npt-nep5-contract-results.v1",
                       "freeze_sha256": sha(freeze_path),
                       "candidate_sha256": freeze["candidate_sha256"], "records": records})
    print(f"PASS {len(records)} NEP5 NPT contracts; evidence {root}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--candidate", type=Path, default=CANDIDATE_DEFAULT)
    parser.add_argument("--reference", type=Path, default=REFERENCE_DEFAULT)
    parser.add_argument("--source-freeze", type=Path, default=SOURCE_FREEZE_DEFAULT)
    parser.add_argument("--freeze-dir", type=Path)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--devices", default="0,1,2,3")
    parser.add_argument("--ranks", default="1,2,4")
    parser.add_argument("--backends", default="HostStaged,CudaAware")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if args.prepare == args.run:
        parser.error("select exactly one of --prepare or --run")
    if args.prepare:
        if args.freeze_dir is None:
            parser.error("--prepare requires --freeze-dir")
        prepare(args)
    else:
        if args.freeze is None or args.work_root is None:
            parser.error("--run requires --freeze and --work-root")
        run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
