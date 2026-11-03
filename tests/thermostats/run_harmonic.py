#!/usr/bin/env python3
"""Run and analyze the preregistered independent harmonic NHC oracle."""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys


SUITE = Path(__file__).resolve().parent
MANIFEST_PATH = SUITE / "manifest.json"
STATISTICS_PATH = SUITE / "sample_statistics.py"
_SPEC = importlib.util.spec_from_file_location("harmonic_sample_statistics", STATISTICS_PATH)
_STATISTICS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_STATISTICS)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_csv(path: Path, expected_count: int, expected_ensemble: str) -> dict[str, list[float]]:
    with path.open("r", newline="", encoding="utf-8") as source:
        lines = list(source)
        metadata = {
            line.strip()[2:].split("=", 1)[0]: line.strip()[2:].split("=", 1)[1]
            for line in lines
            if line.lstrip().startswith("#") and "=" in line
        }
        if metadata.get("ensemble") != expected_ensemble:
            raise ValueError(
                f"expected CSV ensemble {expected_ensemble}, found {metadata.get('ensemble')!r}"
            )
        data_lines = (line for line in lines if line.strip() and not line.lstrip().startswith("#"))
        reader = csv.DictReader(data_lines)
        expected_header = [
            "step",
            "kinetic_energy",
            "mean_mv2_x",
            "mean_mv2_y",
            "mean_mv2_z",
        ]
        if reader.fieldnames != expected_header:
            raise ValueError(f"unexpected CSV header: {reader.fieldnames!r}")
        columns = {name: [] for name in expected_header}
        for row_index, row in enumerate(reader):
            if None in row or any(row[name] is None for name in expected_header):
                raise ValueError(f"malformed CSV row {row_index}")
            try:
                values = {name: float(row[name]) for name in expected_header}
            except (TypeError, ValueError) as error:
                raise ValueError(f"non-numeric CSV row {row_index}: {error}") from error
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError(f"non-finite CSV value at row {row_index}")
            if values["step"] != row_index:
                raise ValueError(f"unexpected step index at row {row_index}: {values['step']}")
            for name in expected_header:
                columns[name].append(values[name])
    actual_count = len(columns["step"])
    if actual_count != expected_count:
        raise ValueError(f"expected {expected_count} samples, found {actual_count}")
    for name in expected_header[1:]:
        if any(value <= 0.0 for value in columns[name]):
            raise ValueError(f"non-positive physical sample in column {name}")
    return columns


def write_results(path: Path, results: dict) -> None:
    path.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run_seed(
    candidate: Path,
    work_root: Path,
    seed: int,
    parameters: dict,
    binary_hash: str,
    manifest_hash: str,
    ensemble: str,
) -> tuple[dict, dict[str, list[float]] | None]:
    stage = work_root / f"seed_{seed}"
    stage.mkdir(exist_ok=False)
    csv_path = stage / "samples.csv"
    stdout_path = stage / "stdout.log"
    stderr_path = stage / "stderr.log"
    command = [
        str(candidate),
        "--output",
        str(csv_path),
        "--seed",
        str(seed),
        "--steps",
        str(parameters["steps"]),
        "--dt",
        format(parameters["dt_internal"], ".17g"),
        "--damping",
        format(parameters["damping_steps"], ".17g"),
        "--natoms",
        str(parameters["natoms"]),
        "--temperature",
        format(parameters["temperature_K"], ".17g"),
    ]
    # Keep the frozen A3 NHC invocation byte-for-byte compatible with its helper.
    if ensemble == "nvt_bdp":
        command.extend(["--ensemble", ensemble])
    record = {
        "seed": seed,
        "ensemble": ensemble,
        "command": command,
        "cwd": str(stage),
        "binary_sha256": binary_hash,
        "manifest_sha256": manifest_hash,
        "csv_path": str(csv_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "expected_samples": parameters["steps"],
        "status": "FAIL",
    }
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        try:
            completed = subprocess.run(
                command,
                cwd=stage,
                stdout=stdout,
                stderr=stderr,
                text=True,
                check=False,
            )
            record["exit_status"] = completed.returncode
        except OSError as error:
            record["exit_status"] = None
            record["launch_error"] = str(error)
    record["stdout_sha256"] = sha256(stdout_path)
    record["stderr_sha256"] = sha256(stderr_path)
    if csv_path.is_file():
        record["csv_sha256"] = sha256(csv_path)
    if record["exit_status"] != 0:
        record["error"] = f"candidate exited with status {record['exit_status']}"
        return record, None
    if not csv_path.is_file():
        record["error"] = "candidate exited successfully without creating samples.csv"
        return record, None
    try:
        columns = parse_csv(csv_path, parameters["steps"], ensemble)
    except (OSError, ValueError) as error:
        record["error"] = str(error)
        return record, None

    discard = parameters["discard_steps"]
    if discard >= parameters["steps"]:
        record["error"] = "discard_steps must be less than steps"
        return record, None
    record["parsed_samples"] = len(columns["step"])
    record["discarded_samples"] = discard
    record["retained_samples"] = parameters["steps"] - discard
    record["status"] = "PASS"
    series = {
        name: values[discard:]
        for name, values in columns.items()
        if name != "step"
    }
    return record, series


def analyze(records: list[dict], chains_by_name: dict[str, list[list[float]]], manifest: dict, parameters: dict) -> dict:
    if any(record["status"] != "PASS" for record in records):
        return {"status": "FAIL", "error": "one or more fixed-seed runs failed validation", "moments": {}}

    harmonic = manifest["harmonic"]
    acceptance = parameters["acceptance"]
    natoms = harmonic["natoms"]
    kt = 8.617343e-5 * harmonic["temperature_K"]
    series_specs = {
        "kinetic_energy": (3.0 * natoms * kt / 2.0, 3.0 * natoms * kt * kt / 2.0),
        "mean_mv2_x": (kt, 2.0 * kt * kt / natoms),
        "mean_mv2_y": (kt, 2.0 * kt * kt / natoms),
        "mean_mv2_z": (kt, 2.0 * kt * kt / natoms),
    }
    moments = {}
    for name, (expected_mean, expected_variance) in series_specs.items():
        moments[name] = _STATISTICS.analyze_series(
            chains_by_name[name], expected_mean, expected_variance, acceptance
        )
    statuses = [result["status"] for result in moments.values()]
    status = "PASS" if all(value == "PASS" for value in statuses) else (
        "FAIL" if any(value == "FAIL" for value in statuses) else "INCONCLUSIVE"
    )
    return {
        "status": status,
        "kT": kt,
        "expected_moments": {
            name: {"mean": mean, "variance": variance}
            for name, (mean, variance) in series_specs.items()
        },
        "acceptance": acceptance,
        "moments": moments,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--half-dt", action="store_true")
    parser.add_argument("--ensemble", choices=("nvt_nhc", "nvt_bdp"), default="nvt_nhc")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--parallel-seeds", type=int, default=1)
    args = parser.parse_args()
    if args.parallel_seeds <= 0:
        parser.error("--parallel-seeds must be a positive integer")

    candidate = args.candidate.resolve(strict=True)
    if not candidate.is_file() or not candidate.stat().st_mode & 0o111:
        parser.error("--candidate must be an executable file")
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    harmonic = manifest["harmonic"]
    parameters = {key: harmonic[key] for key in ("natoms", "steps", "discard_steps", "dt_internal", "damping_steps", "temperature_K", "acceptance", "seeds")}
    if args.half_dt:
        parameters["steps"] *= 2
        parameters["discard_steps"] *= 2
        parameters["dt_internal"] /= 2.0
        parameters["damping_steps"] *= 2
        parameters["acceptance"] = dict(parameters["acceptance"])
        parameters["acceptance"]["block_length"] *= 2
    work_root = args.work_root.resolve()
    try:
        work_root.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"--work-root must be a new directory: {work_root}")

    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    binary_hash = sha256(candidate)
    (work_root / "manifest.json").write_bytes(manifest_bytes)
    results_path = work_root / "results.json"
    results = {
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "candidate": str(candidate),
        "candidate_sha256": binary_hash,
        "manifest_path": str(args.manifest.resolve()),
        "manifest_sha256": manifest_hash,
        "mode": "half_dt" if args.half_dt else "baseline_dt",
        "ensemble": args.ensemble,
        "parallel_seeds": args.parallel_seeds,
        "parameters": {key: value for key, value in parameters.items() if key != "seeds"},
        "seeds": parameters["seeds"],
        "records": [],
        "analysis": None,
        "status": "INCOMPLETE",
    }
    write_results(results_path, results)

    chain_names = ("kinetic_energy", "mean_mv2_x", "mean_mv2_y", "mean_mv2_z")
    chains_by_name = {name: [] for name in chain_names}
    with ThreadPoolExecutor(max_workers=args.parallel_seeds) as executor:
        seed_runs = executor.map(
            lambda seed: run_seed(
                candidate, work_root, seed, parameters, binary_hash, manifest_hash, args.ensemble
            ),
            parameters["seeds"],
        )
        for record, series in seed_runs:
            results["records"].append(record)
            if series is not None:
                for name in chain_names:
                    chains_by_name[name].append(series[name])
            write_results(results_path, results)
            print(f"{record['status']} harmonic seed {record['seed']}", flush=True)

    try:
        results["analysis"] = analyze(results["records"], chains_by_name, manifest, parameters)
        results["status"] = results["analysis"]["status"]
    except (ValueError, OverflowError) as error:
        results["analysis"] = {"status": "FAIL", "error": str(error), "moments": {}}
        results["status"] = "FAIL"
    write_results(results_path, results)
    print(f"harmonic ensemble status: {results['status']} (evidence: {results_path})", flush=True)
    return 0 if results["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
