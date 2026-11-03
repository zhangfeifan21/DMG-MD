#!/usr/bin/env python3
"""Run preregistered harmonic controls with the locked native LAMMPS build."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]
REFERENCE = ROOT.parent / "lammps-reference"
DEFAULT_LMP = ROOT / "build-lammps-nep-kk-preserved" / "lmp"
INITIAL_SOURCE = SUITE / "reference_harmonic_initial_state.cpp"
STATS_PATH = SUITE / "sample_statistics.py"
MANIFEST_PATH = SUITE / "manifest.json"
CONFIRMATION_PATH = SUITE / "harmonic-confirmation.json"
K_B = 8.617343e-5
_spec = importlib.util.spec_from_file_location("reference_sample_statistics", STATS_PATH)
STATS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(STATS)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def make_initial_state(work: Path, seed: int, temperature: float) -> Path:
    source = work / "reference_harmonic_initial_state.cpp"
    source.write_bytes(INITIAL_SOURCE.read_bytes())
    executable = work / "reference_harmonic_initial_state"
    subprocess.run(["c++", "-std=c++17", "-O2", str(source), "-o", str(executable)],
                   cwd=work, check=True)
    data = work / "initial.data"
    subprocess.run([str(executable), str(data), str(seed), format(temperature, ".17g")],
                   cwd=work, check=True)
    return data


def render_input(data: Path, samples: Path, seed: int, steps: int, thermostat: str,
                 target: float, dt: float, damping: float, split: bool = True) -> str:
    common = f"""units lj
atom_style atomic
boundary f f f
read_data {data}
mass * 1.0
compute reference_temp all temp
compute_modify reference_temp extra/dof 0
compute kinetic all ke
variable v2x atom vx*vx
variable v2y atom vy*vy
variable v2z atom vz*vz
compute sumv2x all reduce sum v_v2x
compute sumv2y all reduce sum v_v2y
compute sumv2z all reduce sum v_v2z
variable wx atom 0.15+0.01*(3*(id-1))
variable wy atom 0.15+0.01*(3*(id-1)+1)
variable wz atom 0.15+0.01*(3*(id-1)+2)
variable fx atom -v_wx*v_wx*x
variable fy atom -v_wy*v_wy*y
variable fz atom -v_wz*v_wz*z
fix harmonic all addforce v_fx v_fy v_fz
timestep {dt:.17g}
"""
    if thermostat == "nvt_nhc":
        control = (f"fix thermostat all nvt temp {target:.17g} {target:.17g} {damping:.17g} tchain 4\n"
                   "fix_modify thermostat temp reference_temp\n")
    else:
        control = ("fix integrate all nve\n"
                   f"fix thermostat all temp/csvr {target:.17g} {target:.17g} {damping:.17g} {seed}\n"
                   "fix_modify thermostat temp reference_temp\n")
    output = (f"fix samples all print 1 \"$(step-1:%.0f),$(c_kinetic:%.17g),"
              "$(c_sumv2x/8.0:%.17g),$(c_sumv2y/8.0:%.17g),$(c_sumv2z/8.0:%.17g)\" "
              f"file {samples} screen no title \"\"\n")
    if split:
        return (common + control + "thermo 0\nthermo_modify temp reference_temp\nrun 1\n" +
                output + f"run {steps - 1}\n")
    return common + control + "thermo 0\nthermo_modify temp reference_temp\n" + output + f"run {steps}\n"


def validate_samples(path: Path, expected: int, allow_initial: bool = False) -> None:
    names = ("kinetic_energy", "mean_mv2_x", "mean_mv2_y", "mean_mv2_z")
    sample_index = 0
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.reader(stream):
            if not row or all(not field.strip() for field in row):
                continue
            index = sample_index
            if len(row) != 5:
                raise ValueError(f"row {index}: expected five fields, found {len(row)}")
            parsed = [float(field) for field in row]
            if allow_initial and sample_index == 0 and parsed[0] == -1:
                continue
            if parsed[0] != index or not all(math.isfinite(x) for x in parsed):
                raise ValueError(f"row {index}: bad step index or non-finite field")
            if any(x <= 0 for x in parsed[1:]):
                raise ValueError(f"row {index}: non-positive observable")
            sample_index += 1
    if sample_index != expected:
        raise ValueError(f"expected {expected} rows, found {sample_index}")


def read_series(path: Path, name: str, expected: int, discard: int,
                allow_initial: bool = False) -> list[float]:
    names = ("kinetic_energy", "mean_mv2_x", "mean_mv2_y", "mean_mv2_z")
    column = names.index(name) + 1
    result = []
    sample_index = 0
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.reader(stream):
            if not row or all(not field.strip() for field in row):
                continue
            parsed = [float(field) for field in row]
            if allow_initial and sample_index == 0 and parsed[0] == -1:
                continue
            if sample_index >= discard:
                result.append(parsed[column])
            sample_index += 1
    if sample_index != expected or len(result) != expected - discard:
        raise ValueError(f"{name}: expected {expected - discard} retained values, found {len(result)}")
    return result


def run_one(lmp: Path, work: Path, seed: int, steps: int, discard: int,
            thermostat: str, temperature: float, dt: float, damping: float,
            launcher: list[str], split: bool = True, variant: str = "split") -> tuple[dict, dict | None]:
    stage = work / f"{thermostat}_seed_{seed}_{variant}"
    stage.mkdir()
    data = make_initial_state(stage, seed, temperature)
    input_path, output, log = stage / "in.harmonic", stage / "samples.csv", stage / "lammps.log"
    input_path.write_text(render_input(data, output, seed, steps, thermostat,
                                       K_B * temperature, dt, damping, split=split), encoding="utf-8")
    command = launcher + [str(lmp), "-in", str(input_path), "-log", str(log), "-screen", "none"]
    record = {"seed": seed, "thermostat": thermostat, "variant": variant, "split_run_setup": split,
              "steps": steps, "discard_steps": discard,
              "command": command, "cwd": str(stage), "input_sha256": digest(input_path),
              "initial_data_sha256": digest(data), "lammps_sha256": digest(lmp), "status": "FAIL"}
    stdout_path, stderr_path = stage / "stdout.log", stage / "stderr.log"
    started = time.monotonic()
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        completed = subprocess.run(command, cwd=stage, stdout=stdout, stderr=stderr,
                                   env={**os.environ, "OMP_NUM_THREADS": "1"}, check=False)
    record["elapsed_seconds"] = time.monotonic() - started
    record["exit_status"] = completed.returncode
    for label, path in (("stdout", stdout_path), ("stderr", stderr_path), ("log", log), ("samples", output)):
        if path.is_file():
            record[f"{label}_sha256"] = digest(path)
    if completed.returncode:
        record["error"] = "LAMMPS returned nonzero; inspect stdout/stderr/log"
        return record, None
    try:
        validate_samples(output, steps, allow_initial=not split)
    except (OSError, ValueError) as error:
        record["error"] = str(error)
        return record, None
    record.update({"sample_count": steps, "retained_samples": steps - discard, "status": "PASS"})
    record["samples_path"] = str(output)
    return record, output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", choices=("timing", "smoke", "split-validation", "pilot", "confirmation"), default="pilot")
    parser.add_argument("--lmp", type=Path, default=DEFAULT_LMP)
    parser.add_argument("--build-metadata", type=Path)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--launcher", default="", help="optional MPI launcher and flags")
    parser.add_argument("--steps", type=int, default=10000, help="timing campaign only")
    args = parser.parse_args()
    lmp, work = args.lmp.resolve(strict=True), args.work_root.resolve()
    work.mkdir(parents=True, exist_ok=False)
    manifest_bytes = MANIFEST_PATH.read_bytes()
    manifest = json.loads(manifest_bytes)
    pilot_harmonic = manifest["harmonic"]
    confirmation_bytes = CONFIRMATION_PATH.read_bytes()
    confirmation_manifest = json.loads(confirmation_bytes)
    confirmation_harmonic = confirmation_manifest["harmonic"]
    head = subprocess.check_output(["git", "-C", str(REFERENCE), "rev-parse", "HEAD"], text=True).strip()
    if head != "611ca3b7f8525ba802373b04f9e3d632d515f7e3":
        raise RuntimeError(f"unexpected locked LAMMPS revision {head}")
    reference_status_before = subprocess.check_output(
        ["git", "-C", str(REFERENCE), "status", "--short", "--ignored"], text=True).strip() or "clean"
    if args.campaign in ("timing", "smoke"):
        cfg = pilot_harmonic
        timing_steps = args.steps if args.campaign == "timing" else 100
        cases = [(thermostat, [cfg["seeds"][0]], timing_steps, 0, cfg["dt_internal"],
                  cfg["dt_internal"] * cfg["damping_steps"], cfg["temperature_K"], None)
                 for thermostat in (("nvt_nhc",) if args.campaign == "timing" else ("nvt_nhc", "nvt_bdp"))]
    elif args.campaign in ("pilot", "split-validation"):
        cfg = pilot_harmonic
        if args.campaign == "split-validation":
            cases = []
        else:
            cases = [(name, cfg["seeds"], cfg["steps"], cfg["discard_steps"], cfg["dt_internal"],
                      cfg["dt_internal"] * cfg["damping_steps"], cfg["temperature_K"], cfg["acceptance"])
                     for name in ("nvt_nhc", "nvt_bdp")]
    else:
        cfg = confirmation_harmonic
        cases = [("nvt_nhc", cfg["seeds"], cfg["steps"], cfg["discard_steps"], cfg["dt_internal"],
                  cfg["dt_internal"] * cfg["damping_steps"], cfg["temperature_K"], cfg["acceptance"])]
    active_harmonic = confirmation_harmonic if args.campaign == "confirmation" else pilot_harmonic
    doc = {"campaign": args.campaign, "lammps": str(lmp), "lammps_sha256": digest(lmp),
           "reference": str(REFERENCE), "reference_head": head,
           "reference_status_before": reference_status_before,
           "driver_command": {"argv": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
                              "cwd": str(ROOT), "required_shell_setup": "source ../env/md-mpi.sh"},
           "environment": {"OMP_NUM_THREADS": "1", "DMGMD_ACF_HELPER": os.environ.get("DMGMD_ACF_HELPER")},
           "build_metadata": json.loads(args.build_metadata.read_text()) if args.build_metadata else None,
           "preregistration_sha256": {str(MANIFEST_PATH): hashlib.sha256(manifest_bytes).hexdigest(),
                                      str(CONFIRMATION_PATH): hashlib.sha256(confirmation_bytes).hexdigest()},
           "sources": {str(Path(__file__).resolve()): digest(Path(__file__).resolve()),
                       str(INITIAL_SOURCE): digest(INITIAL_SOURCE), str(STATS_PATH): digest(STATS_PATH),
                       (str(Path(os.environ["DMGMD_ACF_HELPER"]).resolve())
                        if os.environ.get("DMGMD_ACF_HELPER") else "DMGMD_ACF_HELPER (unset)"):
                           digest(Path(os.environ["DMGMD_ACF_HELPER"]).resolve())
                           if os.environ.get("DMGMD_ACF_HELPER") and
                           Path(os.environ["DMGMD_ACF_HELPER"]).is_file() else None},
           "protocol": {"atoms": active_harmonic["natoms"], "mass": 1, "kB": K_B,
                        "temperature_K": active_harmonic["temperature_K"],
                        "target_lj_temperature": K_B * active_harmonic["temperature_K"],
                        "dt": active_harmonic["dt_internal"],
                        "Tdamp": active_harmonic["dt_internal"] * active_harmonic["damping_steps"],
                        "omega_mode": "0.15 + 0.01*mode", "interaction": "none; per-mode harmonic force",
                        "NHC": "fix nvt tchain 4", "BDP": "fix temp/csvr with fix nve",
                        "temp_dof": "extra/dof 0", "initial_state": "sampler mt19937_64/std::normal_distribution sequence",
                        "sample_schedule": "run 1, then install fix print at step 1, then run N-1; t=1 output is sampler sample 0; FixNH::setup on the second run recomputes masses/derivatives without resetting eta/eta_dot; 1000-step continuous comparisons are exact for both fixes",
                        "integrator_differences": "LAMMPS fix nvt uses its native Nose-Hoover chain/timestep splitting (tchain 4), which differs from DMG-MD controller ordering; LAMMPS fix temp/csvr is paired with fix nve and applies its thermostat at end-of-step, also differing from DMG-MD splitting. Physical mode frequencies, mass, kT, dt, damping, initial state, and measured observables are matched; algorithm splitting is reported, not treated as identical."},
           "campaign_parameters": [{"thermostat": item[0], "seeds": item[1], "steps": item[2],
                                    "discard_steps": item[3], "dt_internal": item[4],
                                    "Tdamp": item[5], "temperature_K": item[6], "acceptance": item[7]}
                                   for item in cases],
           "records": [], "status": "INCOMPLETE"}
    results_path = work / "results.json"
    results_path.write_text(json.dumps(doc, indent=2) + "\n")
    if args.campaign == "split-validation":
        cfg = pilot_harmonic
        compare_steps = 1000
        comparisons = {}
        for thermostat in ("nvt_nhc", "nvt_bdp"):
            split_record, split_samples = run_one(
                lmp, work, cfg["seeds"][0], compare_steps, 0, thermostat,
                cfg["temperature_K"], cfg["dt_internal"], cfg["dt_internal"] * cfg["damping_steps"],
                shlex.split(args.launcher), split=True, variant="split")
            continuous_record, continuous_samples = run_one(
                lmp, work, cfg["seeds"][0], compare_steps, 0, thermostat,
                cfg["temperature_K"], cfg["dt_internal"], cfg["dt_internal"] * cfg["damping_steps"],
                shlex.split(args.launcher), split=False, variant="continuous")
            doc["records"].extend((split_record, continuous_record))
            if split_samples is None or continuous_samples is None:
                comparisons[thermostat] = {"status": "FAIL", "reason": "run/sample validation failed"}
                continue
            deltas = {}
            for name in ("kinetic_energy", "mean_mv2_x", "mean_mv2_y", "mean_mv2_z"):
                split_series = read_series(split_samples, name, compare_steps, 0)
                continuous_series = read_series(continuous_samples, name, compare_steps, 0, allow_initial=True)
                deltas[name] = max(abs(a - b) for a, b in zip(split_series, continuous_series))
            comparisons[thermostat] = {"status": "PASS" if all(value == 0.0 for value in deltas.values()) else "FAIL",
                                       "steps": compare_steps, "seed": cfg["seeds"][0],
                                       "exact_series_match": all(value == 0.0 for value in deltas.values()),
                                       "maximum_absolute_difference": deltas,
                                       "split_samples_sha256": split_record.get("samples_sha256"),
                                       "continuous_samples_sha256": continuous_record.get("samples_sha256")}
        doc["split_continuity_comparison"] = comparisons
        doc["status"] = "PASS" if all(item["status"] == "PASS" for item in comparisons.values()) else "FAIL"
        doc["completed_unix"] = time.time()
        doc["reference_status_after"] = subprocess.check_output(
            ["git", "-C", str(REFERENCE), "status", "--short", "--ignored"], text=True).strip() or "clean"
        results_path.write_text(json.dumps(doc, indent=2, allow_nan=False) + "\n")
        print(f"split-vs-continuous setup comparison: {doc['status']} ({results_path})", flush=True)
        return 0 if doc["status"] == "PASS" else 1
    for thermostat, seeds, steps, discard, dt, damping, temperature, acceptance in cases:
        results_by_seed = {}
        with ThreadPoolExecutor(max_workers=min(8, len(seeds))) as pool:
            futures = {pool.submit(run_one, lmp, work, seed, steps, discard, thermostat,
                                   temperature, dt, damping, shlex.split(args.launcher)): seed
                       for seed in seeds}
            for future in as_completed(futures):
                seed = futures[future]
                record, data = future.result()
                results_by_seed[seed] = (record, data)
                doc["records"] = [item for item in doc["records"]
                                  if item["thermostat"] != thermostat]
                doc["records"].extend(results_by_seed[key][0] for key in seeds if key in results_by_seed)
                doc["records"].sort(key=lambda item: (item["thermostat"], item["seed"]))
                results_path.write_text(json.dumps(doc, indent=2) + "\n")
                print(f"{record['status']} {thermostat} seed {seed} {record['elapsed_seconds']:.2f}s", flush=True)
        doc["records"] = [item for item in doc["records"] if item["thermostat"] != thermostat]
        doc["records"].extend(results_by_seed[key][0] for key in seeds)
        doc["records"].sort(key=lambda item: (item["thermostat"], item["seed"]))
        if acceptance:
            records = [r for r in doc["records"] if r["thermostat"] == thermostat]
            if any(record["status"] != "PASS" for record in records):
                analysis = {"status": "FAIL", "error": "at least one run failed validation", "moments": {}}
            else:
                kt = K_B * temperature
                expected = {
                    "kinetic_energy": (3 * active_harmonic["natoms"] * kt / 2,
                                       3 * active_harmonic["natoms"] * kt * kt / 2),
                    "mean_mv2_x": (kt, 2 * kt * kt / active_harmonic["natoms"]),
                    "mean_mv2_y": (kt, 2 * kt * kt / active_harmonic["natoms"]),
                    "mean_mv2_z": (kt, 2 * kt * kt / active_harmonic["natoms"]),
                }
                moments = {}
                for name, (expected_mean, expected_variance) in expected.items():
                    chains = [read_series(Path(results_by_seed[seed][1]), name, steps, discard)
                              for seed in seeds]
                    moments[name] = STATS.analyze_series(chains, expected_mean, expected_variance, acceptance)
                    del chains
                moment_statuses = [item["status"] for item in moments.values()]
                result_status = ("PASS" if all(status == "PASS" for status in moment_statuses) else
                                 "FAIL" if "FAIL" in moment_statuses else "INCONCLUSIVE")
                analysis = {"status": result_status, "kT": kt,
                            "expected_moments": {key: {"mean": value[0], "variance": value[1]}
                                                 for key, value in expected.items()},
                            "acceptance": acceptance, "moments": moments}
            doc.setdefault("analysis", {})[thermostat] = analysis
            results_path.write_text(json.dumps(doc, indent=2) + "\n")
            print(f"{thermostat} statistics {doc['analysis'][thermostat]['status']}", flush=True)
    statuses = [entry["status"] for entry in doc.get("analysis", {}).values()]
    doc["status"] = ("FAIL" if "FAIL" in statuses else
                     "PASS" if statuses and all(x == "PASS" for x in statuses) else
                     "PASS" if args.campaign in ("timing", "smoke") and doc["records"] and
                     all(record["status"] == "PASS" for record in doc["records"]) else
                     "INCONCLUSIVE")
    doc["completed_unix"] = time.time()
    doc["reference_status_after"] = subprocess.check_output(
        ["git", "-C", str(REFERENCE), "status", "--short", "--ignored"], text=True).strip() or "clean"
    results_path.write_text(json.dumps(doc, indent=2, allow_nan=False) + "\n")
    print(f"campaign {args.campaign}: {doc['status']} ({results_path})", flush=True)
    return 0 if doc["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
