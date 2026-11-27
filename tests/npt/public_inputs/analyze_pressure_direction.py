#!/usr/bin/env python3
"""Prepare, run and check measured one-step GPUMD Berendsen pressure directions."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from analyze_preparation import read_thermo
from prepare import FIXTURES, INITIAL_VELOCITY_SEED, pressure_ensemble, sha256, write_json


DIRECTIONS = ("expand", "contract")
PRESSURE_DAMPING_STEPS = 1000
GPUMD_SOURCE_REVISION = "11ba6fbdb4d71b2cdb351b39972fa057521585b3"
LATTICE_RE = re.compile(r'(?:^|\s)Lattice="([^"]+)"')
POSITION_PROPERTIES = "Properties=species:S:1:pos:R:3:mass:R:1"


def read_xyz_header(stream) -> tuple[int, tuple[float, float, float]]:
    count = int(stream.readline())
    comment = stream.readline()
    lattice = LATTICE_RE.search(comment)
    if count <= 0 or lattice is None or 'pbc="T T T"' not in comment:
        raise ValueError("expected a positive atom count and a fully periodic lattice")
    values = [float(value) for value in lattice[1].split()]
    if len(values) != 9 or any(values[i] != 0 for i in (1, 2, 3, 5, 6, 7)):
        raise ValueError("expected an orthorhombic box")
    lengths = values[0], values[4], values[8]
    if not all(math.isfinite(value) and value > 0 for value in lengths):
        raise ValueError("expected finite, positive box lengths")
    return count, lengths


def write_scaled_xyz(source: Path, target: Path, material: str, scale: float) -> None:
    with source.open() as original, target.open("x") as scaled:
        count, lengths = read_xyz_header(original)
        scaled_lengths = tuple(length * scale for length in lengths)
        lx, ly, lz = scaled_lengths
        scaled.write(
            f'{count}\npbc="T T T" Lattice="{lx:.17g} 0 0 0 {ly:.17g} 0 0 0 {lz:.17g}" '
            f"{POSITION_PROPERTIES}\n"
        )
        oxygen = None
        for index in range(count):
            fields = original.readline().split()
            if len(fields) not in (5, 8):
                raise ValueError("expected positions, mass and optional velocities")
            species, x, y, z, mass = fields[:5]
            coordinates = tuple(map(float, (x, y, z)))
            # Keep intramolecular vectors from the O H H generator; alter packing.
            if material == "water" and index % 3:
                if species != "H" or oxygen is None:
                    raise ValueError("expected consecutive O H H water molecules")
                coordinates = tuple(
                    (anchor * scale + math.remainder(position - anchor, length)) % new_length
                    for position, anchor, length, new_length in zip(
                        coordinates, oxygen, lengths, scaled_lengths
                    )
                )
            else:
                if material == "water" and species != "O":
                    raise ValueError("expected consecutive O H H water molecules")
                oxygen = coordinates
                coordinates = tuple(position * scale for position in coordinates)
            scaled.write(f"{species} {' '.join(f'{x:.17g}' for x in coordinates)} {mass}\n")
        if original.read().strip():
            raise ValueError("unexpected data after the final atom")


def prepare_jobs(args) -> None:
    source_dir = args.source_dir.resolve()
    fixture = FIXTURES[args.material]
    source = source_dir / "public-model.xyz"
    potential = source_dir / "nep.txt"
    source_record = json.loads((source_dir / "source.json").read_text())
    source_sha = sha256(source)
    if source_record["material"] != args.material or source_sha != source_record["model_sha256"]:
        raise ValueError("source model does not match the preparation record")
    if sha256(potential) != fixture["potential_sha256"]:
        raise ValueError("potential hash does not match the registered material")
    with source.open() as stream:
        atom_count, _ = read_xyz_header(stream)
        stream.seek(0)
        stream.readline()
        if not stream.readline().strip().endswith(POSITION_PROPERTIES):
            raise ValueError("expected the generated public model with species, positions and masses")
    if atom_count != source_record["atoms"]:
        raise ValueError("source atom count does not match the preparation record")

    contraction_source = source
    contraction_sha = source_sha
    if args.contract_restart is not None:
        contraction_source = args.contract_restart.resolve(strict=True)
        calibration = json.loads((contraction_source.parent / "execution.json").read_text())
        snapshot = calibration["snapshot"]
        contraction_sha = sha256(contraction_source)
        if (
            calibration["status"] != "PASS"
            or calibration["model_in_sha256"] != source_sha
            or snapshot["sha256"] != contraction_sha
            or snapshot["atoms"] != atom_count
        ):
            raise ValueError("contraction restart is not from a completed calibration of this model")

    run_input = "\n".join((
        "potential nep.txt",
        f"time_step {fixture['time_step_fs']:g}",
        f"velocity 300 seed {INITIAL_VELOCITY_SEED}",
        pressure_ensemble(fixture, "ber"),
        "dump_thermo 1",
        "dump_restart 1",
        "run 1",
    )) + "\n"
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for direction, scale in zip(DIRECTIONS, (args.compressed_scale, args.expanded_scale)):
        directory = args.output_dir / direction
        directory.mkdir()
        case_source = contraction_source if direction == "contract" else source
        case_sha = contraction_sha if direction == "contract" else source_sha
        write_scaled_xyz(case_source, directory / "model.xyz", args.material, scale)
        shutil.copyfile(potential, directory / "nep.txt")
        (directory / "run.in").write_text(run_input, encoding="ascii")
        write_json(directory / "input.json", {
            "material": args.material,
            "expected_direction": direction,
            "spatial_scale": scale,
            "source_model": str(case_source),
            "source_model_sha256": case_sha,
            "public_model_sha256": source_sha,
            "model_sha256": sha256(directory / "model.xyz"),
            "potential_sha256": sha256(directory / "nep.txt"),
            "run_input_sha256": sha256(directory / "run.in"),
            "time_step_fs": fixture["time_step_fs"],
            "bulk_modulus_GPa": fixture["bulk_modulus_GPa"],
            "target_pressure_GPa": 0.0,
            "pressure_damping_steps": PRESSURE_DAMPING_STEPS,
            "initial_velocity_seed": INITIAL_VELOCITY_SEED,
            "atoms": atom_count,
        })


def run_jobs(args) -> None:
    binary = args.gpumd_bin.resolve(strict=True)
    executable_sha = sha256(binary)
    cache = (args.output_dir / "cuda-cache").resolve()
    cache.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(CUDA_VISIBLE_DEVICES=args.device, CUDA_CACHE_PATH=str(cache))
    for direction in DIRECTIONS:
        directory = args.output_dir / direction
        launch = directory / "execution.json"
        if (directory / "thermo.out").exists() or (directory / "restart.xyz").exists():
            raise ValueError(f"{directory} already has output; replay in a fresh directory")
        with launch.open("x") as stream:
            record = {
                "gpumd_binary": str(binary),
                "gpumd_sha256": executable_sha,
                "cuda_visible_devices": args.device,
                "started_unix": time.time(),
            }
            json.dump(record, stream)
        start = time.monotonic()
        with (directory / "gpumd.log").open("x") as log:
            completed = subprocess.run([str(binary)], cwd=directory, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, check=False)
        record.update(exit_code=completed.returncode, elapsed_seconds=time.monotonic() - start)
        write_json(launch, record)


def check_job(directory: Path, direction: str) -> dict:
    identity = json.loads((directory / "input.json").read_text())
    execution = json.loads((directory / "execution.json").read_text())
    if execution.get("exit_code") != 0:
        raise ValueError(f"GPUMD did not exit successfully in {directory}")
    if direction != identity["expected_direction"] or identity["target_pressure_GPa"] != 0:
        raise ValueError(f"unexpected test direction or pressure target in {directory}")
    for asset, key in (("model.xyz", "model_sha256"), ("run.in", "run_input_sha256"),
                       ("nep.txt", "potential_sha256")):
        if sha256(directory / asset) != identity[key]:
            raise ValueError(f"input changed in {directory / asset}")

    with (directory / "model.xyz").open() as initial, (directory / "restart.xyz").open() as final:
        initial_count, before_lengths = read_xyz_header(initial)
        final_count, after_lengths = read_xyz_header(final)
    thermo = directory / "thermo.out"
    thermo_count, [sample] = read_thermo(thermo, ((1, 1),), identity["time_step_fs"])
    if not initial_count == final_count == thermo_count == identity["atoms"]:
        raise ValueError(f"mismatched atom counts in {directory}")
    before_volume = math.prod(before_lengths)
    after_volume = math.prod(after_lengths)
    pressure = sample.pressure_GPa
    scale = 1.0 + pressure / (3 * identity["bulk_modulus_GPa"] * identity["pressure_damping_steps"])
    expanded = direction == "expand"
    checks = {
        "pressure_has_expected_sign": pressure > 0 if expanded else pressure < 0,
        "volume_has_expected_change": (
            after_volume > before_volume if expanded else after_volume < before_volume
        ),
        "restart_volume_matches_rounded_thermo": math.isclose(after_volume, sample.volume_A3, rel_tol=2e-10),
        "restart_axes_match_pressure_scaling": all(
            math.isclose(after, before * scale, rel_tol=2e-12)
            for before, after in zip(before_lengths, after_lengths)
        ),
    }
    return {
        **identity,
        **execution,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "pressure_used_GPa": pressure,
        "thermo_sha256": sha256(thermo),
        "restart_sha256": sha256(directory / "restart.xyz"),
        "before_box_lengths_A": list(before_lengths),
        "after_box_lengths_A": list(after_lengths),
        "before_volume_A3": before_volume,
        "after_volume_A3": after_volume,
        "volume_change_A3": after_volume - before_volume,
        "expected_axis_scale": scale,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    prepare = subcommands.add_parser("prepare")
    prepare.add_argument("--material", choices=FIXTURES.keys(), required=True)
    prepare.add_argument("--source-dir", type=Path, required=True)
    prepare.add_argument("--compressed-scale", type=float, default=0.97)
    prepare.add_argument("--expanded-scale", type=float, default=1.03)
    prepare.add_argument("--contract-restart", type=Path,
                         help="use a verified calibration restart for the expanded starting box")
    run = subcommands.add_parser("run")
    run.add_argument("--gpumd-bin", type=Path, required=True)
    run.add_argument("--device", required=True)
    check = subcommands.add_parser("check")
    for subcommand in (prepare, run, check):
        subcommand.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            if not 0 < args.compressed_scale < 1 < args.expanded_scale < math.inf:
                raise ValueError("expected 0 < compressed scale < 1 < expanded scale")
            prepare_jobs(args)
        elif args.action == "run":
            run_jobs(args)
        else:
            cases = {direction: check_job(args.output_dir / direction, direction) for direction in DIRECTIONS}
            passed = all(case["status"] == "PASS" for case in cases.values())
            result = {
                "status": "PASS" if passed else "FAIL",
                "gpumd_source_revision": GPUMD_SOURCE_REVISION,
                "pressure_source": (
                    "Trace/3 of the same thermo[2:5] produced before scaling in Ensemble_BER::compute2 "
                    "and consumed by cpu_pressure_isotropic, written in GPa by dump_thermo."
                ),
                "volume_source": "Initial model and 17 significant digit GPUMD restart lattices.",
                "cases": cases,
            }
            write_json(args.output_dir / "pressure-direction.json", result)
            print(json.dumps(result, indent=2))
            return 0 if passed else 1
    except (OSError, ValueError, KeyError, OverflowError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
