#!/usr/bin/env python3
"""Run the M1 spatial-ownership migration fixtures.

These fixtures are deliberately designed so that ownership *changes* during
the run (the committed baseline cases happen to keep every atom inside one
slab), exercising the migration protocol of
docs/standards/replicated-mpi.md:

- an atom crossing an interior slab boundary;
- an atom wrapping past the periodic end back to the first slab;
- an atom crossing multiple slabs in a single step;
- a rank that temporarily owns zero atoms;
- correct_velocity trigger steps, multi-segment runs, NVT Berendsen scaling;
- velocity/force/potential/virial/unwrapped_position outputs;
- dump_restart followed by a resume with a different rank count.

For every run this script verifies the machine-readable ownership records,
and it recomputes the expected owner of every atom at every step directly
from the dumped (double precision) wrapped positions, requiring the logged
owner transitions to match exactly. Dynamics equality across rank counts is
checked with the committed baseline tolerances (never relaxed), with the
1-rank run (where no migration can happen) as the reference.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = PROJECT_ROOT / "tests" / "baseline"
sys.path.insert(0, str(BASELINE_DIR))
import run_baselines as baseline  # noqa: E402
import check_environment as mpi_environment  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures. All positions stay inside the box, and every velocity is explicit
# (golden-test discipline: dynamics fixtures must not depend on the random
# initial-velocity path). The box is a 24 A cube, so the partition axis
# chosen by the runtime is y (the reference tie rule picks y for a cube).
# ---------------------------------------------------------------------------

CROSSINGS_MODEL = """8
pbc="T T T" Lattice="24 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3
C 4.0 5.5 4.0 0.0 0.8 0.0
C 6.0 23.2 6.0 0.0 0.75 0.0
C 8.0 2.0 8.0 0.0 13.0 0.0
C 10.0 8.0 10.0 0.0 0.01 0.0
C 12.0 9.0 12.0 0.0 0.0 0.0
C 14.0 10.5 14.0 0.0 0.0 0.0
C 16.0 11.5 16.0 0.0 0.2 0.0
C 18.0 8.5 18.0 0.0 -0.1 0.0
"""

# Segment 1 (20 steps) triggers correct_velocity at steps 1 and 11 and dumps
# every step with double precision so the expected owners can be recomputed
# exactly. Segment 2 is an NVT Berendsen continuation (multi-segment state);
# the restart written at step 20 feeds the cross-rank resume case below.
CROSSINGS_RUN = """potential nep.txt

time_step 1.0
ensemble nve
correct_velocity 10
dump_thermo 1
dump_xyz 1 trajectory.xyz precision double velocity force potential virial unwrapped_position
dump_restart 20
run 20

time_step 1.0
ensemble nvt_ber 300 300 1000
dump_thermo 1
dump_xyz 1 nvt.xyz precision double velocity force potential virial unwrapped_position
run 10
"""

# gid 0 starts alone in slab 1 (P=4) and leaves it after one step, so rank 1
# temporarily owns zero atoms until gid 3 (vy = +0.5) repopulates it. Slabs 2
# and 3 start empty at P=4. At P=2 rank 1 starts empty as well; forces may
# later repopulate it, which still exercises a legal zero-count epoch.
EMPTY_SLAB_MODEL = """4
pbc="T T T" Lattice="24 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3
C 5.0 6.5 5.0 0.0 -0.6 0.0
C 7.0 1.0 7.0 0.0 0.0 0.0
C 9.0 2.0 9.0 0.0 0.0 0.0
C 11.0 3.0 11.0 0.0 0.5 0.0
"""

EMPTY_SLAB_RUN = """potential nep.txt

time_step 1.0
ensemble nve
dump_thermo 1
dump_xyz 1 trajectory.xyz precision double velocity force potential virial
run 12
"""

# Explicit N < P coverage. Both atoms start in slab 0 at P=4, so three ranks
# contribute a zero count to every indexed collective. This case guards the
# M1 contract that empty ownership is legal even when there are fewer atoms
# than MPI ranks (the M0 balanced-range precondition no longer applies).
MORE_RANKS_THAN_ATOMS_MODEL = """2
pbc="T T T" Lattice="24 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3
C 4.0 2.0 4.0 0.0 0.0 0.0
C 16.0 3.0 16.0 0.0 0.0 0.0
"""

MORE_RANKS_THAN_ATOMS_RUN = """potential nep.txt

time_step 1.0
ensemble nve
dump_thermo 1
dump_xyz 1 trajectory.xyz precision double velocity force potential virial
run 3
"""

RESUME_RUN = """potential nep.txt

time_step 1.0
ensemble nve
dump_thermo 1
dump_xyz 1 resumed.xyz precision double velocity force potential virial
run 10
"""

# Inputs the M1 slab partitioner must reject at P>1 with one identical,
# diagnosable error on every rank (never a silent projection and never a
# partial collective hang). The same inputs must keep working at P=1, which
# degenerates to the M0 path and never selects a partition axis.
TRICLINIC_MODEL = """8
pbc="T T T" Lattice="12 4 0 0 12 4 4 0 12" Properties=species:S:1:pos:R:3:vel:R:3
C 2.0 2.0 2.0 0.001 0.0 0.0
C 3.5 2.0 2.0 0.0 0.001 0.0
C 2.0 3.5 2.0 0.0 0.0 0.001
C 3.5 3.5 3.5 -0.001 0.0 0.0
C 5.0 5.0 5.0 0.0 -0.001 0.0
C 6.5 5.0 5.0 0.0 0.0 -0.001
C 5.0 6.5 6.5 0.001 0.001 0.0
C 6.5 6.5 6.5 0.0 0.0 0.001
"""

NONPERIODIC_MODEL = """8
pbc="T T F" Lattice="24 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3
C 4.0 5.5 4.0 0.001 0.0 0.0
C 6.0 23.2 6.0 0.0 0.001 0.0
C 8.0 2.0 8.0 0.0 0.0 0.001
C 10.0 8.0 10.0 -0.001 0.0 0.0
C 12.0 9.0 12.0 0.0 -0.001 0.0
C 14.0 10.5 14.0 0.0 0.0 -0.001
C 16.0 11.5 16.0 0.001 0.001 0.0
C 18.0 8.5 18.0 0.0 0.0 0.001
"""

UNSUPPORTED_RUN = """potential nep.txt

time_step 0.5
ensemble nve
dump_thermo 1
dump_xyz 1 trajectory.xyz precision double velocity force potential virial
run 3
"""


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


def key_values(line: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for token in line.split()[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            result[key] = value
    return result


def expected_steps(run_text: str) -> int:
    total = 0
    for line in run_text.splitlines():
        tokens = line.split()
        if tokens and tokens[0] == "run":
            total += int(tokens[1])
    return total


def slab_owner(y: float, box_length: float, world_size: int) -> int:
    """Python twin of slab_owner_of_fractional for a wrapped y coordinate."""
    s = y / box_length
    if s < 0.0:
        s += 1.0
    elif s > 1.0:
        s -= 1.0
    if not (0.0 <= s <= 1.0):
        raise baseline.BaselineError(f"trajectory y={y!r} is outside the wrappable range")
    if world_size == 1:
        return 0
    slab = int(s * world_size)
    return min(slab, world_size - 1)


def initial_y_positions(model_text: str) -> List[float]:
    """y coordinates from an extended-XYZ atom row (species then pos:R:3)."""
    lines = [line for line in model_text.splitlines() if line.strip()]
    natoms = int(lines[0].strip())
    positions = [float(line.split()[2]) for line in lines[2:2 + natoms]]
    if len(positions) != natoms:
        raise baseline.BaselineError("fixture model did not parse into positions")
    return positions


def frame_y_positions(frame: Dict[str, Any]) -> List[float]:
    return [float(row[2]) for row in frame["rows"]]


class MigrationCase:
    """One fixture across the whole rank x backend matrix."""

    def __init__(
        self,
        name: str,
        model: str,
        run: str,
        trajectory_files: List[str],
        outputs: List[str],
        box_length: float,
        natoms: int,
        unwrapped: bool = False,
        correct_velocity_steps: Optional[Set[int]] = None,
    ) -> None:
        self.name = name
        self.model = model
        self.run = run
        self.trajectory_files = trajectory_files  # dumped every step, in order
        self.outputs = outputs  # rank 0 files compared across the matrix
        self.box_length = box_length
        self.natoms = natoms
        self.steps = expected_steps(run)
        self.unwrapped = unwrapped
        self.correct_velocity_steps = correct_velocity_steps or set()

    @property
    def expected_files(self) -> set:
        return {"model.xyz", "run.in", "nep.txt", "neighbor.out"} | set(self.outputs)


def stage_case(
    case: MigrationCase,
    stage_dir: Path,
    potential: Path,
) -> None:
    stage_dir.mkdir(parents=True)
    (stage_dir / "model.xyz").write_text(case.model, encoding="utf-8")
    (stage_dir / "run.in").write_text(case.run, encoding="utf-8")
    shutil.copyfile(potential, stage_dir / "nep.txt")


def execute(
    executable: Path,
    mpiexec: Path,
    stage_dir: Path,
    ranks: int,
    backend: str,
    devices: List[str],
    timeout: int,
) -> Dict[str, Any]:
    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = ",".join(devices[:ranks])
    env["DMGMD_COMM_BACKEND"] = backend
    env["DMGMD_COMM_LOG_INTERVAL"] = "1"
    command = [str(mpiexec), "-n", str(ranks), str(executable)]
    completed = subprocess.run(
        command,
        cwd=stage_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    (stage_dir / "execution.stdout").write_text(completed.stdout, encoding="utf-8")
    (stage_dir / "execution.stderr").write_text(completed.stderr, encoding="utf-8")
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def validate_ownership_records(
    case: MigrationCase,
    stage_dir: Path,
    ranks: int,
    backend_name: str,
    execution: Dict[str, Any],
) -> List[Dict[str, Any]]:
    label = f"{case.name}/{ranks}r/{backend_name}"
    if execution["returncode"] != 0:
        raise baseline.BaselineError(
            f"{label}: dmg-md exited with {execution['returncode']}: "
            f"{execution['stderr'][-2000:]}"
        )
    if execution["stderr"]:
        raise baseline.BaselineError(f"{label}: unexpected stderr: {execution['stderr'][-2000:]}")
    stdout = execution["stdout"]

    coverage = [line for line in stdout.splitlines() if line.startswith("DMGMD_CENTER_PARTITION ")]
    if len(coverage) != 1:
        raise baseline.BaselineError(f"{label}: missing unique center partition proof")
    fields = key_values(coverage[0])
    if fields.get("missing") != "0" or fields.get("overlapping") != "0":
        raise baseline.BaselineError(f"{label}: ownership coverage is incomplete or overlapping")
    if fields.get("owned_output_coverage") != "complete":
        raise baseline.BaselineError(f"{label}: owned output coverage is not complete")
    if fields.get("nep_kernel_centers") != "replicated-full":
        raise baseline.BaselineError(f"{label}: NEP centers must stay replicated-full in M1")
    if fields.get("nep_N1_N2_shard_complete") != "false":
        raise baseline.BaselineError(f"{label}: N1/N2 shard must stay incomplete in M1")
    if int(fields["global_count"]) != case.natoms or int(fields["ranks"]) != ranks:
        raise baseline.BaselineError(f"{label}: center proof disagrees with the fixture")
    if ranks > 1:
        if fields.get("partition") != "spatial-slab" or fields.get("axis") != "y":
            raise baseline.BaselineError(
                f"{label}: expected a y-axis spatial slab partition for a cube"
            )
    elif "partition" in fields:
        raise baseline.BaselineError(f"{label}: P=1 must not report a spatial partition")

    # These fixtures are 24 A boxes: the slab-width guard keeps them on the
    # M1 replicated-full runtime (M2a coverage lives in run_mpi_domain.py).
    domain = [line for line in stdout.splitlines() if line.startswith("DMGMD_DOMAIN ")]
    if len(domain) != 1:
        raise baseline.BaselineError(f"{label}: missing unique DMGMD_DOMAIN record")
    domain_fields = key_values(domain[0])
    if domain_fields.get("mode") != "m1-fallback":
        raise baseline.BaselineError(
            f"{label}: 24 A fixture must stay on the M1 fallback, got "
            f"mode={domain_fields.get('mode')!r}"
        )

    ownership = [line for line in stdout.splitlines() if line.startswith("DMGMD_CENTER_OWNERSHIP ")]
    if len(ownership) != ranks:
        raise baseline.BaselineError(f"{label}: ownership record count mismatch")
    owned_total = 0
    owned_counts: List[int] = []
    for rank, line in enumerate(ownership):
        item = key_values(line)
        if int(item.get("rank", -1)) != rank:
            raise baseline.BaselineError(f"{label}: malformed ownership record: {line}")
        owned_count = int(item.get("owned_count", -1))
        if owned_count < 0:
            raise baseline.BaselineError(f"{label}: negative owned count: {line}")
        owned_counts.append(owned_count)
        owned_total += owned_count
    if owned_total != case.natoms:
        raise baseline.BaselineError(
            f"{label}: owned counts sum to {owned_total}, expected {case.natoms}"
        )
    if ranks > case.natoms and sum(count == 0 for count in owned_counts) < ranks - case.natoms:
        raise baseline.BaselineError(
            f"{label}: N<P startup did not expose the required zero-count ranks"
        )

    epochs: List[Dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.startswith("DMGMD_OWNERSHIP_EPOCH "):
            continue
        item = key_values(line)
        if int(item["owned_sum"]) != case.natoms:
            raise baseline.BaselineError(
                f"{label}: epoch owned_sum is {item['owned_sum']}, expected {case.natoms}"
            )
        transitions: Dict[int, Tuple[int, int]] = {}
        for entry in item.get("transitions", "").strip('"').split(","):
            if not entry:
                continue
            try:
                gid_text, change = entry.split(":")
                old, new = change.split("->")
            except ValueError as error:
                raise baseline.BaselineError(f"{label}: malformed transition '{entry}'") from error
            gid = int(gid_text)
            if not (0 <= gid < case.natoms):
                raise baseline.BaselineError(f"{label}: transition gid {gid} out of range")
            if gid in transitions:
                raise baseline.BaselineError(f"{label}: duplicate transition for gid {gid}")
            transitions[gid] = (int(old), int(new))
        if item.get("truncated") == "false" and len(transitions) != int(item["changed_atoms"]):
            raise baseline.BaselineError(f"{label}: changed_atoms disagrees with transitions")
        epochs.append({"step": int(item["step"]), "transitions": transitions})
    epochs.sort(key=lambda record: record["step"])
    for previous, current in zip(epochs, epochs[1:]):
        if current["step"] <= previous["step"]:
            raise baseline.BaselineError(f"{label}: epoch steps are not increasing")

    # Job-directory discipline: only rank 0 outputs, no scratch leakage.
    actual_files = {entry.name for entry in stage_dir.iterdir() if entry.is_file()}
    missing = case.expected_files - actual_files
    if missing:
        raise baseline.BaselineError(f"{label}: missing expected outputs {sorted(missing)}")
    unexpected = actual_files - case.expected_files - {"execution.stdout", "execution.stderr"}
    if unexpected:
        raise baseline.BaselineError(f"{label}: unexpected files in job dir {sorted(unexpected)}")
    return epochs


def verify_transitions_against_trajectory(
    case: MigrationCase,
    stage_dir: Path,
    epochs: List[Dict[str, Any]],
    ranks: int,
) -> None:
    """Recompute every atom's expected owner from the dumped wrapped positions
    and require the runtime's logged owner transitions to match exactly."""
    # Owner of atom gid after step k (k = 0 is the initial position).
    owners: List[List[int]] = [
        [slab_owner(y, case.box_length, ranks) for y in initial_y_positions(case.model)]
    ]
    for filename in case.trajectory_files:
        for frame in baseline.parse_xyz(stage_dir / filename):
            if frame["natoms"] != case.natoms:
                raise baseline.BaselineError(f"{filename}: unexpected atom count")
            owners.append(
                [slab_owner(y, case.box_length, ranks) for y in frame_y_positions(frame)]
            )
    if len(owners) != case.steps + 1:
        raise baseline.BaselineError(
            f"{case.name}/{ranks}r: have {len(owners) - 1} dumped steps, expected {case.steps}"
        )

    expected_by_step: Dict[int, Dict[int, Tuple[int, int]]] = {}
    for step in range(1, case.steps + 1):
        changes: Dict[int, Tuple[int, int]] = {}
        for gid in range(case.natoms):
            if owners[step - 1][gid] != owners[step][gid]:
                changes[gid] = (owners[step - 1][gid], owners[step][gid])
        if changes:
            expected_by_step[step] = changes

    logged_by_step = {record["step"]: record["transitions"] for record in epochs}
    if logged_by_step != expected_by_step:
        for step in sorted(set(logged_by_step) | set(expected_by_step)):
            logged = logged_by_step.get(step)
            expected = expected_by_step.get(step)
            if logged != expected:
                raise baseline.BaselineError(
                    f"{case.name}/{ranks}r: step {step} owner transitions {logged} "
                    f"do not match the trajectory-derived expectation {expected}"
                )
        raise baseline.BaselineError(f"{case.name}/{ranks}r: owner transition mismatch")

    if ranks == 1:
        if expected_by_step:
            raise baseline.BaselineError(f"{case.name}: P=1 must never migrate ownership")
        return

    if case.name in {"crossings", "empty_slab"} and not expected_by_step:
        raise baseline.BaselineError(
            f"{case.name}/{ranks}r: migration fixture produced no ownership transition"
        )

    if case.name == "crossings" and ranks >= 4:
        transitions = [
            transition
            for changes in expected_by_step.values()
            for transition in changes.values()
        ]
        periodic_edge = {0, ranks - 1}
        has_interior = any(
            abs(old - new) == 1 and {old, new} != periodic_edge
            for old, new in transitions
        )
        has_periodic = any({old, new} == periodic_edge for old, new in transitions)
        has_multi_slab = any(
            abs(old - new) > 1 and {old, new} != periodic_edge
            for old, new in transitions
        )
        missing = [
            name for name, present in (
                ("interior-boundary", has_interior),
                ("periodic-end", has_periodic),
                ("multi-slab", has_multi_slab),
            ) if not present
        ]
        if missing:
            raise baseline.BaselineError(
                f"{case.name}/{ranks}r: fixture no longer exercises {missing} migration"
            )

    if case.name == "empty_slab":
        counts_by_step = [
            [owner_map.count(rank) for rank in range(ranks)]
            for owner_map in owners
        ]
        if ranks >= 4:
            rank_one = [counts[1] for counts in counts_by_step]
            try:
                first_empty = rank_one.index(0, 1)
            except ValueError as error:
                raise baseline.BaselineError(
                    f"{case.name}/{ranks}r: rank 1 never becomes empty"
                ) from error
            if rank_one[0] == 0 or not any(count > 0 for count in rank_one[first_empty + 1:]):
                raise baseline.BaselineError(
                    f"{case.name}/{ranks}r: rank 1 does not show nonempty->empty->nonempty"
                )
        elif not any(0 in counts for counts in counts_by_step):
            raise baseline.BaselineError(
                f"{case.name}/{ranks}r: fixture never produces an empty ownership set"
            )


def verify_comm_accounting(
    case: MigrationCase,
    stage_dir: Path,
    epochs: List[Dict[str, Any]],
    ranks: int,
    backend: str,
) -> None:
    """Require exact calls and byte accounting for every migration step."""
    stdout = (stage_dir / "execution.stdout").read_text(encoding="utf-8")
    steps = [
        key_values(line) for line in stdout.splitlines() if line.startswith("DMGMD_COMM step=")
    ]
    if not steps:
        raise baseline.BaselineError(f"{case.name}/{ranks}r: no communication records")
    if len(steps) != case.steps:
        raise baseline.BaselineError(
            f"{case.name}/{ranks}r: {len(steps)} communication records, expected {case.steps}"
        )
    migration_steps = {record["step"] for record in epochs}
    for record in steps:
        step = int(record["step"])
        if record.get("backend") != backend:
            raise baseline.BaselineError(
                f"{case.name}/{ranks}r: communication backend record is malformed"
            )
        atom_bytes = 8 * case.natoms
        position_bytes = 3 * atom_bytes
        thermo_bytes = 8 * 8 * ranks
        hash_bytes = 2 * 8 * ranks if ranks > 1 else 0
        snapshot_components = 19 + (3 if case.unwrapped else 0)
        snapshot_calls = 5 + (1 if case.unwrapped else 0)
        snapshot_bytes = snapshot_components * atom_bytes

        expected_calls = 2 + snapshot_calls + (1 if ranks > 1 else 0)
        mpi_input = position_bytes + thermo_bytes + hash_bytes + snapshot_bytes
        mpi_output = position_bytes * ranks + thermo_bytes + hash_bytes + snapshot_bytes
        device_to_host = position_bytes + thermo_bytes + snapshot_bytes
        host_to_device = position_bytes * ranks + thermo_bytes

        if step in case.correct_velocity_steps:
            expected_calls += 2
            mpi_input += 2 * position_bytes
            mpi_output += 2 * position_bytes * ranks
            device_to_host += 2 * position_bytes
            host_to_device += 2 * position_bytes * ranks
        if step in migration_steps:
            migrated_fields = 1 + (1 if case.unwrapped else 0)
            expected_calls += migrated_fields
            mpi_input += migrated_fields * position_bytes
            mpi_output += migrated_fields * position_bytes * ranks
            device_to_host += migrated_fields * position_bytes
            host_to_device += migrated_fields * position_bytes * ranks

        expected = {
            "collective_calls": expected_calls,
            "mpi_input_bytes_global": mpi_input,
            "mpi_output_bytes_global": mpi_output,
            "device_to_host_bytes_global": device_to_host if backend == "HostStaged" else 0,
            "host_to_device_bytes_global": host_to_device if backend == "HostStaged" else 0,
            "output_download_bytes": snapshot_bytes if backend == "CudaAware" else 0,
        }
        for key, value in expected.items():
            actual = int(record[key])
            if actual != value:
                raise baseline.BaselineError(
                    f"{case.name}/{ranks}r/{backend}: step {step} {key}={actual}, "
                    f"expected {value}"
                )


# Tolerance categories for numeric output comparison. unwrapped_position
# reuses the position tolerance (same quantity and units).
XYZ_FIELD_CATEGORIES = {
    "Time": "time",
    "Lattice": "lattice",
    "energy": "energy",
    "virial": "virial",
    "stress": "xyz_stress",
}
XYZ_PROPERTY_CATEGORIES = {
    "pos": "position",
    "vel": "velocity",
    "forces": "force",
    "energy_atom": "energy",
    "virial": "virial",
    "mass": "mass",
    "unwrapped_position": "position",
}


def compare_xyz_files(
    reference: Path,
    actual: Path,
    collector: baseline.DiffCollector,
    label: str,
) -> None:
    reference_frames = baseline.parse_xyz(reference)
    actual_frames = baseline.parse_xyz(actual)
    if len(reference_frames) != len(actual_frames):
        raise baseline.BaselineError(f"{label}: frame count mismatch")
    for frame_index, (want, got) in enumerate(zip(reference_frames, actual_frames)):
        context = f"{label}:frame[{frame_index}]"
        if want["natoms"] != got["natoms"]:
            raise baseline.BaselineError(f"atom count mismatch at {context}")
        if want["properties"] != got["properties"]:
            raise baseline.BaselineError(f"properties mismatch at {context}")
        for key in want["field_order"]:
            if key in XYZ_FIELD_CATEGORIES:
                want_values = [float(v) for v in want["fields"][key].split()]
                got_values = [float(v) for v in got["fields"][key].split()]
                if len(want_values) != len(got_values):
                    raise baseline.BaselineError(f"field width mismatch for {key} at {context}")
                for index, (a, b) in enumerate(zip(want_values, got_values)):
                    collector.observe(XYZ_FIELD_CATEGORIES[key], a, b, f"{context}:{key}[{index}]")
            elif want["fields"][key] != got["fields"].get(key):
                raise baseline.BaselineError(f"comment field {key} mismatch at {context}")
        offset = 0
        for name, kind, width in want["properties"]:
            for atom_index, (want_row, got_row) in enumerate(zip(want["rows"], got["rows"])):
                want_tokens = want_row[offset:offset + width]
                got_tokens = got_row[offset:offset + width]
                item = f"{context}:atom[{atom_index}]:{name}"
                if kind == "S":
                    if want_tokens != got_tokens:
                        raise baseline.BaselineError(f"species/order mismatch at {item}")
                elif kind == "I":
                    if [int(t) for t in want_tokens] != [int(t) for t in got_tokens]:
                        raise baseline.BaselineError(f"integer property mismatch at {item}")
                else:
                    category = XYZ_PROPERTY_CATEGORIES.get(name)
                    if category is None:
                        raise baseline.BaselineError(f"no tolerance category for property {name}")
                    for value_index, (a, b) in enumerate(zip(want_tokens, got_tokens)):
                        collector.observe(category, float(a), float(b), f"{item}[{value_index}]")
            offset += width


def compare_case_outputs(
    case: MigrationCase,
    reference_dir: Path,
    actual_dir: Path,
    tolerances: Dict[str, Any],
    label: str,
) -> None:
    collector = baseline.DiffCollector(tolerances, enforce=True)
    for filename in case.outputs:
        reference = reference_dir / filename
        actual = actual_dir / filename
        if not reference.is_file() or not actual.is_file():
            raise baseline.BaselineError(f"{label}: missing output {filename}")
        if filename == "thermo.out":
            baseline.compare_thermo(reference, actual, collector, f"{label}/{filename}")
        elif filename == "neighbor.out":
            if reference.read_bytes() != actual.read_bytes():
                raise baseline.BaselineError(f"exact text mismatch for {label}/{filename}")
        elif filename.endswith(".xyz"):
            compare_xyz_files(reference, actual, collector, f"{label}/{filename}")
        else:
            raise baseline.BaselineError(f"{label}: unexpected output kind {filename}")


def verify_unsupported_box(
    name: str,
    model: str,
    expected_reason: str,
    executable: Path,
    mpiexec: Path,
    devices: List[str],
    potential: Path,
    work_root: Path,
    timeout: int,
) -> None:
    """P>1 must reject the input on every rank with one identical, diagnosable
    error and produce no MD outputs; P=1 must keep accepting the same input
    because the degenerate M0 path never selects a partition axis."""
    if len(devices) < 2:
        raise baseline.BaselineError("the unsupported-box gate needs two devices")

    stage_dir = work_root / "unsupported_box" / f"{name}-2r"
    stage_dir.mkdir(parents=True)
    (stage_dir / "model.xyz").write_text(model, encoding="utf-8")
    (stage_dir / "run.in").write_text(UNSUPPORTED_RUN, encoding="utf-8")
    shutil.copyfile(potential, stage_dir / "nep.txt")
    execution = execute(executable, mpiexec, stage_dir, 2, "HostStaged", devices, timeout)
    if execution["returncode"] == 0:
        raise baseline.BaselineError(f"{name}: a {expected_reason} box must fail at 2 ranks")
    errors = [
        line for line in execution["stderr"].splitlines()
        if line.startswith("DMGMD_ERROR ")
    ]
    if len(errors) != 2:
        raise baseline.BaselineError(
            f"{name}: expected one DMGMD_ERROR per rank, got {len(errors)}: "
            f"{execution['stderr'][-1000:]}"
        )
    by_rank: Dict[int, str] = {}
    for line in errors:
        fields = key_values(line)
        by_rank[int(fields["rank"])] = line
    if set(by_rank) != {0, 1}:
        raise baseline.BaselineError(f"{name}: expected rejections from ranks 0 and 1: {errors}")
    for rank, line in by_rank.items():
        if expected_reason not in line:
            raise baseline.BaselineError(f"{name}: rank {rank} rejection is not diagnosable: {line}")
    messages = {line.split("message=", 1)[1] for line in by_rank.values()}
    if len(messages) != 1:
        raise baseline.BaselineError(f"{name}: rejection messages differ between ranks: {errors}")
    for produced in ("thermo.out", "trajectory.xyz", "restart.xyz"):
        if (stage_dir / produced).exists():
            raise baseline.BaselineError(f"{name}: rejected input produced {produced}")

    single_dir = work_root / "unsupported_box" / f"{name}-1r"
    single_dir.mkdir(parents=True)
    (single_dir / "model.xyz").write_text(model, encoding="utf-8")
    (single_dir / "run.in").write_text(UNSUPPORTED_RUN, encoding="utf-8")
    shutil.copyfile(potential, single_dir / "nep.txt")
    execution = execute(executable, mpiexec, single_dir, 1, "HostStaged", devices, timeout)
    if execution["returncode"] != 0 or execution["stderr"]:
        raise baseline.BaselineError(
            f"{name}: P=1 must keep accepting this input (M0 compatibility): "
            f"exit={execution['returncode']} stderr={execution['stderr'][-1000:]}"
        )
    for required in ("thermo.out", "trajectory.xyz", "neighbor.out"):
        if not (single_dir / required).is_file():
            raise baseline.BaselineError(f"{name}: P=1 run is missing {required}")
    print(f"PASS unsupported_box {name:12s}: rejected at 2 ranks, accepted at 1 rank")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=PROJECT_ROOT / "build" / "dmg-md")
    parser.add_argument("--mpiexec", type=Path, default=Path("mpiexec"))
    parser.add_argument("--ranks", type=integer_list, default=[1, 2, 4])
    parser.add_argument(
        "--backends", type=comma_list, default=["HostStaged", "CudaAware"],
        help="comma-separated HostStaged,CudaAware",
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
    tolerances = baseline.load_manifest()["tolerances"]
    potential = BASELINE_DIR / "inputs" / "potentials" / "nep_C.txt"

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
            f"need at least {max(args.ranks)} visible device IDs for the matrix; got {devices}"
        )
    try:
        mpiexec = mpi_environment.validate_environment(
            executable, args.mpiexec, devices, max(args.ranks), args.timeout
        )
    except mpi_environment.EnvironmentError as error:
        raise baseline.BaselineError(str(error)) from error

    crossings = MigrationCase(
        name="crossings",
        model=CROSSINGS_MODEL,
        run=CROSSINGS_RUN,
        trajectory_files=["trajectory.xyz", "nvt.xyz"],
        outputs=["thermo.out", "trajectory.xyz", "nvt.xyz", "restart.xyz"],
        box_length=24.0,
        natoms=8,
        unwrapped=True,
        correct_velocity_steps={1, 11},
    )
    empty_slab = MigrationCase(
        name="empty_slab",
        model=EMPTY_SLAB_MODEL,
        run=EMPTY_SLAB_RUN,
        trajectory_files=["trajectory.xyz"],
        outputs=["thermo.out", "trajectory.xyz"],
        box_length=24.0,
        natoms=4,
    )
    more_ranks_than_atoms = MigrationCase(
        name="more_ranks_than_atoms",
        model=MORE_RANKS_THAN_ATOMS_MODEL,
        run=MORE_RANKS_THAN_ATOMS_RUN,
        trajectory_files=["trajectory.xyz"],
        outputs=["thermo.out", "trajectory.xyz"],
        box_length=24.0,
        natoms=2,
    )

    work_root = Path(tempfile.mkdtemp(prefix="dmgmd-migration-"))
    succeeded = False
    try:
        # First gate: inputs the slab partitioner must reject identically on
        # every rank at P>1 (while P=1 keeps the M0 compatibility path).
        verify_unsupported_box(
            "triclinic", TRICLINIC_MODEL, "supports only orthogonal boxes",
            executable, mpiexec, devices, potential, work_root, args.timeout,
        )
        verify_unsupported_box(
            "nonperiodic", NONPERIODIC_MODEL, "requires periodicity in all three",
            executable, mpiexec, devices, potential, work_root, args.timeout,
        )
        # The restart input for the resume case comes from the deterministic
        # 1-rank reference run of the crossings fixture.
        reference_root = work_root / crossings.name / "reference-1r"
        stage_case(crossings, reference_root, potential)
        execution = execute(
            executable, mpiexec, reference_root, 1, "HostStaged", devices, args.timeout
        )
        epochs = validate_ownership_records(crossings, reference_root, 1, "HostStaged", execution)
        if epochs:
            raise baseline.BaselineError("1-rank runs must never migrate ownership")
        verify_transitions_against_trajectory(crossings, reference_root, epochs, 1)
        verify_comm_accounting(
            crossings, reference_root, epochs, 1, "HostStaged"
        )
        restart_model = (reference_root / "restart.xyz").read_text(encoding="utf-8")

        resume = MigrationCase(
            name="resume",
            model=restart_model,
            run=RESUME_RUN,
            trajectory_files=["resumed.xyz"],
            outputs=["thermo.out", "resumed.xyz"],
            box_length=24.0,
            natoms=8,
        )
        cases: List[MigrationCase] = [
            crossings, empty_slab, more_ranks_than_atoms, resume,
        ]

        for case in cases:
            case_reference = work_root / case.name / "reference-1r"
            if not case_reference.exists():
                stage_case(case, case_reference, potential)
                execution = execute(
                    executable, mpiexec, case_reference, 1, "HostStaged", devices, args.timeout
                )
                epochs = validate_ownership_records(
                    case, case_reference, 1, "HostStaged", execution
                )
                if epochs:
                    raise baseline.BaselineError(
                        f"{case.name}: 1-rank runs must never migrate ownership"
                    )
                verify_transitions_against_trajectory(case, case_reference, epochs, 1)
                verify_comm_accounting(
                    case, case_reference, epochs, 1, "HostStaged"
                )
            for backend in backends:
                for ranks in args.ranks:
                    if ranks == 1 and backend == "HostStaged":
                        continue  # already executed as the reference
                    stage_dir = work_root / case.name / f"{backend}-{ranks}"
                    stage_case(case, stage_dir, potential)
                    execution = execute(
                        executable, mpiexec, stage_dir, ranks, backend, devices, args.timeout
                    )
                    epochs = validate_ownership_records(
                        case, stage_dir, ranks, backend, execution
                    )
                    verify_transitions_against_trajectory(case, stage_dir, epochs, ranks)
                    verify_comm_accounting(case, stage_dir, epochs, ranks, backend)
                    compare_case_outputs(
                        case, case_reference, stage_dir, tolerances,
                        f"{case.name}/{backend}/{ranks}",
                    )
                    migrated = sum(len(record["transitions"]) for record in epochs)
                    print(
                        f"PASS {case.name:10s} {backend:10s} ranks={ranks}: "
                        f"epochs={len(epochs)} migrated_atoms={migrated}"
                    )
        print(
            f"PASS: M1 migration matrix ranks={args.ranks} backends={backends} "
            "(records, transition log and cross-rank dynamics all verified)"
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
