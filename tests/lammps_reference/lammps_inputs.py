#!/usr/bin/env python3
"""Build physically equivalent MatPL/LAMMPS inputs from GPUMD fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/long_nve"))
import long_nve_common as fixtures  # noqa: E402

# GPUMD defaults for the committed baseline XYZs without a mass column.
# Long-NVE and benchmark models specify masses per atom and use those values.
DEFAULT_MASS = {"C": 12.011, "O": 15.999, "H": 1.008,
                "Ba": 137.327, "Ti": 47.867}


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def elements(potential: str) -> list[str]:
    header = potential.splitlines()[0].split()
    if len(header) < 3 or not header[0].startswith("nep"):
        raise ValueError("expected a GPUMD NEP potential header")
    count = int(header[1])
    if len(header[2:]) != count or len(set(header[2:])) != count:
        raise ValueError("invalid NEP element list")
    return header[2:]


def normalize_potential(source: str) -> tuple[str, dict]:
    """Collapse only equal per-element cutoffs; never alter nonuniform models."""
    names = elements(source)
    lines = source.splitlines()
    matches = [index for index, line in enumerate(lines) if line.split()[:1] == ["cutoff"]]
    if len(matches) != 1:
        raise ValueError("expected one NEP cutoff line")
    index = matches[0]
    tokens = lines[index].split()
    transform = None
    if len(tokens) == 2 * len(names) + 3 and len(names) > 1:
        radial = [float(tokens[1 + 2 * i]) for i in range(len(names))]
        angular = [float(tokens[2 + 2 * i]) for i in range(len(names))]
        if not all(value == radial[0] for value in radial) or not all(
                value == angular[0] for value in angular):
            raise ValueError("nonuniform typewise cutoff cannot be converted without changing physics")
        original = lines[index]
        lines[index] = " ".join(("cutoff", tokens[1], tokens[2], tokens[-2], tokens[-1]))
        transform = {"kind": "equal_typewise_to_uniform", "original_line": original,
                     "lammps_line": lines[index], "elements": names,
                     "pair_cutoff_rule": "arithmetic_mean"}
    elif len(tokens) != 5:
        raise ValueError("unsupported NEP cutoff syntax for this MatPL build")
    target = "\n".join(lines) + "\n" if transform else source
    return target, {"source_sha256": sha_text(source), "lammps_sha256": sha_text(target),
                    "transform": transform}


def parse_model(model: str, species: list[str]) -> tuple[list[float], list[tuple]]:
    lines = model.splitlines()
    if len(lines) < 2:
        raise ValueError("model.xyz is truncated")
    count = int(lines[0])
    if len(lines) != count + 2:
        raise ValueError("model.xyz atom count mismatch")
    header = lines[1]
    lattice = re.search(r'\bLattice="([^"]+)"', header)
    pbc = re.search(r'\bpbc="([^"]+)"', header)
    props = re.search(r'\bProperties=([^\s]+)', header)
    if not lattice or not pbc or not props:
        raise ValueError("model.xyz requires Lattice, pbc and Properties")
    if pbc.group(1).split() != ["T", "T", "T"]:
        raise ValueError("LAMMPS reference supports full periodicity only")
    matrix = [float(value) for value in lattice.group(1).split()]
    if len(matrix) != 9 or any(abs(matrix[i]) > 1e-12 for i in (1, 2, 3, 5, 6, 7)):
        raise ValueError("LAMMPS reference supports orthogonal boxes only")
    box = [matrix[i] for i in (0, 4, 8)]
    if any(not math.isfinite(length) or length <= 0 for length in box):
        raise ValueError("invalid box length")
    fields = props.group(1).split(":")
    if len(fields) % 3:
        raise ValueError("invalid Properties schema")
    columns = {}
    width = 0
    for name, kind, size_text in zip(fields[0::3], fields[1::3], fields[2::3]):
        size = int(size_text)
        if size <= 0 or name in columns or kind not in ("S", "R", "I"):
            raise ValueError("invalid Properties field")
        columns[name] = (width, size)
        width += size
    if any(columns.get(name, (None, None))[1] != size for name, size in
           (("species", 1), ("pos", 3), ("vel", 3))):
        raise ValueError("model.xyz requires species, pos and vel")
    if "mass" in columns and columns["mass"][1] != 1:
        raise ValueError("mass must be scalar")
    types = {name: index + 1 for index, name in enumerate(species)}
    masses = {}
    atoms = []
    for atom_id, line in enumerate(lines[2:], 1):
        tokens = line.split()
        if len(tokens) != width:
            raise ValueError(f"wrong model.xyz column count on atom {atom_id}")
        name = tokens[columns["species"][0]]
        if name not in types:
            raise ValueError(f"model.xyz species {name} absent from potential")
        p = columns["pos"][0]
        v = columns["vel"][0]
        position = tuple(float(value) for value in tokens[p:p + 3])
        velocity = tuple(float(value) * 1000 for value in tokens[v:v + 3])
        mass = (float(tokens[columns["mass"][0]]) if "mass" in columns
                else DEFAULT_MASS[name])
        if not all(math.isfinite(value) for value in (*position, *velocity, mass)) or mass <= 0:
            raise ValueError(f"invalid numeric value on atom {atom_id}")
        if name in masses and not math.isclose(masses[name], mass, abs_tol=1e-9, rel_tol=0):
            raise ValueError(f"LAMMPS atomic style cannot represent varying mass for {name}")
        masses[name] = mass
        atoms.append((atom_id, types[name], name, position, velocity, mass))
    if set(masses) != set(species):
        raise ValueError("potential contains an element absent from model.xyz")
    return box, atoms


def data_file(model: str, potential: str) -> str:
    species = elements(potential)
    box, atoms = parse_model(model, species)
    masses = {name: mass for _, _, name, _, _, mass in atoms}
    lines = ["LAMMPS data converted from GPUMD model.xyz", "",
             f"{len(atoms)} atoms", f"{len(species)} atom types", "",
             *(f"0 {length:.17g} {axis}lo {axis}hi" for axis, length in zip("xyz", box)),
             "", "Masses", "",
             *(f"{i} {masses[name]:.17g} # {name}" for i, name in enumerate(species, 1)),
             "", "Atoms # atomic", ""]
    lines.extend(f"{atom_id} {type_id} " + " ".join(f"{value:.17g}" for value in position)
                 for atom_id, type_id, _, position, _, _ in atoms)
    lines.extend(("", "Velocities", ""))
    lines.extend(f"{atom_id} " + " ".join(f"{value:.17g}" for value in velocity)
                 for atom_id, _, _, _, velocity, _ in atoms)
    return "\n".join(lines) + "\n"


def input_file(potential: str, time_step_fs: float, steps: int,
               *, warmup: int = 0, snapshot: str | None = None) -> str:
    if steps < 0 or warmup < 0 or time_step_fs < 0 or (steps + warmup and not time_step_fs):
        raise ValueError("invalid timestep or step count")
    lines = ["# Generated by tests/lammps_reference/lammps_inputs.py",
             "package kokkos neigh half comm device", "newton on", "units metal",
             "boundary p p p", "atom_style atomic", "read_data data.lmp",
             "pair_style matpl/nep/kk nep.txt", f"pair_coeff * * {' '.join(elements(potential))}",
             "neighbor 1.0 bin", "neigh_modify every 1 delay 0 check yes",
             "thermo_style custom step atoms temp pe ke etotal press",
             f"timestep {time_step_fs / 1000:.12g}"]
    if snapshot:
        lines.extend(("compute atompe all pe/atom", "compute atomvir all stress/atom NULL virial",
                      f"dump snapshot all custom {max(steps, 1)} {snapshot} "
                      "id type x y z vx vy vz fx fy fz c_atompe "
                      "c_atomvir[1] c_atomvir[2] c_atomvir[3] c_atomvir[4] c_atomvir[5] c_atomvir[6]",
                      "dump_modify snapshot sort id format float %.16g"))
    if steps + warmup:
        lines.append("fix integration all nve/kk")
    if warmup:
        lines.extend((f"thermo {warmup}", f"run {warmup}"))
    if steps:
        lines.extend((f"thermo {steps}", f"run {steps}"))
    else:
        lines.extend(("thermo 1", "run 0"))
    return "\n".join(lines) + "\n"


def write_inputs(directory: Path, model: str, source_potential: str,
                 time_step_fs: float, *, steps: int, warmup: int = 0,
                 snapshot: str | None = None) -> dict:
    """Write inputs into one trial directory and return a provenance record."""
    potential, conversion = normalize_potential(source_potential)
    files = {"model.xyz": model, "nep.gpumd.txt": source_potential,
             "nep.txt": potential, "data.lmp": data_file(model, potential),
             "in.lmp": input_file(potential, time_step_fs, steps,
                                  warmup=warmup, snapshot=snapshot)}
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (directory / name).write_text(content)
    return {"model_sha256": sha_text(model), "potential": conversion,
            "files_sha256": {name: sha_text(value) for name, value in files.items()},
            "atoms": int(model.splitlines()[0]), "time_step_fs": time_step_fs,
            "steps": steps, "warmup": warmup, "element_order": elements(potential)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, choices=("baseline", "long-nve"))
    parser.add_argument("--cases", help="comma-separated cases; default all")
    parser.add_argument("--profile", choices=("smoke", "nightly", "release"), default="smoke")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--steps", type=int, default=10, help="short NVE steps")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.steps <= 0 or not 0 <= args.seed <= 9:
        parser.error("steps must be positive and seed must be 0..9")
    output = args.output.resolve()
    records = {}
    if args.suite == "baseline":
        manifest = json.loads((ROOT / "tests/baseline/manifest.json").read_text())
        names = args.cases.split(",") if args.cases else list(manifest["cases"])
        for name in names:
            if name not in manifest["cases"]:
                parser.error(f"unknown baseline case: {name}")
            case = manifest["cases"][name]
            source = (ROOT / "tests/baseline" / case["potential"]).read_text()
            for stage in case["stages"]:
                model_path = ((ROOT / "tests/baseline" / stage["model"])
                              if "model" in stage else
                              (ROOT / "tests/baseline/goldens" / name /
                               stage["model_from_stage"][0] / stage["model_from_stage"][1]))
                model = model_path.read_text()
                base = output / name / stage["name"]
                records[f"{name}/{stage['name']}"] = {
                    "source_model": str(model_path),
                    "static": write_inputs(base / "static", model, source, 0, steps=0,
                                            snapshot="static.lammpstrj"),
                    "nve": write_inputs(base / "nve", model, source, 0.1,
                                         steps=args.steps, snapshot="nve.lammpstrj")}
    else:
        manifest = fixtures.load_manifest()
        names = args.cases.split(",") if args.cases else manifest["profiles"][args.profile]["cases"]
        for name in names:
            if name not in manifest["cases"]:
                parser.error(f"unknown long-NVE case: {name}")
            case = fixtures.profile_case(manifest, args.profile, name)
            model = fixtures.generate_model(case, args.seed)
            source = fixtures.generate_potential(case)
            fixtures.validate_generated_model(case, args.seed, model)
            fixtures.validate_generated_potential(case, source)
            base = output / args.profile / name / f"seed-{args.seed}"
            records[name] = {
                "static": write_inputs(base / "static", model, source, 0, steps=0,
                                       snapshot="static.lammpstrj"),
                "nve": write_inputs(base / "nve", model, source, float(case["time_step_fs"]),
                                    steps=args.steps, snapshot="nve.lammpstrj")}
    output.mkdir(parents=True, exist_ok=True)
    (output / "inputs.json").write_text(json.dumps({"suite": args.suite,
        "profile": args.profile if args.suite == "long-nve" else None,
        "seed": args.seed if args.suite == "long-nve" else None,
        "cases": records}, indent=2, ensure_ascii=False) + "\n")
    print(f"LAMMPS_INPUTS cases={len(records)} output={output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
