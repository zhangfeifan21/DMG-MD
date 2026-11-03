#!/usr/bin/env python3
"""Run the registered harmonic fixture through GPUMD's native GPU integrator.

Requires the isolated test adapter built by build_gpumd_harmonic_native.py.
The adapter removes FCP's inappropriate net-force projection for this external
field, exposes a fixed BDP seed, and observes post-thermostat velocities. Its
NHC/SVR formulas and GPU integration kernels remain the locked reference code.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


FIXTURE = module("gpumd_native_fixture", SUITE / "reference_gpumd_harmonic.py")
HARMONIC = module("gpumd_native_statistics", SUITE / "run_harmonic.py")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(2**20), b""):
            digest.update(data)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--devices", required=True, help="distinct physical GPU indices, comma separated")
    parser.add_argument("--campaign", choices=("smoke", "pilot", "confirmation"), required=True)
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    devices = args.devices.split(",")
    if not devices or any(not d.isdecimal() for d in devices) or len(set(devices)) != len(devices):
        parser.error("--devices must contain distinct nonnegative GPU indices")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    candidate = args.candidate.resolve(strict=True)
    if not os.access(candidate, os.X_OK):
        parser.error("--candidate must be executable")
    manifest_path = SUITE / ("harmonic-confirmation.json" if args.campaign == "confirmation" else "manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    settings = dict(manifest["harmonic"])
    if args.campaign == "smoke":
        settings.update(steps=200, discard_steps=0, seeds=[101])
    ensembles = ["nvt_nhc"] if args.campaign == "confirmation" else ["nvt_nhc", "nvt_bdp"]
    work = args.work_root.resolve()
    work.mkdir(parents=True, exist_ok=False)
    (work / "manifest.json").write_bytes(manifest_bytes)
    binary_hash = sha(candidate)
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(), "status": "INCOMPLETE",
        "campaign": args.campaign, "argv": sys.argv, "cwd": str(Path.cwd()),
        "candidate": str(candidate), "candidate_sha256": binary_hash,
        "devices": devices, "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "settings": settings, "records": [], "analysis": {},
        "sources_sha256": {p.name: sha(p) for p in (
            Path(__file__), SUITE / "reference_gpumd_harmonic.py",
            SUITE / "run_harmonic.py", SUITE / "sample_statistics.py")},
        "coverage": "Native locked GPUMD GPU integration and thermostats, with the recorded external-FCP/seed/observer test adapter; FCP forces use the original float arithmetic.",
        "environment": {k: os.environ[k] for k in ("DMGMD_ACF_HELPER", "OMP_NUM_THREADS") if k in os.environ},
    }
    if "DMGMD_ACF_HELPER" in os.environ:
        report["acf_helper_sha256"] = sha(Path(os.environ["DMGMD_ACF_HELPER"]))
    result_path = work / "results.json"

    def save():
        result_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")

    save()

    def run_one(task, device):
        ensemble, seed = task
        stage = work / f"{ensemble}_seed_{seed}"
        fixture_args = argparse.Namespace(
            outputdir=stage, seed=seed, steps=settings["steps"],
            temperature=settings["temperature_K"], dt_internal=settings["dt_internal"],
            damping_steps=settings["damping_steps"], ensemble=ensemble, cxx="c++")
        FIXTURE.write_native_fixture(fixture_args)
        original_fixture = stage / "fixture.json"
        original_fixture.rename(stage / "fixture-source.json")
        run_input = stage / "run.in"
        lines = run_input.read_text().splitlines()
        if args.campaign == "smoke":
            lines = [line.replace("precision double velocity", "precision double velocity force potential") for line in lines]
        else:
            lines = [line for line in lines if not line.startswith("dump_xyz ")]
        run_input.write_text("\n".join(lines) + "\n")
        samples = stage / "samples.csv"
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": device,
               "GPUMD_TEST_EXTERNAL_FCP": "1", "GPUMD_TEST_SEED": str(seed),
               "GPUMD_TEST_SAMPLES": str(samples), "GPUMD_TEST_ENSEMBLE": ensemble}
        record = {
            "ensemble": ensemble, "seed": seed, "device": device, "status": "FAIL",
            "command": [str(candidate)], "cwd": str(stage), "candidate_sha256": binary_hash,
            "environment": {k: env[k] for k in env if k.startswith("GPUMD_TEST_") or k == "CUDA_VISIBLE_DEVICES"},
            "input_sha256": {str(p.relative_to(stage)): sha(p) for p in stage.rglob("*") if p.is_file()},
            "samples": str(samples), "started_at": datetime.now(timezone.utc).isoformat(),
        }
        start = time.monotonic()
        with (stage / "stdout.log").open("x") as stdout, (stage / "stderr.log").open("x") as stderr:
            try:
                process = subprocess.run([str(candidate)], cwd=stage, env=env,
                                         stdout=stdout, stderr=stderr, timeout=args.timeout)
                record["exit_status"] = process.returncode
            except subprocess.TimeoutExpired:
                record.update(exit_status=None, error="native GPUMD exceeded the recorded timeout")
        record["elapsed_seconds"] = time.monotonic() - start
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        for name in ("stdout.log", "stderr.log", "samples.csv", "samples.xyz"):
            if (stage / name).is_file():
                record[name + "_sha256"] = sha(stage / name)
        series = None
        if record["exit_status"] == 0:
            try:
                columns = HARMONIC.parse_csv(samples, settings["steps"], ensemble)
                if "number of GPUs = 1" not in (stage / "stdout.log").read_text():
                    raise ValueError("native GPUMD did not confirm one visible GPU")
                series = {name: values[settings["discard_steps"]:] for name, values in columns.items() if name != "step"}
                record.update(status="PASS", sample_count=settings["steps"],
                              retained_samples=settings["steps"] - settings["discard_steps"])
            except (OSError, ValueError) as error:
                record["error"] = str(error)
        (stage / "run-record.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"{record['status']} native GPUMD {ensemble} seed {seed} device {device}", flush=True)
        return record, series

    tasks = [(ensemble, seed) for ensemble in ensembles for seed in settings["seeds"]]

    def run_device(index):
        # A device has one worker, so two independent jobs never overlap on it.
        return [run_one(task, devices[index]) for task in tasks[index::len(devices)]]

    by_task = {}
    with ThreadPoolExecutor(max_workers=len(devices)) as executor:
        for results in executor.map(run_device, range(len(devices))):
            for record, series in results:
                by_task[(record["ensemble"], record["seed"])] = (record, series)
                report["records"] = [by_task[task][0] for task in tasks if task in by_task]
                save()
    if any(record["status"] != "PASS" for record in report["records"]):
        report["status"] = "FAIL"
    elif args.campaign == "smoke":
        report["status"] = "PASS"
    else:
        names = ("kinetic_energy", "mean_mv2_x", "mean_mv2_y", "mean_mv2_z")
        kt = 8.617343e-5 * settings["temperature_K"]
        natoms = settings["natoms"]
        expected = {"kinetic_energy": (1.5 * natoms * kt, 1.5 * natoms * kt * kt),
                    **{name: (kt, 2 * kt * kt / natoms) for name in names[1:]}}
        for ensemble in ensembles:
            def analyze(name):
                chains = [by_task[(ensemble, seed)][1][name] for seed in settings["seeds"]]
                return name, HARMONIC._STATISTICS.analyze_series(chains, *expected[name], settings["acceptance"])
            # Independent observables use the unchanged analyzer. Native ACF
            # helper processes run concurrently; arithmetic order is preserved.
            with ThreadPoolExecutor(max_workers=4) as executor:
                moments = dict(executor.map(analyze, names))
            statuses = [m["status"] for m in moments.values()]
            status = "FAIL" if "FAIL" in statuses else "PASS" if all(s == "PASS" for s in statuses) else "INCONCLUSIVE"
            report["analysis"][ensemble] = {"status": status, "moments": moments, "acceptance": settings["acceptance"]}
            save()
        statuses = [analysis["status"] for analysis in report["analysis"].values()]
        report["status"] = "FAIL" if "FAIL" in statuses else "PASS" if all(s == "PASS" for s in statuses) else "INCONCLUSIVE"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
    print(f"native GPUMD {args.campaign}: {report['status']} ({result_path})", flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
