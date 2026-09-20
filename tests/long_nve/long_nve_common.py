#!/usr/bin/env python3
"""Deterministic fixtures and analysis helpers for the long-NVE acceptance suite."""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SUITE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SUITE_DIR.parents[1]
BASELINE_DIR = PROJECT_ROOT / "tests" / "baseline"
sys.path.insert(0, str(BASELINE_DIR))
import run_baselines as baseline  # noqa: E402


MANIFEST_PATH = SUITE_DIR / "manifest.json"
STAGE_COMPLETE_NAME = ".dmgmd-stage-complete.json"
STAGE_FAILURE_NAME = ".dmgmd-stage-failure.json"
CHECKPOINT_ENVIRONMENT = (
    "CUDA_DEVICE_ORDER",
    "CUDA_VISIBLE_DEVICES",
    "DMGMD_COMM_BACKEND",
    "DMGMD_COMM_LOG_INTERVAL",
)
DEFAULT_STAGE_RETRIES = 1
StageEventSink = Callable[[str, Mapping[str, Any]], None]
_stage_event_sink: Optional[StageEventSink] = None


def set_stage_event_sink(sink: Optional[StageEventSink]) -> None:
    """Install a best-effort observer for human-facing stage progress."""
    global _stage_event_sink
    _stage_event_sink = sink


def _notify_stage_event(status: str, **fields: Any) -> None:
    global _stage_event_sink
    if _stage_event_sink is None:
        return
    try:
        _stage_event_sink(status, fields)
    except Exception as error:  # The display must never invalidate a numerical test.
        print(
            "LONG_NVE_UI status=disabled "
            f"reason={type(error).__name__}:{error}",
            file=sys.stderr,
            flush=True,
        )
        _stage_event_sink = None


def load_manifest() -> Dict[str, Any]:
    with MANIFEST_PATH.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    cases = manifest["cases"]
    for name, case in list(cases.items()):
        base_name = case.get("base_case")
        if base_name is None:
            continue
        if base_name not in cases or cases[base_name].get("base_case") is not None:
            raise baseline.BaselineError(
                f"{name}: base_case must name a non-derived manifest case"
            )
        merged = dict(cases[base_name])
        merged.update(case)
        merged["name"] = name
        cases[name] = merged
    return manifest


def profile_case(
    manifest: Mapping[str, Any], profile_name: str, case_name: str
) -> Dict[str, Any]:
    """Return one case with the selected profile's deterministic geometry.

    Derived compatibility cases share their base case's model geometry while
    retaining their own potential transform and coverage metadata.  Keeping
    the geometry selection here prevents the larger nightly/release boxes from
    turning the single-rank smoke profile into a multi-gigabyte I/O job.
    """
    profiles = manifest.get("profiles")
    cases = manifest.get("cases")
    geometries = manifest.get("case_geometries")
    if not isinstance(profiles, Mapping) or profile_name not in profiles:
        raise baseline.BaselineError(f"unknown long-NVE profile {profile_name}")
    if not isinstance(cases, Mapping) or case_name not in cases:
        raise baseline.BaselineError(f"unknown long-NVE case {case_name}")

    profile = profiles[profile_name]
    case = dict(cases[case_name])
    geometry_name = profile.get("case_geometry", "base")
    if geometry_name != "base":
        if not isinstance(geometries, Mapping) or geometry_name not in geometries:
            raise baseline.BaselineError(
                f"{profile_name}: unknown long-NVE case geometry {geometry_name}"
            )
        base_name = str(case.get("base_case", case_name))
        geometry = geometries[geometry_name]
        override = geometry.get(base_name) if isinstance(geometry, Mapping) else None
        if not isinstance(override, Mapping):
            raise baseline.BaselineError(
                f"{profile_name}/{case_name}: no geometry override for base case {base_name}"
            )
        case.update(override)
    case["name"] = case_name
    return case


def stable_unit(seed: int, atom: int, component: int, stream: str) -> float:
    payload = f"dmgmd-long-nve-v1:{stream}:{seed}:{atom}:{component}".encode()
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return (integer + 0.5) / float(1 << 64)


def stable_signed(seed: int, atom: int, component: int, stream: str) -> float:
    return 2.0 * stable_unit(seed, atom, component, stream) - 1.0


def rotation_matrices() -> List[Tuple[Tuple[int, int, int], ...]]:
    matrices: List[Tuple[Tuple[int, int, int], ...]] = []
    for permutation in itertools.permutations(range(3)):
        inversions = sum(
            permutation[left] > permutation[right]
            for left in range(3)
            for right in range(left + 1, 3)
        )
        permutation_sign = -1 if inversions % 2 else 1
        for signs in itertools.product((-1, 1), repeat=3):
            if permutation_sign * signs[0] * signs[1] * signs[2] != 1:
                continue
            rows = []
            for row, axis in enumerate(permutation):
                values = [0, 0, 0]
                values[axis] = signs[row]
                rows.append(tuple(values))
            matrices.append(tuple(rows))
    return matrices


ROTATIONS = rotation_matrices()


def rotate(vector: Sequence[float], matrix: Sequence[Sequence[int]]) -> Tuple[float, float, float]:
    return tuple(sum(row[index] * vector[index] for index in range(3)) for row in matrix)  # type: ignore[return-value]


Atom = Tuple[str, float, float, float, float]


def diamond_atoms(case: Mapping[str, Any], seed: int) -> Tuple[List[Atom], List[float]]:
    del seed
    nx, ny, nz = case["cells"]
    lattice_constant = float(case["lattice_constant_A"])
    mass = float(case["masses_amu"]["C"])
    basis = (
        (0.0, 0.0, 0.0),
        (0.25, 0.25, 0.25),
        (0.5, 0.5, 0.0),
        (0.75, 0.75, 0.25),
        (0.5, 0.0, 0.5),
        (0.75, 0.25, 0.75),
        (0.0, 0.5, 0.5),
        (0.25, 0.75, 0.75),
    )
    atoms: List[Atom] = []
    for ix in range(nx):
        for iy in range(ny):
            for iz in range(nz):
                for sx, sy, sz in basis:
                    atoms.append(
                        (
                            "C",
                            (ix + sx) * lattice_constant,
                            (iy + sy) * lattice_constant,
                            (iz + sz) * lattice_constant,
                            mass,
                        )
                    )
    return atoms, [nx * lattice_constant, ny * lattice_constant, nz * lattice_constant]


def water_atoms(case: Mapping[str, Any], seed: int) -> Tuple[List[Atom], List[float]]:
    nx, ny, nz = case["cells"]
    spacing = float(case["spacing_A"])
    jitter = float(case["oxygen_jitter_A"])
    masses = case["masses_amu"]
    oh1 = (0.9572, 0.0, 0.0)
    oh2 = (-0.2399872, 0.9266272, 0.0)
    lengths = [nx * spacing, ny * spacing, nz * spacing]
    atoms: List[Atom] = []
    molecule = 0
    for ix in range(nx):
        for iy in range(ny):
            for iz in range(nz):
                cell = (ix, iy, iz)
                oxygen = [
                    (cell[axis] + 0.5) * spacing
                    + jitter * stable_signed(seed, molecule, axis, "water-pos")
                    for axis in range(3)
                ]
                rotation = ROTATIONS[
                    int(stable_unit(seed, molecule, 0, "water-rot") * len(ROTATIONS))
                    % len(ROTATIONS)
                ]
                first = rotate(oh1, rotation)
                second = rotate(oh2, rotation)
                atoms.append(("O", *oxygen, float(masses["O"])))
                for displacement in (first, second):
                    position = [
                        (oxygen[axis] + displacement[axis]) % lengths[axis] for axis in range(3)
                    ]
                    atoms.append(("H", *position, float(masses["H"])))
                molecule += 1
    return atoms, lengths


def barium_titanate_atoms(
    case: Mapping[str, Any], seed: int
) -> Tuple[List[Atom], List[float]]:
    nx, ny, nz = case["cells"]
    lattice_constant = float(case["lattice_constant_A"])
    jitter = float(case["position_jitter_A"])
    masses = case["masses_amu"]
    basis = (
        ("Ba", 0.0, 0.0, 0.0),
        ("Ti", 0.5, 0.5, 0.5),
        ("O", 0.5, 0.5, 0.0),
        ("O", 0.5, 0.0, 0.5),
        ("O", 0.0, 0.5, 0.5),
    )
    lengths = [nx * lattice_constant, ny * lattice_constant, nz * lattice_constant]
    central_cell = (nx // 2, ny // 2, nz // 2)
    collision_axis = seed % 3
    collision_sign = -1.0 if (seed // 3) % 2 else 1.0
    collision_distance = float(case["zbl_pair_distance_A"])
    atoms: List[Atom] = []
    atom_index = 0
    for ix in range(nx):
        for iy in range(ny):
            for iz in range(nz):
                cell_origin = [ix * lattice_constant, iy * lattice_constant, iz * lattice_constant]
                for species, sx, sy, sz in basis:
                    fractional = (sx, sy, sz)
                    position = [
                        cell_origin[axis]
                        + fractional[axis] * lattice_constant
                        + jitter * stable_signed(seed, atom_index, axis, "bto-pos")
                        for axis in range(3)
                    ]
                    if (ix, iy, iz) == central_cell and species == "Ti":
                        position = list(cell_origin)
                        position[collision_axis] += collision_sign * collision_distance
                    position = [position[axis] % lengths[axis] for axis in range(3)]
                    atoms.append((species, *position, float(masses[species])))
                    atom_index += 1
    return atoms, lengths


GENERATORS = {
    "diamond": diamond_atoms,
    "dense_water": water_atoms,
    "barium_titanate_zbl": barium_titanate_atoms,
}


def velocities(
    atoms: Sequence[Atom], seed: int, scale: float
) -> List[Tuple[float, float, float]]:
    result = [
        [
            scale * stable_signed(seed, atom, axis, "velocity") / math.sqrt(item[4])
            for axis in range(3)
        ]
        for atom, item in enumerate(atoms)
    ]
    total_mass = sum(atom[4] for atom in atoms)
    center = [
        sum(atom[4] * result[index][axis] for index, atom in enumerate(atoms)) / total_mass
        for axis in range(3)
    ]
    for velocity in result:
        for axis in range(3):
            velocity[axis] -= center[axis]
    return [tuple(value) for value in result]  # type: ignore[return-value]


def generate_model(case: Mapping[str, Any], seed: int) -> str:
    generator_name = case["generator"]
    if generator_name not in GENERATORS:
        raise baseline.BaselineError(f"unknown long-NVE generator {generator_name}")
    atoms, lengths = GENERATORS[generator_name](case, seed)
    expected_atoms = int(case["atoms"])
    if len(atoms) != expected_atoms:
        raise baseline.BaselineError(
            f"{case['name']}: generated {len(atoms)} atoms, expected {expected_atoms}"
        )
    atom_velocities = velocities(atoms, seed, float(case["velocity_scale_A_fs_sqrt_amu"]))
    lines = [
        str(len(atoms)),
        'pbc="T T T" '
        f'Lattice="{lengths[0]:.12e} 0 0 0 {lengths[1]:.12e} 0 0 0 {lengths[2]:.12e}" '
        'Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3',
    ]
    for atom, velocity in zip(atoms, atom_velocities):
        species, x, y, z, mass = atom
        lines.append(
            f"{species} {x:.12e} {y:.12e} {z:.12e} {mass:.12e} "
            f"{velocity[0]:.12e} {velocity[1]:.12e} {velocity[2]:.12e}"
        )
    return "\n".join(lines) + "\n"


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def validate_generated_model(case: Mapping[str, Any], seed: int, text: str) -> None:
    expected = case["model_sha256"].get(str(seed))
    actual = text_sha256(text)
    if expected is None:
        raise baseline.BaselineError(f"{case['name']}/seed-{seed}: generated model hash is not locked")
    if actual != expected:
        raise baseline.BaselineError(
            f"{case['name']}/seed-{seed}: generated model hash mismatch; "
            f"expected {expected}, got {actual}"
        )


def _nep5_equivalent(text: str) -> str:
    """Convert an ordinary NEP4 file to an exactly equivalent NEP5 layout."""
    lines = text.splitlines()
    header = lines[0].split()
    if header[0] not in ("nep4", "nep4_zbl"):
        raise baseline.BaselineError("nep5_equivalent requires an NEP4 source potential")
    type_count = int(header[1])
    header[0] = "nep5_zbl" if header[0] == "nep4_zbl" else "nep5"
    lines[0] = " ".join(header)

    def keyword_line(keyword: str) -> int:
        matches = [index for index, line in enumerate(lines) if line.split()[:1] == [keyword]]
        if len(matches) != 1:
            raise baseline.BaselineError(
                f"NEP potential should contain one {keyword} line, found {len(matches)}"
            )
        return matches[0]

    n_max = lines[keyword_line("n_max")].split()
    l_max = lines[keyword_line("l_max")].split()
    ann_index = keyword_line("ANN")
    ann = lines[ann_index].split()
    n_max_radial = int(n_max[1])
    n_max_angular = int(n_max[2])
    enabled_invariants = sum(int(value) != 0 for value in l_max[2:8])
    descriptor_dimension = (
        n_max_radial + 1 + (n_max_angular + 1) * (int(l_max[1]) + enabled_invariants)
    )
    neurons = int(ann[1])
    per_type_parameters = (descriptor_dimension + 2) * neurons
    cursor = ann_index + 1
    converted = lines[:cursor]
    for _ in range(type_count):
        end = cursor + per_type_parameters
        if end > len(lines):
            raise baseline.BaselineError("NEP4 ANN parameter block is truncated")
        converted.extend(lines[cursor:end])
        # NEP5 subtracts this type-specific output bias in addition to the
        # shared NEP4 bias. Zero therefore preserves the original potential.
        converted.append("  0.0000000e+00")
        cursor = end
    converted.extend(lines[cursor:])
    return "\n".join(converted) + "\n"


def _typewise_cutoff_equivalent(text: str) -> str:
    """Expand a uniform radial/angular cutoff into the typewise syntax."""
    lines = text.splitlines()
    type_count = int(lines[0].split()[1])
    matches = [index for index, line in enumerate(lines) if line.split()[:1] == ["cutoff"]]
    if len(matches) != 1:
        raise baseline.BaselineError("potential should contain exactly one cutoff line")
    index = matches[0]
    tokens = lines[index].split()
    if len(tokens) != 5 or type_count < 2:
        raise baseline.BaselineError(
            "typewise_cutoff_equivalent requires a multi-type uniform cutoff"
        )
    radial, angular, maximum_radial, maximum_angular = tokens[1:]
    values = [value for _ in range(type_count) for value in (radial, angular)]
    lines[index] = " ".join(("cutoff", *values, maximum_radial, maximum_angular))
    return "\n".join(lines) + "\n"


def _flexible_zbl_equivalent(text: str) -> str:
    """Replace fixed universal ZBL by the equivalent flexible parameters."""
    lines = text.splitlines()
    header = lines[0].split()
    if not header[0].endswith("_zbl"):
        raise baseline.BaselineError("flexible_zbl_equivalent requires an NEP-ZBL potential")
    type_count = int(header[1])
    matches = [index for index, line in enumerate(lines) if line.split()[:1] == ["zbl"]]
    if len(matches) != 1:
        raise baseline.BaselineError("potential should contain exactly one zbl line")
    index = matches[0]
    tokens = lines[index].split()
    if len(tokens) != 3 or tokens[1:3] != ["0.75", "1.5"]:
        raise baseline.BaselineError(
            "flexible ZBL equivalence fixture expects the locked 0.75/1.5 cutoff"
        )
    lines[index] = "zbl 0 0"
    universal = (
        "0.75", "1.5", "0.18175", "3.1998", "0.50986", "0.94229",
        "0.28022", "0.4029", "0.02817", "0.20162",
    )
    for _ in range(type_count * (type_count + 1) // 2):
        lines.extend(f"  {value}" for value in universal)
    return "\n".join(lines) + "\n"


def _typewise_zbl_cutoff(text: str) -> str:
    """Enable the universal-ZBL covalent-radius cutoff branch."""
    lines = text.splitlines()
    matches = [index for index, line in enumerate(lines) if line.split()[:1] == ["zbl"]]
    if len(matches) != 1:
        raise baseline.BaselineError("potential should contain exactly one zbl line")
    index = matches[0]
    tokens = lines[index].split()
    if len(tokens) != 3 or tokens[1:3] == ["0", "0"]:
        raise baseline.BaselineError("typewise ZBL cutoff requires fixed universal ZBL")
    lines[index] = " ".join((*tokens, "0.6"))
    return "\n".join(lines) + "\n"


POTENTIAL_TRANSFORMS = {
    "nep5_equivalent": _nep5_equivalent,
    "typewise_cutoff_equivalent": _typewise_cutoff_equivalent,
    "flexible_zbl_equivalent": _flexible_zbl_equivalent,
    "typewise_zbl_cutoff": _typewise_zbl_cutoff,
}


def generate_potential(case: Mapping[str, Any]) -> str:
    path = PROJECT_ROOT / case["potential"]
    text = path.read_text(encoding="utf-8")
    transforms = case.get("potential_transforms", [])
    if isinstance(transforms, str):
        transforms = [transforms]
    for name in transforms:
        if name not in POTENTIAL_TRANSFORMS:
            raise baseline.BaselineError(f"unknown potential transform {name}")
        text = POTENTIAL_TRANSFORMS[name](text)
    return text


def validate_generated_potential(case: Mapping[str, Any], text: str) -> None:
    actual = text_sha256(text)
    if actual != case["potential_sha256"]:
        raise baseline.BaselineError(
            f"{case['name']}: generated potential hash mismatch; "
            f"expected {case['potential_sha256']}, got {actual}"
        )


def static_run(potential_name: str = "nep.txt") -> str:
    return "\n".join(
        (
            f"potential {potential_name}",
            "time_step 0",
            "ensemble nve",
            "dump_thermo 1",
            "dump_xyz 1 static.xyz precision double mass velocity force potential virial",
            "run 1",
            "",
        )
    )


def short_run(time_step_fs: float, steps: int, potential_name: str = "nep.txt") -> str:
    return "\n".join(
        (
            f"potential {potential_name}",
            f"time_step {time_step_fs:.12g}",
            "ensemble nve",
            "dump_thermo 1",
            "dump_xyz 1 short.xyz precision double mass velocity force potential virial",
            f"run {steps}",
            "",
        )
    )


def long_run(
    time_step_fs: float,
    steps: int,
    thermo_interval: int,
    trajectory_interval: int,
    potential_name: str = "nep.txt",
) -> str:
    if steps % thermo_interval or steps % trajectory_interval:
        raise baseline.BaselineError("long-NVE output intervals must divide the step count")
    return "\n".join(
        (
            f"potential {potential_name}",
            f"time_step {time_step_fs:.12g}",
            "ensemble nve",
            f"dump_thermo {thermo_interval}",
            "dump_xyz "
            f"{trajectory_interval} trajectory.xyz precision double mass velocity force "
            "potential unwrapped_position virial",
            f"dump_restart {steps}",
            f"run {steps}",
            "",
        )
    )


def nvt_run(
    time_step_fs: float,
    equilibration_steps: int,
    sampling_steps: int,
    thermo_interval: int,
    trajectory_interval: int,
    temperature: float,
    coupling: float,
    potential_name: str = "nep.txt",
) -> str:
    if equilibration_steps <= 0 or sampling_steps <= 0:
        raise baseline.BaselineError("NVT equilibration and sampling steps must be positive")
    if sampling_steps % thermo_interval or sampling_steps % trajectory_interval:
        raise baseline.BaselineError("NVT output intervals must divide the sampling steps")
    return "\n".join(
        (
            f"potential {potential_name}",
            f"time_step {time_step_fs:.12g}",
            f"ensemble nvt_ber {temperature:.12g} {temperature:.12g} {coupling:.12g}",
            f"run {equilibration_steps}",
            f"ensemble nvt_ber {temperature:.12g} {temperature:.12g} {coupling:.12g}",
            f"dump_thermo {thermo_interval}",
            f"dump_xyz {trajectory_interval} nvt.xyz precision double unwrapped_position",
            f"run {sampling_steps}",
            "",
        )
    )


def restart_run(
    time_step_fs: float,
    steps: int,
    thermo_interval: int,
    output_name: str,
    potential_name: str = "nep.txt",
) -> str:
    if steps % thermo_interval:
        raise baseline.BaselineError("restart thermo interval must divide the step count")
    return "\n".join(
        (
            f"potential {potential_name}",
            f"time_step {time_step_fs:.12g}",
            "ensemble nve",
            f"dump_thermo {thermo_interval}",
            f"dump_xyz {steps} {output_name} precision double mass velocity force potential virial",
            f"dump_restart {steps}",
            f"run {steps}",
            "",
        )
    )


def execute_md(
    executable: Path,
    launcher: Sequence[str],
    model_text: str,
    run_text: str,
    potential_text: str,
    stage_dir: Path,
    env: Mapping[str, str],
    timeout: int,
    required_outputs: Iterable[str],
    resume: bool = False,
    adopt_existing: bool = False,
    retries: int = DEFAULT_STAGE_RETRIES,
) -> Path:
    if retries < 0:
        raise ValueError("retries must be non-negative")
    required_outputs = tuple(required_outputs)
    launcher_executable_sha256 = None
    if launcher:
        launcher_path = shutil.which(launcher[0])
        if launcher_path is not None and Path(launcher_path).is_file():
            launcher_executable_sha256 = baseline.sha256(Path(launcher_path))
    signature = {
        "executable": str(executable.resolve()),
        "executable_sha256": baseline.sha256(executable),
        "launcher": list(launcher),
        "launcher_executable_sha256": launcher_executable_sha256,
        "input_sha256": {
            "model.xyz": text_sha256(model_text),
            "run.in": text_sha256(run_text),
            "nep.txt": text_sha256(potential_text),
        },
        "environment": {name: env.get(name) for name in CHECKPOINT_ENVIRONMENT},
        "required_outputs": sorted(required_outputs),
    }

    def write_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(path)

    def outputs_are_complete(checkpoint: Mapping[str, Any]) -> bool:
        if checkpoint.get("signature") != signature:
            return False
        output_hashes = checkpoint.get("output_sha256")
        if not isinstance(output_hashes, dict):
            return False
        for name in required_outputs:
            path = stage_dir / name
            if not path.is_file() or output_hashes.get(name) != baseline.sha256(path):
                return False
        return True

    def adopt_legacy_stage() -> bool:
        expected_inputs = {
            "model.xyz": model_text,
            "run.in": run_text,
            "nep.txt": potential_text,
        }
        if any(
            not (stage_dir / name).is_file()
            or (stage_dir / name).read_text(encoding="utf-8") != text
            for name, text in expected_inputs.items()
        ):
            return False
        if any(not (stage_dir / name).is_file() for name in required_outputs):
            return False
        stdout_path = stage_dir / "execution.stdout"
        stderr_path = stage_dir / "execution.stderr"
        if not stdout_path.is_file() or not stderr_path.is_file():
            return False
        diagnostic = stderr_path.read_text(encoding="utf-8").lower()
        failure_fragments = (
            "out of memory",
            "exited with non-zero status",
            "cuda error",
            "error code:",
        )
        if any(fragment in diagnostic for fragment in failure_fragments):
            return False
        write_json(
            stage_dir / STAGE_COMPLETE_NAME,
            {
                "schema_version": 1,
                "status": "complete",
                "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "provenance": "adopted-existing-unverified-executable",
                "signature": signature,
                "output_sha256": {
                    name: baseline.sha256(stage_dir / name) for name in required_outputs
                },
            },
        )
        return True

    if stage_dir.exists() and resume:
        complete_path = stage_dir / STAGE_COMPLETE_NAME
        if complete_path.is_file():
            try:
                checkpoint = json.loads(complete_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                checkpoint = {}
            if outputs_are_complete(checkpoint):
                print(f"LONG_NVE_STAGE status=reused path={stage_dir}", flush=True)
                _notify_stage_event("reused", path=stage_dir)
                return stage_dir
        elif adopt_existing and adopt_legacy_stage():
            print(
                "LONG_NVE_STAGE status=adopted-existing "
                f"path={stage_dir} executable_provenance=unverified",
                flush=True,
            )
            _notify_stage_event(
                "adopted-existing",
                path=stage_dir,
                executable_provenance="unverified",
            )
            return stage_dir

        suffix = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archived = stage_dir.with_name(f"{stage_dir.name}.failed-{suffix}")
        stage_dir.rename(archived)
        print(
            f"LONG_NVE_STAGE status=archived-incomplete path={stage_dir} archived={archived}",
            flush=True,
        )
        _notify_stage_event("archived-incomplete", path=stage_dir, archived=archived)

    command = [*launcher, str(executable)]
    max_attempts = retries + 1
    retry_failures: List[Dict[str, Any]] = []

    def prepare_stage() -> Dict[str, str]:
        stage_dir.mkdir(parents=True, exist_ok=False)
        (stage_dir / "model.xyz").write_text(model_text, encoding="utf-8")
        (stage_dir / "run.in").write_text(run_text, encoding="utf-8")
        (stage_dir / "nep.txt").write_text(potential_text, encoding="utf-8")
        return {
            filename: baseline.sha256(stage_dir / filename)
            for filename in ("model.xyz", "run.in", "nep.txt")
        }

    for attempt in range(1, max_attempts + 1):
        input_hashes = prepare_stage()
        print(
            f"LONG_NVE_STAGE status=running attempt={attempt} "
            f"max_attempts={max_attempts} path={stage_dir}",
            flush=True,
        )
        _notify_stage_event(
            "running", attempt=attempt, max_attempts=max_attempts, path=stage_dir
        )
        started = time.monotonic()
        failure: Optional[str] = None
        failure_details: Dict[str, Any] = {}
        failure_exception: Optional[BaseException] = None
        stdout = ""
        stderr = ""
        try:
            result = subprocess.run(
                command,
                cwd=stage_dir,
                env=dict(env),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            stdout = result.stdout
            stderr = result.stderr
            if result.returncode != 0:
                diagnostic = (stdout + "\n" + stderr).lower()
                failure = (
                    "cuda-out-of-memory"
                    if "out of memory" in diagnostic
                    else "nonzero-exit"
                )
                failure_details["returncode"] = result.returncode
                failure_exception = baseline.BaselineError(
                    f"{stage_dir}: executable exited {result.returncode}\n"
                    f"stdout:\n{stdout}\nstderr:\n{stderr}"
                )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            failure = "timeout"
            failure_details["timeout_seconds"] = timeout
            failure_exception = error
        except OSError as error:
            failure = "launch-error"
            stderr = f"{type(error).__name__}: {error}\n"
            failure_exception = error

        (stage_dir / "execution.stdout").write_text(stdout, encoding="utf-8")
        (stage_dir / "execution.stderr").write_text(stderr, encoding="utf-8")
        if failure is None:
            try:
                baseline.stage_inputs_unchanged(stage_dir, input_hashes)
            except baseline.BaselineError as error:
                failure = "input-mutated"
                failure_exception = error
        if failure is None:
            missing = [name for name in required_outputs if not (stage_dir / name).is_file()]
            if missing:
                failure = "missing-output"
                failure_details["missing_outputs"] = missing
                failure_exception = baseline.BaselineError(
                    f"{stage_dir}: missing outputs {missing}"
                )

        elapsed = time.monotonic() - started
        if failure is None:
            write_json(
                stage_dir / STAGE_COMPLETE_NAME,
                {
                    "schema_version": 1,
                    "status": "complete",
                    "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "elapsed_seconds": elapsed,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "retry_failures": retry_failures,
                    "provenance": "executed",
                    "signature": signature,
                    "output_sha256": {
                        name: baseline.sha256(stage_dir / name) for name in required_outputs
                    },
                },
            )
            print(
                f"LONG_NVE_STAGE status=passed attempt={attempt} "
                f"max_attempts={max_attempts} elapsed_seconds={elapsed:.6f} "
                f"path={stage_dir}",
                flush=True,
            )
            _notify_stage_event(
                "passed",
                attempt=attempt,
                max_attempts=max_attempts,
                elapsed_seconds=elapsed,
                path=stage_dir,
            )
            return stage_dir

        failure_record = {
            "schema_version": 1,
            "status": "failed",
            "failed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "failure": failure,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "elapsed_seconds": elapsed,
            "signature": signature,
            "retry_failures": retry_failures,
            **failure_details,
        }
        write_json(stage_dir / STAGE_FAILURE_NAME, failure_record)
        if attempt < max_attempts:
            suffix = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            archived = stage_dir.with_name(
                f"{stage_dir.name}.failed-attempt-{attempt}-{suffix}"
            )
            stage_dir.rename(archived)
            retry_failures.append(
                {"attempt": attempt, "failure": failure, "path": str(archived)}
            )
            print(
                f"LONG_NVE_STAGE status=retrying failed_attempt={attempt} "
                f"next_attempt={attempt + 1} max_attempts={max_attempts} "
                f"reason={failure} archived={archived} path={stage_dir}",
                flush=True,
            )
            _notify_stage_event(
                "retrying",
                attempt=attempt,
                max_attempts=max_attempts,
                elapsed_seconds=elapsed,
                reason=failure,
                archived=archived,
                path=stage_dir,
            )
            continue

        print(
            f"LONG_NVE_STAGE status=failed attempt={attempt} "
            f"max_attempts={max_attempts} reason={failure} path={stage_dir}",
            flush=True,
        )
        _notify_stage_event(
            "failed",
            attempt=attempt,
            max_attempts=max_attempts,
            reason=failure,
            path=stage_dir,
        )
        assert failure_exception is not None
        raise failure_exception

    raise AssertionError("stage retry loop exhausted without a result")


def stage_checkpoint_provenance(stage_dir: Path) -> Optional[str]:
    checkpoint = stage_dir / STAGE_COMPLETE_NAME
    if not checkpoint.is_file():
        return None
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    provenance = payload.get("provenance")
    return provenance if isinstance(provenance, str) else None


def property_offsets(frame: Mapping[str, Any]) -> Dict[str, Tuple[int, int]]:
    result: Dict[str, Tuple[int, int]] = {}
    offset = 0
    for name, _, width in frame["properties"]:
        result[name] = (offset, width)
        offset += width
    return result


def frame_to_model(frame: Mapping[str, Any]) -> str:
    offsets = property_offsets(frame)
    for required in ("species", "pos", "mass", "vel"):
        if required not in offsets:
            raise baseline.BaselineError(f"replay frame has no {required} property")
    pbc = frame["fields"].get("pbc", "T T T")
    lattice = frame["fields"]["Lattice"]
    lines = [
        str(frame["natoms"]),
        f'pbc="{pbc}" Lattice="{lattice}" '
        'Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3',
    ]
    for row in frame["rows"]:
        values: List[str] = []
        for name in ("species", "pos", "mass", "vel"):
            offset, width = offsets[name]
            values.extend(row[offset:offset + width])
        lines.append(" ".join(values))
    return "\n".join(lines) + "\n"


def compare_configuration_frames(
    reference: Mapping[str, Any],
    actual: Mapping[str, Any],
    collector: baseline.DiffCollector,
    label: str,
) -> None:
    if reference["natoms"] != actual["natoms"]:
        raise baseline.BaselineError(f"{label}: atom count mismatch")
    if reference["fields"].get("pbc") != actual["fields"].get("pbc"):
        raise baseline.BaselineError(f"{label}: pbc mismatch")
    reference_lattice = [float(value) for value in reference["fields"]["Lattice"].split()]
    actual_lattice = [float(value) for value in actual["fields"]["Lattice"].split()]
    if len(reference_lattice) != len(actual_lattice):
        raise baseline.BaselineError(f"{label}: Lattice width mismatch")
    for component, (left, right) in enumerate(zip(reference_lattice, actual_lattice)):
        collector.observe("lattice", left, right, f"{label}:Lattice[{component}]")
    reference_offsets = property_offsets(reference)
    actual_offsets = property_offsets(actual)
    fields = {
        "species": None,
        "pos": "position",
        "mass": "mass",
        "vel": "velocity",
        "forces": "force",
        "energy_atom": "energy",
        "virial": "virial",
    }
    for name in fields:
        if name not in reference_offsets or name not in actual_offsets:
            raise baseline.BaselineError(f"{label}: replay property {name} is missing")
        if reference_offsets[name][1] != actual_offsets[name][1]:
            raise baseline.BaselineError(f"{label}: replay property {name} width mismatch")
    for atom, (reference_row, actual_row) in enumerate(zip(reference["rows"], actual["rows"])):
        for name, category in fields.items():
            reference_offset, width = reference_offsets[name]
            actual_offset, _ = actual_offsets[name]
            reference_values = reference_row[reference_offset:reference_offset + width]
            actual_values = actual_row[actual_offset:actual_offset + width]
            if category is None:
                if reference_values != actual_values:
                    raise baseline.BaselineError(f"{label}: species/order mismatch at atom {atom}")
                continue
            for component, (left, right) in enumerate(zip(reference_values, actual_values)):
                collector.observe(
                    category,
                    float(left),
                    float(right),
                    f"{label}:atom[{atom}]:{name}[{component}]",
                )


def compare_configuration_files(
    reference_path: Path,
    actual_path: Path,
    collector: baseline.DiffCollector,
    label: str,
) -> None:
    reference_frames = baseline.parse_xyz(reference_path)
    actual_frames = baseline.parse_xyz(actual_path)
    if len(reference_frames) != len(actual_frames):
        raise baseline.BaselineError(f"{label}: frame count mismatch")
    for index, (reference, actual) in enumerate(zip(reference_frames, actual_frames)):
        compare_configuration_frames(reference, actual, collector, f"{label}:frame[{index}]")


def linear_fit(values: Sequence[float], times: Sequence[float]) -> Tuple[float, float]:
    if len(values) != len(times) or len(values) < 2:
        raise baseline.BaselineError("linear fit requires matching vectors with at least two values")
    mean_time = sum(times) / len(times)
    mean_value = sum(values) / len(values)
    denominator = sum((value - mean_time) ** 2 for value in times)
    if denominator == 0.0:
        raise baseline.BaselineError("linear fit time span is zero")
    slope = sum(
        (time - mean_time) * (value - mean_value)
        for time, value in zip(times, values)
    ) / denominator
    return mean_value - slope * mean_time, slope


def nve_metrics(initial_thermo: Path, long_thermo: Path, atom_count: int) -> Dict[str, float]:
    initial_segments = baseline.parse_thermo(initial_thermo)
    long_segments = baseline.parse_thermo(long_thermo)
    if len(initial_segments) != 1 or len(initial_segments[0]["rows"]) != 1:
        raise baseline.BaselineError("initial NVE thermo must contain exactly one sample")
    if len(long_segments) != 1 or len(long_segments[0]["rows"]) < 2:
        raise baseline.BaselineError("long NVE thermo must contain at least two samples")
    initial_row = initial_segments[0]["rows"][0]
    rows = long_segments[0]["rows"]
    dt_output_fs = float(long_segments[0]["headers"][3].split()[2])
    energies = [(initial_row[1] + initial_row[2]) / atom_count]
    energies.extend((row[1] + row[2]) / atom_count for row in rows)
    if not all(math.isfinite(value) for value in energies):
        raise baseline.BaselineError("long NVE energy contains NaN or infinity")
    times = [0.0] + [dt_output_fs * (index + 1) for index in range(len(rows))]
    intercept, slope = linear_fit(energies, times)
    offsets = [value - energies[0] for value in energies]
    residuals = [value - (intercept + slope * time) for value, time in zip(energies, times)]
    adjacent_jumps = [abs(right - left) for left, right in zip(energies, energies[1:])]
    return {
        "samples": float(len(energies)),
        "duration_fs": times[-1],
        "initial_energy_per_atom_eV": energies[0],
        "final_offset_per_atom_eV": offsets[-1],
        "max_excursion_per_atom_eV": max(abs(value) for value in offsets),
        "drift_slope_eV_per_atom_fs": slope,
        "abs_drift_slope_eV_per_atom_fs": abs(slope),
        "detrended_rms_eV_per_atom": math.sqrt(
            sum(value * value for value in residuals) / len(residuals)
        ),
        "max_sample_jump_eV_per_atom": max(adjacent_jumps),
    }


def frame_vectors(frame: Mapping[str, Any], name: str) -> List[List[float]]:
    offsets = property_offsets(frame)
    if name not in offsets:
        raise baseline.BaselineError(f"frame has no {name} property")
    offset, width = offsets[name]
    return [[float(token) for token in row[offset:offset + width]] for row in frame["rows"]]


def frame_species(frame: Mapping[str, Any]) -> List[str]:
    offsets = property_offsets(frame)
    offset, width = offsets["species"]
    if width != 1:
        raise baseline.BaselineError("species property width is not one")
    return [row[offset] for row in frame["rows"]]


def trajectory_metrics(initial_xyz: Path, trajectory_xyz: Path) -> Dict[str, float]:
    initial = baseline.parse_xyz(initial_xyz)[0]
    frames = baseline.parse_xyz(trajectory_xyz)
    if not frames:
        raise baseline.BaselineError("long trajectory has no frames")
    initial_positions = frame_vectors(initial, "pos")
    initial_masses = [values[0] for values in frame_vectors(initial, "mass")]
    initial_velocities = frame_vectors(initial, "vel")
    initial_momentum = [
        sum(mass * velocity[axis] for mass, velocity in zip(initial_masses, initial_velocities))
        for axis in range(3)
    ]
    maximum_momentum_delta = 0.0
    final_msd = 0.0
    for frame in frames:
        masses = [values[0] for values in frame_vectors(frame, "mass")]
        velocities_now = frame_vectors(frame, "vel")
        momentum = [
            sum(mass * velocity[axis] for mass, velocity in zip(masses, velocities_now))
            for axis in range(3)
        ]
        delta = math.sqrt(sum((value - initial_momentum[axis]) ** 2 for axis, value in enumerate(momentum)))
        maximum_momentum_delta = max(maximum_momentum_delta, delta / frame["natoms"])
    final = frames[-1]
    final_positions = frame_vectors(
        final, "unwrapped_position" if "unwrapped_position" in property_offsets(final) else "pos"
    )
    final_msd = sum(
        sum((right[axis] - left[axis]) ** 2 for axis in range(3))
        for left, right in zip(initial_positions, final_positions)
    ) / final["natoms"]
    return {
        "max_momentum_delta_amu_A_fs_per_atom": maximum_momentum_delta,
        "final_msd_A2": final_msd,
    }


def canonical_pair(left: str, right: str) -> str:
    return "-".join(sorted((left, right)))


def pair_distance_distribution(
    frame: Mapping[str, Any], rmax: float, bins: int, maximum_centers: int = 512
) -> Dict[str, Any]:
    if bins <= 0 or rmax <= 0.0 or maximum_centers <= 0:
        raise baseline.BaselineError("invalid pair-distance histogram parameters")
    lattice = [float(token) for token in frame["fields"]["Lattice"].split()]
    if any(abs(lattice[index]) > 1.0e-12 for index in (1, 2, 3, 5, 6, 7)):
        raise baseline.BaselineError("long-NVE pair histogram requires an orthogonal box")
    lengths = [lattice[0], lattice[4], lattice[8]]
    positions = frame_vectors(frame, "pos")
    species = frame_species(frame)
    cell_counts = [max(1, int(length / rmax)) for length in lengths]
    cells: Dict[Tuple[int, int, int], List[int]] = {}
    for atom, position in enumerate(positions):
        key = tuple(
            int((position[axis] % lengths[axis]) / lengths[axis] * cell_counts[axis])
            % cell_counts[axis]
            for axis in range(3)
        )
        cells.setdefault(key, []).append(atom)
    histograms: Dict[str, List[int]] = {}
    totals: Dict[str, int] = {}
    minimum = math.inf
    selected_count = min(len(positions), maximum_centers)
    stride = max(1, len(positions) // selected_count)
    while math.gcd(stride, len(positions)) != 1:
        stride += 1
    selected_centers = {
        (index * stride) % len(positions) for index in range(selected_count)
    }
    for key, members in cells.items():
        neighbor_keys = {
            tuple((key[axis] + delta[axis]) % cell_counts[axis] for axis in range(3))
            for delta in itertools.product((-1, 0, 1), repeat=3)
        }
        for atom in members:
            if atom not in selected_centers:
                continue
            for neighbor_key in neighbor_keys:
                for other in cells.get(neighbor_key, []):
                    if other == atom:
                        continue
                    displacement = []
                    for axis in range(3):
                        value = positions[other][axis] - positions[atom][axis]
                        value -= round(value / lengths[axis]) * lengths[axis]
                        displacement.append(value)
                    distance = math.sqrt(sum(value * value for value in displacement))
                    minimum = min(minimum, distance)
                    if distance >= rmax:
                        continue
                    pair = canonical_pair(species[atom], species[other])
                    histogram = histograms.setdefault(pair, [0] * bins)
                    histogram[min(int(distance / rmax * bins), bins - 1)] += 1
                    totals[pair] = totals.get(pair, 0) + 1
    normalized = {
        pair: [value / totals[pair] for value in histogram]
        for pair, histogram in histograms.items()
        if totals[pair] != 0
    }
    return {
        "minimum_distance_A": minimum,
        "histograms": normalized,
        "pair_counts": totals,
        "sampled_centers": len(selected_centers),
    }


def histogram_l1(reference: Mapping[str, Any], actual: Mapping[str, Any]) -> float:
    pairs = set(reference["histograms"]) | set(actual["histograms"])
    maximum = 0.0
    for pair in pairs:
        left = reference["histograms"].get(pair)
        right = actual["histograms"].get(pair)
        if left is None or right is None or len(left) != len(right):
            return math.inf
        maximum = max(maximum, sum(abs(x - y) for x, y in zip(left, right)))
    return maximum


def temperature_statistics(thermo_path: Path, target_temperature: float) -> Dict[str, float]:
    segments = baseline.parse_thermo(thermo_path)
    if len(segments) != 1 or len(segments[0]["rows"]) < 2:
        raise baseline.BaselineError("NVT thermo must contain one segment and at least two samples")
    temperatures = [float(row[0]) for row in segments[0]["rows"]]
    if not all(math.isfinite(value) for value in temperatures):
        raise baseline.BaselineError("NVT temperature contains NaN or infinity")
    mean = sum(temperatures) / len(temperatures)
    variance = sum((value - mean) ** 2 for value in temperatures) / len(temperatures)
    rmse = math.sqrt(
        sum((value - target_temperature) ** 2 for value in temperatures)
        / len(temperatures)
    )
    return {
        "temperature_samples": float(len(temperatures)),
        "temperature_mean_K": mean,
        "temperature_std_K": math.sqrt(variance),
        "temperature_rmse_K": rmse,
        "temperature_min_K": min(temperatures),
        "temperature_max_K": max(temperatures),
    }


def radial_distribution(
    frame: Mapping[str, Any], rmax: float, bins: int, maximum_centers: int = 512
) -> Dict[str, Any]:
    """Calculate directed partial g_AB(r) from deterministic center samples."""
    if bins <= 0 or rmax <= 0.0 or maximum_centers <= 0:
        raise baseline.BaselineError("invalid RDF parameters")
    lattice = [float(token) for token in frame["fields"]["Lattice"].split()]
    if len(lattice) != 9 or any(
        abs(lattice[index]) > 1.0e-12 for index in (1, 2, 3, 5, 6, 7)
    ):
        raise baseline.BaselineError("time-averaged RDF requires an orthogonal box")
    lengths = [lattice[0], lattice[4], lattice[8]]
    if any(length <= 0.0 for length in lengths):
        raise baseline.BaselineError("RDF box lengths must be positive")
    volume = lengths[0] * lengths[1] * lengths[2]
    positions = frame_vectors(frame, "pos")
    species = frame_species(frame)
    populations: Dict[str, int] = {}
    for name in species:
        populations[name] = populations.get(name, 0) + 1

    selected_count = min(len(positions), maximum_centers)
    stride = max(1, len(positions) // selected_count)
    while math.gcd(stride, len(positions)) != 1:
        stride += 1
    selected_centers = {
        (index * stride) % len(positions) for index in range(selected_count)
    }
    center_populations: Dict[str, int] = {}
    for atom in selected_centers:
        name = species[atom]
        center_populations[name] = center_populations.get(name, 0) + 1

    cell_counts = [max(1, int(length / rmax)) for length in lengths]
    cells: Dict[Tuple[int, int, int], List[int]] = {}
    for atom, position in enumerate(positions):
        key = tuple(
            int((position[axis] % lengths[axis]) / lengths[axis] * cell_counts[axis])
            % cell_counts[axis]
            for axis in range(3)
        )
        cells.setdefault(key, []).append(atom)

    counts: Dict[str, List[int]] = {}
    for key, members in cells.items():
        neighbor_keys = {
            tuple((key[axis] + delta[axis]) % cell_counts[axis] for axis in range(3))
            for delta in itertools.product((-1, 0, 1), repeat=3)
        }
        for atom in members:
            if atom not in selected_centers:
                continue
            for neighbor_key in neighbor_keys:
                for other in cells.get(neighbor_key, []):
                    if other == atom:
                        continue
                    displacement = []
                    for axis in range(3):
                        value = positions[other][axis] - positions[atom][axis]
                        value -= round(value / lengths[axis]) * lengths[axis]
                        displacement.append(value)
                    distance = math.sqrt(sum(value * value for value in displacement))
                    if distance >= rmax:
                        continue
                    pair = f"{species[atom]}-{species[other]}"
                    histogram = counts.setdefault(pair, [0] * bins)
                    histogram[min(int(distance / rmax * bins), bins - 1)] += 1

    width = rmax / bins
    distributions: Dict[str, List[float]] = {}
    for center_species, number_of_centers in center_populations.items():
        for neighbor_species, population in populations.items():
            available_neighbors = population - int(center_species == neighbor_species)
            if available_neighbors <= 0:
                continue
            pair = f"{center_species}-{neighbor_species}"
            histogram = counts.get(pair, [0] * bins)
            values = []
            for index, count in enumerate(histogram):
                inner = index * width
                outer = (index + 1) * width
                shell_volume = 4.0 * math.pi * (outer ** 3 - inner ** 3) / 3.0
                expected = number_of_centers * available_neighbors * shell_volume / volume
                values.append(count / expected)
            distributions[pair] = values
    return {
        "rmax_A": rmax,
        "bins": bins,
        "sampled_centers": len(selected_centers),
        "distributions": distributions,
    }


def time_averaged_rdf(
    trajectory_path: Path, rmax: float, bins: int, maximum_centers: int = 512
) -> Dict[str, Any]:
    frames = baseline.parse_xyz(trajectory_path)
    if len(frames) < 2:
        raise baseline.BaselineError("time-averaged RDF requires at least two trajectory frames")
    frame_rdfs = [radial_distribution(frame, rmax, bins, maximum_centers) for frame in frames]
    pairs = set().union(*(set(item["distributions"]) for item in frame_rdfs))
    averaged: Dict[str, List[float]] = {}
    for pair in pairs:
        values = [0.0] * bins
        for item in frame_rdfs:
            frame_values = item["distributions"].get(pair, [0.0] * bins)
            for index, value in enumerate(frame_values):
                values[index] += value
        averaged[pair] = [value / len(frame_rdfs) for value in values]
    return {
        "rmax_A": rmax,
        "bins": bins,
        "frames": len(frames),
        "distributions": averaged,
    }


def rdf_l1(reference: Mapping[str, Any], actual: Mapping[str, Any]) -> float:
    if reference["bins"] != actual["bins"] or reference["rmax_A"] != actual["rmax_A"]:
        return math.inf
    pairs = set(reference["distributions"]) | set(actual["distributions"])
    maximum = 0.0
    for pair in pairs:
        left = reference["distributions"].get(pair)
        right = actual["distributions"].get(pair)
        if left is None or right is None or len(left) != len(right):
            return math.inf
        # Mean absolute bin difference is the discretized (1/rmax) integral
        # of |g_ref(r)-g_test(r)| over the configured range.
        maximum = max(maximum, sum(abs(x - y) for x, y in zip(left, right)) / len(left))
    return maximum


def msd_statistics(trajectory_path: Path) -> Dict[str, float]:
    frames = baseline.parse_xyz(trajectory_path)
    if len(frames) < 2:
        raise baseline.BaselineError("MSD requires at least two trajectory frames")
    if any("unwrapped_position" not in property_offsets(frame) for frame in frames):
        raise baseline.BaselineError("MSD trajectory has no unwrapped_position property")
    try:
        times = [float(frame["fields"]["Time"]) for frame in frames]
    except (KeyError, ValueError) as error:
        raise baseline.BaselineError("MSD trajectory has no numeric Time field") from error
    times = [value - times[0] for value in times]
    origin = frame_vectors(frames[0], "unwrapped_position")
    values = []
    for frame in frames:
        positions = frame_vectors(frame, "unwrapped_position")
        if len(positions) != len(origin):
            raise baseline.BaselineError("MSD trajectory atom count changed")
        values.append(
            sum(
                sum((right[axis] - left[axis]) ** 2 for axis in range(3))
                for left, right in zip(origin, positions)
            )
            / len(origin)
        )
    _, slope = linear_fit(values, times)
    return {
        "msd_samples": float(len(values)),
        "msd_mean_A2": sum(values) / len(values),
        "msd_final_A2": values[-1],
        "msd_max_A2": max(values),
        "msd_slope_A2_per_fs": slope,
    }


def quantile(values: Sequence[float], probability: float) -> float:
    if not values or not 0.0 <= probability <= 1.0:
        raise baseline.BaselineError("invalid quantile request")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def distribution_summary(metrics: Sequence[Mapping[str, float]], key: str) -> Dict[str, float]:
    values = [float(item[key]) for item in metrics]
    return {
        "minimum": min(values),
        "median": quantile(values, 0.5),
        "q95": quantile(values, 0.95),
        "maximum": max(values),
    }


def enforce_noninferiority(
    reference: Sequence[Mapping[str, float]],
    actual: Sequence[Mapping[str, float]],
    keys: Sequence[str],
    relative_margin: float,
    absolute_floors: Mapping[str, float],
    label: str,
) -> Dict[str, Any]:
    if len(reference) != len(actual) or not reference:
        raise baseline.BaselineError(f"{label}: metric populations do not match")
    report: Dict[str, Any] = {}
    for key in keys:
        reference_summary = distribution_summary(reference, key)
        actual_summary = distribution_summary(actual, key)
        floor = float(absolute_floors[key])
        q95_limit = reference_summary["q95"] + max(
            relative_margin * reference_summary["q95"], floor
        )
        median_limit = reference_summary["median"] + max(
            relative_margin * reference_summary["median"], floor
        )
        passed = actual_summary["q95"] <= q95_limit and actual_summary["median"] <= median_limit
        report[key] = {
            "reference": reference_summary,
            "actual": actual_summary,
            "median_limit": median_limit,
            "q95_limit": q95_limit,
            "passed": passed,
        }
        if not passed:
            raise baseline.BaselineError(
                f"{label}: {key} is inferior to the locked GPUMD envelope: "
                f"median={actual_summary['median']:.6e}/{median_limit:.6e}, "
                f"q95={actual_summary['q95']:.6e}/{q95_limit:.6e}"
            )
    return report


def enforce_distribution_equivalence(
    reference: Sequence[Mapping[str, float]],
    actual: Sequence[Mapping[str, float]],
    metric_limits: Mapping[str, Mapping[str, float]],
    label: str,
) -> Dict[str, Any]:
    if len(reference) != len(actual) or not reference:
        raise baseline.BaselineError(f"{label}: metric populations do not match")
    report: Dict[str, Any] = {}
    for key, limits in metric_limits.items():
        reference_summary = distribution_summary(reference, key)
        actual_summary = distribution_summary(actual, key)
        comparisons: Dict[str, Any] = {}
        passed = True
        for statistic in ("median", "q95"):
            expected = reference_summary[statistic]
            observed = actual_summary[statistic]
            tolerance = max(
                float(limits["absolute"]), float(limits["relative"]) * abs(expected)
            )
            difference = abs(observed - expected)
            comparisons[statistic] = {
                "difference": difference,
                "tolerance": tolerance,
                "passed": difference <= tolerance,
            }
            passed = passed and difference <= tolerance
        report[key] = {
            "reference": reference_summary,
            "actual": actual_summary,
            "comparisons": comparisons,
            "passed": passed,
        }
        if not passed:
            raise baseline.BaselineError(
                f"{label}: {key} differs from the locked GPUMD distribution envelope"
            )
    return report
