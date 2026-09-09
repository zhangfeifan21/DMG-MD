#!/usr/bin/env python3
"""Deterministic fixtures and analysis helpers for the long-NVE acceptance suite."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SUITE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SUITE_DIR.parents[1]
BASELINE_DIR = PROJECT_ROOT / "tests" / "baseline"
sys.path.insert(0, str(BASELINE_DIR))
import run_baselines as baseline  # noqa: E402


MANIFEST_PATH = SUITE_DIR / "manifest.json"


def load_manifest() -> Dict[str, Any]:
    with MANIFEST_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)


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
    potential: Path,
    stage_dir: Path,
    env: Mapping[str, str],
    timeout: int,
    required_outputs: Iterable[str],
) -> Path:
    stage_dir.mkdir(parents=True, exist_ok=False)
    (stage_dir / "model.xyz").write_text(model_text, encoding="utf-8")
    (stage_dir / "run.in").write_text(run_text, encoding="utf-8")
    shutil.copyfile(potential, stage_dir / "nep.txt")
    input_hashes = {
        filename: baseline.sha256(stage_dir / filename)
        for filename in ("model.xyz", "run.in", "nep.txt")
    }
    result = subprocess.run(
        [*launcher, str(executable)],
        cwd=stage_dir,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    (stage_dir / "execution.stdout").write_text(result.stdout, encoding="utf-8")
    (stage_dir / "execution.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise baseline.BaselineError(
            f"{stage_dir}: executable exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    baseline.stage_inputs_unchanged(stage_dir, input_hashes)
    missing = [name for name in required_outputs if not (stage_dir / name).is_file()]
    if missing:
        raise baseline.BaselineError(f"{stage_dir}: missing outputs {missing}")
    return stage_dir


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
