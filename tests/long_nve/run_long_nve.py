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
import long_nve_ui as terminal_ui  # noqa: E402
import check_environment as mpi_environment  # noqa: E402
import run_mpi_differential as mpi_differential  # noqa: E402


baseline = common.baseline
DEFAULT_REFERENCE = PROJECT_ROOT.parent / "gpumd-reference" / "src" / "gpumd"
RUN_CHECKPOINT_NAME = ".dmgmd-run-checkpoint.json"
CONFIG_COMPLETE_NAME = ".dmgmd-config-complete.json"
DOMAIN_MODE_ORDER = ("m1-fallback", "m2a")


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


def nonnegative_integer(text: str) -> int:
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if value < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return value


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
        "--backends", type=comma_list,
        help="comma-separated HostStaged,CudaAware",
    )
    parser.add_argument(
        "--sections", type=comma_list,
        help="comma-separated short,long,replay,restart,nvt",
    )
    parser.add_argument("--timeout", type=int, default=7200, help="seconds allowed per process")
    parser.add_argument(
        "--retries", type=nonnegative_integer, default=common.DEFAULT_STAGE_RETRIES,
        help="retries after a failed stage (default: 1, for at most two attempts)",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument(
        "--resume-work", type=Path,
        help="resume a retained work directory and reuse verified stage checkpoints",
    )
    parser.add_argument(
        "--adopt-existing", action="store_true",
        help="with --resume-work, explicitly adopt pre-checkpoint stage directories",
    )
    parser.add_argument(
        "--print-model-hashes", action="store_true",
        help="print deterministic model hashes and exit without using a GPU",
    )
    parser.add_argument(
        "--ui", choices=("auto", "dashboard", "plain"), default="auto",
        help="terminal output mode (default: auto)",
    )
    parser.add_argument("--ui-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--ui-log", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def initialize_work_root(
    args: argparse.Namespace,
    contract: Mapping[str, Any],
) -> Tuple[Path, bool]:
    if args.adopt_existing and args.resume_work is None:
        raise baseline.BaselineError("--adopt-existing requires --resume-work")
    if args.resume_work is None:
        root = Path(tempfile.mkdtemp(prefix=f"dmgmd-long-nve-{args.profile}-"))
        write_json_atomic(
            root / RUN_CHECKPOINT_NAME,
            {
                "schema_version": 1,
                "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "contract": dict(contract),
            },
        )
        return root, False

    root = args.resume_work.resolve()
    if not root.is_dir():
        raise baseline.BaselineError(f"resume work directory does not exist: {root}")
    checkpoint_path = root / RUN_CHECKPOINT_NAME
    if checkpoint_path.is_file():
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise baseline.BaselineError(
                f"cannot read run checkpoint {checkpoint_path}: {error}"
            ) from error
        if checkpoint.get("contract") != contract:
            raise baseline.BaselineError(
                "resume contract differs from the retained run; use the same profile, "
                "selection, reference, candidate binary and MPI launcher"
            )
    else:
        if not args.adopt_existing:
            raise baseline.BaselineError(
                f"{root} predates checkpoint support; pass --adopt-existing once to "
                "validate and explicitly adopt its completed stages"
            )
        write_json_atomic(
            checkpoint_path,
            {
                "schema_version": 1,
                "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "contract": dict(contract),
                "legacy_adoption": True,
                "warning": "pre-existing stage executable provenance is unverified",
            },
        )
        print(
            "LONG_NVE_RESUME status=adopting-existing "
            f"work_root={root} executable_provenance=unverified",
            flush=True,
        )
    return root, True


def checkpoint_provenance_summary(work_root: Path) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for checkpoint in work_root.rglob(common.STAGE_COMPLETE_NAME):
        provenance = common.stage_checkpoint_provenance(checkpoint.parent) or "unknown"
        summary[provenance] = summary.get(provenance, 0) + 1
    return summary


def normalized_backends(values: Sequence[str]) -> List[str]:
    supported = {"hoststaged": "HostStaged", "cudaaware": "CudaAware"}
    result = []
    for value in values:
        key = value.replace("_", "").lower()
        if key not in supported:
            raise baseline.BaselineError(f"unsupported communication backend {value}")
        result.append(supported[key])
    return result


def expected_domain_mode(case: Mapping[str, Any], ranks: int) -> str:
    modes = case.get("domain_mode_by_rank")
    mode = modes.get(str(ranks)) if isinstance(modes, Mapping) else None
    if mode not in DOMAIN_MODE_ORDER:
        raise baseline.BaselineError(
            f"{case.get('name', '<unnamed>')}: no valid domain-mode contract for {ranks} ranks"
        )
    return str(mode)


def ordered_configurations(
    case: Mapping[str, Any], ranks: Sequence[int], backends: Sequence[str]
) -> List[Tuple[str, int, str]]:
    return [
        (backend, rank_count, expected_domain_mode(case, rank_count))
        for backend in backends
        for rank_count in ranks
    ]


def resolve_selection(
    manifest: Mapping[str, Any], args: argparse.Namespace
) -> Tuple[Mapping[str, Any], List[str], List[int], List[int], List[str], List[str]]:
    profile = manifest["profiles"][args.profile]
    cases = args.cases or list(profile["cases"])
    seeds = args.seeds if args.seeds is not None else list(profile["seeds"])
    ranks = args.ranks if args.ranks is not None else list(profile["ranks"])
    backends = args.backends if args.backends is not None else list(profile["backends"])
    if any(rank <= 0 for rank in ranks):
        raise baseline.BaselineError("MPI ranks must be positive")
    unknown_cases = sorted(set(cases) - set(manifest["cases"]))
    if unknown_cases:
        raise baseline.BaselineError(f"unknown long-NVE cases: {unknown_cases}")
    unknown_seeds = sorted(set(seeds) - set(range(10)))
    if unknown_seeds:
        raise baseline.BaselineError(f"long-NVE seeds must be in [0,9]: {unknown_seeds}")
    sections = list(args.sections) if args.sections is not None else list(profile["sections"])
    unknown_sections = sorted(set(sections) - {"short", "long", "replay", "restart", "nvt"})
    if unknown_sections:
        raise baseline.BaselineError(f"unknown long-NVE sections: {unknown_sections}")
    if "replay" in sections and "long" not in sections:
        raise baseline.BaselineError("replay requires the long section")
    for case_name in cases:
        case = common.profile_case(manifest, args.profile, case_name)
        for rank in ranks:
            expected_domain_mode(case, rank)
    return profile, cases, seeds, ranks, normalized_backends(backends), sections


def print_hashes(manifest: Mapping[str, Any], profile_name: str) -> None:
    result = {}
    for case_name in manifest["cases"]:
        case = common.profile_case(manifest, profile_name, case_name)
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
        common.validate_generated_potential(case, common.generate_potential(case))
    validated_models = set()
    for profile_name, profile in manifest["profiles"].items():
        for case_name in profile["cases"]:
            case = common.profile_case(manifest, profile_name, case_name)
            for rank in profile["ranks"]:
                expected_domain_mode(case, int(rank))
            base_name = str(case.get("base_case", case_name))
            model_key = (profile_name, base_name)
            if model_key in validated_models:
                continue
            for seed in range(10):
                common.validate_generated_model(case, seed, common.generate_model(case, seed))
            validated_models.add(model_key)


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


def validate_candidate_stage(
    stage: Path, ranks: int, backend: str, expected_mode: str
) -> None:
    mpi_differential.validate_runtime_record(
        stage, ranks, backend, expected_domain_mode=expected_mode
    )
    stdout = (stage / "execution.stdout").read_text(encoding="utf-8")
    timings = [line for line in stdout.splitlines() if line.startswith("DMGMD_TIMING ")]
    if not timings:
        provenance = common.stage_checkpoint_provenance(stage)
        if provenance == "adopted-existing-unverified-executable":
            print(
                f"LONG_NVE_TIMING status=unavailable reason=adopted-existing path={stage}",
                flush=True,
            )
            return
        raise baseline.BaselineError(f"{stage}: missing DMGMD_TIMING records")
    total_records = []
    for line in timings:
        fields = mpi_differential.key_values(line)
        required = {
            "phase", "sequence", "steps", "atoms", "ranks", "backend",
            "seconds_min", "seconds_mean", "seconds_max",
            "global_atom_steps_per_second",
        }
        if not required.issubset(fields):
            raise baseline.BaselineError(f"{stage}: incomplete timing record: {line}")
        if int(fields["ranks"]) != ranks or fields["backend"] != backend:
            raise baseline.BaselineError(f"{stage}: inconsistent timing rank/backend record")
        minimum = float(fields["seconds_min"])
        mean = float(fields["seconds_mean"])
        maximum = float(fields["seconds_max"])
        throughput = float(fields["global_atom_steps_per_second"])
        if minimum < 0.0 or not minimum <= mean <= maximum or throughput < 0.0:
            raise baseline.BaselineError(f"{stage}: invalid timing values: {line}")
        if fields["phase"] == "total":
            total_records.append(line)
    if len(total_records) != 1:
        raise baseline.BaselineError(f"{stage}: expected exactly one total timing record")


def run_reference_stage(
    executable: Path,
    model: str,
    run: str,
    potential_text: str,
    directory: Path,
    env: Mapping[str, str],
    timeout: int,
    outputs: Sequence[str],
    resume: bool,
    adopt_existing: bool,
    retries: int,
) -> Path:
    return common.execute_md(
        executable, (), model, run, potential_text, directory, env, timeout, outputs,
        resume=resume, adopt_existing=adopt_existing, retries=retries,
    )


def run_candidate_stage(
    executable: Path,
    mpiexec: Path,
    ranks: int,
    backend: str,
    expected_mode: str,
    model: str,
    run: str,
    potential_text: str,
    directory: Path,
    env: Mapping[str, str],
    timeout: int,
    outputs: Sequence[str],
    resume: bool,
    adopt_existing: bool,
    retries: int,
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
        potential_text,
        directory,
        stage_env,
        timeout,
        outputs,
        resume=resume,
        adopt_existing=adopt_existing,
        retries=retries,
    )
    validate_candidate_stage(result, ranks, backend, expected_mode)
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


def append_nvt_metrics(
    stage: Path, case: Mapping[str, Any]
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    metrics = common.temperature_statistics(
        stage / "thermo.out", float(case["nvt_temperature_K"])
    )
    metrics.update(common.msd_statistics(stage / "nvt.xyz"))
    rdf = common.time_averaged_rdf(
        stage / "nvt.xyz", float(case["rdf_rmax_A"]), int(case["rdf_bins"])
    )
    return metrics, rdf


def replay_frames(
    source_trajectory: Path,
    evaluator: Path,
    launcher: Sequence[str],
    potential_text: str,
    root: Path,
    env: Mapping[str, str],
    timeout: int,
    collector: baseline.DiffCollector,
    label: str,
    candidate_runtime: Tuple[int, str, str] | None,
    resume: bool,
    adopt_existing: bool,
    retries: int,
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
            potential_text,
            directory,
            evaluator_env,
            timeout,
            ("static.xyz", "thermo.out"),
            resume=resume,
            adopt_existing=adopt_existing,
            retries=retries,
        )
        if candidate_runtime is not None:
            validate_candidate_stage(
                evaluated,
                candidate_runtime[0],
                candidate_runtime[1],
                candidate_runtime[2],
            )
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
    potential_text: str,
    case: Mapping[str, Any],
    profile: Mapping[str, Any],
    root: Path,
    timeout: int,
    collector: baseline.DiffCollector,
    resume: bool,
    adopt_existing: bool,
    retries: int,
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
    reference_a = run_reference_stage(
        reference, model, run_a, potential_text, root / "reference-a", reference_env, timeout,
        ("thermo.out", "restart.xyz", "segment-a.xyz"), resume, adopt_existing, retries,
    )
    reference_restart_model = (reference_a / "restart.xyz").read_text(encoding="utf-8")
    reference_static = run_reference_stage(
        reference, reference_restart_model, common.static_run(), potential_text,
        root / "reference-boundary", reference_env, timeout, ("thermo.out", "static.xyz"),
        resume, adopt_existing, retries,
    )
    reference_b = run_reference_stage(
        reference, reference_restart_model, run_b, potential_text, root / "reference-b",
        reference_env, timeout, ("thermo.out", "restart.xyz", "segment-b.xyz"),
        resume, adopt_existing, retries,
    )
    reference_metrics = common.nve_metrics(
        reference_static / "thermo.out", reference_b / "thermo.out", int(case["atoms"])
    )

    source_env = candidate_environment(
        devices, source_ranks, backend, int(profile["communication_log_interval"])
    )
    source_mode = expected_domain_mode(case, source_ranks)
    destination_mode = expected_domain_mode(case, destination_ranks)
    actual_a = run_candidate_stage(
        candidate, mpiexec, source_ranks, backend, source_mode, model, run_a, potential_text,
        root / f"candidate-r{source_ranks}-a", source_env, timeout,
        ("thermo.out", "restart.xyz", "segment-a.xyz"), resume, adopt_existing, retries,
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
        candidate, mpiexec, destination_ranks, backend, destination_mode, actual_restart_model,
        common.static_run(), potential_text, root / f"candidate-r{destination_ranks}-boundary",
        destination_env, timeout, ("thermo.out", "static.xyz"), resume, adopt_existing, retries,
    )
    common.compare_configuration_files(
        reference_static / "static.xyz", actual_static / "static.xyz", collector,
        f"restart-boundary/{case['name']}/{backend}/r{source_ranks}-to-r{destination_ranks}",
    )
    actual_b = run_candidate_stage(
        candidate, mpiexec, destination_ranks, backend, destination_mode,
        actual_restart_model, run_b, potential_text,
        root / f"candidate-r{destination_ranks}-b", destination_env, timeout,
        ("thermo.out", "restart.xyz", "segment-b.xyz"), resume, adopt_existing, retries,
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
        "source_domain_mode": source_mode,
        "destination_domain_mode": destination_mode,
        "reference_metrics": reference_metrics,
        "candidate_metrics": actual_metrics,
        "noninferiority": comparison,
    }


def main() -> int:
    args = parse_args()
    manifest = common.load_manifest()
    if args.print_model_hashes:
        print_hashes(manifest, args.profile)
        return 0
    if args.candidate is None:
        raise baseline.BaselineError("--candidate is required unless --print-model-hashes is used")
    profile, case_names, seeds, ranks, backends, sections = resolve_selection(manifest, args)
    validate_manifest(manifest)
    cases = {
        case_name: common.profile_case(manifest, args.profile, case_name)
        for case_name in case_names
    }

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

    case_definitions = {
        case_name: {
            "atoms": case["atoms"],
            "cells": case["cells"],
            "model_sha256": {
                str(seed): case["model_sha256"][str(seed)] for seed in seeds
            },
            "domain_mode_by_rank": {
                str(rank): expected_domain_mode(case, rank) for rank in ranks
            },
        }
        for case_name, case in cases.items()
    }
    run_contract = {
        "profile": args.profile,
        "profile_parameters": dict(profile),
        "case_definitions": case_definitions,
        "cases": case_names,
        "seeds": seeds,
        "ranks": ranks,
        "backends": backends,
        "sections": sections,
        "reference": str(reference),
        "reference_sha256": baseline.sha256(reference),
        "candidate": str(candidate),
        "candidate_sha256": baseline.sha256(candidate),
        "mpiexec": str(mpiexec),
        "devices": devices,
        "stage_retries": args.retries,
    }
    work_root, resumed = initialize_work_root(args, run_contract)
    resume_stages = resumed
    expected_config_order = [
        (case_name, seed, backend, rank_count)
        for case_name in case_names
        for seed in seeds
        for backend in backends
        for rank_count in ranks
    ]
    expected_configs = set(expected_config_order)
    domain_mode_counts = {
        mode: sum(
            expected_domain_mode(cases[case_name], rank_count) == mode
            for case_name, _seed, _backend, rank_count in expected_config_order
        )
        for mode in DOMAIN_MODE_ORDER
    }
    active_domain_modes = [
        mode for mode in DOMAIN_MODE_ORDER if domain_mode_counts[mode] > 0
    ]
    restart_transitions = [(min(ranks), max(ranks))]
    if min(ranks) != max(ranks):
        restart_transitions.append((max(ranks), min(ranks)))
    expected_restart_order = [
        (case_name, backend, source_ranks, destination_ranks)
        for case_name in case_names
        if "restart" in sections
        and cases[case_name].get("coverage", "physics") == "physics"
        for backend in backends
        for source_ranks, destination_ranks in restart_transitions
    ]
    completed_configs = {
        key
        for key in expected_configs
        if (
            work_root / key[0] / f"seed-{key[1]}" / f"{key[2]}-r{key[3]}" /
            CONFIG_COMPLETE_NAME
        ).is_file()
    }
    try:
        dashboard = terminal_ui.open_dashboard(
            args.ui,
            args.ui_fd,
            args.profile,
            case_names,
            seeds,
            ranks,
            backends,
            expected_config_order,
            expected_restart_order,
            log_path=args.ui_log.resolve() if args.ui_log else None,
            report_path=args.report.resolve() if args.report else None,
        )
    except OSError as error:
        raise baseline.BaselineError(f"cannot open dashboard terminal: {error}") from error
    if dashboard is not None:
        common.set_stage_event_sink(dashboard.stage_event)
    print(
        f"LONG_NVE_PLAN profile={args.profile} total_configs={len(expected_configs)} "
        f"total_restarts={len(expected_restart_order)} "
        f"completed_configs={len(completed_configs)} "
        f"pending_configs={len(expected_configs) - len(completed_configs)} "
        f"cases={','.join(case_names)} seeds={','.join(str(value) for value in seeds)} "
        f"ranks={','.join(str(value) for value in ranks)} backends={','.join(backends)} "
        f"domain_modes={','.join(active_domain_modes)} "
        f"m1_configs={domain_mode_counts['m1-fallback']} "
        f"m2a_configs={domain_mode_counts['m2a']} "
        f"sections={','.join(sections)} retries={args.retries} work_root={work_root}",
        flush=True,
    )
    if dashboard is not None:
        dashboard.set_plan(work_root, completed_configs)
    succeeded = False
    report: Dict[str, Any] = {
        "schema_version": 1,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile": args.profile,
        "profile_parameters": dict(profile),
        "case_definitions": case_definitions,
        "cases": case_names,
        "seeds": seeds,
        "ranks": ranks,
        "backends": backends,
        "domain_modes": active_domain_modes,
        "domain_mode_config_counts": domain_mode_counts,
        "sections": sections,
        "reference": str(reference),
        "reference_sha256": baseline.sha256(reference),
        "candidate": str(candidate),
        "candidate_sha256": baseline.sha256(candidate),
        "work_root": str(work_root),
        "resumed": resumed,
        "adopted_existing": bool(args.adopt_existing),
        "stage_retries": args.retries,
        "results": {},
    }
    reference_env = reference_environment(devices[0])
    collector = baseline.DiffCollector(baseline.load_manifest()["tolerances"], enforce=True)
    try:
        for case_name in case_names:
            case = cases[case_name]
            potential_text = common.generate_potential(case)
            common.validate_generated_potential(case, potential_text)
            physics_case = case.get("coverage", "physics") == "physics"
            case_report: Dict[str, Any] = {
                "atoms": case["atoms"],
                "time_step_fs": case["time_step_fs"],
                "coverage": case.get("coverage", "physics"),
                "domain_mode_by_config": {},
                "strict_differential": {},
                "reference_metrics": {},
                "candidate_metrics": {},
                "pair_histogram_l1": {},
                "replay_frames": {},
                "noninferiority": {},
                "nvt_reference_metrics": {},
                "nvt_candidate_metrics": {},
                "nvt_rdf_l1": {},
                "nvt_equivalence": {},
                "restart": [],
            }
            report["results"][case_name] = case_report
            reference_metrics_by_seed: Dict[int, Dict[str, float]] = {}
            candidate_metrics_by_config: Dict[str, Dict[int, Dict[str, float]]] = {}
            reference_nvt_metrics_by_seed: Dict[int, Dict[str, float]] = {}
            candidate_nvt_metrics_by_config: Dict[str, Dict[int, Dict[str, float]]] = {}

            for seed in seeds:
                model = common.generate_model(case, seed)
                common.validate_generated_model(case, seed, model)
                seed_root = work_root / case_name / f"seed-{seed}"
                reference_static = run_reference_stage(
                    reference, model, common.static_run(), potential_text,
                    seed_root / "reference-static",
                    reference_env, args.timeout, ("static.xyz", "thermo.out"),
                    resume_stages, args.adopt_existing, args.retries,
                )
                reference_short = None
                if "short" in sections:
                    reference_short = run_reference_stage(
                        reference, model,
                        common.short_run(float(case["time_step_fs"]), int(profile["short_steps"])),
                        potential_text, seed_root / "reference-short", reference_env, args.timeout,
                        ("short.xyz", "thermo.out"),
                        resume_stages, args.adopt_existing, args.retries,
                    )
                reference_long = None
                reference_histogram = None
                if "long" in sections and physics_case:
                    reference_long = run_reference_stage(
                        reference, model,
                        common.long_run(
                            float(case["time_step_fs"]), int(profile["steps"]),
                            int(profile["thermo_interval"]), int(profile["trajectory_interval"]),
                        ),
                        potential_text, seed_root / "reference-long", reference_env, args.timeout,
                        ("trajectory.xyz", "thermo.out", "restart.xyz"),
                        resume_stages, args.adopt_existing, args.retries,
                    )
                    reference_metrics, reference_histogram = append_trajectory_metrics(
                        reference_static, reference_long, case
                    )
                    reference_metrics_by_seed[seed] = reference_metrics
                    case_report["reference_metrics"][str(seed)] = reference_metrics

                reference_nvt_metrics = None
                reference_nvt_rdf = None
                if "nvt" in sections and physics_case:
                    reference_nvt = run_reference_stage(
                        reference,
                        model,
                        common.nvt_run(
                            float(case["time_step_fs"]),
                            int(profile["nvt_equilibration_steps"]),
                            int(profile["nvt_sampling_steps"]),
                            int(profile["nvt_thermo_interval"]),
                            int(profile["nvt_trajectory_interval"]),
                            float(case["nvt_temperature_K"]),
                            float(profile["nvt_temperature_coupling"]),
                        ),
                        potential_text,
                        seed_root / "reference-nvt",
                        reference_env,
                        args.timeout,
                        ("nvt.xyz", "thermo.out"),
                        resume_stages,
                        args.adopt_existing,
                        args.retries,
                    )
                    reference_nvt_metrics, reference_nvt_rdf = append_nvt_metrics(
                        reference_nvt, case
                    )
                    reference_nvt_metrics_by_seed[seed] = reference_nvt_metrics
                    case_report["nvt_reference_metrics"][str(seed)] = reference_nvt_metrics

                for backend, rank_count, expected_mode in ordered_configurations(
                    case, ranks, backends
                ):
                    config = f"{backend}-r{rank_count}"
                    config_root = seed_root / config
                    config_key = (case_name, seed, backend, rank_count)
                    revalidating = config_key in completed_configs
                    print(
                        "LONG_NVE_CONFIG status="
                        f"{'revalidating' if revalidating else 'running'} "
                        f"completed={len(completed_configs)} total={len(expected_configs)} "
                        f"pending={len(expected_configs) - len(completed_configs)} "
                        f"case={case_name} seed={seed} backend={backend} "
                        f"ranks={rank_count} domain_mode={expected_mode}",
                        flush=True,
                    )
                    if dashboard is not None:
                        dashboard.config_started(config_key, revalidating)
                    env = candidate_environment(
                        devices, rank_count, backend,
                        int(profile["communication_log_interval"]),
                    )
                    actual_static = run_candidate_stage(
                        candidate, mpiexec, rank_count, backend, expected_mode, model,
                        common.static_run(), potential_text, config_root / "static", env,
                        args.timeout,
                        ("static.xyz", "thermo.out"),
                        resume_stages, args.adopt_existing, args.retries,
                    )
                    common.compare_configuration_files(
                        reference_static / "static.xyz", actual_static / "static.xyz", collector,
                        f"static/{case_name}/seed-{seed}/{config}",
                    )
                    completed_strict_stages = ["static"]
                    if reference_short is not None:
                        actual_short = run_candidate_stage(
                            candidate, mpiexec, rank_count, backend, expected_mode, model,
                            common.short_run(
                                float(case["time_step_fs"]), int(profile["short_steps"])
                            ),
                            potential_text, config_root / "short", env, args.timeout,
                            ("short.xyz", "thermo.out"),
                            resume_stages, args.adopt_existing, args.retries,
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
                        completed_strict_stages.append("short")
                    case_report["strict_differential"].setdefault(config, {})[
                        str(seed)
                    ] = completed_strict_stages
                    if reference_long is not None and reference_histogram is not None:
                        actual_long = run_candidate_stage(
                            candidate, mpiexec, rank_count, backend, expected_mode, model,
                            common.long_run(
                                float(case["time_step_fs"]), int(profile["steps"]),
                                int(profile["thermo_interval"]),
                                int(profile["trajectory_interval"]),
                            ),
                            potential_text, config_root / "long", env, args.timeout,
                            ("trajectory.xyz", "thermo.out", "restart.xyz"),
                            resume_stages, args.adopt_existing, args.retries,
                        )
                        actual_metrics, actual_histogram = append_trajectory_metrics(
                            actual_static, actual_long, case
                        )
                        candidate_metrics_by_config.setdefault(config, {})[seed] = actual_metrics
                        case_report["candidate_metrics"].setdefault(config, {})[
                            str(seed)
                        ] = actual_metrics
                        histogram_l1 = common.histogram_l1(
                            reference_histogram, actual_histogram
                        )
                        case_report["pair_histogram_l1"].setdefault(config, {})[
                            str(seed)
                        ] = histogram_l1
                        if histogram_l1 > float(
                            manifest["acceptance"]["pair_histogram_l1_limit"]
                        ):
                            raise baseline.BaselineError(
                                f"{case_name}/seed-{seed}/{config}: pair histogram L1 "
                                f"{histogram_l1:.6e} exceeds limit"
                            )
                        if "replay" in sections:
                            forward = replay_frames(
                                reference_long / "trajectory.xyz", candidate,
                                (str(mpiexec), "-n", str(rank_count)), potential_text,
                                config_root / "replay-reference-in-candidate", env,
                                args.timeout, collector,
                                f"replay-reference/{case_name}/seed-{seed}/{config}",
                                (rank_count, backend, expected_mode),
                                resume_stages, args.adopt_existing, args.retries,
                            )
                            reverse = replay_frames(
                                actual_long / "trajectory.xyz", reference, (), potential_text,
                                config_root / "replay-candidate-in-reference", reference_env,
                                args.timeout, collector,
                                f"replay-candidate/{case_name}/seed-{seed}/{config}", None,
                                resume_stages, args.adopt_existing, args.retries,
                            )
                            case_report["replay_frames"].setdefault(config, {})[
                                str(seed)
                            ] = {
                                "reference_in_candidate": forward,
                                "candidate_in_reference": reverse,
                            }

                    if reference_nvt_metrics is not None and reference_nvt_rdf is not None:
                        actual_nvt = run_candidate_stage(
                            candidate,
                            mpiexec,
                            rank_count,
                            backend,
                            expected_mode,
                            model,
                            common.nvt_run(
                                float(case["time_step_fs"]),
                                int(profile["nvt_equilibration_steps"]),
                                int(profile["nvt_sampling_steps"]),
                                int(profile["nvt_thermo_interval"]),
                                int(profile["nvt_trajectory_interval"]),
                                float(case["nvt_temperature_K"]),
                                float(profile["nvt_temperature_coupling"]),
                            ),
                            potential_text,
                            config_root / "nvt",
                            env,
                            args.timeout,
                            ("nvt.xyz", "thermo.out"),
                            resume_stages,
                            args.adopt_existing,
                            args.retries,
                        )
                        actual_nvt_metrics, actual_nvt_rdf = append_nvt_metrics(
                            actual_nvt, case
                        )
                        candidate_nvt_metrics_by_config.setdefault(config, {})[
                            seed
                        ] = actual_nvt_metrics
                        case_report["nvt_candidate_metrics"].setdefault(config, {})[
                            str(seed)
                        ] = actual_nvt_metrics
                        nvt_rdf_l1 = common.rdf_l1(reference_nvt_rdf, actual_nvt_rdf)
                        case_report["nvt_rdf_l1"].setdefault(config, {})[
                            str(seed)
                        ] = nvt_rdf_l1
                        if nvt_rdf_l1 > float(
                            manifest["statistical_acceptance"]["rdf_time_average_l1_limit"]
                        ):
                            raise baseline.BaselineError(
                                f"{case_name}/seed-{seed}/{config}: time-averaged RDF L1 "
                                f"{nvt_rdf_l1:.6e} exceeds limit"
                            )
                    write_json_atomic(
                        config_root / CONFIG_COMPLETE_NAME,
                        {
                            "schema_version": 1,
                            "status": "complete",
                            "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "case": case_name,
                            "seed": seed,
                            "backend": backend,
                            "ranks": rank_count,
                            "domain_mode": expected_mode,
                        },
                    )
                    case_report["domain_mode_by_config"][config] = expected_mode
                    completed_configs.add(config_key)
                    print(
                        f"LONG_NVE_CONFIG status=passed completed={len(completed_configs)} "
                        f"total={len(expected_configs)} "
                        f"pending={len(expected_configs) - len(completed_configs)} "
                        f"case={case_name} seed={seed} backend={backend} "
                        f"ranks={rank_count} domain_mode={expected_mode}",
                        flush=True,
                    )
                    if dashboard is not None:
                        dashboard.config_passed(config_key)

            if "long" in sections and physics_case:
                if dashboard is not None:
                    dashboard.set_phase(
                        "acceptance analysis", f"{case_name}: NVE noninferiority"
                    )
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

            if "nvt" in sections and physics_case:
                if dashboard is not None:
                    dashboard.set_phase(
                        "acceptance analysis", f"{case_name}: NVT equivalence"
                    )
                ordered_reference = [reference_nvt_metrics_by_seed[seed] for seed in seeds]
                for config, values_by_seed in candidate_nvt_metrics_by_config.items():
                    ordered_actual = [values_by_seed[seed] for seed in seeds]
                    comparison = common.enforce_distribution_equivalence(
                        ordered_reference,
                        ordered_actual,
                        manifest["statistical_acceptance"]["metrics"],
                        f"nvt/{case_name}/{config}",
                    )
                    case_report["nvt_equivalence"][config] = comparison

            if "restart" in sections and physics_case:
                restart_seed = seeds[0]
                restart_model = common.generate_model(case, restart_seed)
                transitions = [(min(ranks), max(ranks))]
                if min(ranks) != max(ranks):
                    transitions.append((max(ranks), min(ranks)))
                for backend in backends:
                    for source_ranks, destination_ranks in transitions:
                        restart_key = (
                            case_name, backend, source_ranks, destination_ranks
                        )
                        print(
                            "LONG_NVE_RESTART status=running "
                            f"case={case_name} backend={backend} "
                            f"source_ranks={source_ranks} "
                            f"destination_ranks={destination_ranks}",
                            flush=True,
                        )
                        if dashboard is not None:
                            dashboard.restart_started(restart_key)
                        transition_root = (
                            work_root / case_name / "restart" /
                            f"seed-{restart_seed}-{backend}-r{source_ranks}-to-r{destination_ranks}"
                        )
                        result = restart_transition(
                            reference, candidate, mpiexec, reference_env, devices, backend,
                            source_ranks, destination_ranks, restart_model, potential_text,
                            case, profile,
                            transition_root, args.timeout, collector,
                            resume_stages, args.adopt_existing, args.retries,
                        )
                        result["backend"] = backend
                        result["seed"] = restart_seed
                        case_report["restart"].append(result)
                        print(
                            "LONG_NVE_RESTART status=passed "
                            f"case={case_name} backend={backend} "
                            f"source_ranks={source_ranks} "
                            f"destination_ranks={destination_ranks}",
                            flush=True,
                        )
                        if dashboard is not None:
                            dashboard.restart_passed(restart_key)

        if dashboard is not None:
            dashboard.set_phase("finalizing", "writing final report")
        report["field_differences"] = collector.stats
        report["checkpoint_provenance"] = checkpoint_provenance_summary(work_root)
        report["completed_configs"] = len(completed_configs)
        report["total_configs"] = len(expected_configs)
        report_path = args.report.resolve() if args.report else work_root / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(report_path, report)
        if dashboard is not None:
            dashboard.finish()
            dashboard.close()
        print(
            f"PASS long-NVE profile={args.profile} cases={case_names} seeds={seeds} "
            f"ranks={ranks} backends={backends}"
        )
        if args.report:
            print(f"Wrote {report_path}")
        succeeded = True
        return 0
    except BaseException as error:
        if dashboard is not None:
            summary = " ".join(str(error).splitlines()) or type(error).__name__
            dashboard.fail(summary)
            dashboard.close(show_failure_summary=True)
        raise
    finally:
        common.set_stage_event_sink(None)
        if dashboard is not None:
            dashboard.close()
        if succeeded and not args.keep_work and not resumed:
            shutil.rmtree(work_root)
        else:
            print(f"work directory retained: {work_root}", file=sys.stderr)
            if not succeeded:
                print(
                    "resume with the same selection and add: "
                    f"--resume-work {work_root}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (baseline.BaselineError, subprocess.TimeoutExpired, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
