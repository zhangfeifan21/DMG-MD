#!/usr/bin/env python3
"""Isolate WPE energy and virial differences on three same-state NEP4 fixtures."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
import subprocess

from prepare import FIXTURES, diamond_atoms, fcc_atoms, water_atoms
from long_nve_common import velocities
from static_observables import GPUMD_RUN, ROOT, record, sha256, verify_log, wpe_library, xyz_frame

SEED = 20261125
SMALL_FIXTURES = {
    "carbon": ((20, 6, 6), diamond_atoms),
    "water": ((8, 40, 8), water_atoms),
    "copper": ((8, 32, 8), fcc_atoms),
}
FIELDS = ("position", "velocity", "force", "energy", "virial")
WPE_HOST_REVISION = "a0e55bc87390b93d009da37c798616982573bab2"
OPCODES = {
    "descriptor_angular_fp32": ("build_descriptor_r5x9_angular", ("FADD", "FFMA")),
    "descriptor_radial_fp32": ("build_descriptor_r5x9_filter_radial", ("FADD", "FFMA")),
    "ann_fp32_warp_reduction": ("evaluate_ann_grouped_fused_fb", ("FADD", "FFMA", "SHFL.DOWN")),
    "angular_fp32_atomic_reduction": (
        "find_force_angular_l4_peratom", ("RED.E.ADD.F32.FTZ.RN.STRONG.GPU",)),
    "double_scatter": ("nep_fast_scatter_spatial_outputs", ("DADD",)),
    "double_total_virial_reduction": ("reduce_per_atom_virial_to_total6", ("DADD",)),
}


def setup_fixture(output, material, copper):
    cells, generate = SMALL_FIXTURES[material]
    case = {**FIXTURES[material], "cells": cells}
    potential = copper if material == "copper" else ROOT / case["potential_source"]
    if sha256(potential) != case["potential_sha256"]:
        raise ValueError(f"{material} potential hash differs from the public NEP source")
    if material == "copper":
        atoms, box = generate(case)
    else:
        atoms, box = generate(case, case.get("geometry_seed", SEED))
    rows = [" ".join((element, *(f"{x:.17g}" for x in (*coords, *velocity))))
            for (element, *coords), velocity in zip(atoms, velocities(atoms, SEED, 0.015), strict=True)]
    lx, ly, lz = box
    header = f'{len(atoms)}\npbc="T T T" Lattice="{lx:.17g} 0 0 0 {ly:.17g} 0 0 0 {lz:.17g}" '
    header += "Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3\n"
    order = list(range(len(rows)))
    random.Random(SEED).shuffle(order)
    model = output / "model.xyz"
    model.write_text(header + "\n".join(rows) + "\n")
    permuted = output / "model-permuted.xyz"
    permuted.write_text(header + "\n".join(rows[i] for i in order) + "\n")
    (output / "permutation.txt").write_text("\n".join(str(i + 1) for i in order) + "\n")
    return model, permuted, potential, order


def observe(binary, model, potential, directory, env, *, wpe=False, trace=False):
    directory.mkdir()
    (directory / "model.xyz").symlink_to(os.path.relpath(model, directory))
    (directory / "nep.txt").symlink_to(potential)
    (directory / "run.in").write_text(GPUMD_RUN)
    command = [str(binary)]
    if trace:
        command = ["nsys", "profile", "--trace=cuda", "--sample=none", "--cpuctxsw=none",
                   "--stats=false", "-o", "cuda", *command]
    record(directory / "execution.json", {"argv": command, "gpu": env["CUDA_VISIBLE_DEVICES"]})
    log_path = directory / "run.log"
    with log_path.open("x") as log:
        subprocess.run(command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT,
                       timeout=120, check=True)
    verify_log("wpe" if wpe else "gpumd", log_path, env["CUDA_VISIBLE_DEVICES"])
    with xyz_frame(directory / "static.xyz", output=True) as (count, box, rows):
        return count, box, list(rows)


def restore(frame, order):
    count, box, rows = frame
    restored = [None] * count
    for atom_id, row in zip(order, rows, strict=True):
        restored[atom_id] = row
    return count, box, restored


def differences(first, second):
    count, box, reference = first
    other_count, other_box, candidate = second
    if count != other_count or box != other_box:
        raise ValueError("WPE probe count or box changed")
    largest = dict.fromkeys(FIELDS, 0.0)
    for atom_id, (a, b) in enumerate(zip(reference, candidate, strict=True), 1):
        if a[0] != b[0]:
            raise ValueError(f"WPE probe changed element at atom ID {atom_id}")
        for field, left, right in zip(FIELDS, a[1:], b[1:], strict=True):
            delta = max(abs(x - y) for x, y in zip(left, right, strict=True))
            largest[field] = max(largest[field], delta)
    return largest


def trace_arithmetic(directory, library, sm, env):
    trace = directory / "cuda.nsys-rep"
    database = directory / "cuda.sqlite"
    command = ["nsys", "export", "--type", "sqlite", "--output", str(database), str(trace)]
    with (directory / "nsys-export.log").open("x") as log:
        subprocess.run(command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=120)
    with sqlite3.connect(database) as db:
        names = [row[0] for row in db.execute("""SELECT DISTINCT name.value
            FROM CUPTI_ACTIVITY_KIND_KERNEL AS kernel JOIN StringIds AS name
            ON name.id = kernel.mangledName""")]
    resolved = {}
    with (directory / "cuobjdump.log").open("x") as log:
        for key, (selector, instructions) in OPCODES.items():
            resolved[key] = []
            for name in sorted(n for n in names if selector in n):
                output = subprocess.check_output(["cuobjdump", "-arch", sm, "-fun", name, "-sass",
                                                  str(library)], env=env, stderr=log,
                                                 text=True, timeout=120)
                present = [op for op in instructions if re.search(rf"\b{re.escape(op)}\b", output)]
                resolved[key].append({"launched_kernel": name, "present": present,
                                      "matches": tuple(present) == instructions})
    return resolved


def run(args):
    if not re.fullmatch(r"GPU-[\da-fA-F-]{36}", args.gpu):
        raise ValueError("--gpu must be one full NVIDIA GPU UUID")
    if args.host_revision != WPE_HOST_REVISION:
        raise ValueError(f"probe requires GPUMD host revision {WPE_HOST_REVISION}")
    for tool in ("nsys", "cuobjdump"):
        if not shutil.which(tool):
            raise ValueError(f"{tool} must be on PATH")
    output = args.output.resolve()
    evidence_dir = output / "wpe-diagnosis"
    evidence_dir.mkdir(parents=True)
    tmp = evidence_dir / "tmp"
    tmp.mkdir()
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=args.gpu, CUDA_DEVICE_ORDER="PCI_BUS_ID",
               TMPDIR=str(tmp), CUDA_CACHE_PATH=str(tmp))
    gpu, compute = subprocess.check_output(
        ["nvidia-smi", "-i", args.gpu, "--query-gpu=name,compute_cap", "--format=csv,noheader"],
        env=env, text=True).strip().split(", ")
    sm = f"sm_{compute.replace('.', '')}"
    bins = {engine: getattr(args, f"{engine}_bin").resolve(strict=True)
            for engine in ("gpumd", "native", "wpe")}
    library = wpe_library(bins["wpe"], env)
    trials = {}
    for material in SMALL_FIXTURES:
        directory = evidence_dir / material
        directory.mkdir()
        model, permuted, potential, order = setup_fixture(directory, material,
                                                          args.copper_potential.resolve(strict=True))
        gpumd = observe(bins["gpumd"], model, potential, directory / "gpumd", env)
        native = observe(bins["native"], model, potential, directory / "native", env)
        wpe = observe(bins["wpe"], model, potential, directory / "wpe", env, wpe=True,
                      trace=material == "water")
        repeats = [differences(wpe, observe(bins["wpe"], model, potential,
                   directory / f"wpe-repeat-{i}", env, wpe=True)) for i in (1, 2)]
        perm_wpe = observe(bins["wpe"], permuted, potential, directory / "wpe-permuted", env, wpe=True)
        perm_native = observe(bins["native"], permuted, potential, directory / "native-permuted", env)
        observations = {
            "ordinary_vs_same_host_native": differences(gpumd, native),
            "same_host_native_vs_wpe": differences(native, wpe),
            "same_state_wpe_repeat": {key: max(item[key] for item in repeats) for key in FIELDS},
            "same_state_wpe_permuted": differences(wpe, restore(perm_wpe, order)),
            "same_state_native_permuted": differences(native, restore(perm_native, order)),
        }
        exact = observations["ordinary_vs_same_host_native"]
        repeat = observations["same_state_wpe_repeat"]
        ordered_changes = [observations[key] for key in ("same_host_native_vs_wpe",
                           "same_state_wpe_permuted", "same_state_native_permuted")]
        accepted = (not any(exact.values()) and repeat["energy"] == 0 and repeat["virial"] > 0
                    and all(v["position"] == v["velocity"] == 0 for v in observations.values())
                    and all(v["energy"] > 0 and v["virial"] > 0 for v in ordered_changes))
        trials[material] = {"atoms": gpumd[0], "model_sha256": sha256(model),
                            "potential_sha256": sha256(potential), "observations": observations,
                            "status": "PASS" if accepted else "INCONCLUSIVE"}

    trace_path = evidence_dir / "water/wpe"
    trace = trace_arithmetic(trace_path, library, sm, env)
    record(evidence_dir / "active-kernels.json", trace)
    files = [evidence_dir / "active-kernels.json", trace_path / "cuda.nsys-rep"]
    for material in SMALL_FIXTURES:
        directory = evidence_dir / material
        files.extend(directory / name for name in ("model.xyz", "model-permuted.xyz", "permutation.txt"))
        files.extend(sorted(directory.glob("*/static.xyz")))
        files.extend(sorted(directory.glob("*/run.log")))
    passed = (all(trial["status"] == "PASS" for trial in trials.values())
              and all(any(kernel["matches"] for kernel in stage) for stage in trace.values()))
    report = {
        "schema_version": 1, "host_revision": args.host_revision, "permutation_seed": SEED,
        "release_qualification_status": "PASS" if passed else "INCONCLUSIVE",
        "status": "IDENTIFIED_NUMERICAL_BACKEND" if passed else "INCONCLUSIVE",
        "gpu": {"uuid": args.gpu, "name": gpu, "architecture": sm},
        "sha256": {"gpumd_5_9_1": sha256(bins["gpumd"]),
                   "same_host_native": sha256(bins["native"]),
                   "wpe": sha256(bins["wpe"]), "wpe_library": sha256(library)},
        "material_results": trials, "active_kernel_observations": trace,
        "evidence": {str(path.relative_to(output)): sha256(path) for path in files},
        "scope": "Same-state diagnosis on small fixtures. CUDA arithmetic traced for water.",
        "energy_mechanism": "FP32 descriptors and grouped ANN arithmetic are sensitive to atom "
                            "and neighbour accumulation order.",
        "virial_mechanism": "The active angular NEP kernel uses FP32 reductions. Same-state "
                            "repeat and ordering probes reveal roundoff in per-atom virial.",
    }
    record(output / "wpe-diagnosis.json", report)
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gpumd-bin", "native-bin", "wpe-bin", "copper-potential", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--gpu", required=True)
    parser.add_argument("--host-revision", required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
