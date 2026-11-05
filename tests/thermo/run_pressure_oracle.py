#!/usr/bin/env python3
"""Run same-condition GPUMD/DMG-MD pressure checks into an external artifact dir."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from test_pressure_oracle import test_production_column_and_virial_mappings


ROOT = Path(__file__).resolve().parents[2]
BASE_MODEL = ROOT / "tests/baseline/inputs/single_small_static/model.xyz"
POTENTIAL = ROOT / "tests/baseline/inputs/potentials/nep_C.txt"
COMMON = (ROOT / "src/gpumd_compat/common.cuh").read_text(encoding="utf-8")
K_B = float(re.search(r"#define K_B ([0-9.eE+-]+)", COMMON).group(1))
P_CONV = float(re.search(r"PRESSURE_UNIT_CONVERSION\s*=\s*([0-9.eE+-]+)", COMMON).group(1))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command_output(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()


def gas_model() -> str:
    coords = [(0, 0, 0), (8, 0, 0), (16, 0, 0), (0, 8, 0), (8, 8, 0), (16, 8, 0), (0, 16, 0), (8, 16, 0)]
    vel = [(sx * 0.01, sy * 0.01, sz * 0.01) for sx, sy, sz in ((1,1,1), (1,1,-1), (1,-1,1), (1,-1,-1), (-1,1,1), (-1,1,-1), (-1,-1,1), (-1,-1,-1))]
    lines = [
        "8",
        'pbc="T T T" Lattice="24 0 0 0 24 0 0 0 24" Properties=species:S:1:pos:R:3:vel:R:3',
    ]
    lines.extend(f"C {x} {y} {z} {vx:.12g} {vy:.12g} {vz:.12g}" for (x,y,z),(vx,vy,vz) in zip(coords, vel))
    return "\n".join(lines) + "\n"


REFERENCE_SCALE = 1.0023


def crystal_model(scale: float = REFERENCE_SCALE, shear: float = 0.0) -> str:
    lines = BASE_MODEL.read_text(encoding="utf-8").splitlines()
    header = lines[1]
    match = re.search(r'Lattice="([^"]+)"', header)
    if match is None:
        raise RuntimeError("fixture has no lattice")
    lattice = [3.57 * scale, 0.0, 0.0, 0.0, 3.57 * scale, 0.0, 0.0, 0.0, 3.57 * scale]
    # Extxyz stores the three cell vectors consecutively. Add the xy cell
    # component to the second vector, then apply the same affine transform.
    lattice[3] += shear * lattice[4]
    header = header[:match.start(1)] + " ".join(f"{value:.12g}" for value in lattice) + header[match.end(1):]
    output = [lines[0], header]
    for line in lines[2:]:
        tokens = line.split()
        x, y, z = (float(value) for value in tokens[1:4])
        x, y, z = scale * (x + shear * y), scale * y, scale * z
        output.append(f"{tokens[0]} {x:.12g} {y:.12g} {z:.12g} 0 0 0")
    return "\n".join(output) + "\n"


def prepare(work: Path, executable: Path, env: dict[str, str], model: str) -> dict:
    work.mkdir(parents=True)
    (work / "model.xyz").write_text(model, encoding="utf-8")
    shutil.copyfile(POTENTIAL, work / "nep.txt")
    (work / "run.in").write_text(
        "potential nep.txt\ntime_step 0\nensemble nve\ndump_thermo 1\n"
        "dump_xyz 1 pressure.xyz precision double force potential virial\nrun 1\n",
        encoding="utf-8",
    )
    hashes = {name: sha256(work / name) for name in ("model.xyz", "nep.txt", "run.in")}
    result = subprocess.run([str(executable)], cwd=work, env=env, capture_output=True, text=True, check=False, timeout=180)
    (work / "execution.stdout").write_text(result.stdout, encoding="utf-8")
    (work / "execution.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"{executable} exited {result.returncode}: {result.stdout}\n{result.stderr}")
    if any(sha256(work / name) != value for name, value in hashes.items()):
        raise RuntimeError(f"{executable} modified an input in {work}")
    for required in ("thermo.out", "pressure.xyz"):
        if not (work / required).exists():
            raise RuntimeError(f"{executable} did not write {required}")
    return {"input_sha256": hashes, "stdout_sha256": sha256(work / "execution.stdout"), "stderr_sha256": sha256(work / "execution.stderr"), "thermo_sha256": sha256(work / "thermo.out"), "xyz_sha256": sha256(work / "pressure.xyz")}


def read_thermo(path: Path) -> tuple[list[str], list[list[float]]]:
    headers: list[str] = []
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            headers.append(line.strip())
        elif line.strip():
            rows.append([float(token) for token in line.split()])
    return headers, rows


def read_xyz(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    natoms = int(lines[0])
    metadata = lines[1]
    schema = re.search(r"Properties=([^ ]+)", metadata)
    if schema is None:
        raise RuntimeError(f"{path}: xyz Properties missing")
    fields = schema.group(1).split(":")
    properties = []
    offset = 0
    for index in range(0, len(fields), 3):
        name, kind, width = fields[index:index + 3]
        properties.append({"name": name, "kind": kind, "width": int(width), "offset": offset})
        offset += int(width)
    virial_property = next((item for item in properties if item["name"] == "virial"), None)
    if virial_property is None or virial_property["width"] != 9:
        raise RuntimeError(f"{path}: per-atom 9 virial property missing")

    def tensor(name: str) -> list[float]:
        match = re.search(rf"(?:^| )" + re.escape(name) + r"=\"([^\"]+)\"", metadata)
        if match is None:
            raise RuntimeError(f"{path}: frame {name} tensor missing")
        values = [float(item) for item in match.group(1).split()]
        if len(values) != 9:
            raise RuntimeError(f"{path}: frame {name} width {len(values)} != 9")
        return values

    atom_virials = []
    for line in lines[2:2 + natoms]:
        tokens = line.split()
        numeric = [float(item) for item in tokens[1:]]
        start = virial_property["offset"] - 1  # species is the leading nonnumeric token
        atom_virials.append(numeric[start:start + 9])
    if len(atom_virials) != natoms or any(len(row) != 9 for row in atom_virials):
        raise RuntimeError(f"{path}: malformed per-atom virial rows")
    return {
        "natoms": natoms,
        "properties": schema.group(1),
        "frame_virial": tensor("virial"),
        "frame_stress": tensor("stress"),
        "atom_virials": atom_virials,
    }


def run_case(name: str, model: str, out: Path, candidate: Path, reference: Path, env: dict[str, str]) -> dict:
    outputs = {}
    for label, executable in (("gpumd", reference), ("dmgmd", candidate)):
        outputs[label] = prepare(out / name / label, executable, env, model)
    ref_headers, ref_rows = read_thermo(out / name / "gpumd/thermo.out")
    got_headers, got_rows = read_thermo(out / name / "dmgmd/thermo.out")
    if ref_headers != got_headers or len(ref_rows) != len(got_rows) or len(ref_rows) != 1:
        raise RuntimeError(f"{name}: thermo shape/header mismatch")
    # Compare the six physical stress columns independently. Header includes
    # engine-specific comments in some versions, so the canonical column line
    # is also checked literally.
    canonical = "# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz"
    if canonical not in ref_headers or canonical not in got_headers:
        raise RuntimeError(f"{name}: unexpected thermo column mapping: GPUMD={ref_headers}, DMG-MD={got_headers}")
    diffs = []
    for index in range(3, 9):
        expected, actual = ref_rows[0][index], got_rows[0][index]
        absolute = abs(actual - expected)
        allowed = 1e-5 + 1e-4 * abs(expected)
        diffs.append({"column": canonical.split()[2 + index], "gpumd_GPa": expected, "dmgmd_GPa": actual, "absolute_error_GPa": absolute, "allowed_GPa": allowed, "pass": absolute <= allowed})
    if not all(item["pass"] for item in diffs):
        raise RuntimeError(f"{name}: six-stress GPUMD differential failed: {diffs}")
    ref_xyz = read_xyz(out / name / "gpumd/pressure.xyz")
    got_xyz = read_xyz(out / name / "dmgmd/pressure.xyz")
    if ref_xyz["natoms"] != got_xyz["natoms"] or ref_xyz["properties"] != got_xyz["properties"]:
        raise RuntimeError(f"{name}: xyz schema or atom count mismatch")
    tensor_diffs = []
    for label, atol in (("frame_virial", 1e-6), ("frame_stress", 1e-8)):
        for index, (expected, actual) in enumerate(zip(ref_xyz[label], got_xyz[label])):
            allowed = atol + 1e-4 * abs(expected)
            tensor_diffs.append({"quantity": label, "component": index, "gpumd": expected, "dmgmd": actual, "absolute_error": abs(actual - expected), "allowed": allowed, "pass": abs(actual - expected) <= allowed})
    atom_diffs = []
    for atom, (ref_values, got_values) in enumerate(zip(ref_xyz["atom_virials"], got_xyz["atom_virials"])):
        for index, (expected, actual) in enumerate(zip(ref_values, got_values)):
            allowed = 1e-6 + 1e-4 * abs(expected)
            atom_diffs.append({"atom": atom, "component": index, "gpumd_eV": expected, "dmgmd_eV": actual, "absolute_error_eV": abs(actual - expected), "allowed_eV": allowed, "pass": abs(actual - expected) <= allowed})
    if not all(item["pass"] for item in tensor_diffs + atom_diffs):
        raise RuntimeError(f"{name}: XYZ 9-component virial/stress differential failed")
    return {"status": "PASS", "thermo_headers": got_headers, "gpumd_row": ref_rows[0], "dmgmd_row": got_rows[0], "stress_comparison": diffs, "xyz_schema": got_xyz["properties"], "frame_tensor_comparison": tensor_diffs, "per_atom_virial_comparison": atom_diffs, "artifacts": outputs}


def ideal_gas_expected(row: list[float]) -> dict:
    n = 8
    volume = 24.0**3
    expected_ev_a3 = n * K_B * row[0] / volume
    expected_gpa = expected_ev_a3 * P_CONV
    calculated = sum(row[index] for index in (3, 4, 5)) / 3.0
    error = abs(calculated - expected_gpa)
    relative = error / abs(expected_gpa)
    return {"N": n, "K_B_eV_per_K": K_B, "volume_A3": volume, "temperature_K": row[0], "analytic_pressure_eV_per_A3": expected_ev_a3, "analytic_pressure_GPa": expected_gpa, "mean_diagonal_thermo_GPa": calculated, "absolute_error_GPa": error, "relative_error": relative, "limit_relative": 1e-10, "pass": relative < 1e-10}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=ROOT.parent / "gpumd-reference/src/gpumd")
    parser.add_argument("--device", default="0")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.candidate, args.reference = args.candidate.resolve(), args.reference.resolve()
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = args.device
    env.pop("DMGMD_COMM_BACKEND", None)
    args.output.mkdir(parents=True, exist_ok=True)
    cases = {
        "ideal_gas": gas_model(),
        "near_zero_reference": crystal_model(),
        "compressed_0p5pct": crystal_model(scale=REFERENCE_SCALE * 0.995),
        "expanded_0p5pct": crystal_model(scale=REFERENCE_SCALE * 1.005),
        "nonzero_shear": crystal_model(shear=0.025),
    }
    results = {}
    for name, model in cases.items():
        results[name] = run_case(name, model, args.output, args.candidate, args.reference, env)
    expected = ideal_gas_expected(results["ideal_gas"]["dmgmd_row"])
    if not expected["pass"]:
        raise RuntimeError(f"ideal gas analytic pressure failed: {expected}")
    base = [item["gpumd_GPa"] for item in results["near_zero_reference"]["stress_comparison"]]
    compressed = [item["gpumd_GPa"] for item in results["compressed_0p5pct"]["stress_comparison"]]
    expanded = [item["gpumd_GPa"] for item in results["expanded_0p5pct"]["stress_comparison"]]
    zero_reference = {"mean_abs_diagonal_GPa": sum(abs(value) for value in base[:3]) / 3.0, "near_zero_limit_GPa": 0.1}
    zero_reference["pass"] = zero_reference["mean_abs_diagonal_GPa"] < zero_reference["near_zero_limit_GPa"]
    shear_row = results["nonzero_shear"]["gpumd_row"]
    nonzero_shear = {"gpumd_offdiagonal_GPa": [shear_row[6], shear_row[7], shear_row[8]], "minimum_max_abs_GPa": 1e-5}
    nonzero_shear["pass"] = max(abs(value) for value in nonzero_shear["gpumd_offdiagonal_GPa"]) > nonzero_shear["minimum_max_abs_GPa"]
    # Record observed responses without assuming an arbitrary starting state
    # has a predetermined pressure sign. Direction is evaluated relative to
    # this measured reference and each GPUMD counterpart remains the oracle.
    response = {
        "compression_mean_diagonal_delta_GPa": sum(compressed[i] - base[i] for i in range(3)) / 3.0,
        "expansion_mean_diagonal_delta_GPa": sum(expanded[i] - base[i] for i in range(3)) / 3.0,
        "opposite_response_product_GPa2": (sum(compressed[i] - base[i] for i in range(3)) / 3.0) * (sum(expanded[i] - base[i] for i in range(3)) / 3.0),
    }
    results_summary = {
        "schema_version": 1,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "PASS" if zero_reference["pass"] and nonzero_shear["pass"] and response["opposite_response_product_GPa2"] < 0 else "INCONCLUSIVE_REFERENCE_GEOMETRY",
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "device_visible": args.device, "candidate": str(args.candidate), "candidate_sha256": sha256(args.candidate), "reference": str(args.reference), "reference_sha256": sha256(args.reference), "nvidia_smi": command_output(["nvidia-smi", "-L"]), "nvcc": command_output(["nvcc", "--version"])},
        "cpu_oracle": test_production_column_and_virial_mappings(),
        "analytic_ideal_gas": expected,
        "near_zero_reference": zero_reference,
        "compression_expansion_response": response,
        "physical_nonzero_shear": nonzero_shear,
        "cases": results,
        "comparison_tolerance": {"thermo_stress_atol_GPa": 1e-5, "thermo_stress_rtol": 1e-4, "xyz_frame_stress_atol_eV_A3": 1e-8, "xyz_frame_stress_rtol": 1e-4, "virial_atol_eV": 1e-6, "virial_rtol": 1e-4, "basis": "existing tests/baseline/manifest.json thermo_stress, xyz_stress and virial tolerances"},
    }
    report = args.output / "pressure-oracle.json"
    report.write_text(json.dumps(results_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(results_summary, indent=2, sort_keys=True))
    return 0 if results_summary["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
