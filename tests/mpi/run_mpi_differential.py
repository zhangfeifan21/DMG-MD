#!/usr/bin/env python3
"""Run the acceptance matrix specified by docs/standards/replicated-mpi.md.

Besides numerical golden comparisons, this checks the machine-readable
startup/backend records, the exact owned-center coverage proof, per-step
communication accounting, NVE drift, and cross-rank/backend output stability.
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = PROJECT_ROOT / "tests" / "baseline"
sys.path.insert(0, str(BASELINE_DIR))
import run_baselines as baseline  # noqa: E402
import check_environment as mpi_environment  # noqa: E402


def comma_list(text: str) -> List[str]:
    values = [value.strip() for value in text.split(",") if value.strip()]
    if not values:
        raise argparse.ArgumentTypeError("list must not be empty")
    return values


def integer_list(text: str) -> List[int]:
    try:
        values = [int(value) for value in comma_list(text)]
    except ValueError as error:
        raise argparse.ArgumentTypeError("ranks must be integers") from error
    if any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError("ranks must be positive")
    return values


def expected_steps(run_file: Path) -> int:
    total = 0
    for line in run_file.read_text(encoding="utf-8").splitlines():
        tokens = line.split()
        if tokens and tokens[0] == "run":
            total += int(tokens[1])
    return total


def key_values(line: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for token in line.split()[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            result[key] = value
    return result


def validate_runtime_record(
    stage_dir: Path,
    ranks: int,
    backend_name: str,
    expected_domain_mode: str = "m1-fallback",
    require_domain_layouts: bool = True,
) -> None:
    if expected_domain_mode not in ("m1-fallback", "m2a"):
        raise baseline.BaselineError(
            f"{stage_dir}: unsupported expected domain mode {expected_domain_mode!r}"
        )
    stdout = (stage_dir / "execution.stdout").read_text(encoding="utf-8")
    implementations = [
        line for line in stdout.splitlines() if line.startswith("DMGMD_MPI implementation=")
    ]
    if len(implementations) != 1 or implementations[0].endswith('implementation=""'):
        raise baseline.BaselineError(f"{stage_dir}: missing unique MPI implementation record")
    rank_records = [line for line in stdout.splitlines() if line.startswith("DMGMD_MPI rank=")]
    if len(rank_records) != ranks:
        raise baseline.BaselineError(
            f"{stage_dir}: found {len(rank_records)} rank startup records, expected {ranks}"
        )
    selected_uuids = set()
    seen_world_ranks = set()
    local_ranks_by_host: Dict[str, set[int]] = {}
    for line in rank_records:
        fields = key_values(line)
        required = {
            "rank", "world_size", "local_rank", "local_size", "hostname",
            "cuda_device", "cuda_uuid", "cuda_aware_capability",
            "cuda_aware_self_test", "backend",
        }
        if not required.issubset(fields):
            raise baseline.BaselineError(f"{stage_dir}: incomplete MPI startup record: {line}")
        if int(fields["world_size"]) != ranks:
            raise baseline.BaselineError(f"{stage_dir}: startup world size is inconsistent")
        if fields.get("backend") != backend_name:
            raise baseline.BaselineError(
                f"{stage_dir}: selected backend {fields.get('backend')}, expected {backend_name}"
            )
        if fields.get("cuda_aware_capability") != "supported":
            raise baseline.BaselineError(f"{stage_dir}: Open MPI did not report CUDA awareness")
        expected_self_test = "passed" if backend_name == "CudaAware" else "not-run"
        if fields.get("cuda_aware_self_test") != expected_self_test:
            raise baseline.BaselineError(
                f"{stage_dir}: CUDA-aware self-test is "
                f"{fields.get('cuda_aware_self_test')}, expected {expected_self_test}"
            )
        seen_world_ranks.add(int(fields["rank"]))
        selected_uuids.add(fields["cuda_uuid"])
        local_ranks_by_host.setdefault(fields["hostname"], set()).add(
            int(fields["local_rank"])
        )
    if seen_world_ranks != set(range(ranks)):
        raise baseline.BaselineError(f"{stage_dir}: startup records do not cover every world rank")
    if len(selected_uuids) != ranks:
        raise baseline.BaselineError(f"{stage_dir}: CUDA device UUIDs are not unique per rank")
    for hostname, local_ranks in local_ranks_by_host.items():
        if local_ranks != set(range(len(local_ranks))):
            raise baseline.BaselineError(
                f"{stage_dir}: non-contiguous local ranks on host {hostname}: {local_ranks}"
            )

    coverage = [
        line for line in stdout.splitlines() if line.startswith("DMGMD_CENTER_PARTITION ")
    ]
    if len(coverage) != 1:
        raise baseline.BaselineError(f"{stage_dir}: missing unique center partition proof")
    fields = key_values(coverage[0])
    expected = {
        "missing": "0",
        "overlapping": "0",
        "owned_output_coverage": "complete",
        "nep_kernel_centers": (
            "replicated-full"
            if expected_domain_mode == "m1-fallback"
            else "local-domain-force-centers"
        ),
        "nep_N1_N2_shard_complete": (
            "false" if expected_domain_mode == "m1-fallback" else "true"
        ),
    }
    for key, value in expected.items():
        if fields.get(key) != value:
            raise baseline.BaselineError(
                f"{stage_dir}: center proof {key}={fields.get(key)}, expected {value}"
            )

    # The short differential suite passes the default M1 expectation for its
    # 24 A fixtures.  Long-NVE callers pass an explicit per-fixture expectation
    # so an accidental fallback can never be mistaken for M2a coverage.
    domain = [
        line for line in stdout.splitlines() if line.startswith("DMGMD_DOMAIN ")
    ]
    if len(domain) != 1:
        raise baseline.BaselineError(f"{stage_dir}: missing unique DMGMD_DOMAIN record")
    domain_fields = key_values(domain[0])
    if domain_fields.get("mode") != expected_domain_mode:
        raise baseline.BaselineError(
            f"{stage_dir}: expected domain mode {expected_domain_mode!r}, got "
            f"{domain_fields.get('mode')!r}"
        )

    global_count = int(fields["global_count"])
    # M1 spatial ownership: one owned-count record per rank (the M0 contiguous
    # begin/end ranges no longer exist). The partition must cover every atom
    # exactly once, but individual ranks may own zero atoms.
    ownership = [
        line for line in stdout.splitlines() if line.startswith("DMGMD_CENTER_OWNERSHIP ")
    ]
    if len(ownership) != ranks:
        raise baseline.BaselineError(f"{stage_dir}: center ownership record count is incomplete")
    owned_total = 0
    for rank, line in enumerate(ownership):
        item = key_values(line)
        owned_count = int(item.get("owned_count", -1))
        if int(item.get("rank", -1)) != rank or owned_count < 0:
            raise baseline.BaselineError(f"{stage_dir}: malformed center ownership record: {line}")
        owned_total += owned_count
    if owned_total != global_count:
        raise baseline.BaselineError(
            f"{stage_dir}: center ownership counts cover {owned_total}, expected {global_count}"
        )

    layouts = [
        line for line in stdout.splitlines() if line.startswith("DMGMD_DOMAIN_LAYOUT ")
    ]
    if expected_domain_mode == "m1-fallback":
        if layouts:
            raise baseline.BaselineError(
                f"{stage_dir}: M1 fallback unexpectedly emitted local-domain layouts"
            )
    elif require_domain_layouts:
        initial_layout_ranks = set()
        initial_owned_total = 0
        for line in layouts:
            item = key_values(line)
            required = {
                "rank", "step", "owned", "dep_left", "dep_right",
                "coord_left", "coord_right", "local_count",
            }
            if not required.issubset(item):
                raise baseline.BaselineError(
                    f"{stage_dir}: incomplete local-domain layout record: {line}"
                )
            values = {
                key: int(item[key])
                for key in required
            }
            rank = values["rank"]
            if rank < 0 or rank >= ranks:
                raise baseline.BaselineError(
                    f"{stage_dir}: local-domain layout has invalid rank {rank}"
                )
            count_keys = (
                "owned", "dep_left", "dep_right", "coord_left", "coord_right"
            )
            if any(values[key] < 0 for key in count_keys) or values["local_count"] < 0:
                raise baseline.BaselineError(
                    f"{stage_dir}: local-domain layout has a negative count: {line}"
                )
            if sum(values[key] for key in count_keys) != values["local_count"]:
                raise baseline.BaselineError(
                    f"{stage_dir}: local-domain layout parts do not equal local_count: {line}"
                )
            if values["step"] == 0:
                if rank in initial_layout_ranks:
                    raise baseline.BaselineError(
                        f"{stage_dir}: duplicate step-0 layout for rank {rank}"
                    )
                initial_layout_ranks.add(rank)
                initial_owned_total += values["owned"]
        if initial_layout_ranks != set(range(ranks)):
            raise baseline.BaselineError(
                f"{stage_dir}: step-0 local-domain layouts do not cover every rank"
            )
        if initial_owned_total != global_count:
            raise baseline.BaselineError(
                f"{stage_dir}: step-0 local-domain owned counts cover "
                f"{initial_owned_total}, expected {global_count}"
            )
    elif layouts:
        raise baseline.BaselineError(
            f"{stage_dir}: performance-mode stage unexpectedly emitted domain layouts"
        )

    accounting = [
        line for line in stdout.splitlines() if line.startswith("DMGMD_COMM accounting=")
    ]
    if len(accounting) != 1:
        raise baseline.BaselineError(f"{stage_dir}: missing unique communication accounting record")
    accounting_fields = key_values(accounting[0])
    log_interval = int(accounting_fields.get("log_interval", "1"))
    if log_interval <= 0:
        raise baseline.BaselineError(f"{stage_dir}: invalid communication log interval")

    run_steps = expected_steps(stage_dir / "run.in")
    expected_log_steps = list(range(log_interval, run_steps + 1, log_interval))
    communication = [line for line in stdout.splitlines() if line.startswith("DMGMD_COMM step=")]
    if len(communication) != len(expected_log_steps):
        raise baseline.BaselineError(
            f"{stage_dir}: found {len(communication)} step communication records, "
            f"expected {len(expected_log_steps)} for interval {log_interval}"
        )
    for expected_step, line in zip(expected_log_steps, communication):
        fields = key_values(line)
        if int(fields["step"]) != expected_step or fields["backend"] != backend_name:
            raise baseline.BaselineError(f"{stage_dir}: malformed communication step record")
        for key in (
            "collective_calls",
            "mpi_input_bytes_global",
            "mpi_output_bytes_global",
            "device_to_host_bytes_global",
            "host_to_device_bytes_global",
            "output_download_bytes",
        ):
            if int(fields[key]) < 0:
                raise baseline.BaselineError(f"{stage_dir}: negative communication volume")
        if int(fields["mpi_input_bytes_global"]) == 0 or int(fields["mpi_output_bytes_global"]) == 0:
            raise baseline.BaselineError(f"{stage_dir}: empty per-step MPI byte accounting")
        if int(fields["collective_calls"]) < 2:
            raise baseline.BaselineError(f"{stage_dir}: expected runtime control collectives")
        if backend_name == "HostStaged":
            if int(fields["device_to_host_bytes_global"]) == 0:
                raise baseline.BaselineError(f"{stage_dir}: HostStaged omitted device-to-host bytes")
            if int(fields["host_to_device_bytes_global"]) == 0:
                raise baseline.BaselineError(f"{stage_dir}: HostStaged omitted host-to-device bytes")
            if int(fields["output_download_bytes"]) != 0:
                raise baseline.BaselineError(f"{stage_dir}: HostStaged used CudaAware output download")
        elif (
            int(fields["device_to_host_bytes_global"]) != 0
            or int(fields["host_to_device_bytes_global"]) != 0
        ):
            raise baseline.BaselineError(f"{stage_dir}: CudaAware unexpectedly used host staging")


def nve_metrics(path: Path, atom_count: int) -> Tuple[float, float]:
    segment = baseline.parse_thermo(path)[-1]
    energies = [(row[1] + row[2]) / atom_count for row in segment["rows"]]
    if len(energies) < 2:
        raise baseline.BaselineError(f"{path}: NVE drift requires at least two samples")
    dt_fs = float(segment["headers"][3].split()[2])
    times = [dt_fs * index for index in range(len(energies))]
    mean_time = sum(times) / len(times)
    mean_energy = sum(energies) / len(energies)
    denominator = sum((time - mean_time) ** 2 for time in times)
    slope = sum(
        (time - mean_time) * (energy - mean_energy)
        for time, energy in zip(times, energies)
    ) / denominator
    excursion = max(abs(energy - energies[0]) for energy in energies)
    return excursion, slope


def validate_nve_drift(
    candidate_path: Path,
    manifest: Dict[str, Any],
    label: str,
) -> Tuple[float, float]:
    reference_path = BASELINE_DIR / "goldens" / "single_large_nve" / "main" / "thermo.out"
    atom_count = manifest["cases"]["single_large_nve"]["stages"][0]["outputs"][
        "trajectory.xyz"
    ]["natoms"]
    reference = nve_metrics(reference_path, atom_count)
    candidate = nve_metrics(candidate_path, atom_count)
    rows = baseline.parse_thermo(reference_path)[-1]["rows"]
    tolerance = manifest["tolerances"]["energy"]
    maximum_row_error = max(
        tolerance["atol"] + tolerance["rtol"] * abs(row[1])
        + tolerance["atol"] + tolerance["rtol"] * abs(row[2])
        for row in rows
    ) / atom_count
    excursion_tolerance = 2.0 * maximum_row_error
    duration = float(len(rows) - 1) * float(
        baseline.parse_thermo(reference_path)[-1]["headers"][3].split()[2]
    )
    slope_tolerance = 2.0 * excursion_tolerance / duration
    if abs(candidate[0] - reference[0]) > excursion_tolerance:
        raise baseline.BaselineError(
            f"{label}: NVE max excursion {candidate[0]:.6e} differs from baseline "
            f"{reference[0]:.6e} by more than {excursion_tolerance:.3e} eV/atom"
        )
    if abs(candidate[1] - reference[1]) > slope_tolerance:
        raise baseline.BaselineError(
            f"{label}: NVE drift slope {candidate[1]:.6e} differs from baseline "
            f"{reference[1]:.6e} by more than {slope_tolerance:.3e} eV/(atom fs)"
        )
    return candidate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=PROJECT_ROOT / "build" / "dmg-md")
    parser.add_argument("--mpiexec", type=Path, default=Path("mpiexec"))
    parser.add_argument("--ranks", type=integer_list, default=[1, 2, 4])
    parser.add_argument(
        "--backends", type=comma_list, default=["HostStaged", "CudaAware"],
        help="comma-separated HostStaged,CudaAware; CudaAware must pass its active self-test",
    )
    parser.add_argument(
        "--devices", type=str,
        help="comma-separated CUDA device IDs/UUIDs; defaults to CUDA_VISIBLE_DEVICES",
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--keep-work", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executable = args.candidate.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise baseline.BaselineError(f"candidate is not executable: {executable}")
    manifest = baseline.load_manifest()
    baseline.validate_input_hashes(manifest)
    supported_backends = {"hoststaged": "HostStaged", "cudaaware": "CudaAware"}
    backends: List[str] = []
    for requested in args.backends:
        normalized = requested.replace("_", "").lower()
        if normalized not in supported_backends:
            raise baseline.BaselineError(f"unsupported communication backend {requested}")
        backends.append(supported_backends[normalized])

    devices_text = args.devices or os.environ.get("CUDA_VISIBLE_DEVICES", "")
    devices = comma_list(devices_text) if devices_text else []
    if len(devices) < max(args.ranks):
        raise baseline.BaselineError(
            f"need at least {max(args.ranks)} visible device IDs for rank matrix; got {devices}"
        )

    # Keep this before manifest staging and every MD execution.  The dedicated
    # preflight distinguishes a broken Open MPI/UCX/CUDA stack from numerical
    # failures in the replicated runtime.
    try:
        mpiexec = mpi_environment.validate_environment(
            executable, args.mpiexec, devices, max(args.ranks), args.timeout
        )
    except mpi_environment.EnvironmentError as error:
        raise baseline.BaselineError(str(error)) from error

    work_root = Path(tempfile.mkdtemp(prefix="dmgmd-mpi-differential-"))
    succeeded = False
    result_sets: Dict[Tuple[str, int], Dict[Tuple[str, str], Path]] = {}
    try:
        for backend_name in backends:
            for ranks in args.ranks:
                env = os.environ.copy()
                env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
                env["CUDA_VISIBLE_DEVICES"] = ",".join(devices[:ranks])
                env["DMGMD_COMM_BACKEND"] = backend_name
                env["DMGMD_COMM_LOG_INTERVAL"] = "1"
                run_root = work_root / backend_name / f"ranks-{ranks}"
                result_dirs = baseline.execute_suite(
                    executable,
                    manifest,
                    run_root,
                    env,
                    args.timeout,
                    launcher=[str(mpiexec), "-n", str(ranks)],
                )
                baseline.compare_with_goldens(result_dirs, manifest, exact_reference=False)
                for stage_dir in result_dirs.values():
                    validate_runtime_record(stage_dir, ranks, backend_name)
                metrics = validate_nve_drift(
                    result_dirs[("single_large_nve", "main")] / "thermo.out",
                    manifest,
                    f"{backend_name}/{ranks} ranks",
                )
                print(
                    f"PASS {backend_name:10s} ranks={ranks}: "
                    f"NVE max_excursion={metrics[0]:.6e} eV/atom "
                    f"slope={metrics[1]:.6e} eV/(atom fs)"
                )
                result_sets[(backend_name, ranks)] = result_dirs

        reference_key = (backends[0], args.ranks[0])
        reference_dirs = result_sets[reference_key]
        for key, actual_dirs in result_sets.items():
            if key == reference_key:
                continue
            collector = baseline.DiffCollector(manifest["tolerances"], enforce=True)
            for case_name, case in manifest["cases"].items():
                for stage in case["stages"]:
                    stage_name = stage["name"]
                    baseline.compare_stage(
                        reference_dirs[(case_name, stage_name)],
                        actual_dirs[(case_name, stage_name)],
                        stage["outputs"],
                        collector,
                        f"cross-rank/{key[0]}/{key[1]}/{case_name}/{stage_name}",
                    )
        print(
            f"PASS: replicated-data MPI differential matrix ranks={args.ranks} "
            f"backends={backends}"
        )
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
    except (baseline.BaselineError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
