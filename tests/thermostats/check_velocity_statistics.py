#!/usr/bin/env python3
"""Check per-axis velocity moments on the preregistered interacting NEP fixture."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]
sys.path.insert(0, str(ROOT / "tests/baseline"))
import run_baselines as baseline
import run_thermostats as thermostats

K_B = 8.617343e-5
TEMPERATURE_K = 300.0
TIME_UNIT_CONVERSION = 1.018051e1
ATOMS = 432
MASS_AMU = 12.011


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract() -> tuple[dict, dict]:
    contract = json.loads((SUITE / "velocity-manifest.json").read_text())
    source = json.loads((SUITE / "manifest.json").read_text())
    source_hash = sha256(SUITE / "manifest.json")
    if contract["input_manifest_sha256"] != source_hash:
        raise RuntimeError("thermostat input manifest hash differs from velocity-manifest.json")
    if contract["fixture"]["seeds"] != source["statistics"]["seeds"]:
        raise RuntimeError("velocity and thermostat manifests disagree on seed set")
    if contract["fixture"]["atoms"] != math.prod(source["geometries"]["cluster_domain"]["cells"]) * 8:
        raise RuntimeError("velocity fixture atom count disagrees with the locked geometry")
    return contract, source


def analysis_config(contract: dict) -> dict:
    """Translate the frozen velocity manifest's frame terminology to analyzer API."""
    frozen = contract["statistics"]
    config = {
        "mean_relative_bound": frozen["mean_relative_bound"],
        "variance_relative_bound": frozen["variance_relative_bound"],
        "confidence_z": frozen["confidence_z"],
        "block_length": frozen["block_length_frames"],
        "minimum_effective_samples": frozen["minimum_effective_samples"],
        "minimum_blocks": frozen["minimum_blocks"],
    }
    if config["block_length"] != frozen["block_length_frames"]:
        raise RuntimeError("frozen block length mapping failed")
    return config


def selected_sampling_looks(contract: dict, minimum_steps: int) -> list[int]:
    return [budget for budget in contract["execution"]["analysis_looks_steps"]
            if budget >= minimum_steps]


def run_input(ensemble: str, steps: int, half_dt: bool) -> str:
    scale = 2 if half_dt else 1
    dt = 0.25 / scale
    damping = 40 * scale
    actual_steps = steps * scale
    lines = [
        "potential nep.txt",
        f"time_step {dt:.17g}",
        f"ensemble {ensemble} 300 300 {damping}",
        f"dump_thermo {50 * scale}",
        f"dump_xyz {50 * scale} v.xyz precision double velocity mass",
        f"dump_restart {actual_steps}",
        f"run {actual_steps}",
    ]
    return "\n".join(lines) + "\n"


def velocity_samples(path: Path, expected_frames: int) -> list[list[float]]:
    """Stream XYZ frames and return three per-frame mass-weighted moments."""
    samples = [[], [], []]
    frame_count = 0
    with path.open(encoding="utf-8") as stream:
        while True:
            count_line = stream.readline()
            if count_line == "":
                break
            frame_count += 1
            try:
                count = int(count_line.strip())
            except ValueError as error:
                raise RuntimeError(f"{path}: invalid atom count at frame {frame_count}") from error
            if count != ATOMS:
                raise RuntimeError(f"{path}: frame {frame_count} has {count} atoms, expected {ATOMS}")
            comment = stream.readline()
            if not comment:
                raise RuntimeError(f"{path}: truncated comment at frame {frame_count}")
            _, fields = baseline.parse_fields(comment)
            if "Properties" not in fields:
                raise RuntimeError(f"{path}: missing Properties at frame {frame_count}")
            properties = baseline.parse_properties(fields["Properties"])
            offsets: dict[str, tuple[int, int]] = {}
            cursor = 0
            for name, _kind, width in properties:
                offsets[name] = (cursor, width)
                cursor += width
            if offsets.get("vel", (None, None))[1] != 3 or offsets.get("mass", (None, None))[1] != 1:
                raise RuntimeError(f"{path}: frame {frame_count} must contain vel:R:3 and mass:R:1")
            vel_start = offsets["vel"][0]
            mass_index = offsets["mass"][0]
            totals = [[], [], []]
            for atom in range(ATOMS):
                row_line = stream.readline()
                if not row_line:
                    raise RuntimeError(f"{path}: truncated frame {frame_count} at atom {atom}")
                columns = row_line.split()
                if len(columns) != cursor:
                    raise RuntimeError(f"{path}: frame {frame_count} atom {atom} has wrong column count")
                try:
                    mass = float(columns[mass_index])
                    velocity = [float(columns[vel_start + axis]) for axis in range(3)]
                except ValueError as error:
                    raise RuntimeError(f"{path}: nonnumeric mass/velocity in frame {frame_count}") from error
                if not math.isfinite(mass) or mass <= 0 or not all(math.isfinite(value) for value in velocity):
                    raise RuntimeError(f"{path}: non-finite or invalid mass/velocity in frame {frame_count}")
                if not math.isclose(mass, MASS_AMU, rel_tol=0.0, abs_tol=1.0e-9):
                    raise RuntimeError(f"{path}: frame {frame_count} mass differs from locked carbon mass")
                for axis in range(3):
                    value = mass * (velocity[axis] * TIME_UNIT_CONVERSION) ** 2
                    if not math.isfinite(value):
                        raise RuntimeError(f"{path}: non-finite squared velocity in frame {frame_count}")
                    totals[axis].append(value)
            for axis in range(3):
                samples[axis].append(math.fsum(totals[axis]) / ATOMS)
    if frame_count != expected_frames:
        raise RuntimeError(f"{path}: found {frame_count} frames, expected {expected_frames}")
    return samples


def write_results(path: Path, payload: dict, lock: threading.Lock) -> None:
    with lock:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
        temporary.replace(path)


def make_runner_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        candidate=args.candidate.resolve(),
        reference=ROOT.parent / "gpumd-reference/src/gpumd",
        devices=args.devices,
        ranks=[1],
        backends=["HostStaged"],
        work_root=args.work_root.resolve(),
        timeout=1800,
        parallel_seeds=min(len(args.devices), 8),
    )


def execute(contract: dict, runner: thermostats.Runner, args: argparse.Namespace,
            budget: int, seed: int, gpu: str, half_dt: bool) -> tuple[int, Path]:
    profile = {
        "steps": budget,
        "dt_fs": 0.25,
        "damping_steps": 40,
        "thermo_interval": 50,
        "trajectory_interval": 50,
        "communication_interval": budget,
        "statistics": True,
    }
    label = f"velocity-{args.ensemble}-look{budget}" + ("-halfdt" if half_dt else "") + f"-seed{seed}"
    stage = runner.execute(
        label, args.ensemble, "cluster_domain", 1, "HostStaged", profile,
        seed=seed,
        half_dt=half_dt,
        run=run_input(args.ensemble, budget, half_dt),
        extra_env={"CUDA_VISIBLE_DEVICES": gpu},
    )
    runner_record = next(record for record in runner.records if record.get("label") == label)
    runner_record["stdout_path"] = str(stage / "stdout.log")
    runner_record["stderr_path"] = str(stage / "stderr.log")
    runner_record["velocity_output"] = "v.xyz"
    runner_record["velocity_output_sha256"] = sha256(stage / "v.xyz")
    return seed, stage


def run(args: argparse.Namespace) -> int:
    contract, thermostat_manifest = load_contract()
    args.candidate = args.candidate.resolve()
    if not args.candidate.is_file():
        raise RuntimeError(f"candidate binary does not exist: {args.candidate}")
    if not args.devices or len(set(args.devices)) != len(args.devices):
        raise RuntimeError("--devices must contain unique GPU IDs")
    if args.work_root.exists():
        raise RuntimeError(f"work root must be a new directory: {args.work_root}")
    runner_args = make_runner_args(args)
    runner = thermostats.Runner(runner_args, thermostat_manifest)
    # Make the run-specific manifest and a complete, durable evidence index.
    velocity_manifest_path = SUITE / "velocity-manifest.json"
    shutil_manifest = runner.root / "velocity-manifest.json"
    shutil_manifest.write_bytes(velocity_manifest_path.read_bytes())
    results_path = runner.root / "velocity-results.json"
    config = analysis_config(contract)
    expected_mean = K_B * TEMPERATURE_K
    expected_variance = 2.0 * expected_mean * expected_mean / ATOMS
    payload = {
        "schema": "dmgmd.velocity-statistics-results.v1",
        "status": "IN_PROGRESS",
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "candidate": str(args.candidate),
        "candidate_sha256": sha256(args.candidate),
        "velocity_manifest_sha256": sha256(velocity_manifest_path),
        "thermostat_manifest_sha256": sha256(SUITE / "manifest.json"),
        "velocity_script_sha256": sha256(SUITE / "check_velocity_statistics.py"),
        "run_thermostats_sha256": sha256(SUITE / "run_thermostats.py"),
        "sample_statistics_sha256": sha256(SUITE / "sample_statistics.py"),
        "analysis_looks": [],
        "runner_records_path": str(runner.root / "results.json"),
        "records": runner.records,
    }
    result_lock = threading.Lock()
    write_results(results_path, payload, result_lock)
    gpu_pool: queue.Queue[str] = queue.Queue()
    for gpu in args.devices:
        gpu_pool.put(gpu)
    workers = min(len(args.devices), len(contract["fixture"]["seeds"]))
    try:
        for budget in selected_sampling_looks(contract, args.minimum_steps):
            actual_steps = budget * (2 if args.half_dt else 1)
            interval = 50 * (2 if args.half_dt else 1)
            expected_frames = actual_steps // interval

            def sample(seed: int) -> tuple[int, Path]:
                gpu = gpu_pool.get()
                try:
                    return execute(contract, runner, args, budget, seed, gpu, args.half_dt)
                finally:
                    gpu_pool.put(gpu)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                stages = list(executor.map(sample, contract["fixture"]["seeds"]))
            chains = [[[], [], []] for _ in contract["fixture"]["seeds"]]
            burn_frames = int(expected_frames * contract["statistics"]["burn_in_fraction"])
            for chain_index, (_seed, stage) in enumerate(stages):
                axes = velocity_samples(stage / "v.xyz", expected_frames)
                for axis in range(3):
                    chains[chain_index][axis] = axes[axis][burn_frames:]

            axis_results = {}
            for axis, name in enumerate(("x", "y", "z")):
                analysis_chains = [chain[axis] for chain in chains]
                axis_results[name] = thermostats.analysis.analyze_series(
                    analysis_chains, expected_mean, expected_variance, config
                )
            statuses = {item["status"] for item in axis_results.values()}
            look_status = "FAIL" if "FAIL" in statuses else ("PASS" if statuses == {"PASS"} else "INCONCLUSIVE")
            payload["analysis_looks"].append({
                "steps": budget,
                "actual_steps": actual_steps,
                "frames_per_seed": expected_frames,
                "burn_in_frames_per_seed": burn_frames,
                "seeds": list(contract["fixture"]["seeds"]),
                "status": look_status,
                "axis_analysis": axis_results,
            })
            payload["status"] = look_status
            write_results(results_path, payload, result_lock)
            print(json.dumps(payload["analysis_looks"][-1], allow_nan=False), flush=True)
            if look_status == "FAIL":
                raise RuntimeError(f"velocity moment check failed at {budget} steps")
            if look_status == "PASS":
                break
        if payload["status"] == "INCONCLUSIVE":
            raise RuntimeError("velocity moments remain INCONCLUSIVE at the preregistered 80000-step budget")
        payload["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_results(results_path, payload, result_lock)
        print(f"PASS velocity moments; evidence {runner.root}")
        return 0
    except Exception as error:
        payload["status"] = "FAIL" if not isinstance(error, RuntimeError) or "INCONCLUSIVE" not in str(error) else "INCONCLUSIVE"
        payload["error"] = str(error)
        payload["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_results(results_path, payload, result_lock)
        raise


def _verify_file(path: Path, expected: str, description: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"missing {description}: {path}")
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"{description} SHA-256 mismatch at {path}: expected {expected}, got {actual}")
    return actual


def analyze_existing(args: argparse.Namespace) -> int:
    """Reanalyze only complete, hash-verified looks from a frozen run directory."""
    root = args.work_root.resolve()
    output_path = root / "velocity-reanalysis.json"
    if output_path.exists():
        raise RuntimeError(f"refusing to overwrite existing reanalysis: {output_path}")
    velocity_manifest_path = root / "velocity-manifest.json"
    thermostat_manifest_path = root / "manifest.json"
    velocity_results_path = root / "velocity-results.json"
    runner_results_path = root / "results.json"
    if not root.is_dir():
        raise RuntimeError(f"existing evidence directory does not exist: {root}")
    for path in (velocity_manifest_path, thermostat_manifest_path,
                 velocity_results_path, runner_results_path):
        if not path.is_file():
            raise RuntimeError(f"existing evidence is missing {path.name}: {path}")

    # This branch intentionally reads both manifests from the evidence bundle.
    contract = json.loads(velocity_manifest_path.read_text())
    thermostat_manifest = json.loads(thermostat_manifest_path.read_text())
    original = json.loads(velocity_results_path.read_text())
    runner_results = json.loads(runner_results_path.read_text())
    velocity_manifest_hash = sha256(velocity_manifest_path)
    thermostat_manifest_hash = sha256(thermostat_manifest_path)
    if contract.get("input_manifest_sha256") != thermostat_manifest_hash:
        raise RuntimeError("frozen velocity manifest does not lock the frozen thermostat manifest")
    if original.get("velocity_manifest_sha256") != velocity_manifest_hash:
        raise RuntimeError("velocity-results.json does not match the frozen velocity manifest")
    if original.get("thermostat_manifest_sha256") != thermostat_manifest_hash:
        raise RuntimeError("velocity-results.json does not match the frozen thermostat manifest")
    if runner_results.get("manifest_sha256") != thermostats.sha(
        json.dumps(thermostat_manifest, indent=2) + "\n"
    ):
        raise RuntimeError("Runner results do not match the frozen thermostat manifest")

    frozen_fixture = contract["fixture"]
    seeds = frozen_fixture["seeds"]
    if len(seeds) != 8 or len(set(seeds)) != 8:
        raise RuntimeError("frozen velocity manifest must contain exactly eight unique seeds")
    if (frozen_fixture["atoms"] != ATOMS or frozen_fixture["mass_amu"] != MASS_AMU
            or contract["statistics"]["constants"]["TIME_UNIT_CONVERSION"] != TIME_UNIT_CONVERSION):
        raise RuntimeError("frozen fixture constants do not match the registered velocity parser")
    if (thermostat_manifest["geometries"]["cluster_domain"]["cells"] != [6, 3, 3]
            or math.prod(thermostat_manifest["geometries"]["cluster_domain"]["cells"]) * 8 != ATOMS):
        raise RuntimeError("frozen thermostat geometry is not the registered 432-atom cluster")

    candidate = args.candidate.resolve() if args.candidate else Path(original["candidate"]).resolve()
    candidate_hash = _verify_file(candidate, original["candidate_sha256"], "candidate binary")
    if runner_results.get("candidate_sha256") != candidate_hash:
        raise RuntimeError("Runner candidate hash differs from velocity-results.json")
    if Path(runner_results["candidate"]).resolve() != candidate:
        raise RuntimeError("candidate path differs from the original run evidence")

    runner_record_by_label = {record["label"]: record for record in runner_results.get("records", [])}
    original_records = original.get("records", [])
    if len(original_records) != len(runner_record_by_label):
        raise RuntimeError("velocity and Runner record counts differ")
    seed_locks = thermostat_manifest["input_sha256"]["cluster_domain"]
    potential_hash = thermostat_manifest["potential"]["sha256"]
    allowed_budgets = contract["execution"]["analysis_looks_steps"]
    record_groups: dict[int, dict[int, tuple[dict, Path, str, bool]]] = {}
    record_hash_evidence = []
    pattern = re.compile(r"^velocity-(nvt_nhc|nvt_bdp)-look(\d+)(-halfdt)?-seed(\d+)$")
    for record in original_records:
        label = record.get("label", "")
        match = pattern.fullmatch(label)
        if not match:
            raise RuntimeError(f"unrecognized velocity run label in frozen evidence: {label!r}")
        ensemble, budget_text, halfdt_suffix, seed_text = match.groups()
        budget, seed = int(budget_text), int(seed_text)
        half_dt = bool(halfdt_suffix)
        if budget not in allowed_budgets or seed not in seeds:
            raise RuntimeError(f"record is outside the preregistered budget/seed set: {label}")
        if record.get("status") != "PASS":
            raise RuntimeError(f"seed run was not PASS: {label}: {record.get('status')}")
        if record.get("ranks") != 1 or record.get("backend") != "HostStaged" or record.get("reference"):
            raise RuntimeError(f"record violates P1 HostStaged candidate contract: {label}")
        if record.get("executable_sha256") != candidate_hash:
            raise RuntimeError(f"record executable hash differs from candidate: {label}")
        if label not in runner_record_by_label:
            raise RuntimeError(f"Runner record missing for {label}")
        runner_record = runner_record_by_label[label]
        for key in ("status", "seed", "input_sha256", "output_sha256"):
            if runner_record.get(key) != record.get(key):
                raise RuntimeError(f"Runner and velocity record disagree for {label}: {key}")

        stage = (root / label).resolve()
        if Path(record["cwd"]).resolve() != stage:
            raise RuntimeError(f"record cwd does not resolve to its frozen stage: {label}")
        input_hashes = record.get("input_sha256", {})
        for name in ("model.xyz", "run.in", "nep.txt"):
            _verify_file(stage / name, input_hashes[name], f"{label} input {name}")
        if input_hashes["model.xyz"] != seed_locks[str(seed)]:
            raise RuntimeError(f"{label} model hash differs from the seed lock")
        if input_hashes["nep.txt"] != potential_hash:
            raise RuntimeError(f"{label} potential hash differs from the frozen lock")
        expected_run = run_input(ensemble, budget, half_dt)
        if (stage / "run.in").read_text() != expected_run:
            raise RuntimeError(f"{label} run.in differs from its frozen ensemble/step contract")
        if record.get("velocity_output") != "v.xyz":
            raise RuntimeError(f"{label} is missing the registered velocity output name")
        velocity_path = stage / "v.xyz"
        velocity_hash = _verify_file(velocity_path, record["velocity_output_sha256"], f"{label} v.xyz")
        for name, digest in record.get("output_sha256", {}).items():
            _verify_file(stage / name, digest, f"{label} output {name}")
        if not {"thermo.out", "restart.xyz"}.issubset(record.get("output_sha256", {})):
            raise RuntimeError(f"{label} is missing required thermo/restart output hashes")
        for stream_name in ("stdout", "stderr"):
            log_path = stage / f"{stream_name}.log"
            if Path(record.get(f"{stream_name}_path", "")).resolve() != log_path.resolve() or not log_path.is_file():
                raise RuntimeError(f"{label} is missing its Runner {stream_name} log")
        key = record_groups.setdefault(budget, {})
        if seed in key:
            raise RuntimeError(f"duplicate seed record for budget {budget}: {seed}")
        key[seed] = (record, stage, ensemble, half_dt)
        record_hash_evidence.append({
            "label": label,
            "seed": seed,
            "budget_steps": budget,
            "input_sha256": input_hashes,
            "output_sha256": record["output_sha256"],
            "velocity_sha256": velocity_hash,
        })

    complete_looks = []
    for budget in allowed_budgets:
        group = record_groups.get(budget, {})
        if set(group) == set(seeds):
            complete_looks.append(budget)
        elif group:
            print(f"SKIP incomplete existing look {budget}: seeds {sorted(group)}", flush=True)
    if not complete_looks:
        raise RuntimeError("no complete preregistered look has all eight PASS seed outputs")

    # Frozen values are taken only from the evidence bundle above.
    config = analysis_config(contract)
    constants = contract["statistics"]["constants"]
    expected_mean = constants["kB_eV_per_K"] * constants["temperature_K"]
    expected_variance = 2.0 * expected_mean * expected_mean / constants["N"]
    results = []
    for budget in complete_looks:
        group = record_groups[budget]
        ensembles = {group[seed][2] for seed in seeds}
        half_dt_values = {group[seed][3] for seed in seeds}
        if len(ensembles) != 1 or len(half_dt_values) != 1:
            raise RuntimeError(f"seeds in look {budget} do not share ensemble and step-size mode")
        half_dt = next(iter(half_dt_values))
        actual_steps = budget * (2 if half_dt else 1)
        dump_interval = contract["execution"]["velocity_dump_interval_steps"] * (2 if half_dt else 1)
        if actual_steps % dump_interval:
            raise RuntimeError(f"look {budget} step count is not divisible by its dump interval")
        expected_frames = actual_steps // dump_interval
        burn_frames = int(expected_frames * contract["statistics"]["burn_in_fraction"])
        chains = [[[], [], []] for _ in seeds]
        for chain_index, seed in enumerate(seeds):
            _record, stage, _ensemble, _half_dt = group[seed]
            axes = velocity_samples(stage / "v.xyz", expected_frames)
            for axis in range(3):
                chains[chain_index][axis] = axes[axis][burn_frames:]
        axis_analysis = {}
        for axis, name in enumerate(("x", "y", "z")):
            axis_analysis[name] = thermostats.analysis.analyze_series(
                [chain[axis] for chain in chains], expected_mean, expected_variance, config
            )
        axis_statuses = {analysis["status"] for analysis in axis_analysis.values()}
        look_status = "FAIL" if "FAIL" in axis_statuses else (
            "PASS" if axis_statuses == {"PASS"} else "INCONCLUSIVE"
        )
        results.append({
            "steps": budget,
            "actual_steps": actual_steps,
            "ensemble": next(iter(ensembles)),
            "half_dt": half_dt,
            "frames_per_seed": expected_frames,
            "burn_in_frames_per_seed": burn_frames,
            "seeds": list(seeds),
            "status": look_status,
            "axis_analysis": axis_analysis,
        })
        print(json.dumps(results[-1], allow_nan=False), flush=True)

    statuses = [result["status"] for result in results]
    final_status = "FAIL" if "FAIL" in statuses else statuses[-1]
    reanalysis = {
        "schema": "dmgmd.velocity-statistics-reanalysis.v1",
        "status": final_status,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "candidate": str(candidate),
        "candidate_sha256": candidate_hash,
        "velocity_manifest_sha256": velocity_manifest_hash,
        "thermostat_manifest_sha256": thermostat_manifest_hash,
        "velocity_results_sha256": sha256(velocity_results_path),
        "runner_results_sha256": sha256(runner_results_path),
        "sample_statistics_sha256": sha256(SUITE / "sample_statistics.py"),
        "original_status": original.get("status"),
        "original_error": original.get("error"),
        "analysis_config": config,
        "expected_mean_eV": expected_mean,
        "expected_variance_eV2": expected_variance,
        "verified_seed_records": record_hash_evidence,
        "looks": results,
    }
    # Exclusive create prevents accidental overwrite even if another process raced us.
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(reanalysis, indent=2, allow_nan=False) + "\n")
    print(f"{final_status} offline velocity reanalysis; evidence {output_path}", flush=True)
    return 0 if final_status == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--work-root", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--analyze-existing", action="store_true",
                        help="analyze frozen run evidence in --work-root without launching GPU work")
    parser.add_argument("--devices", type=lambda value: value.split(","), default=["2", "4", "5", "7"])
    parser.add_argument("--ensemble", choices=["nvt_nhc", "nvt_bdp"], default="nvt_nhc")
    parser.add_argument("--half-dt", action="store_true", help="double damping, duration, and physical dump interval")
    parser.add_argument("--minimum-steps", type=int, choices=[20000, 40000, 80000], default=20000,
                        help="first preregistered sampling look to run; larger looks remain available if inconclusive")
    args = parser.parse_args()
    if args.analyze_existing:
        return analyze_existing(args)
    if args.candidate is None:
        parser.error("--candidate is required unless --analyze-existing is selected")
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, baseline.BaselineError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
