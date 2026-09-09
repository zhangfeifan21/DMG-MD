#!/usr/bin/env python3
"""Run deterministic short, 100k-step NVE, replay, and cross-rank restart tests."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


SUITE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SUITE_DIR.parents[1]
MPI_DIR = PROJECT_ROOT / "tests" / "mpi"
sys.path.insert(0, str(SUITE_DIR))
sys.path.insert(0, str(MPI_DIR))

import long_nve_common as common  # noqa: E402
import check_environment as mpi_environment  # noqa: E402
import run_mpi_differential as mpi_differential  # noqa: E402


baseline = common.baseline
DEFAULT_REFERENCE = PROJECT_ROOT.parent / "gpumd-reference" / "src" / "gpumd"


def comma_list(text: str) -> List[str]:
    values = [value.strip() for value in text.split(",") if value.strip()]
    if not values:
        raise argparse.ArgumentTypeError("list must not be empty")
    return values


def integer_list(text: str) -> List[int]:
    try:
        values = [int(value) for value in comma_list(text)]
    except ValueError as error:
        raise argparse.ArgumentTypeError("values must be integers") from error
    if any(value < 0 for value in values):
        raise argparse.ArgumentTypeError("values must be non-negative")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--mpiexec", type=Path, default=Path("mpiexec"))
    parser.add_argument("--devices", required=False, help="comma-separated CUDA IDs/UUIDs")
    parser.add_argument("--profile", choices=("smoke", "nightly", "release"), default="smoke")
    parser.add_argument("--cases", type=comma_list)
    parser.add_argument("--seeds", type=integer_list)
    parser.add_argument("--ranks", type=integer_list)
    parser.add_argument(
        "--backends", type=comma_list, default=["HostStaged"],
        help="comma-separated HostStaged,CudaAware",
    )
    parser.add_argument(
        "--sections", type=comma_list, default=["short", "long", "replay", "restart"],
        help="comma-separated short,long,replay,restart",
    )
    parser.add_argument("--timeout", type=int, default=7200, help="seconds allowed per process")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument(
        "--print-model-hashes", action="store_true",
        help="print deterministic model hashes and exit without using a GPU",
    )
    return parser.parse_args()


def normalized_backends(values: Sequence[str]) -> List[str]:
    supported = {"hoststaged": "HostStaged", "cudaaware": "CudaAware"}
    result = []
    for value in values:
        key = value.replace("_", "").lower()
        if key not in supported:
            raise baseline.BaselineError(f"unsupported communication backend {value}")
        result.append(supported[key])
    return result


def resolve_selection(
    manifest: Mapping[str, Any], args: argparse.Namespace
) -> Tuple[Mapping[str, Any], List[str], List[int], List[int], List[str], List[str]]:
    profile = manifest["profiles"][args.profile]
    cases = args.cases or list(profile["cases"])
    seeds = args.seeds if args.seeds is not None else list(profile["seeds"])
    ranks = args.ranks if args.ranks is not None else list(profile["ranks"])
    if any(rank <= 0 for rank in ranks):
        raise baseline.BaselineError("MPI ranks must be positive")
    unknown_cases = sorted(set(cases) - set(manifest["cases"]))
    if unknown_cases:
        raise baseline.BaselineError(f"unknown long-NVE cases: {unknown_cases}")
    unknown_seeds = sorted(set(seeds) - set(range(10)))
    if unknown_seeds:
        raise baseline.BaselineError(f"long-NVE seeds must be in [0,9]: {unknown_seeds}")
    sections = list(args.sections)
    unknown_sections = sorted(set(sections) - {"short", "long", "replay", "restart"})
    if unknown_sections:
        raise baseline.BaselineError(f"unknown long-NVE sections: {unknown_sections}")
    if "replay" in sections and "long" not in sections:
        raise baseline.BaselineError("replay requires the long section")
    return profile, cases, seeds, ranks, normalized_backends(args.backends), sections


def print_hashes(manifest: Mapping[str, Any]) -> None:
    result = {}
    for case_name, case in manifest["cases"].items():
        result[case_name] = {
            str(seed): common.text_sha256(common.generate_model(case, seed)) for seed in range(10)
        }
    print(json.dumps(result, indent=2, sort_keys=True))


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    baseline_manifest = baseline.load_manifest()
    if manifest["reference"] != {
        "commit": baseline_manifest["reference"]["commit"],
        "executable_sha256": baseline_manifest["reference"]["executable_sha256"],
    }:
        raise baseline.BaselineError("long-NVE and short golden reference locks differ")
    for case in manifest["cases"].values():
        potential = PROJECT_ROOT / case["potential"]
        if not potential.is_file():
            raise baseline.BaselineError(f"missing long-NVE potential: {potential}")
        actual = baseline.sha256(potential)
        if actual != case["potential_sha256"]:
            raise baseline.BaselineError(
                f"potential hash mismatch for {potential}: {actual}"
            )
        for seed in range(10):
            common.validate_generated_model(case, seed, common.generate_model(case, seed))


def reference_environment(device: str) -> Dict[str, str]:
    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = device
    env.pop("DMGMD_COMM_BACKEND", None)
    env.pop("DMGMD_COMM_LOG_INTERVAL", None)
    return env


def candidate_environment(
    devices: Sequence[str], ranks: int, backend: str, log_interval: int
) -> Dict[str, str]:
    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = ",".join(devices[:ranks])
    env["DMGMD_COMM_BACKEND"] = backend
    env["DMGMD_COMM_LOG_INTERVAL"] = str(log_interval)
    return env


def validate_candidate_stage(stage: Path, ranks: int, backend: str) -> None:
    mpi_differential.validate_runtime_record(stage, ranks, backend)


def run_reference_stage(
    executable: Path,
    model: str,
    run: str,
    potential: Path,
    directory: Path,
    env: Mapping[str, str],
    timeout: int,
    outputs: Sequence[str],
) -> Path:
    return common.execute_md(
        executable, (), model, run, potential, directory, env, timeout, outputs
    )


def run_candidate_stage(
    executable: Path,
    mpiexec: Path,
    ranks: int,
    backend: str,
    model: str,
    run: str,
    potential: Path,
    directory: Path,
    env: Mapping[str, str],
    timeout: int,
    outputs: Sequence[str],
) -> Path:
    stage_env = dict(env)
    total_steps = sum(
        int(line.split()[1])
        for line in run.splitlines()
        if line.split() and line.split()[0] == "run"
    )
    requested_interval = int(stage_env["DMGMD_COMM_LOG_INTERVAL"])
    stage_env["DMGMD_COMM_LOG_INTERVAL"] = str(min(requested_interval, total_steps))
    result = common.execute_md(
        executable,
        (str(mpiexec), "-n", str(ranks)),
        model,
        run,
        potential,
        directory,
        stage_env,
        timeout,
        outputs,
    )
    validate_candidate_stage(result, ranks, backend)
    return result


def append_trajectory_metrics(
    static_dir: Path, long_dir: Path, case: Mapping[str, Any]
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    metrics = common.nve_metrics(
        static_dir / "thermo.out", long_dir / "thermo.out", int(case["atoms"])
    )
    metrics.update(common.trajectory_metrics(static_dir / "static.xyz", long_dir / "trajectory.xyz"))
    frames = baseline.parse_xyz(long_dir / "trajectory.xyz")
    histogram = common.pair_distance_distribution(
        frames[-1], float(case["rdf_rmax_A"]), int(case["rdf_bins"])
    )
    metrics["minimum_distance_A"] = float(histogram["minimum_distance_A"])
    return metrics, histogram


def replay_frames(
    source_trajectory: Path,
    evaluator: Path,
    launcher: Sequence[str],
    potential: Path,
    root: Path,
    env: Mapping[str, str],
    timeout: int,
    collector: baseline.DiffCollector,
    label: str,
    candidate_runtime: Tuple[int, str] | None,
) -> int:
    frames = baseline.parse_xyz(source_trajectory)
    evaluator_env = dict(env)
    if candidate_runtime is not None:
        evaluator_env["DMGMD_COMM_LOG_INTERVAL"] = "1"
    for index, source_frame in enumerate(frames):
        directory = root / f"frame-{index:04d}"
        evaluated = common.execute_md(
            evaluator,
            launcher,
            common.frame_to_model(source_frame),
            common.static_run(),
            potential,
            directory,
            evaluator_env,
            timeout,
            ("static.xyz", "thermo.out"),
        )
        if candidate_runtime is not None:
            validate_candidate_stage(evaluated, candidate_runtime[0], candidate_runtime[1])
        evaluated_frame = baseline.parse_xyz(evaluated / "static.xyz")[0]
        common.compare_configuration_frames(
            source_frame, evaluated_frame, collector, f"{label}:frame[{index}]"
        )
    return len(frames)


def restart_transition(
    reference: Path,
    candidate: Path,
    mpiexec: Path,
    reference_env: Mapping[str, str],
    devices: Sequence[str],
    backend: str,
    source_ranks: int,
    destination_ranks: int,
    model: str,
    case: Mapping[str, Any],
    profile: Mapping[str, Any],
    root: Path,
    timeout: int,
    collector: baseline.DiffCollector,
) -> Dict[str, Any]:
    half_steps = int(profile["steps"]) // 2
    thermo_interval = int(profile["thermo_interval"])
    if half_steps == 0 or half_steps % thermo_interval:
        raise baseline.BaselineError("restart split must be positive and divisible by thermo interval")
    run_a = common.restart_run(
        float(case["time_step_fs"]), half_steps, thermo_interval, "segment-a.xyz"
    )
    run_b = common.restart_run(
        float(case["time_step_fs"]), half_steps, thermo_interval, "segment-b.xyz"
    )
    potential = PROJECT_ROOT / case["potential"]

    reference_a = run_reference_stage(
        reference, model, run_a, potential, root / "reference-a", reference_env, timeout,
        ("thermo.out", "restart.xyz", "segment-a.xyz"),
    )
    reference_restart_model = (reference_a / "restart.xyz").read_text(encoding="utf-8")
    reference_static = run_reference_stage(
        reference, reference_restart_model, common.static_run(), potential,
        root / "reference-boundary", reference_env, timeout, ("thermo.out", "static.xyz"),
    )
    reference_b = run_reference_stage(
        reference, reference_restart_model, run_b, potential, root / "reference-b",
        reference_env, timeout, ("thermo.out", "restart.xyz", "segment-b.xyz"),
    )
    reference_metrics = common.nve_metrics(
        reference_static / "thermo.out", reference_b / "thermo.out", int(case["atoms"])
    )

    source_env = candidate_environment(
        devices, source_ranks, backend, int(profile["communication_log_interval"])
    )
    actual_a = run_candidate_stage(
        candidate, mpiexec, source_ranks, backend, model, run_a, potential,
        root / f"candidate-r{source_ranks}-a", source_env, timeout,
        ("thermo.out", "restart.xyz", "segment-a.xyz"),
    )
    actual_restart_model = (actual_a / "restart.xyz").read_text(encoding="utf-8")
    restart_spec = {"kind": "restart"}
    baseline.compare_xyz(
        reference_a / "restart.xyz", actual_a / "restart.xyz", restart_spec, collector,
        f"restart/{case['name']}/{backend}/r{source_ranks}-to-r{destination_ranks}",
    )
    destination_env = candidate_environment(
        devices, destination_ranks, backend, int(profile["communication_log_interval"])
    )
    actual_static = run_candidate_stage(
        candidate, mpiexec, destination_ranks, backend, actual_restart_model,
        common.static_run(), potential, root / f"candidate-r{destination_ranks}-boundary",
        destination_env, timeout, ("thermo.out", "static.xyz"),
    )
    common.compare_configuration_files(
        reference_static / "static.xyz", actual_static / "static.xyz", collector,
        f"restart-boundary/{case['name']}/{backend}/r{source_ranks}-to-r{destination_ranks}",
    )
    actual_b = run_candidate_stage(
        candidate, mpiexec, destination_ranks, backend, actual_restart_model, run_b, potential,
        root / f"candidate-r{destination_ranks}-b", destination_env, timeout,
        ("thermo.out", "restart.xyz", "segment-b.xyz"),
    )
    actual_metrics = common.nve_metrics(
        actual_static / "thermo.out", actual_b / "thermo.out", int(case["atoms"])
    )
    acceptance = common.load_manifest()["acceptance"]
    comparison = common.enforce_noninferiority(
        [reference_metrics], [actual_metrics],
        [key for key in acceptance["metrics"] if key in reference_metrics],
        float(acceptance["relative_noninferiority_margin"]),
        acceptance["absolute_floors"],
        f"restart/{case['name']}/{backend}/r{source_ranks}-to-r{destination_ranks}",
    )
    return {
        "source_ranks": source_ranks,
        "destination_ranks": destination_ranks,
        "reference_metrics": reference_metrics,
        "candidate_metrics": actual_metrics,
        "noninferiority": comparison,
    }


def main() -> int:
    args = parse_args()
    manifest = common.load_manifest()
    if args.print_model_hashes:
        print_hashes(manifest)
        return 0
    if args.candidate is None:
        raise baseline.BaselineError("--candidate is required unless --print-model-hashes is used")
    profile, case_names, seeds, ranks, backends, sections = resolve_selection(manifest, args)
    validate_manifest(manifest)

    reference = args.reference.resolve()
    candidate = args.candidate.resolve()
    if not reference.is_file() or not os.access(reference, os.X_OK):
        raise baseline.BaselineError(f"reference is not executable: {reference}")
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise baseline.BaselineError(f"candidate is not executable: {candidate}")
    baseline.validate_reference(reference, baseline.load_manifest())

    devices_text = args.devices or os.environ.get("CUDA_VISIBLE_DEVICES", "")
    devices = comma_list(devices_text) if devices_text else []
    if len(devices) < max(ranks):
        raise baseline.BaselineError(
            f"need {max(ranks)} visible device IDs for rank matrix; got {devices}"
        )
    try:
        mpiexec = mpi_environment.validate_environment(
            candidate, args.mpiexec, devices, max(ranks), args.timeout
        )
    except mpi_environment.EnvironmentError as error:
        raise baseline.BaselineError(str(error)) from error

    work_root = Path(tempfile.mkdtemp(prefix=f"dmgmd-long-nve-{args.profile}-"))
    succeeded = False
    report: Dict[str, Any] = {
        "schema_version": 1,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile": args.profile,
        "profile_parameters": dict(profile),
        "cases": case_names,
        "seeds": seeds,
        "ranks": ranks,
        "backends": backends,
        "sections": sections,
        "reference": str(reference),
        "reference_sha256": baseline.sha256(reference),
        "candidate": str(candidate),
        "candidate_sha256": baseline.sha256(candidate),
        "results": {},
    }
    reference_env = reference_environment(devices[0])
    collector = baseline.DiffCollector(baseline.load_manifest()["tolerances"], enforce=True)
    try:
        for case_name in case_names:
            case = manifest["cases"][case_name]
            potential = PROJECT_ROOT / case["potential"]
            case_report: Dict[str, Any] = {
                "atoms": case["atoms"],
                "time_step_fs": case["time_step_fs"],
                "reference_metrics": {},
                "candidate_metrics": {},
                "pair_histogram_l1": {},
                "replay_frames": {},
                "noninferiority": {},
                "restart": [],
            }
            report["results"][case_name] = case_report
            reference_metrics_by_seed: Dict[int, Dict[str, float]] = {}
            candidate_metrics_by_config: Dict[str, Dict[int, Dict[str, float]]] = {}

            for seed in seeds:
                model = common.generate_model(case, seed)
                common.validate_generated_model(case, seed, model)
                seed_root = work_root / case_name / f"seed-{seed}"
                reference_static = run_reference_stage(
                    reference, model, common.static_run(), potential, seed_root / "reference-static",
                    reference_env, args.timeout, ("static.xyz", "thermo.out"),
                )
                reference_short = None
                if "short" in sections:
                    reference_short = run_reference_stage(
                        reference, model,
                        common.short_run(float(case["time_step_fs"]), int(profile["short_steps"])),
                        potential, seed_root / "reference-short", reference_env, args.timeout,
                        ("short.xyz", "thermo.out"),
                    )
                reference_long = None
                reference_histogram = None
                if "long" in sections:
                    reference_long = run_reference_stage(
                        reference, model,
                        common.long_run(
                            float(case["time_step_fs"]), int(profile["steps"]),
                            int(profile["thermo_interval"]), int(profile["trajectory_interval"]),
                        ),
                        potential, seed_root / "reference-long", reference_env, args.timeout,
                        ("trajectory.xyz", "thermo.out", "restart.xyz"),
                    )
                    reference_metrics, reference_histogram = append_trajectory_metrics(
                        reference_static, reference_long, case
                    )
                    reference_metrics_by_seed[seed] = reference_metrics
                    case_report["reference_metrics"][str(seed)] = reference_metrics

                for backend in backends:
                    for rank_count in ranks:
                        config = f"{backend}-r{rank_count}"
                        config_root = seed_root / config
                        env = candidate_environment(
                            devices, rank_count, backend,
                            int(profile["communication_log_interval"]),
                        )
                        actual_static = run_candidate_stage(
                            candidate, mpiexec, rank_count, backend, model, common.static_run(),
                            potential, config_root / "static", env, args.timeout,
                            ("static.xyz", "thermo.out"),
                        )
                        common.compare_configuration_files(
                            reference_static / "static.xyz", actual_static / "static.xyz", collector,
                            f"static/{case_name}/seed-{seed}/{config}",
                        )
                        if reference_short is not None:
                            actual_short = run_candidate_stage(
                                candidate, mpiexec, rank_count, backend, model,
                                common.short_run(
                                    float(case["time_step_fs"]), int(profile["short_steps"])
                                ),
                                potential, config_root / "short", env, args.timeout,
                                ("short.xyz", "thermo.out"),
                            )
                            baseline.compare_xyz(
                                reference_short / "short.xyz", actual_short / "short.xyz",
                                {"kind": "xyz"}, collector,
                                f"short/{case_name}/seed-{seed}/{config}/short.xyz",
                            )
                            baseline.compare_thermo(
                                reference_short / "thermo.out", actual_short / "thermo.out", collector,
                                f"short/{case_name}/seed-{seed}/{config}/thermo.out",
                            )
                        if reference_long is None or reference_histogram is None:
                            continue
                        actual_long = run_candidate_stage(
                            candidate, mpiexec, rank_count, backend, model,
                            common.long_run(
                                float(case["time_step_fs"]), int(profile["steps"]),
                                int(profile["thermo_interval"]),
                                int(profile["trajectory_interval"]),
                            ),
                            potential, config_root / "long", env, args.timeout,
                            ("trajectory.xyz", "thermo.out", "restart.xyz"),
                        )
                        actual_metrics, actual_histogram = append_trajectory_metrics(
                            actual_static, actual_long, case
                        )
                        candidate_metrics_by_config.setdefault(config, {})[seed] = actual_metrics
                        case_report["candidate_metrics"].setdefault(config, {})[str(seed)] = actual_metrics
                        histogram_l1 = common.histogram_l1(reference_histogram, actual_histogram)
                        case_report["pair_histogram_l1"].setdefault(config, {})[str(seed)] = histogram_l1
                        if histogram_l1 > float(manifest["acceptance"]["pair_histogram_l1_limit"]):
                            raise baseline.BaselineError(
                                f"{case_name}/seed-{seed}/{config}: pair histogram L1 "
                                f"{histogram_l1:.6e} exceeds limit"
                            )
                        if "replay" in sections:
                            forward = replay_frames(
                                reference_long / "trajectory.xyz", candidate,
                                (str(mpiexec), "-n", str(rank_count)), potential,
                                config_root / "replay-reference-in-candidate", env, args.timeout,
                                collector, f"replay-reference/{case_name}/seed-{seed}/{config}",
                                (rank_count, backend),
                            )
                            reverse = replay_frames(
                                actual_long / "trajectory.xyz", reference, (), potential,
                                config_root / "replay-candidate-in-reference", reference_env,
                                args.timeout, collector,
                                f"replay-candidate/{case_name}/seed-{seed}/{config}", None,
                            )
                            case_report["replay_frames"].setdefault(config, {})[str(seed)] = {
                                "reference_in_candidate": forward,
                                "candidate_in_reference": reverse,
                            }

            if "long" in sections:
                ordered_reference = [reference_metrics_by_seed[seed] for seed in seeds]
                acceptance = manifest["acceptance"]
                for config, values_by_seed in candidate_metrics_by_config.items():
                    ordered_actual = [values_by_seed[seed] for seed in seeds]
                    comparison = common.enforce_noninferiority(
                        ordered_reference, ordered_actual, acceptance["metrics"],
                        float(acceptance["relative_noninferiority_margin"]),
                        acceptance["absolute_floors"], f"long/{case_name}/{config}",
                    )
                    case_report["noninferiority"][config] = comparison

            if "restart" in sections:
                restart_seed = seeds[0]
                restart_model = common.generate_model(case, restart_seed)
                transitions = [(min(ranks), max(ranks))]
                if min(ranks) != max(ranks):
                    transitions.append((max(ranks), min(ranks)))
                for backend in backends:
                    for source_ranks, destination_ranks in transitions:
                        transition_root = (
                            work_root / case_name / "restart" /
                            f"seed-{restart_seed}-{backend}-r{source_ranks}-to-r{destination_ranks}"
                        )
                        result = restart_transition(
                            reference, candidate, mpiexec, reference_env, devices, backend,
                            source_ranks, destination_ranks, restart_model, case, profile,
                            transition_root, args.timeout, collector,
                        )
                        result["backend"] = backend
                        result["seed"] = restart_seed
                        case_report["restart"].append(result)

        report["field_differences"] = collector.stats
        report_path = args.report.resolve() if args.report else work_root / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            f"PASS long-NVE profile={args.profile} cases={case_names} seeds={seeds} "
            f"ranks={ranks} backends={backends}"
        )
        if args.report:
            print(f"Wrote {report_path}")
        succeeded = True
        return 0
    finally:
        if succeeded and not args.keep_work:
            shutil.rmtree(work_root)
        else:
            print(f"work directory retained: {work_root}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (baseline.BaselineError, subprocess.TimeoutExpired, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
