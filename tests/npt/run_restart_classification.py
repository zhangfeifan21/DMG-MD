#!/usr/bin/env python3
"""Classify NPT Ber single, same-process split, and file-restart paths."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tests/baseline"))
sys.path.insert(0, str(ROOT / "tests/mpi"))
import run_baselines as baseline
import check_environment
import run_npt_ber


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def model_and_runs(source: Path) -> tuple[str, dict[str, str]]:
    model = (source / "ber216" / "model.xyz").read_text()
    common = ["potential nep.txt", "time_step 0.25", "ensemble npt_ber 300 300 40 0 425 200",
              "dump_thermo 1",
              "dump_xyz 10 trajectory.xyz precision double velocity force potential virial",
              "dump_restart 20"]
    measurements = common[3:]
    runs = {
        "long40": "\n".join(common + ["run 40"]) + "\n",
        "split20x2": "\n".join(common + ["run 20"] + measurements + ["run 20"]) + "\n",
        "checkpoint20": "\n".join(common + ["run 20"]) + "\n",
        "resume20": "\n".join(common + ["run 20"]) + "\n",
    }
    return model, runs


def register(args: argparse.Namespace) -> None:
    model_source = args.b2_inputs.resolve()
    candidate = args.candidate.resolve()
    potential = (ROOT / "tests/baseline/inputs/potentials/nep_C.txt").resolve()
    model, runs = model_and_runs(model_source)
    if sha(model_source / "ber216" / "model.xyz") != "127124305704235915fb9953c51feb71323136a3304e6eb617972c255e735b82":
        raise SystemExit("B2-frozen model hash changed")
    if sha(potential) != "add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623":
        raise SystemExit("pinned NEP potential hash changed")
    out = args.registered.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "model.xyz").write_text(model)
    shutil_copy = __import__("shutil").copyfile
    shutil_copy(potential, out / "nep.txt")
    for name, text in runs.items():
        (out / f"{name}.run.in").write_text(text)
    scripts = [HERE / "run_restart_classification.py", HERE / "run_npt_ber.py",
               ROOT / "tests/baseline/run_baselines.py"]
    payload = {
        "schema": "dmgmd.npt-ber-restart-classification-freeze.v1",
        "frozen_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidate_path": str(candidate), "candidate_sha256": sha(candidate),
        "model_path": str(out / "model.xyz"), "model_sha256": sha(out / "model.xyz"),
        "potential_path": str(out / "nep.txt"), "potential_sha256": sha(out / "nep.txt"),
        "input_root": str(out),
        "runs": {f"{name}.run.in": sha(out / f"{name}.run.in") for name in runs},
        "script_sha256": {str(path.relative_to(ROOT)): sha(path) for path in scripts},
        "classification": "same seed/initial model and fixed T1=T2=300 K; report divergence only, do not require trajectory continuity",
        "steps": {"long40": 40, "split20x2": 40, "checkpoint20": 20, "resume20": 20},
        "dt_fs": 0.25, "temperature_K": 300, "target_pressure_GPa": 0.0,
        "trajectory_interval_steps": 10, "restart_interval_steps": 20,
        "restart_precision": "C %g, six significant digits",
    }
    write_json(args.freeze.resolve(), payload)
    print(json.dumps({"freeze": str(args.freeze.resolve()), "candidate_sha256": payload["candidate_sha256"],
                      "model_sha256": payload["model_sha256"], "run_input_sha256": payload["runs"]}, indent=2))


def numeric_diff(left: list[list[float]], right: list[list[float]], columns: list[int] | None = None) -> dict:
    max_abs = 0.0
    first = None
    for row_index, (a, b) in enumerate(zip(left, right)):
        for column, (x, y) in enumerate(zip(a, b)):
            if columns is not None and column not in columns:
                continue
            delta = abs(x - y)
            max_abs = max(max_abs, delta)
            if first is None and delta != 0.0:
                first = {"row": row_index, "column": column, "left": x, "right": y, "abs_diff": delta}
    return {"max_abs": max_abs, "first_exact_numeric_value_difference": first}


def xyz_numeric(frame: dict, properties: tuple[str, ...] = ("pos", "vel")) -> list[list[float]]:
    offsets = {}
    offset = 0
    for name, _kind, width in frame["properties"]:
        offsets[name] = (offset, width)
        offset += width
    columns = []
    for prop in properties:
        start, width = offsets[prop]
        columns.extend(range(start, start + width))
    return [[float(row[index]) for index in columns] for row in frame["rows"]]


def run(args: argparse.Namespace) -> None:
    freeze_path = args.freeze.resolve()
    freeze = json.loads(freeze_path.read_text())
    candidate = args.candidate.resolve()
    if sha(candidate) != freeze["candidate_sha256"]:
        raise SystemExit("candidate binary does not match classification freeze")
    for relative, expected in freeze["script_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise SystemExit(f"script changed after restart classification freeze: {relative}")
    inputs = Path(freeze["input_root"])
    for name, expected in freeze["runs"].items():
        if sha(inputs / name) != expected:
            raise SystemExit(f"registered run input hash changed: {name}")
    if sha(inputs / "model.xyz") != freeze["model_sha256"] or sha(inputs / "nep.txt") != freeze["potential_sha256"]:
        raise SystemExit("registered model/potential hash changed")
    devices = args.devices.split(",")
    mpiexec = check_environment.validate_environment(candidate, "mpiexec", devices, 1, args.timeout)
    evidence = args.work_root.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    shutil_copy = __import__("shutil").copyfile
    shutil_copy(inputs / "nep.txt", evidence / "nep.txt")
    shared_model = (inputs / "model.xyz").read_text()
    records = []

    def launch(label: str, model_text: str, run_filename: str) -> Path:
        stage = evidence / label
        stage.mkdir()
        (stage / "model.xyz").write_text(model_text)
        (stage / "run.in").write_bytes((inputs / run_filename).read_bytes())
        shutil_copy(inputs / "nep.txt", stage / "nep.txt")
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = devices[0]
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        env["DMGMD_COMM_BACKEND"] = "HostStaged"
        command = [str(mpiexec), "-n", "1", str(candidate)]
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        with (stage / "stdout").open("w") as out, (stage / "stderr").open("w") as err:
            begin = time.monotonic()
            completed = subprocess.run(command, cwd=stage, env=env, stdout=out, stderr=err,
                                       timeout=args.timeout, check=False)
        record = {"label": label, "command": command, "cwd": str(stage), "started_at_utc": started,
                  "ended_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "duration_seconds": time.monotonic() - begin, "returncode": completed.returncode,
                  "input_sha256": {name: sha(stage / name) for name in ("model.xyz", "run.in", "nep.txt")},
                  "output_sha256": {p.name: sha(p) for p in stage.iterdir() if p.is_file()}}
        records.append(record)
        write_json(evidence / "results.partial.json", {"schema": "dmgmd.npt-ber-restart-classification.v1",
                                                         "freeze_sha256": sha(freeze_path), "runs": records})
        if completed.returncode != 0:
            raise RuntimeError(f"{label} failed (exit {completed.returncode}); evidence {stage}")
        for required in ("thermo.out", "trajectory.xyz", "restart.xyz"):
            if not (stage / required).is_file():
                raise RuntimeError(f"{label} missing {required}; evidence {stage}")
        return stage

    long = launch("single-long-40", shared_model, "long40.run.in")
    split = launch("same-process-split-20x2", shared_model, "split20x2.run.in")
    checkpoint = launch("file-restart-checkpoint-20", shared_model, "checkpoint20.run.in")
    resumed = launch("file-restart-resume-20", (checkpoint / "restart.xyz").read_text(), "resume20.run.in")

    from run_baselines import parse_xyz, parse_thermo
    long_thermo = [row for segment in parse_thermo(long / "thermo.out") for row in segment["rows"]]
    split_thermo = [row for segment in parse_thermo(split / "thermo.out") for row in segment["rows"]]
    resumed_thermo = [row for segment in parse_thermo(resumed / "thermo.out") for row in segment["rows"]]
    # Measurements are segment-scoped: the split input repeats dump commands
    # before segment two so both halves are recorded without reinitializing state.
    if len(long_thermo) != 40 or len(split_thermo) != 40 or len(resumed_thermo) != 20:
        raise RuntimeError("restart classification thermo rows differ from segment output contract")
    long_xyz = parse_xyz(long / "trajectory.xyz")[-1]
    split_xyz = parse_xyz(split / "trajectory.xyz")[-1]
    resumed_xyz = parse_xyz(resumed / "trajectory.xyz")[-1]
    restart_contract = {label: run_npt_ber.restart_semantics_check(evidence / label)
                        for label in ("single-long-40", "same-process-split-20x2",
                                      "file-restart-checkpoint-20", "file-restart-resume-20")}
    comparisons = {
        "same_process_split_vs_single_long": {
            "thermo_second_half": numeric_diff(long_thermo[20:], split_thermo[20:]),
            "final_position_velocity": numeric_diff(xyz_numeric(long_xyz), xyz_numeric(split_xyz)),
        },
        "file_restart_second_half_vs_single_long_second_half": {
            "thermo_second_half": numeric_diff(long_thermo[20:], resumed_thermo),
            "final_position_velocity": numeric_diff(xyz_numeric(long_xyz), xyz_numeric(resumed_xyz)),
        },
    }
    payload = {"schema": "dmgmd.npt-ber-restart-classification.v1",
               "freeze_sha256": sha(freeze_path), "candidate_sha256": sha(candidate),
               "classification_only_no_continuity_gate": True, "runs": records,
               "comparisons": comparisons,
               "restart_serialization_contract": restart_contract,
               "files": {str(path.relative_to(evidence)): sha(path) for path in evidence.rglob("*")
                         if path.is_file() and path.name != "results.partial.json"}}
    write_json(evidence / "results.json", payload)
    (evidence / "results.partial.json").unlink(missing_ok=True)
    print(json.dumps({"evidence": str(evidence), "freeze_sha256": sha(freeze_path),
                      "candidate_sha256": sha(candidate), "comparisons": comparisons}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--b2-inputs", type=Path)
    parser.add_argument("--registered", type=Path)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--devices", default="4")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--register-only", action="store_true")
    args = parser.parse_args()
    if args.register_only:
        if not args.b2_inputs or not args.registered:
            parser.error("--register-only requires --b2-inputs and --registered")
        register(args)
    else:
        if not args.work_root:
            parser.error("execution requires --work-root")
        run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
