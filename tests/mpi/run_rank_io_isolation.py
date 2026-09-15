#!/usr/bin/env python3
"""Verify the per-rank node-local scratch I/O isolation contract.

Complements tests/mpi/run_mpi_differential.py: instead of numerics, this gate
exercises the per-rank node-local scratch I/O isolation contract in
docs/standards/replicated-mpi.md ("I/O 与 NEP_MULTIGPU"; decision record:
docs/plans/multi-node-io.md) against the real executable under mpiexec:

* every non-root rank works inside its OWN node-local scratch directory -
  each rank gets a distinct TMPDIR root through a bash wrapper, which is the
  single-node stand-in for node-local (and on two nodes, mutually invisible)
  temp directories;
* a successful job leaves the job directory with exactly the manifest output
  set (only rank 0 writes it) and no ``dmgmd-rank-io-*`` leftovers anywhere;
* an injected single-rank failure (mkdir / neighbor.out file / chdir / restore
  / cleanup, selected via DMGMD_RANK_IO_FAULT on exactly one rank) exits the
  whole job bounded - the timeout is the no-hang gate - with world rank,
  hostname, target path and reason in the error record;
* with at least three ranks, a setup failure is paired with a different rank's
  injected setup-restore failure, proving that scratch is retained while that
  process may still have it as cwd and is discoverable by a node-local probe;
* a restore failure fails the job even though all MD outputs are complete,
  while a cleanup failure only warns, keeps the exact directory for
  diagnosis, and the retained directory proves the owner-only (0700) plain
  scratch layout including a regular (non-symlink) neighbor.out.

The strict "rank 0's temp root does not exist on the second node" regression
needs two physical nodes. Repeat ``--mpiexec-arg`` for site-specific host and
mapping options. Every normally returning rank reports its node-local scratch
state before the wrapper removes test residue, so remote-node leaks do not
depend on the launcher's filesystem being visible to the test driver.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = PROJECT_ROOT / "tests" / "baseline"
sys.path.insert(0, str(BASELINE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_baselines as baseline  # noqa: E402
import check_environment as mpi_environment  # noqa: E402


# DMGMD_RANK_IO_FAULT must reach exactly one non-root rank; the bash wrapper
# below installs it per rank, so a stale outer value must never be inherited.
FAULT_ENVIRONMENT = "DMGMD_RANK_IO_FAULT"
LOCAL_STATE_PATTERN = re.compile(
    r"^DMGMD_RANK_IO_TEST rank=(\d+) scratch_leftovers=(\d+) "
    r"scratch_mode=([^ ]+) neighbor_regular=([^ ]+)$"
)
PROBE_PATTERN = re.compile(
    r"^DMGMD_RANK_IO_PROBE rank=(\d+) hostname=([^ ]+) scratch_leftovers=(\d+)$"
)


def comma_list(text: str) -> List[str]:
    values = [value.strip() for value in text.split(",") if value.strip()]
    if not values:
        raise argparse.ArgumentTypeError("list must not be empty")
    return values


def require(condition: bool, message: str) -> None:
    if not condition:
        raise baseline.BaselineError(message)


def scratch_leftovers(tmpdir_root: Path) -> List[Path]:
    return sorted(
        path
        for path in tmpdir_root.rglob("dmgmd-rank-io-*")
        if path.is_dir() or path.is_symlink()
    )


def stage_inputs(case_name: str, stage_dir: Path, manifest: Dict) -> Dict:
    case = manifest["cases"][case_name]
    stage = case["stages"][0]
    stage_dir.mkdir(parents=True)
    shutil.copyfile(BASELINE_DIR / stage["model"], stage_dir / "model.xyz")
    shutil.copyfile(BASELINE_DIR / stage["run"], stage_dir / "run.in")
    shutil.copyfile(BASELINE_DIR / case["potential"], stage_dir / "nep.txt")
    return stage


def check_job_directory(stage_dir: Path, stage: Dict, expect_outputs: bool) -> None:
    """The job directory must contain exactly the staged inputs plus, for a
    completed job, rank 0's compatible outputs - nothing from non-root ranks."""
    staged = {"model.xyz", "run.in", "nep.txt"}
    bookkeeping = {"execution.stdout", "execution.stderr"}
    actual = {path.name for path in stage_dir.iterdir()} - staged - bookkeeping
    expected = set(stage["outputs"]) if expect_outputs else set()
    require(
        actual == expected,
        f"{stage_dir}: job directory files are {sorted(actual)}, "
        f"expected {sorted(expected)}; non-root ranks must not write here",
    )
    if expect_outputs:
        for filename, spec in stage["outputs"].items():
            baseline.validate_output_shape(stage_dir / filename, spec)


def run_isolated_job(
    mpiexec: Path,
    mpiexec_args: Sequence[str],
    executable: Path,
    stage_dir: Path,
    ranks: int,
    tmpdir_root: Path,
    env: Dict[str, str],
    timeout: int,
    faults: Sequence[Tuple[int, str]],
) -> subprocess.CompletedProcess:
    """Runs one job where every rank resolves its own TMPDIR (a distinct
    per-rank directory), optionally with DMGMD_RANK_IO_FAULT enabled on one
    rank. The wrapper is also what makes this script usable unchanged on a
    two-node host allocation: each rank only ever sees its local temp root."""
    fault_setup = f"unset {FAULT_ENVIRONMENT}; "
    for fault_rank, fault_operation in faults:
        fault_setup += (
            f'if [ "$OMPI_COMM_WORLD_RANK" = "{fault_rank}" ]; then '
            f'export {FAULT_ENVIRONMENT}="{fault_rank}:{fault_operation}"; fi; '
        )
    tmpdir_base = shlex.quote(str(tmpdir_root))
    executable_command = shlex.quote(str(executable))
    wrapper = (
        f'export TMPDIR={tmpdir_base}/rank-$OMPI_COMM_WORLD_RANK; '
        f'mkdir -p "$TMPDIR" || exit 125; '
        f"{fault_setup}"
        f"{executable_command}; app_status=$?; "
        "scratch_count=0; scratch_mode=none; neighbor_regular=na; "
        "for candidate in \"$TMPDIR\"/dmgmd-rank-io-*; do "
        "  if [ ! -d \"$candidate\" ] && [ ! -L \"$candidate\" ]; then continue; fi; "
        "  scratch_count=$((scratch_count + 1)); "
        "  if [ \"$scratch_count\" -eq 1 ]; then "
        "    scratch_mode=$(stat -c '%a' -- \"$candidate\" 2>/dev/null || echo error); "
        "    if [ -f \"$candidate/neighbor.out\" ] && "
        "       [ ! -L \"$candidate/neighbor.out\" ]; then "
        "      neighbor_regular=1; "
        "    else neighbor_regular=0; fi; "
        "  fi; "
        "done; "
        "printf 'DMGMD_RANK_IO_TEST rank=%s scratch_leftovers=%s "
        "scratch_mode=%s neighbor_regular=%s\\n' "
        "  \"$OMPI_COMM_WORLD_RANK\" \"$scratch_count\" \"$scratch_mode\" "
        "  \"$neighbor_regular\"; "
        "if [ \"$app_status\" -eq 0 ]; then "
        "  for candidate in \"$TMPDIR\"/dmgmd-rank-io-*; do "
        "    if [ -d \"$candidate\" ] || [ -L \"$candidate\" ]; then "
        "      rm -rf -- \"$candidate\"; "
        "    fi; "
        "  done; "
        "fi; "
        "exit \"$app_status\""
    )
    command = [str(mpiexec), *mpiexec_args, "-n", str(ranks), "bash", "-c", wrapper]
    try:
        result = subprocess.run(
            command,
            cwd=stage_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise baseline.BaselineError(
            f"{stage_dir.name}: job did not terminate within {timeout}s; "
            "a rank I/O isolation failure must exit bounded, not hang"
        ) from error
    (stage_dir / "execution.stdout").write_text(result.stdout, encoding="utf-8")
    (stage_dir / "execution.stderr").write_text(result.stderr, encoding="utf-8")
    return result


def local_scratch_states(stdout: str) -> Dict[int, Tuple[int, str, str]]:
    states: Dict[int, Tuple[int, str, str]] = {}
    for line in stdout.splitlines():
        match = LOCAL_STATE_PATTERN.match(line)
        if match is None:
            continue
        rank = int(match.group(1))
        require(rank not in states, f"duplicate local scratch state for rank {rank}")
        states[rank] = (int(match.group(2)), match.group(3), match.group(4))
    return states


def require_local_scratch_states(
    result: subprocess.CompletedProcess,
    ranks: int,
    expected_leftovers: Dict[int, int],
) -> Dict[int, Tuple[int, str, str]]:
    states = local_scratch_states(result.stdout)
    require(
        set(states) == set(range(ranks)),
        f"expected node-local scratch reports from ranks 0..{ranks - 1}, got {states}",
    )
    for rank in range(ranks):
        expected = expected_leftovers.get(rank, 0)
        require(
            states[rank][0] == expected,
            f"rank {rank} reported {states[rank][0]} scratch leftovers, expected {expected}",
        )
    return states


def probe_node_local_scratch(
    mpiexec: Path,
    mpiexec_args: Sequence[str],
    ranks: int,
    tmpdir_root: Path,
    env: Dict[str, str],
    timeout: int,
) -> Dict[int, int]:
    """Scan every allocated node after a job, then remove exact test residue.

    Each probe rank scans all rank-* roots at the same node-local path, so a
    failed MPI job cannot hide remote scratch merely because the Python driver
    only sees the launch node's filesystem. Duplicate scans on one node are
    harmless; each world rank still emits one independently checkable record.
    """
    base = shlex.quote(str(tmpdir_root))
    wrapper = (
        f"base={base}; count=0; "
        "for candidate in \"$base\"/rank-*/dmgmd-rank-io-*; do "
        "  if [ -d \"$candidate\" ] || [ -L \"$candidate\" ]; then "
        "    count=$((count + 1)); "
        "  fi; "
        "done; "
        "printf 'DMGMD_RANK_IO_PROBE rank=%s hostname=%s scratch_leftovers=%s\\n' "
        "  \"$OMPI_COMM_WORLD_RANK\" \"$(hostname)\" \"$count\"; "
        "for candidate in \"$base\"/rank-*/dmgmd-rank-io-*; do "
        "  if [ -d \"$candidate\" ] || [ -L \"$candidate\" ]; then "
        "    rm -rf -- \"$candidate\"; "
        "  fi; "
        "done; "
        "for rank_root in \"$base\"/rank-*; do "
        "  if [ -d \"$rank_root\" ] && [ ! -L \"$rank_root\" ]; then "
        "    rmdir -- \"$rank_root\" 2>/dev/null || true; "
        "  fi; "
        "done; "
        "if [ -d \"$base\" ] && [ ! -L \"$base\" ]; then "
        "  rmdir -- \"$base\" 2>/dev/null || true; "
        "fi"
    )
    command = [str(mpiexec), *mpiexec_args, "-n", str(ranks), "bash", "-c", wrapper]
    try:
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise baseline.BaselineError(
            f"node-local scratch probe timed out after {timeout}s"
        ) from error
    require(
        result.returncode == 0,
        f"node-local scratch probe failed:\nstdout:\n{result.stdout}"
        f"\nstderr:\n{result.stderr}",
    )
    counts: Dict[int, int] = {}
    for line in result.stdout.splitlines():
        match = PROBE_PATTERN.match(line)
        if match is None:
            continue
        rank = int(match.group(1))
        require(rank not in counts, f"duplicate node-local probe record for rank {rank}")
        counts[rank] = int(match.group(3))
    require(
        set(counts) == set(range(ranks)),
        f"expected node-local probe records from ranks 0..{ranks - 1}, got {counts}",
    )
    return counts


def expect_bounded_failure(
    result: subprocess.CompletedProcess,
    stage_dir: Path,
    phase: str,
    fault_rank: int,
) -> None:
    require(
        result.returncode != 0,
        f"{stage_dir.name}: injected {phase} failure must fail the job, "
        f"got exit 0\nstdout:\n{result.stdout}",
    )
    stderr = result.stderr
    require(
        f"DMGMD_ERROR rank={fault_rank}" in stderr,
        f"{stage_dir.name}: error record must name world rank {fault_rank}; "
        f"stderr:\n{stderr}",
    )
    require(
        "hostname=" in stderr,
        f"{stage_dir.name}: error record must name the hostname; stderr:\n{stderr}",
    )
    require(
        f"rank I/O isolation {phase} failed" in stderr,
        f"{stage_dir.name}: aggregated message must identify phase {phase}; "
        f"stderr:\n{stderr}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=PROJECT_ROOT / "build" / "dmg-md")
    parser.add_argument("--mpiexec", type=Path, default=Path("mpiexec"))
    parser.add_argument("--ranks", type=int, default=2)
    parser.add_argument("--case", default="single_small_static")
    parser.add_argument(
        "--devices", type=str,
        help="comma-separated CUDA device IDs/UUIDs; defaults to CUDA_VISIBLE_DEVICES",
    )
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--mpiexec-arg",
        action="append",
        default=[],
        help="additional launcher argument; repeat as needed (use --mpiexec-arg=--host)",
    )
    parser.add_argument("--keep-work", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executable = args.candidate.resolve()
    require(
        executable.is_file() and os.access(executable, os.X_OK),
        f"candidate is not executable: {executable}",
    )
    if args.ranks < 2:
        raise baseline.BaselineError("rank I/O isolation needs at least 2 ranks")
    fault_rank = 1

    devices_text = args.devices or os.environ.get("CUDA_VISIBLE_DEVICES", "")
    devices = comma_list(devices_text) if devices_text else []
    require(
        len(devices) >= args.ranks,
        f"need at least {args.ranks} visible device IDs; got {devices}",
    )
    manifest = baseline.load_manifest()
    baseline.validate_input_hashes(manifest)
    require(args.case in manifest["cases"], f"unknown baseline case {args.case}")

    # Same preflight gate as the numerical differential: it separates an
    # Open MPI/UCX/CUDA stack failure from an isolation-contract failure.
    try:
        mpiexec = mpi_environment.validate_environment(
            executable,
            args.mpiexec,
            devices,
            args.ranks,
            args.timeout,
            args.mpiexec_arg,
        )
    except mpi_environment.EnvironmentError as error:
        raise baseline.BaselineError(str(error)) from error

    env = os.environ.copy()
    env.pop(FAULT_ENVIRONMENT, None)
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = ",".join(devices[: args.ranks])
    env["DMGMD_COMM_BACKEND"] = "HostStaged"
    env["DMGMD_COMM_LOG_INTERVAL"] = "1"

    work_root = Path(tempfile.mkdtemp(prefix="dmgmd-rank-io-isolation-"))
    tmpdir_root = work_root / "rank-tmpdirs"
    succeeded = False
    try:
        # 1. Healthy multi-rank job with per-rank (node-local stand-in) TMPDIR
        #    roots: only rank 0 writes the job directory and no scratch leaks.
        stage_dir = work_root / "healthy"
        stage = stage_inputs(args.case, stage_dir, manifest)
        result = run_isolated_job(
            mpiexec,
            args.mpiexec_arg,
            executable,
            stage_dir,
            args.ranks,
            tmpdir_root,
            env,
            args.timeout,
            (),
        )
        require(
            result.returncode == 0,
            f"healthy job failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        check_job_directory(stage_dir, stage, expect_outputs=True)
        require_local_scratch_states(result, args.ranks, {})
        require(
            not scratch_leftovers(tmpdir_root),
            f"successful job leaked scratch directories: {scratch_leftovers(tmpdir_root)}",
        )
        remote_counts = probe_node_local_scratch(
            mpiexec, args.mpiexec_arg, args.ranks, tmpdir_root, env, args.timeout
        )
        require(
            not any(remote_counts.values()),
            f"successful job leaked node-local scratch: {remote_counts}",
        )
        print(f"PASS healthy ranks={args.ranks}: distinct per-rank TMPDIR, no scratch leaks")

        # 2. Setup failures on one rank: bounded collective exit with a full
        #    diagnostic, no scratch residue, and no partial outputs written.
        for operation in ("mkdir", "file", "chdir"):
            scenario = work_root / f"setup-fault-{operation}"
            fault_stage = stage_inputs(args.case, scenario, manifest)
            result = run_isolated_job(
                mpiexec,
                args.mpiexec_arg,
                executable,
                scenario,
                args.ranks,
                tmpdir_root,
                env,
                args.timeout,
                ((fault_rank, operation),),
            )
            expect_bounded_failure(result, scenario, "setup", fault_rank)
            require(
                "injected" in result.stderr,
                f"{scenario.name}: diagnostic must name the injected operation; "
                f"stderr:\n{result.stderr}",
            )
            check_job_directory(scenario, fault_stage, expect_outputs=False)
            require(
                not scratch_leftovers(tmpdir_root),
                f"failed setup left scratch directories: {scratch_leftovers(tmpdir_root)}",
            )
            remote_counts = probe_node_local_scratch(
                mpiexec, args.mpiexec_arg, args.ranks, tmpdir_root, env, args.timeout
            )
            require(
                not any(remote_counts.values()),
                f"failed setup left node-local scratch: {remote_counts}",
            )
            print(f"PASS setup fault {operation:6s} rank={fault_rank}: bounded, diagnosable exit")

        # A second non-root rank can fail to restore cwd while the world is
        # unwinding another rank's setup failure. The runtime must keep that
        # scratch directory (deleting the process cwd is unsafe), and the
        # follow-up node-local probe must be able to find and remove it.
        if args.ranks >= 3:
            restore_rank = 2
            scenario = work_root / "setup-restore-fault"
            fault_stage = stage_inputs(args.case, scenario, manifest)
            result = run_isolated_job(
                mpiexec,
                args.mpiexec_arg,
                executable,
                scenario,
                args.ranks,
                tmpdir_root,
                env,
                args.timeout,
                ((fault_rank, "file"), (restore_rank, "setup_restore")),
            )
            expect_bounded_failure(result, scenario, "setup", fault_rank)
            require(
                f"rank={restore_rank} " in result.stderr
                and "restore cwd" in result.stderr
                and "cleanup skipped" in result.stderr,
                f"{scenario.name}: diagnostic must show the failed cwd restore and "
                f"skipped cleanup for rank {restore_rank}; stderr:\n{result.stderr}",
            )
            check_job_directory(scenario, fault_stage, expect_outputs=False)
            remote_counts = probe_node_local_scratch(
                mpiexec, args.mpiexec_arg, args.ranks, tmpdir_root, env, args.timeout
            )
            require(
                any(remote_counts.values()),
                "setup-restore failure must retain scratch on at least one allocated node",
            )
            require(
                not scratch_leftovers(tmpdir_root),
                "node-local probe failed to remove setup-restore test residue",
            )
            print(
                f"PASS setup restore fault rank={restore_rank}: unsafe deletion skipped, "
                "node-local residue observed"
            )

        # 3. Restore failure: all MD outputs are already complete, but the job
        #    must still be judged failed. Residue is tolerated here only
        #    because MPI_Abort can race the failing rank's local destructor.
        scenario = work_root / "restore-fault"
        fault_stage = stage_inputs(args.case, scenario, manifest)
        result = run_isolated_job(
            mpiexec,
            args.mpiexec_arg,
            executable,
            scenario,
            args.ranks,
            tmpdir_root,
            env,
            args.timeout,
            ((fault_rank, "restore"),),
        )
        expect_bounded_failure(result, scenario, "restore", fault_rank)
        check_job_directory(scenario, fault_stage, expect_outputs=True)
        # MPI_Abort can prevent the wrapper from reporting or cleaning. Probe
        # every allocated node and remove any exact test residue; restore
        # failure is allowed to retain its private scratch for diagnosis.
        probe_node_local_scratch(
            mpiexec, args.mpiexec_arg, args.ranks, tmpdir_root, env, args.timeout
        )
        print(f"PASS restore fault  rank={fault_rank}: complete outputs but job failed")

        # 4. Cleanup failure: warning only, job succeeds, and the exact
        #    directory is kept for diagnosis with owner-only permissions.
        scenario = work_root / "cleanup-fault"
        fault_stage = stage_inputs(args.case, scenario, manifest)
        result = run_isolated_job(
            mpiexec,
            args.mpiexec_arg,
            executable,
            scenario,
            args.ranks,
            tmpdir_root,
            env,
            args.timeout,
            ((fault_rank, "cleanup"),),
        )
        require(
            result.returncode == 0,
            f"cleanup failure must not fail the job:\nstdout:\n{result.stdout}"
            f"\nstderr:\n{result.stderr}",
        )
        check_job_directory(scenario, fault_stage, expect_outputs=True)
        warnings = [
            line
            for line in result.stdout.splitlines()
            if line.startswith("DMGMD_RANK_IO_CLEANUP status=warning")
        ]
        require(
            len(warnings) == 1 and f"rank={fault_rank} " in warnings[0],
            f"expected one cleanup warning naming rank {fault_rank}, got: {warnings}",
        )
        states = require_local_scratch_states(
            result, args.ranks, {fault_rank: 1}
        )
        require(
            states[fault_rank][1:] == ("700", "1"),
            f"retained scratch must be mode 0700 with a regular neighbor.out, got "
            f"{states[fault_rank]}",
        )
        require(
            not scratch_leftovers(tmpdir_root),
            "test wrapper failed to remove local cleanup-fault residue",
        )
        remote_counts = probe_node_local_scratch(
            mpiexec, args.mpiexec_arg, args.ranks, tmpdir_root, env, args.timeout
        )
        require(
            not any(remote_counts.values()),
            f"test wrapper left node-local cleanup-fault residue: {remote_counts}",
        )
        print(
            f"PASS cleanup fault  rank={fault_rank}: warning only, retained 0700 "
            "scratch observed and test residue removed"
        )

        succeeded = True
        print(
            f"PASS: rank I/O isolation contract ranks={args.ranks} case={args.case}"
        )
        return 0
    finally:
        if succeeded and not args.keep_work:
            shutil.rmtree(work_root, ignore_errors=True)
        else:
            print(f"work directory retained: {work_root}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (baseline.BaselineError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
