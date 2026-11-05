#!/usr/bin/env python3
"""Recompute a descriptive zero-KE static EOS from the accepted B0 evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_row(path: Path) -> list[float]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [float(x) for x in line.split()]
        if len(values) != 18:
            raise RuntimeError(f"expected 18 thermo columns in {path}, got {len(values)}")
        rows.append(values)
    if len(rows) != 1:
        raise RuntimeError(f"expected one static thermo row in {path}, got {len(rows)}")
    return rows[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pressure-oracle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.pressure_oracle.resolve()
    root = evidence.parent
    source = json.loads(evidence.read_text())
    cases = []
    names = ("compressed_0p5pct", "near_zero_reference", "expanded_0p5pct")
    for name in names:
        record = source["cases"][name]
        engine_data = {}
        for engine in ("dmgmd", "gpumd"):
            directory = root / name / engine
            hashes = record["artifacts"][engine]
            actual_inputs = {n: sha(directory / n) for n in ("model.xyz", "nep.txt", "run.in")}
            if actual_inputs != hashes["input_sha256"]:
                raise RuntimeError(f"{name}/{engine} input hash mismatch")
            for n, h in actual_inputs.items():
                if hashes["input_sha256"].get(n) != h:
                    raise RuntimeError(f"{name}/{engine}/{n} differs from oracle record")
            model = (directory / "model.xyz").read_text().splitlines()
            lattice_line = model[1]
            marker = 'Lattice="'
            start = lattice_line.index(marker) + len(marker)
            lattice = [float(x) for x in lattice_line[start:lattice_line.index('"', start)].split()]
            if len(lattice) != 9:
                raise RuntimeError(f"{name}/{engine} model does not have a 3x3 lattice")
            velocities = []
            for atom_line in model[2:]:
                fields = atom_line.split()
                velocities.extend(float(x) for x in fields[4:7])
            if velocities and any(x != 0.0 for x in velocities):
                raise RuntimeError(f"{name}/{engine} input has nonzero velocity")
            row = read_row(directory / "thermo.out")
            if row[1] != 0.0:
                raise RuntimeError(f"{name}/{engine} has nonzero kinetic energy {row[1]}")
            expected_thermo = hashes["thermo_sha256"]
            if sha(directory / "thermo.out") != expected_thermo:
                raise RuntimeError(f"{name}/{engine} thermo hash mismatch")
            volume = lattice[0] * (lattice[4] * lattice[8] - lattice[5] * lattice[7]) \
                - lattice[1] * (lattice[3] * lattice[8] - lattice[5] * lattice[6]) \
                + lattice[2] * (lattice[3] * lattice[7] - lattice[4] * lattice[6])
            pressure = sum(row[i] for i in (3, 4, 5)) / 3.0
            engine_data[engine] = {"volume_A3": volume, "pressure_GPa": pressure,
                                   "kinetic_energy_eV": row[1],
                                   "model_sha256": actual_inputs["model.xyz"],
                                   "thermo_sha256": expected_thermo,
                                   "input_sha256": actual_inputs}
        if engine_data["dmgmd"]["volume_A3"] != engine_data["gpumd"]["volume_A3"] or \
                engine_data["dmgmd"]["pressure_GPa"] != engine_data["gpumd"]["pressure_GPa"]:
            raise RuntimeError(f"{name}: accepted B0 static pressure outputs are not identical")
        cases.append({"name": name, **engine_data["gpumd"]})

    x = [c["volume_A3"] for c in cases]
    y = [c["pressure_GPa"] for c in cases]
    xbar, ybar = sum(x) / 3.0, sum(y) / 3.0
    slope = sum((a - xbar) * (b - ybar) for a, b in zip(x, y)) / \
        sum((a - xbar) ** 2 for a in x)
    intercept = ybar - slope * xbar
    residuals = [b - (intercept + slope * a) for a, b in zip(x, y)]
    v0, p0 = x[1], y[1]
    slope_compression = (y[1] - y[0]) / (x[1] - x[0])
    slope_expansion = (y[2] - y[1]) / (x[2] - x[1])
    result = {
        "schema": "dmgmd.static-eos-diagnostic.v1",
        "source_pressure_oracle": str(evidence),
        "source_pressure_oracle_sha256": sha(evidence),
        "method": "unweighted three-point OLS of mean diagonal GPUMD pressure vs determinant volume; one-sided secants around near-zero point",
        "static_zero_kinetic_energy_verified": True,
        "cases": cases,
        "fit": {"slope_dP_dV_GPa_per_A3": slope,
                "intercept_GPa": intercept,
                "bulk_modulus_at_reference_volume_GPa": -v0 * slope,
                "residuals_GPa_in_case_order": residuals,
                "max_abs_residual_GPa": max(abs(v) for v in residuals),
                "left_compression_slope_GPa_per_A3": slope_compression,
                "left_compression_bulk_modulus_GPa": -v0 * slope_compression,
                "right_expansion_slope_GPa_per_A3": slope_expansion,
                "right_expansion_bulk_modulus_GPa": -v0 * slope_expansion,
                "one_sided_bulk_modulus_range_GPa": [
                    min(-v0 * slope_compression, -v0 * slope_expansion),
                    max(-v0 * slope_compression, -v0 * slope_expansion)],
                "volume_range_A3": [min(x), max(x)],
                "pressure_range_GPa": [min(y), max(y)]},
        "interpretation": "descriptive three-point 0 K static EOS only; fit residuals are not a sampling confidence interval, the result is not a finite-temperature material modulus, and B=425 GPa is a controller input rather than a measurement target",
    }
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "sha256": sha(output), "fit": result["fit"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
