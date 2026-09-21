#!/usr/bin/env python3
"""Run the M2a local-domain acceptance matrix (docs/plans/domain-decomposition.md
section 10).

Every fixture uses a box that genuinely satisfies the NEP large-box criterion
and the slab_width >= d_coord guard for the pinned nep_C.txt potential
(rc_radial = 7, rc_angular = 4 => d_dep = 8, d_coord = 16, large-box limit
2.5*(rc+1) = 20): the lattice box is 64x24x24, so the slabs are 32 A (P=2)
and 16 A (P=4). The same input at P=1 (which always stays on the replicated
path) is the numerical oracle.

Verified per run:
- the DMGMD_DOMAIN record actually reports mode=m2a (a fallback PASS never
  counts as M2a coverage);
- per-rank layouts (owned / dependency ghosts / coordinate-only ghosts /
  local_count) from the DMGMD_DOMAIN_LAYOUT records;
- the owner transitions logged by DMGMD_DOMAIN_MIGRATION match the owners
  recomputed from the dumped double-precision wrapped positions, including
  interior boundaries, periodic ends, one-step multi-slab crossings and
  temporarily empty slabs (N < P included);
- per-atom energy/force/virial, thermo and short trajectories against the
  P=1 oracle with the committed baseline tolerances (never relaxed);
- a genuine logical local_count=0 rank for 1000 steps, including the periodic
  neighbor.out record at force call 1000;
- two-step large-box P=1-oracle cases for NEP5, mixed typewise radial/angular
  cutoff, flexible ZBL and covalent-radius typewise ZBL;
- every dumped frame carries exactly N atoms (owned global IDs exactly once;
  ghosts never reach the output or the thermo);
- exact per-step communication accounting: the collective fields carry no
  N-scaled term on ordinary steps (no position Allgatherv exists in the M2a
  protocol), and every rank's p2p halo/migration/control bytes match the
  layout-derived model exactly.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = PROJECT_ROOT / "tests" / "baseline"
sys.path.insert(0, str(BASELINE_DIR))
import run_baselines as baseline  # noqa: E402
import check_environment as mpi_environment  # noqa: E402
LONG_NVE_DIR = PROJECT_ROOT / "tests" / "long_nve"
sys.path.insert(0, str(LONG_NVE_DIR))
import long_nve_common  # noqa: E402

# Pinned geometry of the fixtures: the nep_C.txt potential gives d_dep = 8 and
# d_coord = 16; the 64 A partition axis gives slabs of 32 A (P=2) / 16 A (P=4).
BOX_LENGTHS = (64.0, 24.0, 24.0)
AXIS = 0
D_DEP = 8.0
D_COORD = 16.0
RC_RADIAL = 7.0
SKIN = 1.0

# ---------------------------------------------------------------------------
# Fixtures. All velocities are explicit (no random initial velocity path).
# ---------------------------------------------------------------------------

def lattice_model() -> str:
    """576-atom perturbed lattice spanning the box: interior boundaries,
    full per-atom outputs, unwrapped tracking."""
    import random
    random.seed(20260917)
    lines = [
        "576",
        'pbc="T T T" Lattice="64 0 0 0 24 0 0 0 24" '
        "Properties=species:S:1:pos:R:3:vel:R:3",
    ]
    for i in range(16):
        for j in range(6):
            for k in range(6):
                x = 1.0 + 4.0 * i + (i % 2) * 0.1
                y = 1.0 + 4.0 * j + (j % 3) * 0.05
                z = 1.0 + 4.0 * k + (k % 2) * 0.08
                vx = random.uniform(-0.01, 0.01)
                vy = random.uniform(-0.005, 0.005)
                vz = random.uniform(-0.005, 0.005)
                lines.append(
                    f"C {x:.10f} {y:.10f} {z:.10f} {vx:.10f} {vy:.10f} {vz:.10f}")
    return "\n".join(lines) + "\n"


LATTICE_RUN = """potential nep.txt

time_step 1.0 0.5
ensemble nve
correct_velocity 10
dump_thermo 1
dump_xyz 1 trajectory.xyz precision double velocity force potential virial unwrapped_position
dump_restart 10
run 10
"""

# Three atoms forming the two-hop chain: owned i (x=15.5, slab 0 of 4) whose
# force consumes ghost j (x=18, owned by slab 1, dependency ghost on rank 0),
# while j's descriptor consumes k (x=24.5) which lies 8.5 A from rank 0's slab
# -- beyond d_dep = 8 but inside d_coord = 16, i.e. a coordinate-only ghost on
# rank 0. If the halo were only one hop deep, k would be missing and the
# force on i would be wrong; matching the P=1 oracle proves the closure.
CHAIN_MODEL = """3
pbc="T T T" Lattice="64 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3
C 15.5 12.0 12.0 0.004 0.0 0.0
C 18.0 12.0 12.0 -0.003 0.0 0.0
C 24.5 12.0 12.0 0.002 0.0 0.0
"""

CHAIN_RUN = """potential nep.txt

time_step 1.0
ensemble nve
dump_thermo 1
dump_xyz 1 trajectory.xyz precision double velocity force potential virial
run 6
"""

# Both atoms remain in slab 0 and are outside the force cutoff.  At P=4 they
# reach ranks 1 and 3 as ghosts, while rank 2 has neither owned atoms nor
# ghosts: logical local_count is exactly zero.  1000 steps plus the initial
# force produce force calls 0..1000 and exercise the second neighbor.out
# record and its two per-step MPI_MAX reductions.
EMPTY_LOCAL_MODEL = """2
pbc="T T T" Lattice="64 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3
C 1.0 12.0 12.0 0.0 0.0 0.0
C 9.0 12.0 12.0 0.0 0.0 0.0
"""

EMPTY_LOCAL_RUN = """potential nep.txt

time_step 1.0
ensemble nve
run 1000
"""

VARIANT_RUN = """potential nep.txt

time_step 0.001
ensemble nve
dump_thermo 1
dump_xyz 1 static.xyz precision double velocity force potential virial
run 2
"""

WATER_VARIANT_MODEL = """6
pbc="T T T" Lattice="64 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3
O 15.0 12.0 12.0 0.0 0.0 0.0
H 15.9572 12.0 12.0 0.0 0.0 0.0
H 14.7600128 12.9266272 12.0 0.0 0.0 0.0
O 18.0 12.0 12.0 0.0 0.0 0.0
H 18.9572 12.0 12.0 0.0 0.0 0.0
H 17.7600128 12.9266272 12.0 0.0 0.0 0.0
"""

BTO_VARIANT_MODEL = """5
pbc="T T T" Lattice="64 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3
Ba 15.0 12.0 12.0 0.0 0.0 0.0
Ti 16.0 12.0 12.0 0.0 0.0 0.0
O 17.0 12.0 12.0 0.0 0.0 0.0
O 16.0 13.5 12.0 0.0 0.0 0.0
O 16.0 12.0 13.5 0.0 0.0 0.0
"""

# Eight atoms with large explicit velocities: interior boundary crossings, a
# periodic end wrap, one-step multi-slab crossings and temporarily empty
# slabs (only 8 atoms over 64 A of axis, so several slabs start empty).
CROSSINGS_MODEL = """8
pbc="T T T" Lattice="64 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3
C 4.0  12.0 12.0 0.0 0.0 0.0
C 15.0 12.0 12.0 20.0 0.0 0.0
C 20.0 12.0 12.0 -18.0 0.0 0.0
C 40.0 12.0 12.0 40.0 0.0 0.0
C 55.0 12.0 12.0 -45.0 0.0 0.0
C 30.0 6.0  12.0 0.0 0.0 0.0
C 8.0  18.0 12.0 5.0 0.0 0.0
C 60.0 12.0 12.0 -70.0 0.0 0.0
"""

CROSSINGS_RUN = """potential nep.txt

time_step 1.0
ensemble nve
dump_thermo 1
dump_xyz 1 trajectory.xyz precision double velocity force potential virial unwrapped_position
dump_restart 4
run 4

time_step 1.0
ensemble nvt_ber 300 300 1000
dump_thermo 1
dump_xyz 1 nvt.xyz precision double velocity force potential virial unwrapped_position
run 4
"""

RESUME_RUN = """potential nep.txt

time_step 1.0
ensemble nve
dump_thermo 1
dump_xyz 1 resumed.xyz precision double velocity force potential virial
run 6
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


def run_lengths(run_text: str) -> List[int]:
    return [int(line.split()[1]) for line in run_text.splitlines()
            if line.split()[:1] == ["run"]]


def neighbor_record_call_indices(run_text: str) -> List[int]:
    total_force_calls = sum(run_lengths(run_text)) + len(run_lengths(run_text))
    return list(range(0, total_force_calls, 1000))


def neighbor_record_steps(run_text: str) -> Set[int]:
    """1-based MD steps whose force call emits a periodic record.

    Every run segment has one uncounted initial force.  Force calls made in
    the step loop use that step's CommunicationVolume.
    """
    call_index = 0
    global_step = 0
    result: Set[int] = set()
    for length in run_lengths(run_text):
        call_index += 1  # segment-initial force, outside per-step accounting
        for _ in range(length):
            global_step += 1
            if call_index % 1000 == 0:
                result.add(global_step)
            call_index += 1
    return result


def initial_axis_positions(model_text: str) -> List[float]:
    lines = [line for line in model_text.splitlines() if line.strip()]
    natoms = int(lines[0].strip())
    positions = [float(line.split()[1]) for line in lines[2:2 + natoms]]
    if len(positions) != natoms:
        raise baseline.BaselineError("fixture model did not parse into positions")
    return positions


def frame_axis_positions(frame: Dict[str, Any]) -> List[float]:
    return [float(row[1]) for row in frame["rows"]]


def slab_owner(x: float, box_length: float, world_size: int) -> int:
    s = x / box_length
    if s < 0.0:
        s += 1.0
    elif s > 1.0:
        s -= 1.0
    if not (0.0 <= s <= 1.0):
        raise baseline.BaselineError(f"trajectory x={x!r} is outside the wrappable range")
    if world_size == 1:
        return 0
    return min(int(s * world_size), world_size - 1)


class DomainCase:
    def __init__(
        self,
        name: str,
        model: str,
        run: str,
        trajectory_files: List[str],
        outputs: List[str],
        natoms: int,
        unwrapped: bool = False,
        adaptive: bool = False,
        correction_interval: int = 0,
        restart_interval: int = 0,
        snapshot_every_step: bool = True,
        communication_interval: int = 1,
        potential_text: Optional[str] = None,
        d_dep: float = D_DEP,
        d_coord: float = D_COORD,
    ) -> None:
        self.name = name
        self.model = model
        self.run = run
        self.trajectory_files = trajectory_files
        self.outputs = outputs
        self.natoms = natoms
        self.steps = expected_steps(run)
        self.unwrapped = unwrapped
        self.adaptive = adaptive
        self.correction_interval = correction_interval
        self.restart_interval = restart_interval
        self.snapshot_every_step = snapshot_every_step
        self.communication_interval = communication_interval
        self.potential_text = potential_text
        self.d_dep = d_dep
        self.d_coord = d_coord

    @property
    def expected_files(self) -> set:
        return {"model.xyz", "run.in", "nep.txt", "neighbor.out"} | set(self.outputs)


def stage_case(case: DomainCase, stage_dir: Path, potential: Path) -> None:
    stage_dir.mkdir(parents=True)
    (stage_dir / "model.xyz").write_text(case.model, encoding="utf-8")
    (stage_dir / "run.in").write_text(case.run, encoding="utf-8")
    if case.potential_text is None:
        shutil.copyfile(potential, stage_dir / "nep.txt")
    else:
        (stage_dir / "nep.txt").write_text(case.potential_text, encoding="utf-8")


def execute(
    executable: Path,
    mpiexec: Path,
    stage_dir: Path,
    ranks: int,
    backend: str,
    devices: List[str],
    timeout: int,
    communication_interval: Optional[int] = 1,
    domain_diagnostics: bool = True,
) -> Dict[str, Any]:
    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = ",".join(devices[:ranks])
    env["DMGMD_COMM_BACKEND"] = backend
    if communication_interval is None:
        env.pop("DMGMD_COMM_LOG_INTERVAL", None)
    else:
        env["DMGMD_COMM_LOG_INTERVAL"] = str(communication_interval)
    if domain_diagnostics:
        env["DMGMD_DOMAIN_DIAGNOSTICS"] = "1"
    else:
        env.pop("DMGMD_DOMAIN_DIAGNOSTICS", None)
    completed = subprocess.run(
        [str(mpiexec), "-n", str(ranks), str(executable)],
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


class StepIndex:
    """Parses the per-rank M2a records of one execution."""

    def __init__(self, stdout: str, case: DomainCase, ranks: int) -> None:
        self.ranks = ranks
        self.case = case
        self.layouts: Dict[int, List[Dict[str, Any]]] = {}  # rank -> records
        self.migrations: Dict[int, List[Dict[str, Any]]] = {}
        self.summaries: Dict[int, List[Dict[str, Any]]] = {}
        for line in stdout.splitlines():
            if line.startswith("DMGMD_DOMAIN_LAYOUT "):
                fields = key_values(line)
                rank = int(fields["rank"])
                self.layouts.setdefault(rank, []).append(fields)
            elif line.startswith("DMGMD_DOMAIN_MIGRATION "):
                fields = key_values(line)
                rank = int(fields["rank"])
                self.migrations.setdefault(rank, []).append(fields)
            elif line.startswith("DMGMD_DOMAIN_SUMMARY "):
                fields = key_values(line)
                rank = int(fields["rank"])
                self.summaries.setdefault(rank, []).append(fields)
        for rank in range(ranks):
            records = self.layouts.get(rank, [])
            if len(records) != len(self.layouts.get(0, [])):
                raise baseline.BaselineError(
                    f"layout record counts differ between ranks ({rank})")

    def layout_in_effect(self, rank: int, step: int) -> Dict[str, Any]:
        """Latest layout record strictly before `step` (a record AT step is
        produced after that step's position refresh)."""
        chosen: Optional[Dict[str, Any]] = None
        for record in self.layouts.get(rank, []):
            if int(record["step"]) < step:
                chosen = record
        if chosen is None:
            raise baseline.BaselineError("no layout record predates the step")
        return chosen

    def layout_at(self, rank: int, step: int) -> Dict[str, Any]:
        for record in self.layouts.get(rank, []):
            if int(record["step"]) == step:
                return record
        return self.layout_in_effect(rank, step)

    def migration_steps(self) -> Set[int]:
        steps: Set[int] = set()
        for records in self.migrations.values():
            for record in records:
                if int(record["migrated_in"]) > 0 or record.get("transitions", "").strip('"'):
                    steps.add(int(record["step"]))
        return steps

    def transitions_at(self, step: int) -> Dict[int, Tuple[int, int]]:
        """All owner transitions of one step: gid -> (old, new)."""
        result: Dict[int, Tuple[int, int]] = {}
        for records in self.migrations.values():
            for record in records:
                if int(record["step"]) != step:
                    continue
                for entry in record.get("transitions", "").strip('"').split(","):
                    if not entry:
                        continue
                    gid_text, change = entry.split(":")
                    old, new = change.split("->")
                    gid = int(gid_text)
                    if gid in result and result[gid] != (int(old), int(new)):
                        raise baseline.BaselineError(f"conflicting transition for gid {gid}")
                    result[gid] = (int(old), int(new))
        return result

    def rebuild_steps(self) -> Set[int]:
        """Steps whose epoch advanced without a migration."""
        migration = self.migration_steps()
        rebuild: Set[int] = set()
        for rank in range(self.ranks):
            records = self.layouts.get(rank, [])
            for previous, current in zip(records, records[1:]):
                step = int(current["step"])
                if step not in migration and int(current["epoch"]) != int(previous["epoch"]):
                    rebuild.add(step)
        return rebuild


def verify_domain_records(
    case: DomainCase,
    stage_dir: Path,
    ranks: int,
    execution: Dict[str, Any],
) -> StepIndex:
    label = f"{case.name}/{ranks}r"
    if execution["returncode"] != 0:
        raise baseline.BaselineError(
            f"{label}: dmg-md exited with {execution['returncode']}: "
            f"{execution['stderr'][-2000:]}"
        )
    if execution["stderr"]:
        raise baseline.BaselineError(f"{label}: unexpected stderr: {execution['stderr'][-2000:]}")
    stdout = execution["stdout"]

    domain = [line for line in stdout.splitlines() if line.startswith("DMGMD_DOMAIN ")]
    if len(domain) != 1:
        raise baseline.BaselineError(f"{label}: missing unique DMGMD_DOMAIN record")
    fields = key_values(domain[0])
    if fields.get("mode") != "m2a":
        raise baseline.BaselineError(
            f"{label}: expected mode=m2a, got {fields.get('mode')!r} "
            "(a fallback PASS is not M2a coverage)")
    if fields.get("axis") != "x":
        raise baseline.BaselineError(f"{label}: expected axis=x for the 64 A edge")
    if float(fields["d_dep"]) != case.d_dep or float(fields["d_coord"]) != case.d_coord:
        raise baseline.BaselineError(f"{label}: unexpected halo depths: {domain[0]}")

    coverage = [line for line in stdout.splitlines() if line.startswith("DMGMD_CENTER_PARTITION ")]
    if len(coverage) != 1:
        raise baseline.BaselineError(f"{label}: missing unique center partition proof")
    center = key_values(coverage[0])
    for key, value in (
        ("missing", "0"), ("overlapping", "0"),
        ("owned_output_coverage", "complete"),
        ("nep_kernel_centers", "local-domain-force-centers"),
        ("nep_N1_N2_shard_complete", "true"),
    ):
        if center.get(key) != value:
            raise baseline.BaselineError(
                f"{label}: center proof {key}={center.get(key)!r}, expected {value!r}")

    index = StepIndex(stdout, case, ranks)
    for rank in range(ranks):
        summaries = index.summaries.get(rank, [])
        if not summaries:
            raise baseline.BaselineError(f"{label}: rank {rank} lacks a run summary")
        for summary in summaries:
            for key in (
                "migration_steps", "rebuild_steps", "layout_uploads",
                "workspace_updates", "capacity_growth_events", "gpu_allocations",
                "gpu_allocations_cumulative",
            ):
                if int(summary[key]) < 0:
                    raise baseline.BaselineError(
                        f"{label}: negative {key} in rank {rank} summary")
            if int(summary["workspace_updates"]) > int(summary["layout_uploads"]):
                raise baseline.BaselineError(
                    f"{label}: workspace updates exceed layout uploads")
            if int(summary["capacity_growth_events"]) > int(summary["layout_uploads"]):
                raise baseline.BaselineError(
                    f"{label}: capacity growth events exceed layout uploads")
    # Every rank must report one initial layout at step 0 with owned counts
    # summing to N.
    owned_total = 0
    for rank in range(ranks):
        records = index.layouts.get(rank, [])
        if not records or int(records[0]["step"]) != 0:
            raise baseline.BaselineError(f"{label}: rank {rank} lacks a step-0 layout")
        owned_total += int(records[0]["owned"])
    if owned_total != case.natoms:
        raise baseline.BaselineError(
            f"{label}: initial owned counts sum to {owned_total}, expected {case.natoms}")
    # Every layout of every rank keeps the counting identity.
    for rank in range(ranks):
        for record in index.layouts.get(rank, []):
            local = int(record["local_count"])
            parts = sum(int(record[key]) for key in
                        ("owned", "dep_left", "dep_right", "coord_left", "coord_right"))
            if local != parts:
                raise baseline.BaselineError(
                    f"{label}: layout parts {parts} != local_count {local}: {record}")
        for previous, current in zip(
                index.layouts.get(rank, []), index.layouts.get(rank, [])[1:]):
            shape_fields = (
                "local_count", "send_left", "send_right", "recv_left", "recv_right")
            if (all(int(previous[key]) == int(current[key]) for key in shape_fields)
                    and int(current["gpu_allocations"]) != 0):
                raise baseline.BaselineError(
                    f"{label}: identical-shape layout upload reallocated GPU storage: {current}")

    neighbor = (stage_dir / "neighbor.out").read_text(encoding="utf-8").splitlines()
    expected_calls = neighbor_record_call_indices(case.run)
    actual_calls = []
    for line in neighbor:
        prefix = "Neighbor info at step "
        if not line.startswith(prefix) or ":" not in line[len(prefix):]:
            raise baseline.BaselineError(f"{label}: malformed neighbor.out line {line!r}")
        actual_calls.append(int(line[len(prefix):].split(":", 1)[0]))
    if actual_calls != expected_calls:
        raise baseline.BaselineError(f"{label}: unexpected neighbor.out content {neighbor}")

    if case.name == "empty_local" and ranks == 4:
        rank2 = index.layouts.get(2, [])
        if not rank2 or any(int(record["local_count"]) != 0 for record in rank2):
            raise baseline.BaselineError(
                f"{label}: rank 2 must keep logical local_count=0, got {rank2}")

    actual_files = {entry.name for entry in stage_dir.iterdir() if entry.is_file()}
    missing = case.expected_files - actual_files
    if missing:
        raise baseline.BaselineError(f"{label}: missing expected outputs {sorted(missing)}")
    unexpected = actual_files - case.expected_files - {"execution.stdout", "execution.stderr"}
    if unexpected:
        raise baseline.BaselineError(f"{label}: unexpected files in job dir {sorted(unexpected)}")
    return index


def verify_transitions_against_trajectory(
    case: DomainCase,
    stage_dir: Path,
    index: StepIndex,
    ranks: int,
) -> None:
    """The logged owner transitions must match owners recomputed from the
    dumped double-precision wrapped positions."""
    if not case.trajectory_files:
        if index.migration_steps():
            raise baseline.BaselineError(
                f"{case.name}/{ranks}r: no-trajectory stationary fixture migrated")
        return
    owners: List[List[int]] = [
        [slab_owner(x, BOX_LENGTHS[AXIS], ranks) for x in initial_axis_positions(case.model)]
    ]
    for filename in case.trajectory_files:
        for frame in baseline.parse_xyz(stage_dir / filename):
            if frame["natoms"] != case.natoms:
                raise baseline.BaselineError(f"{filename}: unexpected atom count")
            owners.append(
                [slab_owner(x, BOX_LENGTHS[AXIS], ranks) for x in frame_axis_positions(frame)])
    if len(owners) != case.steps + 1:
        raise baseline.BaselineError(
            f"{case.name}/{ranks}r: have {len(owners) - 1} dumped steps, expected {case.steps}")

    expected_by_step: Dict[int, Dict[int, Tuple[int, int]]] = {}
    for step in range(1, case.steps + 1):
        changes: Dict[int, Tuple[int, int]] = {}
        for gid in range(case.natoms):
            if owners[step - 1][gid] != owners[step][gid]:
                changes[gid] = (owners[step - 1][gid], owners[step][gid])
        if changes:
            expected_by_step[step] = changes
    logged_by_step = {
        step: index.transitions_at(step) for step in sorted(index.migration_steps())
    }
    if logged_by_step != expected_by_step:
        for step in sorted(set(logged_by_step) | set(expected_by_step)):
            logged = logged_by_step.get(step)
            expected = expected_by_step.get(step)
            if logged != expected:
                raise baseline.BaselineError(
                    f"{case.name}/{ranks}r: step {step} owner transitions {logged} "
                    f"do not match the trajectory-derived expectation {expected}")
    if ranks >= 4 and case.name == "crossings":
        transitions = [
            change for changes in expected_by_step.values() for change in changes.values()
        ]
        periodic_edge = {0, ranks - 1}
        if not any(abs(old - new) > 1 and {old, new} != periodic_edge for old, new in transitions):
            raise baseline.BaselineError(
                f"{case.name}/{ranks}r: fixture no longer exercises one-step multi-slab routing")
        if not any({old, new} == periodic_edge for old, new in transitions):
            raise baseline.BaselineError(
                f"{case.name}/{ranks}r: fixture no longer exercises periodic-end migration")
    # P=2 with 3 atoms also covers N < P at P=4.
    if case.name == "chain" and ranks == 4:
        empty = [step_records for step_records in owners if 0 in
                 [step_records.count(rank) for rank in range(ranks)]]
        if not empty:
            raise baseline.BaselineError("chain/P=4 must exercise empty slabs (N < P)")


def expected_communication(
    case: DomainCase,
    index: StepIndex,
    ranks: int,
    backend: str,
) -> List[Dict[str, Dict[str, int]]]:
    """Exact per-step model of every DMGMD_COMM / DMGMD_DOMAIN_COMM field.

    Collective fields are global aggregates; the p2p classes are per-rank
    local values derived from the layout records:
      * ordinary step: one 24 B/atom position refresh through the cached plan;
      * rebuild step: the refresh plus a face count handshake (2 x 4 B) and a
        40 B/atom membership exchange with the NEW layout's counts;
      * migration step: an Alltoall count handshake (4 B per rank), the face
        counts, the membership exchange, and an Alltoallv of migration
        records (record_bytes per atom; 104 B with unwrapped tracking, else
        80 B for this group-less potential).
    No N-scaled collective ever appears on an ordinary step.
    """
    record_bytes = 104 if case.unwrapped else 80
    migration_steps = index.migration_steps()
    rebuild_steps = index.rebuild_steps()
    periodic_record_steps = neighbor_record_steps(case.run)
    model: List[Dict[str, Dict[str, int]]] = []
    for step in range(1, case.steps + 1):
        global_fields = {
            "collective_calls": 0,
            "mpi_input_bytes_global": 0,
            "mpi_output_bytes_global": 0,
        }
        staging = {"device_to_host": 0, "host_to_device": 0, "download": 0,
                   "host_to_device_both": 0}
        per_rank: Dict[int, Dict[str, int]] = {
            rank: {
                "p2p_calls": 0, "halo_send": 0, "halo_recv": 0,
                "migration_send": 0, "migration_recv": 0,
                "control_send": 0, "control_recv": 0,
            } for rank in range(ranks)
        }
        P = ranks
        N = case.natoms
        # Migration decision OR (every step).
        global_fields["collective_calls"] += 1
        global_fields["mpi_input_bytes_global"] += 8 * P
        global_fields["mpi_output_bytes_global"] += 8 * P
        migrated = step in migration_steps
        if not migrated:
            # Position refresh with the layout in effect, then the rebuild OR.
            for rank in range(ranks):
                layout = index.layout_in_effect(rank, step)
                sends = int(layout["send_left"]) + int(layout["send_right"])
                recvs = (int(layout["dep_left"]) + int(layout["dep_right"]) +
                         int(layout["coord_left"]) + int(layout["coord_right"]))
                per_rank[rank]["p2p_calls"] += 4
                per_rank[rank]["halo_send"] += 24 * sends
                per_rank[rank]["halo_recv"] += 24 * recvs
            global_fields["collective_calls"] += 1
            global_fields["mpi_input_bytes_global"] += 8 * P
            global_fields["mpi_output_bytes_global"] += 8 * P
        else:
            # Alltoall count handshake (control).
            for rank in range(ranks):
                per_rank[rank]["control_send"] += 4 * P
                per_rank[rank]["control_recv"] += 4 * P
        if migrated or step in rebuild_steps:
            # Face count handshake + membership exchange with the NEW layout.
            for rank in range(ranks):
                layout = index.layout_at(rank, step)
                sends = int(layout["send_left"]) + int(layout["send_right"])
                recvs = (int(layout["dep_left"]) + int(layout["dep_right"]) +
                         int(layout["coord_left"]) + int(layout["coord_right"]))
                per_rank[rank]["p2p_calls"] += 8
                per_rank[rank]["control_send"] += 8
                per_rank[rank]["control_recv"] += 8
                per_rank[rank]["halo_send"] += 40 * sends
                per_rank[rank]["halo_recv"] += 40 * recvs
        if migrated:
            # Alltoallv migration records.
            transitions = index.transitions_at(step)
            incoming = {rank: 0 for rank in range(ranks)}
            outgoing = {rank: 0 for rank in range(ranks)}
            for old, new in transitions.values():
                incoming[new] += 1
                outgoing[old] += 1
            for rank in range(ranks):
                per_rank[rank]["migration_send"] += record_bytes * outgoing[rank]
                per_rank[rank]["migration_recv"] += record_bytes * incoming[rank]
        # Adaptive timestep.
        if case.adaptive:
            global_fields["collective_calls"] += 1
            global_fields["mpi_input_bytes_global"] += 8 * P
            global_fields["mpi_output_bytes_global"] += 8 * P
        # thermo Allreduce (8 doubles).
        global_fields["collective_calls"] += 1
        global_fields["mpi_input_bytes_global"] += 64 * P
        global_fields["mpi_output_bytes_global"] += 64 * P
        staging["device_to_host"] += 64 * P
        staging["host_to_device"] += 64 * P
        # Every 1000th domain force call performs two scalar MPI_MAX
        # reductions for the rank-local radial/angular occupancy maxima.
        if step in periodic_record_steps:
            global_fields["collective_calls"] += 2
            global_fields["mpi_input_bytes_global"] += 16 * P
            global_fields["mpi_output_bytes_global"] += 16 * P
        # correct_velocity trigger steps (the runtime tests the 0-based loop
        # step, so interval 10 fires on the 1-based steps 1, 11, ...).
        if case.correction_interval and (step - 1) % case.correction_interval == 0:
            global_fields["collective_calls"] += 4
            global_fields["mpi_input_bytes_global"] += 80 * N
            global_fields["mpi_output_bytes_global"] += 80 * N
            staging["device_to_host"] += 48 * N
            # CudaAware roots download the two gathered fields instead.
            staging["download"] += 48 * N
            # The scatter's final upload is host-born (the root computed the
            # correction on the CPU), so this H2D applies on both backends.
            staging["host_to_device_both"] += 24 * N
            for rank in range(ranks):
                per_rank[rank]["control_send"] += 4
                per_rank[rank]["control_recv"] += 4 * P
        # Output steps.
        if case.snapshot_every_step and step % 1 == 0:
            components = 19 + (3 if case.unwrapped else 0)
            global_fields["collective_calls"] += 6 + (1 if case.unwrapped else 0)
            global_fields["mpi_input_bytes_global"] += 8 * N + components * 8 * N
            global_fields["mpi_output_bytes_global"] += 8 * N + components * 8 * N
            staging["device_to_host"] += components * 8 * N
            staging["download"] += components * 8 * N
            for rank in range(ranks):
                per_rank[rank]["control_send"] += 4
                per_rank[rank]["control_recv"] += 4 * P
        entry = {"global": global_fields, "per_rank": per_rank, "staging": staging}
        model.append(entry)
    return model


def verify_communication(
    case: DomainCase,
    stage_dir: Path,
    index: StepIndex,
    ranks: int,
    backend: str,
) -> None:
    label = f"{case.name}/{ranks}r/{backend}"
    stdout = (stage_dir / "execution.stdout").read_text(encoding="utf-8")
    comm = [key_values(line) for line in stdout.splitlines()
            if line.startswith("DMGMD_COMM step=")]
    domain_comm = [key_values(line) for line in stdout.splitlines()
                   if line.startswith("DMGMD_DOMAIN_COMM ")]
    logged_steps = list(range(
        case.communication_interval, case.steps + 1, case.communication_interval))
    if len(comm) != len(logged_steps):
        raise baseline.BaselineError(f"{label}: {len(comm)} DMGMD_COMM records")
    by_rank_step = {}
    for fields in domain_comm:
        by_rank_step[(int(fields["rank"]), int(fields["step"]))] = fields
    model = expected_communication(case, index, ranks, backend)
    host_staged = backend == "HostStaged"
    for step, fields in zip(logged_steps, comm):
        expected = model[step - 1]
        if int(fields["step"]) != step or fields.get("backend") != backend:
            raise baseline.BaselineError(f"{label}: malformed DMGMD_COMM record at step {step}")
        for key, value in expected["global"].items():
            if int(fields[key]) != value:
                raise baseline.BaselineError(
                    f"{label}: step {step} {key}={fields[key]}, expected {value}")
        d2h = expected["staging"]["device_to_host"] if host_staged else 0
        h2d = (expected["staging"]["host_to_device"] if host_staged else 0) + \
            expected["staging"]["host_to_device_both"]
        download = 0 if host_staged else expected["staging"]["download"]
        if int(fields["device_to_host_bytes_global"]) != d2h:
            raise baseline.BaselineError(
                f"{label}: step {step} device_to_host={fields['device_to_host_bytes_global']}, "
                f"expected {d2h}")
        if int(fields["host_to_device_bytes_global"]) != h2d:
            raise baseline.BaselineError(
                f"{label}: step {step} host_to_device={fields['host_to_device_bytes_global']}, "
                f"expected {h2d}")
        if int(fields["output_download_bytes"]) != download:
            raise baseline.BaselineError(
                f"{label}: step {step} output_download={fields['output_download_bytes']}, "
                f"expected {download}")
        for rank in range(ranks):
            record = by_rank_step.get((rank, step))
            if record is None:
                raise baseline.BaselineError(f"{label}: missing DMGMD_DOMAIN_COMM rank={rank}")
            want = expected["per_rank"][rank]
            pairs = (
                ("p2p_calls", "p2p_calls"),
                ("halo_send_bytes_local", "halo_send"),
                ("halo_recv_bytes_local", "halo_recv"),
                ("migration_send_bytes_local", "migration_send"),
                ("migration_recv_bytes_local", "migration_recv"),
                ("control_send_bytes_local", "control_send"),
                ("control_recv_bytes_local", "control_recv"),
            )
            for key, want_key in pairs:
                if int(record[key]) != want[want_key]:
                    raise baseline.BaselineError(
                        f"{label}: step {step} rank {rank} {key}={record[key]}, "
                        f"expected {want[want_key]}")


# Tolerance categories for numeric output comparison (same mapping as the
# migration runner).
XYZ_FIELD_CATEGORIES = {
    "Time": "time", "Lattice": "lattice", "energy": "energy",
    "virial": "virial", "stress": "xyz_stress",
}
XYZ_PROPERTY_CATEGORIES = {
    "pos": "position", "vel": "velocity", "forces": "force",
    "energy_atom": "energy", "virial": "virial", "mass": "mass",
    "unwrapped_position": "position",
}


def compare_xyz_atom_rows(
    reference: Path, actual: Path, collector: baseline.DiffCollector, label: str
) -> None:
    reference_frames = baseline.parse_xyz(reference)
    actual_frames = baseline.parse_xyz(actual)
    if len(reference_frames) != len(actual_frames):
        raise baseline.BaselineError(f"{label}: frame count mismatch")
    for frame_index, (want, got) in enumerate(zip(reference_frames, actual_frames)):
        if want["natoms"] != got["natoms"]:
            raise baseline.BaselineError(f"{label}: atom count mismatch at frame {frame_index}")
        offset = 0
        for name, kind, width in want["properties"]:
            for atom_index, (want_row, got_row) in enumerate(zip(want["rows"], got["rows"])):
                want_tokens = want_row[offset:offset + width]
                got_tokens = got_row[offset:offset + width]
                item = f"{label}:frame[{frame_index}]:atom[{atom_index}]:{name}"
                if kind == "S":
                    if want_tokens != got_tokens:
                        raise baseline.BaselineError(f"species/order mismatch at {item}")
                else:
                    category = XYZ_PROPERTY_CATEGORIES.get(name)
                    if category is None:
                        raise baseline.BaselineError(f"no tolerance category for {name}")
                    for value_index, (a, b) in enumerate(zip(want_tokens, got_tokens)):
                        collector.observe(category, float(a), float(b), f"{item}[{value_index}]")
            offset += width


def compare_case_outputs(
    case: DomainCase,
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
            compare_xyz_atom_rows(reference, actual, collector, f"{label}/{filename}")
        else:
            raise baseline.BaselineError(f"{label}: unexpected output kind {filename}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=PROJECT_ROOT / "build" / "dmg-md")
    parser.add_argument("--mpiexec", type=Path, default=Path("mpiexec"))
    parser.add_argument("--ranks", type=integer_list, default=[2, 4])
    parser.add_argument(
        "--backends", type=comma_list, default=["HostStaged", "CudaAware"],
        help="comma-separated HostStaged,CudaAware",
    )
    parser.add_argument(
        "--devices", type=str,
        help="comma-separated CUDA device IDs/UUIDs; defaults to CUDA_VISIBLE_DEVICES",
    )
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--keep-work", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executable = args.candidate.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise baseline.BaselineError(f"candidate is not executable: {executable}")
    tolerances = baseline.load_manifest()["tolerances"]
    potential = BASELINE_DIR / "inputs" / "potentials" / "nep_C.txt"
    carbon_potential = potential.read_text(encoding="utf-8")
    water_potential = (
        BASELINE_DIR / "inputs" / "potentials" / "nep_water.txt"
    ).read_text(encoding="utf-8")
    bto_potential = (
        BASELINE_DIR / "inputs" / "potentials" / "nep_BaTiO3_zbl.txt"
    ).read_text(encoding="utf-8")

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

    lattice = DomainCase(
        name="lattice",
        model=lattice_model(),
        run=LATTICE_RUN,
        trajectory_files=["trajectory.xyz"],
        outputs=["thermo.out", "trajectory.xyz", "restart.xyz", "neighbor.out"],
        natoms=576,
        unwrapped=True,
        adaptive=True,
        correction_interval=10,
        restart_interval=10,
    )
    chain = DomainCase(
        name="chain",
        model=CHAIN_MODEL,
        run=CHAIN_RUN,
        trajectory_files=["trajectory.xyz"],
        outputs=["thermo.out", "trajectory.xyz", "neighbor.out"],
        natoms=3,
    )
    empty_local = DomainCase(
        name="empty_local",
        model=EMPTY_LOCAL_MODEL,
        run=EMPTY_LOCAL_RUN,
        trajectory_files=[],
        outputs=["neighbor.out"],
        natoms=2,
        snapshot_every_step=False,
        communication_interval=1000,
    )
    crossings = DomainCase(
        name="crossings",
        model=CROSSINGS_MODEL,
        run=CROSSINGS_RUN,
        trajectory_files=["trajectory.xyz", "nvt.xyz"],
        outputs=["thermo.out", "trajectory.xyz", "nvt.xyz", "restart.xyz", "neighbor.out"],
        natoms=8,
        unwrapped=True,
        restart_interval=4,
    )
    nep5 = DomainCase(
        name="nep5",
        model=CHAIN_MODEL,
        run=VARIANT_RUN,
        trajectory_files=["static.xyz"],
        outputs=["thermo.out", "static.xyz", "neighbor.out"],
        natoms=3,
        potential_text=long_nve_common.POTENTIAL_TRANSFORMS["nep5_equivalent"](
            carbon_potential),
    )
    typewise_text = long_nve_common.POTENTIAL_TRANSFORMS[
        "typewise_cutoff_equivalent"
    ](water_potential)
    typewise_text = typewise_text.replace(
        "cutoff 6.0 4.0 6.0 4.0 251 80",
        "cutoff 6.0 4.0 5.8 3.8 251 80",
    )
    if "cutoff 6.0 4.0 5.8 3.8 251 80" not in typewise_text:
        raise baseline.BaselineError("failed to construct mixed typewise-cutoff fixture")
    typewise = DomainCase(
        name="typewise",
        model=WATER_VARIANT_MODEL,
        run=VARIANT_RUN,
        trajectory_files=["static.xyz"],
        outputs=["thermo.out", "static.xyz", "neighbor.out"],
        natoms=6,
        potential_text=typewise_text,
        d_dep=7.0,
        d_coord=14.0,
    )
    flexible_zbl = DomainCase(
        name="flexible_zbl",
        model=BTO_VARIANT_MODEL,
        run=VARIANT_RUN,
        trajectory_files=["static.xyz"],
        outputs=["thermo.out", "static.xyz", "neighbor.out"],
        natoms=5,
        potential_text=long_nve_common.POTENTIAL_TRANSFORMS["flexible_zbl_equivalent"](
            bto_potential),
        d_dep=7.0,
        d_coord=14.0,
    )
    typewise_zbl = DomainCase(
        name="typewise_zbl",
        model=BTO_VARIANT_MODEL,
        run=VARIANT_RUN,
        trajectory_files=["static.xyz"],
        outputs=["thermo.out", "static.xyz", "neighbor.out"],
        natoms=5,
        potential_text=long_nve_common.POTENTIAL_TRANSFORMS["typewise_zbl_cutoff"](
            bto_potential),
        d_dep=7.0,
        d_coord=14.0,
    )
    variant_cases = [nep5, typewise, flexible_zbl, typewise_zbl]

    work_root = Path(tempfile.mkdtemp(prefix="dmgmd-domain-"))
    succeeded = False
    try:
        # P=1 references (the numerical oracle) come first.
        references: Dict[str, Path] = {}
        for case in (lattice, chain, empty_local, crossings, *variant_cases):
            reference_root = work_root / case.name / "reference-1r"
            stage_case(case, reference_root, potential)
            execution = execute(
                executable, mpiexec, reference_root, 1, "HostStaged", devices, args.timeout,
                case.communication_interval,
            )
            if execution["returncode"] != 0 or execution["stderr"]:
                raise baseline.BaselineError(
                    f"{case.name}: P=1 oracle failed: {execution['stderr'][-1500:]}")
            domain = [line for line in execution["stdout"].splitlines()
                      if line.startswith("DMGMD_DOMAIN ")]
            if len(domain) != 1 or key_values(domain[0]).get("mode") != "m1-fallback":
                raise baseline.BaselineError(
                    f"{case.name}: P=1 must stay on the M1 path (mode=m1-fallback)")
            references[case.name] = reference_root

        # Default runtime mode must be quiet on the hot path: no per-layout,
        # per-migration or per-step communication records. Rank-local segment
        # summaries remain available without enabling diagnostics.
        quiet_ranks = min(args.ranks)
        quiet_dir = work_root / "default-quiet" / f"ranks-{quiet_ranks}"
        stage_case(chain, quiet_dir, potential)
        quiet = execute(
            executable, mpiexec, quiet_dir, quiet_ranks, "HostStaged", devices,
            args.timeout, communication_interval=None, domain_diagnostics=False,
        )
        if quiet["returncode"] != 0 or quiet["stderr"]:
            raise baseline.BaselineError(
                f"default quiet-mode run failed: {quiet['stderr'][-1500:]}")
        quiet_lines = quiet["stdout"].splitlines()
        if any(line.startswith(("DMGMD_DOMAIN_LAYOUT ", "DMGMD_DOMAIN_MIGRATION ",
                                "DMGMD_COMM step=", "DMGMD_DOMAIN_COMM "))
               for line in quiet_lines):
            raise baseline.BaselineError(
                "default quiet mode emitted a hot-path diagnostic record")
        settings = [line for line in quiet_lines
                    if line.startswith("DMGMD_DOMAIN_DIAGNOSTICS ")]
        summaries = [line for line in quiet_lines
                     if line.startswith("DMGMD_DOMAIN_SUMMARY ")]
        if (len(settings) != 1 or key_values(settings[0]).get("detailed") != "off"
                or len(summaries) != quiet_ranks):
            raise baseline.BaselineError(
                "default quiet mode lacks its setting record or rank summaries")

        # The restart written by the deterministic P=1 crossings run feeds the
        # cross-rank resume matrix.
        restart_model = (references["crossings"] / "restart.xyz").read_text(encoding="utf-8")
        resume = DomainCase(
            name="resume",
            model=restart_model,
            run=RESUME_RUN,
            trajectory_files=["resumed.xyz"],
            outputs=["thermo.out", "resumed.xyz", "neighbor.out"],
            natoms=8,
        )
        resume_reference = work_root / "resume" / "reference-1r"
        stage_case(resume, resume_reference, potential)
        execution = execute(
            executable, mpiexec, resume_reference, 1, "HostStaged", devices, args.timeout,
            resume.communication_interval,
        )
        if execution["returncode"] != 0 or execution["stderr"]:
            raise baseline.BaselineError("resume: P=1 oracle failed")
        references["resume"] = resume_reference

        cases: List[DomainCase] = [
            lattice, chain, empty_local, crossings, resume, *variant_cases]
        for case in cases:
            for backend in backends:
                for ranks in args.ranks:
                    stage_dir = work_root / case.name / f"{backend}-{ranks}"
                    stage_case(case, stage_dir, potential)
                    execution = execute(
                        executable, mpiexec, stage_dir, ranks, backend, devices, args.timeout,
                        case.communication_interval,
                    )
                    index = verify_domain_records(case, stage_dir, ranks, execution)
                    verify_transitions_against_trajectory(case, stage_dir, index, ranks)
                    verify_communication(case, stage_dir, index, ranks, backend)
                    compare_case_outputs(
                        case, references[case.name], stage_dir, tolerances,
                        f"{case.name}/{backend}/{ranks}",
                    )
                    epochs = max(
                        len(index.layouts.get(rank, [])) for rank in range(ranks))
                    migrated = len(index.migration_steps())
                    layout_uploads = max(
                        sum(int(record["layout_uploads"])
                            for record in index.summaries.get(rank, []))
                        for rank in range(ranks))
                    capacity_growths = max(
                        sum(int(record["capacity_growth_events"])
                            for record in index.summaries.get(rank, []))
                        for rank in range(ranks))
                    gpu_allocations = max(
                        sum(int(record["gpu_allocations"])
                            for record in index.summaries.get(rank, []))
                        for rank in range(ranks))
                    print(
                        f"PASS {case.name:10s} {backend:10s} ranks={ranks}: "
                        f"layout_epochs={epochs} migration_steps={migrated} "
                        f"layout_uploads_max={layout_uploads} "
                        f"capacity_growth_events_max={capacity_growths} "
                        f"gpu_allocations_max={gpu_allocations}"
                    )
        print(
            f"PASS: M2a domain matrix ranks={args.ranks} backends={backends} "
            "(mode proof, layouts, transitions, oracle differential and "
            "exact byte accounting all verified)"
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
