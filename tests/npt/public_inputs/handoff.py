#!/usr/bin/env python3
"""Publish one validated million-atom input after replaying the holdout in fresh jobs."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from analyze_preparation import CONDITIONS, MANIFEST
from prepare import (
    CALIBRATION_SEEDS, FIXTURES, check_completed_stage, check_preparation, preparation_stages, sha256,
)
from analyze_pressure_direction import check_job
from replay import ENGINES, verify_job
from static_observables import WATER_SELECTION_FILES, check_holdout_selection, water_selection_provenance

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_static(root, material, source, frame, ref_sha, final_stage):
    base = root / material
    static = root / "static" / material
    approval = water_selection_provenance(base, frame["sha256"]) if material == "water" else None
    calibration = read(static / "calibration.json")
    require(calibration["calibration_status"] == "PASS", "static calibration did not pass")
    require(calibration["material"] == material, "static calibration belongs to another material")
    pilot_sha = []
    measurement_sha = []
    for seed in CALIBRATION_SEEDS:
        stage = f"calibration-{seed}"
        record = check_completed_stage(base / stage, stage, FIXTURES[material], seed,
                                       source["model_sha256"], ref_sha)
        measurement = static / stage / "measurement.json"
        measured = read(measurement)
        require(measured["material"] == material
                and measured["trial"] == stage
                and measured["source"] == {"model": record["snapshot"]["sha256"],
                                           "potential": source["potential_sha256"]}
                and measured["engines"]["gpumd"]["sha256"] == ref_sha
                and measured["trial_evidence"]["seed"] == seed
                and measured["trial_evidence"]["sha256"] == sha256(base / stage / "execution.json"),
                f"calibration evidence changed: {measurement}")
        measurement_sha.append(sha256(measurement))
        pilot_sha.append(record["snapshot"]["sha256"])
    require(sorted(measurement_sha) == sorted(calibration["measurements"].values()),
            "calibration measurements differ from the frozen trials")
    require(len(set(pilot_sha)) == len(CALIBRATION_SEEDS)
            and set(pilot_sha) == set(calibration["snapshot_sha256"])
            and frame["sha256"] not in pilot_sha, "calibration and holdout identities differ from the trials")
    candidate_path = static / "holdout/measurement.json"
    actual = read(candidate_path)
    verification = read(static / "holdout-verification.json")
    require(actual["material"] == calibration["material"] == verification["material"] == material
            and actual["trial"] == "holdout"
            and actual["trial_evidence"].get("preparation_stage", "scr") == final_stage
            and actual["trial_evidence"]["sha256"] == sha256(base / final_stage / "execution.json")
            and actual["trial_evidence"]["calibration_sha256"] == sha256(static / "calibration.json")
            and actual["source"] == {"model": frame["sha256"], "potential": source["potential_sha256"]}
            and actual["engines"]["gpumd"]["sha256"] == ref_sha
            and verification["holdout_measurement_sha256"] == sha256(candidate_path)
            and verification["calibration_sha256"] == sha256(static / "calibration.json")
            and verification["numeric_status"] == "PASS", "held-out static verification failed or stale")
    if approval:
        check_holdout_selection(actual["trial_evidence"], actual["started_utc"], approval)
    diagnosis = read(root / "static/wpe-diagnosis.json")
    wpe = actual["engines"]["wpe"]
    require(calibration["wpe_diagnosis"]["sha256"] == sha256(root / "static/wpe-diagnosis.json")
            and diagnosis["release_qualification_status"] == "PASS"
            and diagnosis["sha256"]["gpumd_5_9_1"] == ref_sha
            and diagnosis["sha256"]["wpe"] == wpe["sha256"]
            and diagnosis["sha256"]["wpe_library"] == wpe["library"]["sha256"]
            and sha256(Path(wpe["library"]["path"])) == wpe["library"]["sha256"],
            "WPE backend diagnosis is incomplete or uses another build")
    return actual


def publish(args):
    root = args.root.resolve(strict=True)
    material = args.material
    base = root / material
    static = root / "static" / material
    stages = preparation_stages(material, evidence_dir=base)
    final_stage = stages[-1]
    source, frame, ref_sha = check_preparation(base, material)
    potential_sha = source["potential_sha256"]
    directions = read(args.direction)
    for name in ("expand", "contract"):
        original = check_job(args.direction.parent / name, name)
        require(original == directions["cases"][name], f"{name} pressure direction evidence changed")
        require(sha256(Path(original["source_model"])) == original["source_model_sha256"],
                f"{name} pressure probe source changed")
    require(directions["status"] == "PASS" and all(
        directions["cases"][name]["status"] == "PASS"
        and directions["cases"][name]["material"] == material
        and directions["cases"][name]["potential_sha256"] == potential_sha
        and directions["cases"][name]["gpumd_sha256"] == ref_sha
        and directions["cases"][name]["atoms"] == frame["atoms"]
        for name in ("expand", "contract")), "pressure direction evidence failed or belongs to another case")
    actual = check_static(root, material, source, frame, ref_sha, final_stage)

    lammps_geometry = actual["engines"]["lammps"].get("geometry", "native")
    pilot_hashes = read(static / "calibration.json")["snapshot_sha256"]
    capacity = []
    for engine in ENGINES:
        for ranks in ((1,) if engine == "wpe" else (1, 2, 4)):
            path = root / "capacity" / f"{material}-{engine}-p{ranks}" / "result.json"
            row = read(path)
            require(row["material"] == material and row["engine"] == engine and len(row["devices"]) == ranks
                    and row["nep.txt_sha256"] == potential_sha and row["atoms"] == frame["atoms"]
                    and row["binary_sha256"] == actual["engines"][engine]["sha256"]
                    and row["model.xyz_sha256"] in pilot_hashes,
                    f"capacity row mismatch: {path}")
            require(row["status"] in ("PASS", "CAPACITY_LIMIT") and row["timed_out"] is False
                    and ((row["status"] == "PASS") == (row["returncode"] == 0)),
                    f"unresolved capacity probe: {path}")
            if engine == "lammps":
                require(row.get("geometry", "native") == lammps_geometry, f"MatPL geometry mismatch: {path}")
            if row["status"] == "PASS":
                row.update(verify_job(path.parent, engine, row["devices"], row["atoms"], lammps_geometry))
            capacity.append(row)

    # A directory without manifest.json is an incomplete attempt. Retain failed job evidence.
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(base / final_stage / "restart.xyz", output / "model.xyz")
    shutil.copyfile(base / "nep.txt", output / "nep.txt")
    for engine in ENGINES:
        binary = Path(actual["engines"][engine]["binary"])
        require(sha256(binary) == actual["engines"][engine]["sha256"], f"{engine} executable changed")
        geometry_args = ["--lammps-geometry", lammps_geometry] if engine == "lammps" else []
        subprocess.run([sys.executable, str(HERE / "replay.py"), *geometry_args, "--engine", engine, "--binary", str(binary),
                        "--material", material, "--devices", args.device, "--model", str(output / "model.xyz"),
                        "--model-sha256", frame["sha256"], "--potential", str(output / "nep.txt"),
                        "--potential-sha256", potential_sha, "--output", str(output / "replay" / engine)],
                       check=True)
        if engine == "wpe":
            result = read(output / "replay" / engine / "result.json")
            require(result["library"]["sha256"] == actual["engines"][engine]["library"]["sha256"],
                    "replay selected a different Wisevolve library")
    evidence = {"source.json": base / "source.json", "direction.json": args.direction,
                "wpe-diagnosis.json": root / "static/wpe-diagnosis.json",
                "static-calibration.json": static / "calibration.json",
                "static-measurement.json": static / "holdout/measurement.json",
                "static-verification.json": static / "holdout-verification.json"}
    for relative, digest in read(root / "static/wpe-diagnosis.json")["evidence"].items():
        path = (root / "static" / relative).resolve(strict=True)
        require(path.is_relative_to(root / "static") and not Path(relative).is_absolute()
                and ".." not in Path(relative).parts and sha256(path) == digest, "invalid WPE evidence")
        evidence[f"wpe/{relative}"] = path
    for row in capacity:
        cell = f"{material}-{row['engine']}-p{len(row['devices'])}"
        files = ["result.json", "telemetry.jsonl", "stdout.txt", "stderr.txt"]
        if row["engine"] == "lammps":
            files.extend(("in.lmp", "conversion.json"))
        else:
            files.append("run.in")
            if row["status"] == "PASS":
                files.append("thermo.out")
        for name in files:
            evidence[f"capacity/{cell}/{name}"] = root / "capacity" / cell / name
    for seed in CALIBRATION_SEEDS:
        trial = f"calibration-{seed}"
        evidence[f"{trial}/measurement.json"] = static / trial / "measurement.json"
        evidence[f"{trial}/execution.json"] = base / trial / "execution.json"
    for trial in [*(f"calibration-{seed}" for seed in CALIBRATION_SEEDS), "holdout"]:
        for name in ("in.lmp", "run.log"):
            evidence[f"{trial}/lammps/{name}"] = static / trial / "lammps" / name
    for stage in stages:
        job = base / stage
        for name in ("run.in", "execution.json", "thermo.out"):
            evidence[f"{stage}/{name}"] = job / name
        for name in ("summary.json", "thermo.csv", "thermo.svg"):
            evidence[f"{stage}/{name}"] = job / "analysis" / name
    if material == "water":
        evidence.update({name: base / name for name in WATER_SELECTION_FILES})
    for name, path in evidence.items():
        target = output / "evidence" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    manifest = {"material": material, "preparation_stage": final_stage, "model_file": "model.xyz", "model": frame,
                "potential_file": "nep.txt", "potential_sha256": potential_sha,
                "atom_order": "same as public input; one-based ID = XYZ row after header",
                "masses_amu": source["masses_amu"], "time_step_fs": source["time_step_fs"],
                "target_K": CONDITIONS["temperature_K"], "target_GPa": CONDITIONS["pressure_GPa"],
                "temperature_damping_steps": CONDITIONS["temperature_damping_steps"],
                "pressure_damping_steps": CONDITIONS["pressure_damping_steps"], "bulk_modulus_GPa": source["bulk_modulus_GPa"],
                "production_output": MANIFEST["production_output"],
                "replay": {engine: f"replay/{engine}/result.json" for engine in ENGINES},
                "capacity_calibration_frame": capacity,
                "evidence_sha256": {f"evidence/{name}": sha256(output / "evidence" / name) for name in evidence}}
    require(manifest["evidence_sha256"][f"evidence/{final_stage}/execution.json"] == actual["trial_evidence"]["sha256"],
            "published preparation evidence changed during replay")
    require(sha256(output / "model.xyz") == frame["sha256"] and sha256(output / "nep.txt") == potential_sha,
            "published inputs changed during replay")
    if material == "water":
        approval = water_selection_provenance(output / "evidence", frame["sha256"])
        check_holdout_selection(actual["trial_evidence"], actual["started_utc"], approval)
    temp = output / "manifest.json.tmp"
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.rename(output / "manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="preparation, static and capacity evidence root")
    parser.add_argument("--material", choices=FIXTURES, required=True)
    parser.add_argument("--direction", type=Path, required=True, help="successful two-direction report")
    parser.add_argument("--device", required=True, help="single idle GPU for four consecutive replays")
    parser.add_argument("--output", type=Path, required=True, help="new handoff directory")
    try:
        publish(parser.parse_args())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"input not released: {error}\n")


if __name__ == "__main__":
    main()
