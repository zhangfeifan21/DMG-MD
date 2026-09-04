#!/usr/bin/env python3
"""Validate the canonical Open MPI + UCX CUDA environment before MD tests.

The checks are intentionally ordered from installation identity to runtime
device collectives.  A failure here is an environment failure; the numerical
suite is not started, so it cannot be mistaken for an NEP regression.
"""

from __future__ import annotations

import argparse
import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class EnvironmentError(RuntimeError):
    """The active shell does not provide the supported MPI/CUDA stack."""


def _resolve_command(command: Path | str) -> Path:
    text = str(command)
    if "/" in text:
        resolved = Path(text).resolve()
    else:
        found = shutil.which(text)
        if found is None:
            raise EnvironmentError(f"required command is missing from PATH: {text}")
        resolved = Path(found).resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise EnvironmentError(f"command is not executable: {resolved}")
    return resolved


def _required_directory(environment: Mapping[str, str], name: str) -> Path:
    value = environment.get(name, "")
    if not value:
        raise EnvironmentError(
            f"{name} is unset; run 'source ../env/md-mpi.sh' from the repository root"
        )
    path = Path(value).resolve()
    if not path.is_dir():
        raise EnvironmentError(f"{name} is not a directory: {path}")
    return path


def _run(command: Sequence[str], environment: Mapping[str, str]) -> str:
    result = subprocess.run(
        list(command),
        env=dict(environment),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise EnvironmentError(
            f"environment command exited {result.returncode}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout + result.stderr


def _require_under(command: Path, root: Path, label: str) -> None:
    if not command.is_relative_to(root):
        raise EnvironmentError(
            f"{label} resolved outside the canonical installation: {command} (root {root})"
        )


def _key_values(line: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for token in line.split()[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            result[key] = value
    return result


def validate_environment(
    candidate: Path,
    mpiexec_command: Path | str,
    devices: Sequence[str],
    ranks: int,
    timeout: int,
) -> Path:
    """Validate installations, linkage, transports, GPUs, then active collectives."""

    environment = os.environ.copy()
    ompi_home = _required_directory(environment, "OMPI_HOME")
    ucx_home = _required_directory(environment, "UCX_HOME")
    cuda_home = _required_directory(environment, "CUDA_HOME")

    mpiexec = _resolve_command(mpiexec_command)
    mpicxx = _resolve_command("mpicxx")
    ompi_info = _resolve_command("ompi_info")
    ucx_info = _resolve_command("ucx_info")
    nvcc = _resolve_command("nvcc")
    for command, root, label in (
        (mpiexec, ompi_home, "mpiexec"),
        (mpicxx, ompi_home, "mpicxx"),
        (ompi_info, ompi_home, "ompi_info"),
        (ucx_info, ucx_home, "ucx_info"),
        (nvcc, cuda_home, "nvcc"),
    ):
        _require_under(command, root, label)

    expected_environment = {
        "OMPI_MCA_pml": "ucx",
        "OMPI_MCA_coll": "^hcoll",
        "UCX_WARN_UNUSED_ENV_VARS": "n",
    }
    for name, expected in expected_environment.items():
        if environment.get(name) != expected:
            raise EnvironmentError(
                f"{name}={environment.get(name)!r}, expected {expected!r}; "
                "reload ../env/md-mpi.sh"
            )
    component_path = Path(environment.get("OMPI_MCA_mca_base_component_path", "")).resolve()
    if component_path != (ompi_home / "lib" / "openmpi").resolve():
        raise EnvironmentError("Open MPI MCA component path does not match OMPI_HOME")
    pmix_component_path = Path(
        environment.get("PMIX_MCA_mca_base_component_path", "")
    ).resolve()
    if not pmix_component_path.is_dir():
        raise EnvironmentError("PMIx MCA component path is missing or invalid")

    mpi_version = _run([str(mpiexec), "--version"], environment)
    if "Open MPI" not in mpi_version:
        raise EnvironmentError(f"mpiexec is not Open MPI:\n{mpi_version}")
    ompi_config = _run([str(ompi_info), "--config"], environment)
    if f"--with-cuda={environment['CUDA_HOME']}" not in ompi_config:
        raise EnvironmentError("Open MPI was not configured with the selected CUDA_HOME")
    if f"--with-ucx={environment['UCX_HOME']}" not in ompi_config:
        raise EnvironmentError("Open MPI was not configured with the selected UCX_HOME")
    ompi_all = _run([str(ompi_info), "--parsable", "--all"], environment)
    if "mpi_built_with_cuda_support:value:true" not in ompi_all:
        raise EnvironmentError("Open MPI does not report CUDA buffer support")
    if "mca:pml:ucx:version" not in ompi_all:
        raise EnvironmentError("Open MPI does not provide the UCX PML component")
    if not (ompi_home / "include" / "mpi-ext.h").is_file():
        raise EnvironmentError("Open MPI mpi-ext.h is missing")

    ucx_version = _run([str(ucx_info), "-v"], environment)
    if str(ucx_home) not in ucx_version:
        raise EnvironmentError("ucx_info does not report the selected UCX_HOME library")
    ucx_devices = _run([str(ucx_info), "-d"], environment)
    for transport in ("cuda_copy", "cuda_ipc"):
        if f"Transport: {transport}" not in ucx_devices:
            raise EnvironmentError(f"UCX transport is unavailable: {transport}")

    candidate = candidate.resolve()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise EnvironmentError(f"candidate is not executable: {candidate}")
    linkage = _run(["ldd", str(candidate)], environment)
    for library_root, library in (
        (ompi_home / "lib", "libmpi"),
        (ucx_home / "lib", "libucp"),
        (ucx_home / "lib", "libuct"),
    ):
        matching_lines = [line for line in linkage.splitlines() if library in line]
        if not any(str(library_root) in line for line in matching_lines):
            raise EnvironmentError(
                f"candidate linkage does not resolve {library} from {library_root}"
            )

    if ranks <= 0:
        raise EnvironmentError("probe rank count must be positive")
    if len(devices) < ranks:
        raise EnvironmentError(f"need {ranks} CUDA devices for preflight; got {list(devices)}")
    nvidia_smi = _resolve_command("nvidia-smi")
    gpu_records = [
        line
        for line in _run(
            [
                str(nvidia_smi),
                "--query-gpu=index,name,uuid,driver_version",
                "--format=csv,noheader",
            ],
            environment,
        ).splitlines()
        if line.strip()
    ]
    if len(gpu_records) < ranks:
        raise EnvironmentError(f"nvidia-smi exposes {len(gpu_records)} GPUs, need {ranks}")

    # The executable probe runs before any test input is staged.  CudaAware is
    # requested deliberately so MPIX_Query_cuda_support and all device-buffer
    # collective forms must pass before the numerical matrix can start.
    probe_environment = environment.copy()
    probe_environment["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    probe_environment["CUDA_VISIBLE_DEVICES"] = ",".join(devices[:ranks])
    probe_environment["DMGMD_COMM_BACKEND"] = "CudaAware"
    try:
        result = subprocess.run(
            [str(mpiexec), "-n", str(ranks), str(candidate), "--probe-mpi-environment"],
            cwd=PROJECT_ROOT,
            env=probe_environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise EnvironmentError(f"CUDA-aware environment probe timed out after {timeout}s") from error
    if result.returncode != 0:
        raise EnvironmentError(
            f"CUDA-aware environment probe exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    lines = result.stdout.splitlines()
    if len([line for line in lines if line.startswith("DMGMD_MPI implementation=")]) != 1:
        raise EnvironmentError("probe did not record a unique MPI implementation")
    stack_records = [line for line in lines if line.startswith("DMGMD_MPI_STACK ")]
    if len(stack_records) != 1 or "provider=OpenMPI transport=UCX" not in stack_records[0]:
        raise EnvironmentError("probe did not record the Open MPI + UCX stack")
    rank_records = [line for line in lines if line.startswith("DMGMD_MPI rank=")]
    if len(rank_records) != ranks:
        raise EnvironmentError(f"probe recorded {len(rank_records)} ranks, expected {ranks}")
    uuids = set()
    for line in rank_records:
        fields = _key_values(line)
        if fields.get("cuda_aware_capability") != "supported":
            raise EnvironmentError(f"Open MPI CUDA-aware query failed: {line}")
        if fields.get("cuda_aware_self_test") != "passed":
            raise EnvironmentError(f"CUDA-aware numerical self-test failed: {line}")
        if fields.get("backend") != "CudaAware":
            raise EnvironmentError(f"probe fell back from CudaAware: {line}")
        uuids.add(fields.get("cuda_uuid", ""))
    if len(uuids) != ranks or "" in uuids:
        raise EnvironmentError("probe did not bind one unique CUDA UUID per rank")
    marker = "DMGMD_MPI_ENVIRONMENT status=passed stack=OpenMPI+UCX backend=CudaAware"
    if lines.count(marker) != 1:
        raise EnvironmentError("probe completion marker is missing")

    mpi_summary = next(line for line in mpi_version.splitlines() if line.strip())
    ucx_summaries = [
        line.removeprefix("# Library version: ")
        for line in ucx_version.splitlines()
        if line.startswith("# Library version:")
    ]
    if not ucx_summaries:
        raise EnvironmentError("ucx_info did not report a library version")
    ucx_summary = ucx_summaries[0]
    print(
        f"PASS environment: {mpi_summary}; UCX {ucx_summary}; "
        f"cuda_copy/cuda_ipc; CudaAware probe ranks={ranks}"
    )
    return mpiexec


def _comma_list(text: str) -> List[str]:
    return [value.strip() for value in text.split(",") if value.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=PROJECT_ROOT / "build" / "dmg-md")
    parser.add_argument("--mpiexec", type=Path, default=Path("mpiexec"))
    parser.add_argument("--devices", required=True, type=_comma_list)
    parser.add_argument("--ranks", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    validate_environment(args.candidate, args.mpiexec, args.devices, args.ranks, args.timeout)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EnvironmentError, OSError) as error:
        print(f"FAIL environment: {error}", file=sys.stderr)
        raise SystemExit(1)
