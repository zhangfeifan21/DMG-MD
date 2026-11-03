#!/usr/bin/env python3
"""Build a pristine GPUMD binary and a narrowly patched harmonic test adapter."""

from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile


REFERENCE_COMMIT = "9d23496e41319b9e2af5221a7df6285387401d1e"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REFERENCE = ROOT.parent / "gpumd-reference"
DEFAULT_PREFIX = Path("/tmp/dmgmd-reference-controls-20261103")
REFERENCE = DEFAULT_REFERENCE
PREFIX = DEFAULT_PREFIX
SOURCE = PREFIX / "gpumd-native-build-src"
BUILD = PREFIX / "gpumd-native-build"
EVIDENCE = PREFIX / "gpumd-harmonic-native-build"
CUDA_ARCH = "89"
JOBS = 16


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_record(record: dict) -> None:
    (EVIDENCE / "build-record.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def command(
    record: dict,
    name: str,
    argv: list[str],
    cwd: Path,
    *,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    stdout_path = EVIDENCE / f"{name}.stdout.log"
    stderr_path = EVIDENCE / f"{name}.stderr.log"
    completed = subprocess.run(
        argv, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(completed.stderr, encoding="utf-8", errors="replace")
    record["commands"].append(
        {
            "name": name,
            "argv": argv,
            "cwd": str(cwd),
            "exit_status": completed.returncode,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }
    )
    write_record(record)
    if completed.returncode != 0 and not allow_failure:
        raise RuntimeError(f"{name} failed with exit status {completed.returncode}")
    return completed


def git_text(argv: list[str]) -> str:
    completed = subprocess.run(
        ["git", "-C", str(REFERENCE), *argv],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def positive_int(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one patch anchor for {label}, found {count}")
    return text.replace(old, new, 1)


def apply_test_adapter(record: dict) -> None:
    paths = {
        "src/force/force.cu": SOURCE / "src/force/force.cu",
        "src/integrate/ensemble_bdp.cu": SOURCE / "src/integrate/ensemble_bdp.cu",
        "src/main_gpumd/run.cu": SOURCE / "src/main_gpumd/run.cu",
    }
    originals = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    changed = dict(originals)

    force = changed["src/force/force.cu"]
    force = replace_once(
        force,
        "#include <cstring>\n",
        "#include <cstring>\n#include <cstdlib>\n",
        "force.cu cstdlib include",
    )
    anchor = "if (is_fcp) {"
    if force.count(anchor) != 2:
        raise RuntimeError(f"expected the two FCP net-force correction guards, got {force.count(anchor)}")
    force = force.replace(
        anchor,
        'if (is_fcp && std::getenv("GPUMD_TEST_EXTERNAL_FCP") == nullptr) {',
    )
    changed["src/force/force.cu"] = force

    bdp = changed["src/integrate/ensemble_bdp.cu"]
    bdp = replace_once(
        bdp,
        "#include <chrono>\n#include <cstring>\n",
        "#include <chrono>\n#include <cstring>\n#include <cerrno>\n#include <cstdint>\n"
        "#include <cstdlib>\n#include <limits>\n#include <stdexcept>\n",
        "ensemble_bdp.cu seed includes",
    )
    bdp = replace_once(
        bdp,
        "void Ensemble_BDP::initialize_rng()\n{\n#ifdef DEBUG\n",
        "void Ensemble_BDP::initialize_rng()\n{\n"
        "  const char* test_seed = std::getenv(\"GPUMD_TEST_SEED\");\n"
        "  if (test_seed != nullptr) {\n"
        "    char* end = nullptr;\n"
        "    errno = 0;\n"
        "    const unsigned long seed = std::strtoul(test_seed, &end, 10);\n"
        "    if (test_seed == end || *end != '\\0' || errno == ERANGE ||\n"
        "        seed > std::numeric_limits<std::uint32_t>::max()) {\n"
        "      throw std::invalid_argument(\"GPUMD_TEST_SEED must be a uint32 decimal integer\");\n"
        "    }\n"
        "    rng = std::mt19937(static_cast<std::uint32_t>(seed));\n"
        "    return;\n"
        "  }\n"
        "#ifdef DEBUG\n",
        "ensemble_bdp.cu seeded RNG hook",
    )
    changed["src/integrate/ensemble_bdp.cu"] = bdp

    run = changed["src/main_gpumd/run.cu"]
    run = replace_once(
        run,
        "#include <chrono>\n#include <cstring>\n",
        "#include <chrono>\n#include <cstring>\n#include <cstdio>\n#include <cstdlib>\n"
        "#include <cstddef>\n#include <vector>\n",
        "run.cu observer includes",
    )
    run = replace_once(
        run,
        "  measure.initialize(number_of_steps, time_step, integrate, group, atom, box, force);\n\n",
        "  measure.initialize(number_of_steps, time_step, integrate, group, atom, box, force);\n\n"
        "  FILE* harmonic_samples = nullptr;\n"
        "  std::vector<double> harmonic_velocity;\n"
        "  const char* harmonic_sample_path = std::getenv(\"GPUMD_TEST_SAMPLES\");\n"
        "  if (harmonic_sample_path != nullptr) {\n"
        "    const char* harmonic_ensemble = std::getenv(\"GPUMD_TEST_ENSEMBLE\");\n"
        "    if (harmonic_ensemble == nullptr ||\n"
        "        (std::strcmp(harmonic_ensemble, \"nvt_nhc\") != 0 &&\n"
        "         std::strcmp(harmonic_ensemble, \"nvt_bdp\") != 0)) {\n"
        "      std::fprintf(stderr, \"GPUMD_TEST_ENSEMBLE must be nvt_nhc or nvt_bdp\\n\");\n"
        "      std::exit(EXIT_FAILURE);\n"
        "    }\n"
        "    harmonic_samples = std::fopen(harmonic_sample_path, \"wx\");\n"
        "    if (harmonic_samples == nullptr) {\n"
        "      std::perror(\"cannot create GPUMD_TEST_SAMPLES output\");\n"
        "      std::exit(EXIT_FAILURE);\n"
        "    }\n"
        "    std::fprintf(harmonic_samples, \"# ensemble=%s\\n\", harmonic_ensemble);\n"
        "    std::fprintf(harmonic_samples,\n"
        "                 \"step,kinetic_energy,mean_mv2_x,mean_mv2_y,mean_mv2_z\\n\");\n"
        "    harmonic_velocity.resize(3 * static_cast<std::size_t>(atom.number_of_atoms));\n"
        "  }\n"
        "\n",
        "run.cu observer initialization",
    )
    run = replace_once(
        run,
        "    integrate.compute2(time_step, double(step) / number_of_steps, group, box, atom, thermo, force);\n\n"
        "    mc.compute(step, number_of_steps, atom, box, group);\n",
        "    integrate.compute2(time_step, double(step) / number_of_steps, group, box, atom, thermo, force);\n\n"
        "    if (harmonic_samples != nullptr) {\n"
        "      const int atom_count = atom.number_of_atoms;\n"
        "      atom.velocity_per_atom.copy_to_host(harmonic_velocity.data());\n"
        "      double kinetic_energy = 0.0;\n"
        "      double axis_mv2[3] = {0.0, 0.0, 0.0};\n"
        "      for (int i = 0; i < atom_count; ++i) {\n"
        "        const double vx = harmonic_velocity[i];\n"
        "        const double vy = harmonic_velocity[atom_count + i];\n"
        "        const double vz = harmonic_velocity[2 * atom_count + i];\n"
        "        const double vx2 = vx * vx;\n"
        "        const double vy2 = vy * vy;\n"
        "        const double vz2 = vz * vz;\n"
        "        kinetic_energy += 0.5 * vx2;\n"
        "        kinetic_energy += 0.5 * vy2;\n"
        "        kinetic_energy += 0.5 * vz2;\n"
        "        axis_mv2[0] += vx2;\n"
        "        axis_mv2[1] += vy2;\n"
        "        axis_mv2[2] += vz2;\n"
        "      }\n"
        "      std::fprintf(harmonic_samples, \"%d,%.17g,%.17g,%.17g,%.17g\\n\", step,\n"
        "                   kinetic_energy, axis_mv2[0] / atom_count, axis_mv2[1] / atom_count,\n"
        "                   axis_mv2[2] / atom_count);\n"
        "    }\n\n"
        "    mc.compute(step, number_of_steps, atom, box, group);\n",
        "run.cu post-compute2 observer",
    )
    run = replace_once(
        run,
        "  print_line_1();\n  const auto time_finish = std::chrono::high_resolution_clock::now();\n",
        "  if (harmonic_samples != nullptr) {\n"
        "    const int flush_status = std::fflush(harmonic_samples);\n"
        "    const int close_status = std::fclose(harmonic_samples);\n"
        "    if (flush_status != 0 || close_status != 0) {\n"
        "      std::fprintf(stderr, \"failed to flush GPUMD_TEST_SAMPLES output\\n\");\n"
        "      std::exit(EXIT_FAILURE);\n"
        "    }\n"
        "  }\n\n"
        "  print_line_1();\n  const auto time_finish = std::chrono::high_resolution_clock::now();\n",
        "run.cu observer close",
    )
    changed["src/main_gpumd/run.cu"] = run

    patch_parts: list[str] = []
    source_hashes = {}
    for relative, path in paths.items():
        before = originals[relative]
        after = changed[relative]
        path.write_text(after, encoding="utf-8")
        source_hashes[relative] = {
            "original_sha256": sha256_bytes(before.encode("utf-8")),
            "adapter_sha256": sha256_bytes(after.encode("utf-8")),
        }
        patch_parts.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
    patch_bytes = "".join(patch_parts).encode("utf-8")
    patch_path = EVIDENCE / "gpumd-harmonic-test-adapter.patch"
    patch_path.write_bytes(patch_bytes)
    record["adapter_patch"] = {
        "path": str(patch_path),
        "sha256": sha256_bytes(patch_bytes),
        "source_hashes": source_hashes,
        "allowed_changes": [
            "force.cu: bypass only both FCP mean-force correction blocks when GPUMD_TEST_EXTERNAL_FCP is set",
            "ensemble_bdp.cu: optional uint32 GPUMD_TEST_SEED override in initialize_rng, otherwise original code path",
            "main_gpumd/run.cu: optional post-compute2 velocity observer to GPUMD_TEST_SAMPLES; metadata uses GPUMD_TEST_ENSEMBLE",
        ],
    }
    write_record(record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_PREFIX)
    parser.add_argument("--reference-root", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--cuda-arch", default="89")
    parser.add_argument("--jobs", type=positive_int, default=16)
    args = parser.parse_args()

    global REFERENCE, PREFIX, SOURCE, BUILD, EVIDENCE, CUDA_ARCH, JOBS
    PREFIX = args.work_root.expanduser().resolve()
    REFERENCE = args.reference_root.expanduser().resolve(strict=True)
    SOURCE = PREFIX / "gpumd-native-build-src"
    BUILD = PREFIX / "gpumd-native-build"
    EVIDENCE = PREFIX / "gpumd-harmonic-native-build"
    CUDA_ARCH = args.cuda_arch
    JOBS = args.jobs

    if SOURCE.exists() or BUILD.exists() or EVIDENCE.exists():
        raise SystemExit(
            "refusing to overwrite existing GPUMD test build paths: "
            + ", ".join(str(path) for path in (SOURCE, BUILD, EVIDENCE) if path.exists())
        )
    if not REFERENCE.is_dir():
        raise SystemExit(f"missing read-only GPUMD reference checkout: {REFERENCE}")

    EVIDENCE.mkdir(parents=True, exist_ok=False)
    record = {
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "INCOMPLETE",
        "reference": {
            "path": str(REFERENCE),
            "expected_commit": REFERENCE_COMMIT,
            "head_before": git_text(["rev-parse", "HEAD"]),
            "status_before": git_text(["status", "--porcelain=v1", "--untracked-files=all"]),
        },
        "paths": {"source": str(SOURCE), "build": str(BUILD), "evidence": str(EVIDENCE)},
        "cuda_architectures": CUDA_ARCH,
        "jobs": JOBS,
        "invocation": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
        "commands": [],
    }
    write_record(record)
    if record["reference"]["head_before"] != REFERENCE_COMMIT:
        record["status"] = "FAILED_WRONG_REFERENCE_COMMIT"
        write_record(record)
        raise SystemExit(
            f"reference HEAD is {record['reference']['head_before']}, expected {REFERENCE_COMMIT}"
        )

    try:
        for tool, argv in {
            "git": ["git", "--version"],
            "cmake": ["cmake", "--version"],
            "nvcc": ["nvcc", "--version"],
            "cxx": ["c++", "--version"],
            "python": [sys.executable, "--version"],
            "nvidia_smi": ["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
        }.items():
            command(record, f"tool-{tool}", argv, ROOT, allow_failure=(tool == "nvidia_smi"))
        record["platform"] = platform.platform()
        record["environment"] = {
            key: os.environ.get(key)
            for key in ("CUDA_HOME", "CUDA_PATH", "CUDACXX", "CXX", "PATH")
        }
        write_record(record)

        SOURCE.mkdir(parents=True, exist_ok=False)
        archive_path = EVIDENCE / "gpumd-reference-head.tar"
        with archive_path.open("wb") as archive_stream:
            completed = subprocess.run(
                ["git", "-C", str(REFERENCE), "archive", "--format=tar", REFERENCE_COMMIT],
                stdout=archive_stream,
                stderr=subprocess.PIPE,
                check=False,
            )
        (EVIDENCE / "git-archive.stderr.log").write_text(
            completed.stderr.decode("utf-8", errors="replace"), encoding="utf-8"
        )
        record["commands"].append(
            {
                "name": "git-archive-reference",
                "argv": ["git", "-C", str(REFERENCE), "archive", "--format=tar", REFERENCE_COMMIT],
                "cwd": str(REFERENCE),
                "exit_status": completed.returncode,
                "archive": str(archive_path),
                "archive_sha256": sha256_file(archive_path),
                "stderr": str(EVIDENCE / "git-archive.stderr.log"),
            }
        )
        write_record(record)
        if completed.returncode != 0:
            raise RuntimeError("git archive failed")
        with tarfile.open(archive_path, "r:") as archive:
            archive.extractall(SOURCE)
        record["source_tree"] = {
            "cmake_lists_sha256": sha256_file(SOURCE / "CMakeLists.txt"),
            "force_sha256": sha256_file(SOURCE / "src/force/force.cu"),
            "bdp_sha256": sha256_file(SOURCE / "src/integrate/ensemble_bdp.cu"),
            "run_sha256": sha256_file(SOURCE / "src/main_gpumd/run.cu"),
        }
        write_record(record)

        BUILD.mkdir(parents=True, exist_ok=False)
        command(
            record,
            f"cmake-configure-release-cuda{CUDA_ARCH}",
            [
                "cmake",
                "-S",
                str(SOURCE),
                "-B",
                str(BUILD),
                "-DCMAKE_BUILD_TYPE=Release",
                f"-DCMAKE_CUDA_ARCHITECTURES={CUDA_ARCH}",
            ],
            ROOT,
        )
        command(record, f"build-pristine-gpumd-j{JOBS}", ["cmake", "--build", str(BUILD), "--target", "gpumd", f"-j{JOBS}"], ROOT)
        native_binary = BUILD / "gpumd"
        if not native_binary.is_file():
            raise RuntimeError(f"native GPUMD executable not found at {native_binary}")
        native_copy = EVIDENCE / "gpumd-native-original"
        shutil.copy2(native_binary, native_copy)
        record["pristine_binary"] = {
            "path": str(native_copy),
            "sha256": sha256_file(native_copy),
            "build_binary_sha256": sha256_file(native_binary),
        }
        write_record(record)

        apply_test_adapter(record)
        command(record, f"build-harmonic-test-adapter-j{JOBS}", ["cmake", "--build", str(BUILD), "--target", "gpumd", f"-j{JOBS}"], ROOT)
        adapter_binary = EVIDENCE / "gpumd-harmonic-test-adapter"
        shutil.copy2(native_binary, adapter_binary)
        record["adapter_binary"] = {
            "path": str(adapter_binary),
            "sha256": sha256_file(adapter_binary),
            "build_binary_sha256": sha256_file(native_binary),
        }

        record["reference"]["head_after"] = git_text(["rev-parse", "HEAD"])
        record["reference"]["status_after"] = git_text(["status", "--porcelain=v1", "--untracked-files=all"])
        if record["reference"]["head_after"] != REFERENCE_COMMIT:
            raise RuntimeError("read-only GPUMD reference HEAD changed during build")
        if record["reference"]["status_after"] != record["reference"]["status_before"]:
            raise RuntimeError("read-only GPUMD reference status changed during build")
        record["status"] = "PASS"
        record["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_record(record)
        print(json.dumps({"status": record["status"], "pristine_binary": record["pristine_binary"], "adapter_binary": record["adapter_binary"], "evidence": str(EVIDENCE / 'build-record.json')}, indent=2))
        return 0
    except Exception as error:
        record["status"] = "FAILED"
        record["error"] = f"{type(error).__name__}: {error}"
        record["failed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            record["reference"]["head_after"] = git_text(["rev-parse", "HEAD"])
            record["reference"]["status_after"] = git_text(["status", "--porcelain=v1", "--untracked-files=all"])
        except Exception as ref_error:
            record["reference"]["final_state_error"] = str(ref_error)
        write_record(record)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
