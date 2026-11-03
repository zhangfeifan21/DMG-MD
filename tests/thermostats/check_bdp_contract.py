#!/usr/bin/env python3
"""Exercise BDP seed defaults, validation, MPI agreement, and restart boundaries."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]
sys.path.insert(0, str(ROOT / "tests/baseline"))
sys.path.insert(0, str(ROOT / "tests/mpi"))
import run_baselines as baseline
import run_thermostats as thermostats
import check_environment


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_for_steps(manifest: dict, steps: int, trajectory_interval: int | None = None) -> dict:
    profile = dict(manifest["short"])
    profile["steps"] = steps
    if trajectory_interval is not None:
        profile["trajectory_interval"] = trajectory_interval
    return profile


def raw_run(steps: int, thermo_interval: int, xyz_interval: int | None = None,
            xyz_name: str = "trajectory.xyz") -> str:
    lines = [
        "potential nep.txt",
        "time_step 0.10000000000000001",
        "ensemble nvt_bdp 300 300 100",
        f"dump_thermo {thermo_interval}",
    ]
    if xyz_interval is not None:
        lines.append(
            f"dump_xyz {xyz_interval} {xyz_name} precision double velocity force potential virial"
        )
    lines.extend((f"dump_restart {steps}", f"run {steps}"))
    return "\n".join(lines) + "\n"


def multi_segment_run() -> str:
    return "\n".join((
        "potential nep.txt",
        "time_step 0.10000000000000001",
        "ensemble nvt_bdp 300 300 100",
        "dump_thermo 1",
        "dump_xyz 3 first.xyz precision double velocity force potential virial",
        "dump_restart 3",
        "run 3",
        "dump_xyz 3 second.xyz precision double velocity force potential virial",
        "dump_restart 3",
        "run 3",
        "",
    ))


def velocities(frame: dict) -> list[float]:
    offset = 0
    velocity_offset = None
    velocity_width = None
    for name, _kind, width in frame["properties"]:
        if name == "vel":
            velocity_offset, velocity_width = offset, width
        offset += width
    if velocity_offset is None or velocity_width != 3:
        raise RuntimeError("trajectory frame does not expose vel:R:3")
    return [float(row[velocity_offset + axis]) for row in frame["rows"] for axis in range(3)]


def thermo_rows(path: Path) -> list[list[float]]:
    return [row for segment in baseline.parse_thermo(path) for row in segment["rows"]]


def runner_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        candidate=args.candidate.resolve(),
        reference=ROOT.parent / "gpumd-reference/src/gpumd",
        devices=args.devices,
        ranks=[1, 2, 4],
        backends=["HostStaged"],
        work_root=args.work_root.resolve(),
        timeout=120,
        parallel_seeds=1,
    )


def stage_hashes(stage: Path, names: list[str]) -> dict[str, str]:
    return {name: sha256(stage / name) for name in names if (stage / name).is_file()}


def attach_runner_evidence(runner: thermostats.Runner, stage: Path) -> dict:
    record = next(record for record in runner.records if record.get("cwd") == str(stage))
    record["stdout_path"] = str(stage / "stdout.log")
    record["stderr_path"] = str(stage / "stderr.log")
    record["stdout_sha256"] = sha256(stage / "stdout.log")
    record["stderr_sha256"] = sha256(stage / "stderr.log")
    record["all_output_sha256"] = stage_hashes(stage, [
        "thermo.out", "trajectory.xyz", "restart.xyz", "neighbor.out",
        "first.xyz", "second.xyz", "restart-start.xyz",
    ])
    runner.save()
    return record


def write_results(path: Path, payload: dict, lock: threading.Lock) -> None:
    with lock:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
        temporary.replace(path)


def run_rank_mismatch(runner: thermostats.Runner, manifest: dict, args: argparse.Namespace,
                      payload: dict, save_lock: threading.Lock, ranks: int,
                      rank_zero_seed: str, other_seed: str, case_name: str) -> None:
    label = f"rank-seed-{case_name}-p{ranks}"
    stage = runner.root / label
    stage.mkdir(parents=True, exist_ok=False)
    geometry = manifest["geometries"]["cluster_domain"]
    model = thermostats.model_text(geometry, 1)
    expected_model_hash = manifest["input_sha256"]["cluster_domain"]["1"]
    if thermostats.sha(model) != expected_model_hash:
        raise RuntimeError("seed_rank negative used a model outside the locked seed-1 fixture")
    profile = profile_for_steps(manifest, 10, 1)
    run = thermostats.run_text("nvt_bdp", profile)
    potential = (ROOT / manifest["potential"]["path"]).read_text()
    for name, content in {"model.xyz": model, "run.in": run, "nep.txt": potential}.items():
        (stage / name).write_text(content)

    env = runner.env.copy()
    env["DMGMD_COMM_BACKEND"] = "HostStaged"
    env["DMGMD_ENSEMBLE_SEED"] = "1"  # wrapper overrides this per rank.
    env["DMGMD_COMM_LOG_INTERVAL"] = "1"
    env["DMGMD_DOMAIN_DIAGNOSTICS"] = "1"
    candidate = str(args.candidate.resolve())
    command = [str(runner.mpirun), "-n", str(ranks), sys.executable,
               str(SUITE / "seed_rank.py"), "--candidate", candidate,
               "--rank-zero-seed", rank_zero_seed, "--other-seed", other_seed]
    with (stage / "stdout.log").open("w") as stdout, (stage / "stderr.log").open("w") as stderr:
        process = subprocess.Popen(command, cwd=stage, env=env, stdout=stdout, stderr=stderr,
                                   start_new_session=True)
        timed_out = False
        try:
            returncode = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                returncode = process.wait(timeout=5)
    stdout_text = (stage / "stdout.log").read_text()
    stderr_text = (stage / "stderr.log").read_text()
    combined = stdout_text + stderr_text
    snapshots = [name for name in ("thermo.out", "trajectory.xyz", "restart.xyz")
                 if (stage / name).exists()]
    check = {
        "label": label,
        "status": "PASS" if (not timed_out and returncode != 0 and
                              "DMGMD_ENSEMBLE_SEED" in combined and not snapshots) else "FAIL",
        "ranks": ranks,
        "rank_seed_text": {"rank0": rank_zero_seed, "other_ranks": other_seed},
        "strings_are_equal": rank_zero_seed == other_seed,
        "expected": "nonzero launcher exit with DMGMD_ENSEMBLE_SEED diagnostic; no thermo/XYZ/restart output",
        "actual_returncode": returncode,
        "timeout_seconds": 120,
        "timed_out": timed_out,
        "launcher_exited_all_ranks": not timed_out and process.poll() is not None,
        "diagnostic_present": "DMGMD_ENSEMBLE_SEED" in combined,
        "scientific_snapshots": snapshots,
        "command": command,
        "cwd": str(stage),
        "environment": {key: value for key, value in env.items()
                         if key.startswith(("DMGMD_", "CUDA_", "OMPI_", "UCX_", "PMIX_"))},
        "input_sha256": {name: sha256(stage / name) for name in ("model.xyz", "run.in", "nep.txt")},
        "output_sha256": stage_hashes(stage, ["thermo.out", "trajectory.xyz", "restart.xyz"]),
        "stdout_path": str(stage / "stdout.log"),
        "stderr_path": str(stage / "stderr.log"),
        "stdout_sha256": sha256(stage / "stdout.log"),
        "stderr_sha256": sha256(stage / "stderr.log"),
    }
    payload["checks"].append(check)
    write_results(runner.root / "bdp-contract-results.json", payload, save_lock)
    if check["status"] != "PASS":
        raise RuntimeError(f"{label}: rank seed mismatch contract did not fail cleanly")


def run(args: argparse.Namespace) -> int:
    if not args.candidate.is_file():
        raise RuntimeError(f"candidate binary does not exist: {args.candidate}")
    if args.work_root.exists():
        raise RuntimeError(f"work root must be a new directory: {args.work_root}")
    if len(args.devices) < 4 or len(set(args.devices)) != len(args.devices):
        raise RuntimeError("--devices must provide at least four unique GPU IDs for the P4 cases")
    manifest = json.loads((SUITE / "manifest.json").read_text())
    parsed_args = runner_args(args)
    runner = thermostats.Runner(parsed_args, manifest)
    results_path = runner.root / "bdp-contract-results.json"
    save_lock = threading.Lock()
    payload = {
        "schema": "dmgmd.bdp-contract-results.v1",
        "status": "IN_PROGRESS",
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "candidate": str(args.candidate.resolve()),
        "candidate_sha256": sha256(args.candidate.resolve()),
        "manifest_sha256": sha256(SUITE / "manifest.json"),
        "run_thermostats_sha256": sha256(SUITE / "run_thermostats.py"),
        "seed_rank_sha256": sha256(SUITE / "seed_rank.py"),
        "timeout_seconds": 120,
        "checks": [],
        "runner_records": runner.records,
        "seed_rules": {
            "default_when_unset": 12345678,
            "rank_agreement": "exact seed string, not numeric equivalence",
            "invalid_values": ["", "-1", "+1", " 1", "4294967296", "1x"],
        },
    }
    write_results(results_path, payload, save_lock)
    profile = manifest["short"]
    model_hash = manifest["input_sha256"]["cluster_domain"]["1"]
    base_model = thermostats.model_text(manifest["geometries"]["cluster_domain"], 1)
    if thermostats.sha(base_model) != model_hash:
        raise RuntimeError("cluster_domain seed-1 model differs from the locked input hash")
    run = thermostats.run_text("nvt_bdp", profile)

    try:
        # Unset BDP seed and the documented explicit default must be bitwise equivalent.
        unset_stage = runner.execute(
            "bdp-default-unset-p1", "nvt_bdp", "cluster_domain", 1, "HostStaged",
            profile, seed=1, extra_env={"DMGMD_ENSEMBLE_SEED": None}, run=run,
        )
        unset_record = attach_runner_evidence(runner, unset_stage)
        explicit_stage = runner.execute(
            "bdp-default-explicit-p1", "nvt_bdp", "cluster_domain", 1, "HostStaged",
            profile, seed=1, extra_env={"DMGMD_ENSEMBLE_SEED": "12345678"}, run=run,
        )
        explicit_record = attach_runner_evidence(runner, explicit_stage)
        compared_names = ("thermo.out", "trajectory.xyz", "restart.xyz")
        equality_hashes = {}
        for name in compared_names:
            left, right = unset_stage / name, explicit_stage / name
            if not left.is_file() or not right.is_file():
                raise RuntimeError(f"default seed comparison is missing {name}")
            equality_hashes[name] = {"unset": sha256(left), "explicit": sha256(right)}
            if equality_hashes[name]["unset"] != equality_hashes[name]["explicit"]:
                raise RuntimeError(f"unset and explicit default seed differ in {name}")
        env_unset = unset_record["environment"].get("DMGMD_ENSEMBLE_SEED")
        env_explicit = explicit_record["environment"].get("DMGMD_ENSEMBLE_SEED")
        if env_unset is not None or env_explicit != "12345678":
            raise RuntimeError("Runner evidence does not prove unset versus explicit default seed")
        payload["checks"].append({
            "label": "default-seed-unset-equals-explicit",
            "status": "PASS",
            "seed_contract": {"unset": env_unset, "explicit": env_explicit},
            "compared_files": equality_hashes,
            "unset_run": {"command": unset_record["command"], "cwd": unset_record["cwd"],
                          "environment": unset_record["environment"], "input_sha256": unset_record["input_sha256"],
                          "output_sha256": unset_record["all_output_sha256"], "stdout_path": unset_record["stdout_path"],
                          "stderr_path": unset_record["stderr_path"]},
            "explicit_run": {"command": explicit_record["command"], "cwd": explicit_record["cwd"],
                             "environment": explicit_record["environment"], "input_sha256": explicit_record["input_sha256"],
                             "output_sha256": explicit_record["all_output_sha256"], "stdout_path": explicit_record["stdout_path"],
                             "stderr_path": explicit_record["stderr_path"]},
        })
        write_results(results_path, payload, save_lock)

        # Different RNG seeds change sampled scaling; first thermo T is pre-scaling.
        seed_stages = {}
        for seed in (1, 2):
            stage = runner.execute(
                f"bdp-seed-{seed}-p1", "nvt_bdp", "cluster_domain", 1, "HostStaged",
                profile, seed=1, extra_env={"DMGMD_ENSEMBLE_SEED": str(seed)}, run=run,
            )
            seed_stages[seed] = stage
            attach_runner_evidence(runner, stage)
        frames = {seed: baseline.parse_xyz(stage / "trajectory.xyz")[0]
                  for seed, stage in seed_stages.items()}
        velocities_by_seed = {seed: velocities(frame) for seed, frame in frames.items()}
        rows_by_seed = {seed: thermo_rows(stage / "thermo.out")
                        for seed, stage in seed_stages.items()}
        if any(len(rows_by_seed[seed]) < 2 for seed in (1, 2)):
            raise RuntimeError("different-seed run has fewer than two thermo rows")
        if velocities_by_seed[1] == velocities_by_seed[2]:
            raise RuntimeError("different BDP seeds produced identical first-frame velocities")
        second_temperatures = {seed: rows_by_seed[seed][1][0] for seed in (1, 2)}
        if not all(math.isfinite(value) for value in second_temperatures.values()) or \
                second_temperatures[1] == second_temperatures[2]:
            raise RuntimeError("different BDP seeds did not change the second thermo temperature")
        seed_records = [next(record for record in runner.records
                             if record.get("cwd") == str(seed_stages[seed])) for seed in (1, 2)]
        payload["checks"].append({
            "label": "different-seed-rng-effect",
            "status": "PASS",
            "same_locked_input": all(record["input_sha256"]["model.xyz"] == model_hash
                                      for record in seed_records),
            "first_frame_velocity_sha256": {
                str(seed): hashlib.sha256(json.dumps(velocities_by_seed[seed]).encode()).hexdigest()
                for seed in (1, 2)
            },
            "first_thermo_temperature": {str(seed): rows_by_seed[seed][0][0] for seed in (1, 2)},
            "second_thermo_temperature": {str(seed): value for seed, value in second_temperatures.items()},
            "second_temperature_differs": True,
            "first_temperature_difference_required": False,
            "runs": [{"command": record["command"], "cwd": record["cwd"],
                      "environment": record["environment"], "input_sha256": record["input_sha256"],
                      "output_sha256": record["all_output_sha256"], "stdout_path": record["stdout_path"],
                      "stderr_path": record["stderr_path"]} for record in seed_records],
        })
        write_results(results_path, payload, save_lock)

        invalid_values = ("", "-1", "+1", " 1", "4294967296", "1x")
        invalid_names = {"": "empty", "-1": "minus1", "+1": "plus1", " 1": "space1",
                         "4294967296": "uint32-overflow", "1x": "suffix"}
        for ranks in (1, 2):
            for value in invalid_values:
                label = f"bdp-invalid-{invalid_names[value]}-p{ranks}"
                stage = runner.execute(
                    label, "nvt_bdp", "cluster_domain", ranks, "HostStaged",
                    profile, seed=1, extra_env={"DMGMD_ENSEMBLE_SEED": value}, run=run,
                    expect_error="DMGMD_ENSEMBLE_SEED",
                )
                record = attach_runner_evidence(runner, stage)
                payload["checks"].append({
                    "label": label,
                    "status": "PASS",
                    "rank_count": ranks,
                    "seed_text": value,
                    "diagnostic": record.get("expected_error"),
                    "returncode": record.get("returncode"),
                    "launcher_termination": record.get("termination"),
                    "all_ranks_terminated": record.get("termination") == "launcher returned before timeout",
                    "diagnostic_present": "DMGMD_ENSEMBLE_SEED" in (
                        (stage / "stdout.log").read_text() + (stage / "stderr.log").read_text()
                    ),
                    "scientific_snapshots_absent": not any((stage / name).exists()
                                                           for name in ("trajectory.xyz", "restart.xyz")),
                    "command": record["command"], "cwd": record["cwd"],
                    "environment": record["environment"], "input_sha256": record["input_sha256"],
                    "output_sha256": record["all_output_sha256"],
                    "stdout_path": record["stdout_path"], "stderr_path": record["stderr_path"],
                })
                write_results(results_path, payload, save_lock)

        for ranks in (2, 4):
            run_rank_mismatch(runner, manifest, args, payload, save_lock, ranks,
                              "1", "2", "numeric-difference")
            run_rank_mismatch(runner, manifest, args, payload, save_lock, ranks,
                              "01", "1", "string-difference")

        # Separate segments reinitialize controllers. Restart is a fresh job smoke,
        # deliberately not a continuity claim.
        segment_profile = profile_for_steps(manifest, 6)
        segmented = runner.execute(
            "bdp-two-segment-run", "nvt_bdp", "cluster_domain", 1, "HostStaged",
            segment_profile, seed=1, extra_env={"DMGMD_ENSEMBLE_SEED": "1"},
            run=multi_segment_run(),
        )
        segmented_record = attach_runner_evidence(runner, segmented)
        first_frames = baseline.parse_xyz(segmented / "first.xyz")
        second_frames = baseline.parse_xyz(segmented / "second.xyz")
        if not first_frames or not second_frames or not (segmented / "restart.xyz").is_file():
            raise RuntimeError("two-segment run did not produce both XYZ files and restart.xyz")
        restart_model = (segmented / "restart.xyz").read_text()
        restart_profile = profile_for_steps(manifest, 3, 3)
        restart_run = raw_run(3, 1, 3, "restart-start.xyz")
        restarted = runner.execute(
            "bdp-restart-new-job", "nvt_bdp", "cluster_domain", 1, "HostStaged",
            restart_profile, seed=1, model=restart_model,
            extra_env={"DMGMD_ENSEMBLE_SEED": "1"}, run=restart_run,
        )
        restarted_record = attach_runner_evidence(runner, restarted)
        restart_frames = baseline.parse_xyz(restarted / "restart-start.xyz")
        if not restart_frames:
            raise RuntimeError("fresh restart job produced no restart-start.xyz frame")
        payload["checks"].append({
            "label": "two-segments-and-fresh-restart-job",
            "status": "PASS",
            "segments": [
                {"steps": 3, "dump": "first.xyz", "frame_count": len(first_frames)},
                {"steps": 3, "dump": "second.xyz", "frame_count": len(second_frames)},
            ],
            "restart_sha256": sha256(segmented / "restart.xyz"),
            "fresh_restart_job": {"label": "bdp-restart-new-job", "steps": 3,
                                  "frame_count": len(restart_frames),
                                  "restart_input_sha256": restarted_record["input_sha256"]["model.xyz"]},
            "continuity_asserted": False,
            "semantics": "each run segment initializes a fresh thermostat controller/RNG; restart check is a new-job smoke only",
            "runs": [{"command": segmented_record["command"], "cwd": segmented_record["cwd"],
                      "environment": segmented_record["environment"], "input_sha256": segmented_record["input_sha256"],
                      "output_sha256": segmented_record["all_output_sha256"], "stdout_path": segmented_record["stdout_path"],
                      "stderr_path": segmented_record["stderr_path"]},
                     {"command": restarted_record["command"], "cwd": restarted_record["cwd"],
                      "environment": restarted_record["environment"], "input_sha256": restarted_record["input_sha256"],
                      "output_sha256": restarted_record["all_output_sha256"], "stdout_path": restarted_record["stdout_path"],
                      "stderr_path": restarted_record["stderr_path"]}],
        })
        payload["status"] = "PASS"
        payload["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_results(results_path, payload, save_lock)
        print(f"PASS {len(payload['checks'])} BDP contract checks; evidence {runner.root}", flush=True)
        return 0
    except Exception as error:
        payload["status"] = "FAIL"
        payload["error"] = str(error)
        payload["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_results(results_path, payload, save_lock)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--devices", type=lambda value: value.split(","), default=["2", "4", "5", "7"])
    parser.add_argument("--work-root", type=Path, required=True, help="new evidence directory")
    args = parser.parse_args()
    args.candidate = args.candidate.resolve()
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, baseline.BaselineError,
            check_environment.EnvironmentError, subprocess.SubprocessError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
