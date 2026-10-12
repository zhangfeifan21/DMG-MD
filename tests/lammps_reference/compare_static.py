#!/usr/bin/env python3
"""Compare GPUMD/DMG-MD XYZ with MatPL/LAMMPS static physical observables."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

from lammps_inputs import elements

HERE = Path(__file__).resolve().parent


def xyz_frame(path: Path) -> dict:
    lines = path.read_text().splitlines()
    count = int(lines[0])
    if len(lines) < count + 2:
        raise ValueError(f"truncated XYZ: {path}")
    header = lines[1]
    properties = re.search(r"\bProperties=([^\s]+)", header)
    energy = re.search(r"\benergy=([^\s]+)", header)
    if not properties or not energy:
        raise ValueError(f"XYZ lacks physical output fields: {path}")
    schema = properties.group(1).split(":")
    if len(schema) % 3:
        raise ValueError("invalid XYZ Properties schema")
    columns = {}
    width = 0
    for name, _, size in zip(schema[0::3], schema[1::3], schema[2::3]):
        columns[name] = (width, int(size))
        width += int(size)
    for name, size in (("species", 1), ("pos", 3), ("forces", 3),
                       ("energy_atom", 1), ("virial", 9)):
        if columns.get(name, (None, None))[1] != size:
            raise ValueError(f"XYZ lacks {name} physical output")
    if "vel" in columns and columns["vel"][1] != 3:
        raise ValueError("invalid XYZ velocity field")
    atoms = []
    for line in lines[2:count + 2]:
        tokens = line.split()
        if len(tokens) != width:
            raise ValueError("XYZ atom column count mismatch")
        def values(name):
            start, size = columns[name]
            return tuple(float(value) for value in tokens[start:start + size])
        virial = values("virial")
        atoms.append({"species": tokens[columns["species"][0]], "pos": values("pos"),
                      "vel": values("vel") if "vel" in columns else None,
                      "force": values("forces"),
                      "energy": values("energy_atom")[0],
                      "virial": tuple(virial[index] for index in (0, 4, 8, 1, 2, 5))})
    return {"atoms": atoms, "energy": float(energy.group(1))}


def lammps_frame(path: Path, potential: str, nktv2p: float) -> dict:
    lines = path.read_text().splitlines()
    if lines[:1] != ["ITEM: TIMESTEP"] or int(lines[1]) != 0:
        raise ValueError("expected a LAMMPS step-0 static dump")
    if lines[2] != "ITEM: NUMBER OF ATOMS":
        raise ValueError("LAMMPS dump lacks atom count")
    count = int(lines[3])
    header_index = next((i for i, line in enumerate(lines) if line.startswith("ITEM: ATOMS ")), None)
    if header_index is None:
        raise ValueError("LAMMPS dump lacks atom fields")
    names = lines[header_index].split()[2:]
    required = ["id", "type", "x", "y", "z", "vx", "vy", "vz", "fx", "fy", "fz",
                "c_atompe", *(f"c_atomvir[{i}]" for i in range(1, 7))]
    if names != required:
        raise ValueError("unexpected LAMMPS atom output schema")
    if len(lines) < header_index + 1 + count:
        raise ValueError("truncated LAMMPS atom dump")
    species = elements(potential)
    atoms = []
    for expected_id, line in enumerate(lines[header_index + 1:header_index + 1 + count], 1):
        row = [float(value) for value in line.split()]
        if len(row) != len(required) or int(row[0]) != expected_id:
            raise ValueError("LAMMPS atom IDs are missing or reordered")
        type_id = int(row[1])
        if not 1 <= type_id <= len(species):
            raise ValueError("LAMMPS atom type outside potential")
        atoms.append({"species": species[type_id - 1], "pos": tuple(row[2:5]),
                      "vel": tuple(value / 1000 for value in row[5:8]),
                      "force": tuple(row[8:11]), "energy": row[11],
                      "virial": tuple(-value / nktv2p for value in row[12:18])})
    return {"atoms": atoms, "energy": math.fsum(atom["energy"] for atom in atoms)}


def compare(reference: dict, other: dict, tolerances: dict) -> dict:
    if len(reference["atoms"]) != len(other["atoms"]):
        raise ValueError("atom count mismatch")
    summary = {"atoms": len(reference["atoms"]), "fields": {}}
    for field, tolerance_name in (("pos", "position_A"), ("vel", "velocity_A_per_fs"),
                                  ("energy", "energy_per_atom_eV"),
                                  ("force", "force_eV_per_A"), ("virial", "virial_eV")):
        if field == "vel" and (reference["atoms"][0]["vel"] is None or
                               other["atoms"][0]["vel"] is None):
            summary["fields"][tolerance_name] = {"pass": True, "status": "absent_in_source_xyz"}
            continue
        atol = tolerances[tolerance_name]["atol"]
        rtol = tolerances[tolerance_name]["rtol"]
        maximum = maximum_scaled = 0.0
        for i, (left, right) in enumerate(zip(reference["atoms"], other["atoms"]), 1):
            if left["species"] != right["species"]:
                raise ValueError(f"species mismatch at atom {i}")
            lv = left[field] if isinstance(left[field], tuple) else (left[field],)
            rv = right[field] if isinstance(right[field], tuple) else (right[field],)
            for source, value in zip(lv, rv):
                if not math.isfinite(source) or not math.isfinite(value):
                    raise ValueError(f"nonfinite {field} at atom {i}")
                delta = abs(source - value)
                maximum = max(maximum, delta)
                maximum_scaled = max(maximum_scaled, delta / (atol + rtol * abs(source)))
        summary["fields"][tolerance_name] = {
            "max_abs": maximum, "max_limit_fraction": maximum_scaled,
            "pass": maximum_scaled <= 1.0}
    delta = abs(reference["energy"] - other["energy"]) / len(reference["atoms"])
    limit = tolerances["total_energy_per_atom_eV"]["atol"]
    summary["fields"]["total_energy_per_atom_eV"] = {
        "abs_difference": delta, "limit_fraction": delta / limit, "pass": delta <= limit}
    summary["pass"] = all(item["pass"] for item in summary["fields"].values())
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpumd-xyz", required=True, type=Path)
    parser.add_argument("--lammps-dump", required=True, type=Path)
    parser.add_argument("--potential", required=True, type=Path,
                        help="original GPUMD NEP potential; only element order is read")
    parser.add_argument("--dmgmd-xyz", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    contract = json.loads((HERE / "manifest.json").read_text())
    reference = xyz_frame(args.gpumd_xyz)
    lammps = lammps_frame(args.lammps_dump, args.potential.read_text(),
                           contract["lammps_metal_nktv2p_bar_A3_per_eV"])
    results = {"gpumd_vs_lammps": compare(reference, lammps, contract["tolerances"]),
               "input_paths": {"gpumd_xyz": str(args.gpumd_xyz.resolve()),
                               "lammps_dump": str(args.lammps_dump.resolve()),
                               "potential": str(args.potential.resolve())},
               "contract": contract}
    if args.dmgmd_xyz:
        candidate = xyz_frame(args.dmgmd_xyz)
        results["gpumd_vs_dmgmd"] = compare(reference, candidate, contract["tolerances"])
        results["lammps_vs_dmgmd"] = compare(lammps, candidate, contract["tolerances"])
        results["input_paths"]["dmgmd_xyz"] = str(args.dmgmd_xyz.resolve())
    results["pass"] = all(value["pass"] for key, value in results.items()
                          if key.startswith("gpumd_vs_") or key == "lammps_vs_dmgmd")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print(f"LAMMPS_PHYSICS status={'PASS' if results['pass'] else 'FAIL'} "
          f"atoms={len(reference['atoms'])} "
          f"force_fraction={results['gpumd_vs_lammps']['fields']['force_eV_per_A']['max_limit_fraction']:.3g} "
          f"virial_fraction={results['gpumd_vs_lammps']['fields']['virial_eV']['max_limit_fraction']:.3g}")
    return 0 if results["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
