#!/usr/bin/env python3
"""Generate deterministic NPT fixtures and run.in files from the preregistration."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MANIFEST = HERE / "manifest.json"
BASIS = ((0, 0, 0), (.25, .25, .25), (.5, .5, 0), (.75, .75, .25),
         (.5, 0, .5), (.75, .25, .75), (0, .5, .5), (.25, .75, .75))
MASS_AMU = 12.011
KB = 8.617343e-5
VELOCITY_CONVERSION = 10.18051


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def model_text(cells: tuple[int, int, int], seed: int, scale: float = 1.0023,
               exact_lengths: tuple[float, float, float] | None = None) -> str:
    """Use the existing thermostat fixture's seed/basis convention, scaled uniformly."""
    nx, ny, nz = cells
    lengths = list(exact_lengths) if exact_lengths is not None else [3.57 * count * scale for count in cells]
    sigma = math.sqrt(KB * 300.0 / MASS_AMU) / VELOCITY_CONVERSION
    rng = random.Random(seed)
    lines = [
        str(8 * nx * ny * nz),
        'pbc="T T T" Lattice="%s 0 0 0 %s 0 0 0 %s" '
        'Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3' % tuple(lengths),
    ]
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                for x, y, z in BASIS:
                    pos = (1 + 3.57 * scale * (i + x),
                           1 + 3.57 * scale * (j + y),
                           1 + 3.57 * scale * (k + z))
                    vel = [rng.gauss(0.0, sigma) for _ in range(3)]
                    lines.append("C " + " ".join(format(v, ".17g") for v in (*pos, MASS_AMU, *vel)))
    return "\n".join(lines) + "\n"


def ber_run(steps: int, target_pressure_gpa: float = 0.0,
            bulk_modulus_gpa: float = 425.0, trajectory_interval: int | None = None,
            restart_interval: int | None = None, tau_p_steps: int = 200,
            ensemble_line: str | None = None) -> str:
    lines = [
        "potential nep.txt",
        "time_step 0.25",
        ensemble_line or f"ensemble npt_ber 300 300 40 {target_pressure_gpa:g} {bulk_modulus_gpa:g} {tau_p_steps}",
        "dump_thermo 1",
    ]
    if trajectory_interval:
        lines.append(f"dump_xyz {trajectory_interval} trajectory.xyz precision double velocity force potential virial")
    if restart_interval:
        lines.append(f"dump_restart {restart_interval}")
    lines.append(f"run {steps}")
    return "\n".join(lines) + "\n"


def scr_run(steps: int, dt_fs: float = 0.25, tdamp_steps: int = 40,
            tau_p_steps: int = 200) -> str:
    """One single SCR segment with thermo only; no trajectory output."""
    return ("potential nep.txt\n"
            f"time_step {dt_fs}\n"
            f"ensemble npt_scr 300 300 {tdamp_steps} 0 425 {tau_p_steps}\n"
            "dump_thermo 1\n"
            f"run {steps}\n")


def zero_ke_model() -> str:
    return ("2\n"
            'pbc="T T T" Lattice="30 0 0 0 30 0 0 0 30" '
            "Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3\n"
            "C 5 5 5 12.011 0 0 0\n"
            "C 20 20 20 12.011 0 0 0\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="write fixture files")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = {
        "ber216": ((3, 3, 3), 101, 1.0023),
        "ber2016": ((7, 6, 6), 101, 1.0023),
        "compress216": ((3, 3, 3), 101, 1.0023 * 0.995),
        "expand216": ((3, 3, 3), 101, 1.0023 * 1.005),
        "route5184": ((18, 6, 6), 101, 1.0),
    }
    records = {}
    for name, (cells, seed, scale) in cases.items():
        text = model_text(cells, seed, scale,
                          exact_lengths=(64.26, 21.42, 21.42) if name == "route5184" else None)
        digest = sha256_bytes(text.encode())
        if name in ("ber216", "compress216", "expand216"):
            fixture_key = {"ber216": "statistics", "compress216": "compressed",
                           "expand216": "expanded"}[name]
            expected = manifest["fixtures"][fixture_key]["model_xyz_sha256_by_seed"][str(seed)]
        elif name == "ber2016":
            expected = manifest["fixtures"]["ber_golden_large"]["model_xyz_sha256_by_seed"][str(seed)]
        else:
            expected = manifest["fixtures"]["m2a_eligible"]["model_xyz_sha256_by_seed"][str(seed)]
        if digest != expected:
            raise SystemExit(f"{name} model hash mismatch: {digest} != {expected}")
        case_dir = output / name
        case_dir.mkdir(exist_ok=True)
        if args.write:
            (case_dir / "model.xyz").write_text(text)
        records[name] = {"natoms": 8 * math.prod(cells), "model_xyz_sha256": digest}

    base_model = model_text((3, 3, 3), 101)
    nonperiodic_model = base_model.replace('pbc="T T T"', 'pbc="T T F"', 1)
    triclinic_model = re.sub(r'Lattice="[^"]+"',
                             'Lattice="10.736433 0 0 0.1 10.736433 0 0 0 10.736433"',
                             base_model, count=1)
    for name, text in (("negative-nonperiodic", nonperiodic_model),
                       ("negative-triclinic", triclinic_model)):
        digest = sha256_bytes(text.encode())
        expected = manifest["fixtures"]["negative_models"][name]["model_xyz_sha256"]
        if digest != expected:
            raise SystemExit(f"{name} model hash mismatch: {digest} != {expected}")
        records[name] = {"natoms": 216, "model_xyz_sha256": digest}
        if args.write:
            (output / name).mkdir(exist_ok=True)
            (output / name / "model.xyz").write_text(text)
    zero_model = zero_ke_model()
    zero_hash = sha256_bytes(zero_model.encode())
    if zero_hash != manifest["fixtures"]["negative_models"]["zero-ke"]["model_xyz_sha256"]:
        raise SystemExit(f"zero-ke model hash mismatch: {zero_hash}")
    records["zero-ke"] = {"natoms": 2, "model_xyz_sha256": zero_hash}
    if args.write:
        (output / "zero-ke").mkdir(exist_ok=True)
        (output / "zero-ke" / "model.xyz").write_text(zero_model)

    run_profiles = {
        "ber-golden-200": ber_run(200, trajectory_interval=20, restart_interval=200),
        "ber-route-20-target5": ber_run(20, target_pressure_gpa=5.0,
                                        trajectory_interval=5, restart_interval=20),
        "ber-pressure-track-20": ber_run(20, trajectory_interval=1, restart_interval=20),
        "ber-negative-pressure-20-target-minus1": ber_run(20, target_pressure_gpa=-1.0, trajectory_interval=5),
        "ber-fixed-box-20-B2001": ber_run(20, target_pressure_gpa=-1.0, bulk_modulus_gpa=2001.0,
                                           trajectory_interval=5),
        "ber-restart-resume-3": ber_run(3, target_pressure_gpa=5.0,
                                        trajectory_interval=1, restart_interval=3),
        "ber-zero-ke-1": ber_run(1),
        "ber-unsafe-scale-1": ber_run(1, target_pressure_gpa=10000.0,
                                      bulk_modulus_gpa=1.0, tau_p_steps=1),
        "ber-invalid-t-nan": ber_run(1, ensemble_line="ensemble npt_ber nan 300 40 0 425 200"),
        "ber-invalid-b-zero": ber_run(1, ensemble_line="ensemble npt_ber 300 300 40 0 0 200"),
        "ber-invalid-tau-zero": ber_run(1, ensemble_line="ensemble npt_ber 300 300 40 0 425 0"),
        "ber-invalid-token-12": ber_run(1, ensemble_line="ensemble npt_ber 300 300 40 0 425 200 0 0 0 200"),
        "ber-invalid-token-18": ber_run(1, ensemble_line="ensemble npt_ber 300 300 40 0 425 200 0 0 0 200 0 0 0 200 0 0"),
    }
    records["run_inputs"] = {}
    for name, text in run_profiles.items():
        digest = sha256_bytes(text.encode())
        expected = manifest["input_locking"]["run_in_sha256"].get(name)
        if digest != expected:
            raise SystemExit(f"{name} run.in hash mismatch: {digest} != {expected}")
        records["run_inputs"][name] = {"sha256": digest, "bytes": len(text.encode())}
        if args.write:
            (output / name).mkdir(exist_ok=True)
            (output / name / "run.in").write_text(text)
    scr_profiles = {}
    for profile, dt_fs, tdamp, tau_p, budgets in (
        ("base", 0.25, 40, 200, (20000, 40000, 80000)),
        ("half", 0.125, 80, 400, (40000, 80000, 160000)),
    ):
        for steps in budgets:
            text = scr_run(steps, dt_fs, tdamp, tau_p)
            digest = sha256_bytes(text.encode())
            key = f"scr-{profile}-{steps}"
            scr_profiles[key] = {"sha256": digest, "bytes": len(text.encode()),
                                 "dt_fs": dt_fs, "steps": steps}
            if args.write:
                target = output / "scr" / profile / str(steps)
                target.mkdir(parents=True, exist_ok=True)
                (target / "run.in").write_text(text)
    records["scr_run_inputs"] = scr_profiles
    print(json.dumps(records, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
