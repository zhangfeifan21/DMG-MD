#!/usr/bin/env python3
"""Measure and freeze four-engine, same-state static NEP differences."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from hashlib import file_digest
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/lammps_reference"))
import lammps_inputs  # noqa: E402
from analyze_preparation import MANIFEST
from water_selection import DIAGNOSTICS_FILENAME, SELECTION_FILENAME, check_selection

ENGINE_NAMES = ("gpumd", "dmgmd", "lammps", "wpe")
MATERIALS = {"carbon": ["C"], "water": ["O", "H"], "copper": ["Cu"]}
ENERGY = ("energy", ("energy",), "eV")
FORCE = ("force", ("x", "y", "z"), "eV/Å")
VIRIAL = ("virial", ("xx", "xy", "xz", "yx", "yy", "yz", "zx", "zy", "zz"), "eV")
QUANTITIES = (FORCE, ENERGY, VIRIAL)
STRESS_TO_ROW_MAJOR = (0, 3, 4, 6, 1, 5, 7, 8, 2)
XYZ_WIDTHS = {"species": 1, "pos": 3, "vel": 3, "forces": 3, "energy_atom": 1, "virial": 9}
LMP_FIELDS = ["id", "type", "x", "y", "z", "vx", "vy", "vz", "fx", "fy", "fz",
              "c_atompe", *(f"c_atomvir[{index}]" for index in range(1, 10))]
# Coordinates may differ by a periodic image, and velocities round trip via
# the GPUMD natural units and LAMMPS metal-unit conversion.
MATCH_LIMITS = MANIFEST["static"]["match_limits"]
CALIBRATION_MULTIPLIER = MANIFEST["static"]["calibration_multiplier"]
WATER_SELECTION_FILES = (SELECTION_FILENAME, DIAGNOSTICS_FILENAME)
GPUMD_RUN = ("potential nep.txt\ntime_step 0\nensemble nve\ndump_thermo 1\n"
             "dump_xyz 1 static.xyz precision double velocity force potential virial\nrun 1\n")


def sha256(path):
    with Path(path).open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def record(path, data):
    with path.open("x") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def finite(values):
    result = tuple(map(float, values))
    if not all(map(math.isfinite, result)):
        raise ValueError("static frame contains nonfinite data")
    return result


def xyz_header(stream, widths):
    count = int(stream.readline())
    tags = dict(word.split("=", 1) for word in shlex.split(stream.readline()) if "=" in word)
    tags = {key.lower(): val for key, val in tags.items()}
    lattice = finite(tags["lattice"].split())
    if count <= 0 or tags["pbc"].split() != ["T", "T", "T"] or len(lattice) != 9:
        raise ValueError("expected a nonempty fully periodic XYZ frame")
    if any(lattice[i] != 0 for i in (1, 2, 3, 5, 6, 7)):
        raise ValueError("expected an orthogonal XYZ box")
    box = lattice[::4]
    if min(box) <= 0:
        raise ValueError("expected positive box lengths")

    properties = tags["properties"].split(":")
    if len(properties) % 3:
        raise ValueError("invalid XYZ Properties schema")
    offset = 0
    fields = {}
    for name, kind, size in zip(properties[::3], properties[1::3], properties[2::3]):
        size = int(size)
        if size <= 0 or name in fields or kind not in {"S", "R", "I"}:
            raise ValueError("invalid XYZ property")
        fields[name] = slice(offset, offset + size)
        offset += size
    if any(name not in fields or fields[name].stop - fields[name].start != size
           for name, size in widths.items()):
        raise ValueError("XYZ missing expected field")
    return count, box, tags, fields, offset


@contextmanager
def xyz_frame(path, *, output):
    with path.open() as stream:
        required = XYZ_WIDTHS if output else {"species": 1, "pos": 3, "vel": 3, "mass": 1}
        count, box, tags, fields, width = xyz_header(stream, required)
        if output and float(tags["time"]) != 0:
            raise ValueError(f"nonzero elapsed integration time in {path}")

        def rows():
            for _ in range(count):
                row = stream.readline().split()
                if len(row) != width:
                    raise ValueError(f"truncated or malformed XYZ frame {path}")
                atom = (row[fields["species"].start], finite(row[fields["pos"]]),
                        finite(row[fields["vel"]]))
                if output:
                    yield (*atom, *(finite(row[fields[field]])
                                    for field in ("forces", "energy_atom", "virial")))
                else:
                    mass = finite(row[fields["mass"]])[0]
                    if mass <= 0:
                        raise ValueError(f"nonpositive mass in {path}")
                    yield atom
            if any(line.strip() for line in stream):
                raise ValueError(f"unexpected frame data after {count} atoms in {path}")

        yield count, box, rows()


@contextmanager
def lammps_frame(path, species, pressure_factor):
    with path.open() as stream:
        if [stream.readline().strip() for _ in range(3)] != [
                "ITEM: TIMESTEP", "0", "ITEM: NUMBER OF ATOMS"]:
            raise ValueError(f"expected a step-zero LAMMPS dump in {path}")
        count = int(stream.readline())
        if stream.readline().split() != ["ITEM:", "BOX", "BOUNDS", "pp", "pp", "pp"]:
            raise ValueError(f"expected a periodic orthogonal LAMMPS box in {path}")
        bounds = [finite(stream.readline().split()) for _ in range(3)]
        if any(len(bound) != 2 or bound[0] != 0 or bound[1] <= 0 for bound in bounds):
            raise ValueError(f"unexpected LAMMPS box origin or length in {path}")
        box = tuple(high for _, high in bounds)
        if stream.readline().split() != ["ITEM:", "ATOMS", *LMP_FIELDS]:
            raise ValueError(f"unexpected LAMMPS atom fields in {path}")

        def rows():
            for atom_id in range(1, count + 1):
                row = stream.readline().split()
                if len(row) != len(LMP_FIELDS) or int(row[0]) != atom_id:
                    raise ValueError(f"missing or reordered LAMMPS atom ID {atom_id}")
                type_id = int(row[1])
                if not 1 <= type_id <= len(species):
                    raise ValueError(f"invalid LAMMPS atom type {type_id}")
                numbers = finite(row[2:])
                velocity = tuple(v / 1000 for v in numbers[3:6])
                virial = tuple(-numbers[10 + i] / pressure_factor for i in STRESS_TO_ROW_MAJOR)
                yield species[type_id - 1], numbers[:3], velocity, numbers[6:9], numbers[9:10], virial
            if any(line.strip() for line in stream):
                raise ValueError(f"unexpected data after step-zero dump in {path}")

        yield count, box, rows()


def guard_bands():
    baseline = json.loads((ROOT / "tests/baseline/manifest.json").read_text())["tolerances"]
    cross_engine = json.loads((ROOT / "tests/lammps_reference/manifest.json").read_text())
    mapped = {"energy": "energy_per_atom_eV", "force": "force_eV_per_A", "virial": "virial_eV"}
    lammps_bounds = {key: cross_engine["tolerances"][value] for key, value in mapped.items()}
    dmgmd_bounds = {key: baseline[key] for key, _, _ in QUANTITIES}
    # WPE shares this absolute+relative ceiling as an additional conservative
    # check. Its admissible error must still come from its own independent frames.
    guards = {"dmgmd": dmgmd_bounds, "lammps": lammps_bounds, "wpe": lammps_bounds}
    return guards, cross_engine["lammps_metal_nktv2p_bar_A3_per_eV"]


def compare(model, reference, candidate, species, guard, pressure_factor, *, engine):
    result = {name: {"unit": unit, "max_abs": 0.0, "max_guard_fraction": 0.0,
                     "components": dict.fromkeys(components, 0.0), "worst_atom_id": 1,
                     "worst_component": components[0]}
              for name, components, unit in QUANTITIES}
    signed_totals = {name: dict.fromkeys(components, 0.0)
                     for name, components, _ in (ENERGY, VIRIAL)}
    geometry = {name: 0.0 for name in MATCH_LIMITS}
    have_nonzero_velocity = False
    with ExitStack() as stack:
        src_n, box, source = stack.enter_context(xyz_frame(model, output=False))
        ref_n, ref_box, expected = stack.enter_context(xyz_frame(reference, output=True))
        output = (lammps_frame(candidate, species, pressure_factor) if engine == "lammps"
                  else xyz_frame(candidate, output=True))
        other_n, other_box, reported = stack.enter_context(output)
        if src_n != ref_n or src_n != other_n:
            raise ValueError("cross-engine atom count mismatch")
        geometry["box_A"] = max(abs(a - b) for converted in (ref_box, other_box)
                                for a, b in zip(box, converted))
        for atom_id, (atom, ref, other) in enumerate(zip(source, expected, reported, strict=True), 1):
            element, position, velocity = atom
            if element not in species or element != ref[0] or element != other[0]:
                raise ValueError(f"species or atom ID mismatch on input row {atom_id}")
            have_nonzero_velocity |= any(velocity)
            for observed in (ref, other):
                delta = max(abs(math.remainder(x - y, length))
                            for x, y, length in zip(position, observed[1], box))
                geometry["position_A"] = max(geometry["position_A"], delta)
                geometry["velocity_A_per_fs"] = max(
                    geometry["velocity_A_per_fs"], *(abs(a - b) for a, b in zip(velocity, observed[2])))
            for index, (name, components, _) in enumerate(QUANTITIES, 3):
                field = result[name]
                bounds = guard[name]
                for component, left, right in zip(components, ref[index], other[index], strict=True):
                    signed_difference = right - left
                    if not math.isfinite(signed_difference):
                        raise ValueError(f"nonfinite {name} difference at atom ID {atom_id}")
                    if name in signed_totals:
                        signed_totals[name][component] += signed_difference
                    delta = abs(signed_difference)
                    field["components"][component] = max(delta, field["components"][component])
                    if delta > field["max_abs"]:
                        field.update(max_abs=delta, worst_atom_id=atom_id, worst_component=component)
                    limit = bounds["atol"] + bounds["rtol"] * abs(left)
                    field["max_guard_fraction"] = max(field["max_guard_fraction"], delta / limit)

    if not have_nonzero_velocity:
        raise ValueError("source XYZ has zero velocities")
    if any(geometry[name] > limit for name, limit in MATCH_LIMITS.items()):
        raise ValueError(f"{engine} did not preserve the frozen state: {geometry}")
    return {"atoms": src_n, "box_A": box, "geometry": geometry, "quantities": result,
            "candidate_minus_reference_total_eV": signed_totals,
            "total_energy_difference_per_atom_eV": abs(signed_totals["energy"]["energy"]) / src_n}


def verify_log(engine, log, uuid, lammps_geometry="native"):
    contents = log.read_text()
    if engine == "lammps":
        script = (log.parent / "in.lmp").read_text()
        lammps_inputs.verify_geometry(contents, lammps_geometry, initial_faces=" initial_face_images " in script)
        checks = (r"KOKKOS mode .*is enabled", r"pair matpl/nep/kk",
                  r"NEP Kokkos potential .+ loaded successfully", r"Loop time .* on 1 procs for 0 steps",
                  rf"LAMMPS_GPU_BIND rank=0 local_rank=0 cuda_uuid={uuid}")
    elif engine == "dmgmd":
        checks = (r"DMGMD_MPI rank=0 world_size=1", rf"cuda_uuid={uuid[4:].replace('-', '').lower()}",
                  r"DMGMD_TIMING")
    else:
        checks = (r"number of GPUs = 1\b", r"Time used for this run")
        if "Try to use" in contents:
            raise ValueError(f"{engine} unexpectedly selected the multi-GPU NEP path")
        if engine == "wpe":
            if contents.count("Potential engine: Wisevolve Potential Engine 1.0.0") != 1:
                raise ValueError(f"{log} did not identify exactly one WPE 1.0.0 instance")
        else:
            checks += (r"version 5\.9\.1\b", r"Use the NEP4 potential")
            if "Wisevolve Potential Engine" in contents:
                raise ValueError("GPUMD reference selected Wisevolve")
    missing = [pattern for pattern in checks if not re.search(pattern, contents)]
    if missing:
        raise ValueError(f"{engine} log {log} missing identity markers: {missing}")


def wpe_library(binary, env):
    linkage = subprocess.check_output(["ldd", str(binary)], text=True, env=env)
    match = re.search(r"libwisevolve_potential\.so\S* => (\S+)", linkage)
    if not match:
        raise ValueError("unable to identify the linked WPE library")
    return Path(match[1]).resolve(strict=True)


def check_holdout_execution(execution, material):
    from prepare import check_preparation, preparation_stages

    evidence = execution.parents[1]
    stage = preparation_stages(material, evidence_dir=evidence)[-1]
    if execution.name != "execution.json" or execution.parent.name != stage:
        raise ValueError(f"holdout requires execution.json from the {stage} preparation for {material}")
    return check_preparation(evidence, material)


def water_selection_provenance(root, frame_sha256):
    selection, diagnostics = (root / name for name in WATER_SELECTION_FILES)
    try:
        digest = sha256(selection)
    except OSError as error:
        raise ValueError(f"missing or unreadable water selection: {selection}") from error
    approved = check_selection(selection, diagnostics, frame_sha256)
    if sha256(selection) != digest:
        raise ValueError("water selection changed during verification")
    return {"sha256": digest, "created_utc": approved["created_utc"]}


def check_holdout_selection(trial_evidence, started_utc, approval):
    if trial_evidence.get("water_selection") != approval:
        raise ValueError("water holdout differs from the approved selection record")
    approved_utc = datetime.fromisoformat(approval["created_utc"])
    started_utc = datetime.fromisoformat(started_utc)
    if approved_utc.tzinfo is None or started_utc.tzinfo is None or started_utc <= approved_utc:
        raise ValueError("water holdout must start after the recorded selection")


def holdout_provenance(args):
    if not args.calibration:
        raise ValueError("holdout requires a passing frozen --calibration before measurement")
    freeze = checked_calibration(args.calibration)
    baseline = freeze["baseline"]
    if (freeze["calibration_status"] != "PASS" or freeze["material"] != args.material
            or baseline["gpu_uuid"] != args.gpu
            or baseline["engines"]["lammps"].get("geometry", "native") != args.lammps_geometry):
        raise ValueError("holdout conditions differ from the passing calibration")
    for name in ENGINE_NAMES:
        if sha256(getattr(args, f"{name}_bin")) != baseline["engines"][name]["sha256"]:
            raise ValueError(f"{name} build differs from the frozen calibration")
    library = wpe_library(args.wpe_bin.resolve(strict=True), os.environ)
    if sha256(library) != baseline["engines"]["wpe"]["library"]["sha256"]:
        raise ValueError("WPE shared library differs from the frozen calibration")
    if sha256(args.potential) != baseline["source"]["potential"]:
        raise ValueError("holdout potential differs from the calibration")
    # Look up the preparation in the caller's evidence root, including when
    # the individual execution file is linked to an older archive.
    execution = (args.trial_evidence or args.model.parent / "execution.json").absolute()
    source, frame, reference = check_holdout_execution(execution, args.material)
    if (frame["sha256"] in freeze["snapshot_sha256"]
            or source["potential_sha256"] != baseline["source"]["potential"]
            or reference != baseline["engines"]["gpumd"]["sha256"]):
        raise ValueError("SCR provenance differs from the calibrated preparation")
    evidence = {"path": str(execution), "sha256": sha256(execution), "model_sha256": frame["sha256"],
                "calibration_sha256": sha256(args.calibration), "preparation_stage": execution.parent.name}
    if args.material == "water":
        evidence["water_selection"] = water_selection_provenance(execution.parents[1], frame["sha256"])
    return evidence


def measure(args):
    from prepare import SCR_STAGES

    if not re.fullmatch(r"GPU-[\da-fA-F-]{36}", args.gpu):
        raise ValueError("--gpu must be one full NVIDIA GPU UUID")
    if not os.environ.get("OMPI_HOME") or not os.environ.get("UCX_HOME"):
        raise ValueError("source ../env/md-mpi.sh before starting")
    model, potential = args.model.resolve(strict=True), args.potential.resolve(strict=True)
    if args.trial != "holdout" and (args.calibration
            or any(path.parent.name in SCR_STAGES and path.name == "restart.xyz" for path in (args.model, model))
            or (args.trial_evidence and any(path.parent.name in SCR_STAGES
                                           for path in (args.trial_evidence, args.trial_evidence.resolve())))):
        raise ValueError("SCR restart and frozen calibration require --trial holdout")
    trial_evidence = holdout_provenance(args) if args.trial == "holdout" else None
    started = datetime.now(timezone.utc).isoformat()
    if trial_evidence and "water_selection" in trial_evidence:
        check_holdout_selection(trial_evidence, started, trial_evidence["water_selection"])
    nep = potential.read_bytes().decode()
    species = lammps_inputs.lammps_elements(nep, MATERIALS[args.material])
    source_hash = {"model": sha256(model), "potential": sha256(potential)}
    if args.trial == "holdout" and trial_evidence["model_sha256"] != source_hash["model"]:
        raise ValueError("holdout model differs from the accepted SCR restart")
    if args.trial.startswith("calibration-"):
        if args.trial_evidence is None:
            raise ValueError("calibration requires --trial-evidence from the independent source job")
        provenance = json.loads(args.trial_evidence.read_text())
        expected_trial = f"calibration-{provenance['velocity_seed']}"
        if (provenance["status"] != "PASS" or provenance["exit_code"] != 0
                or type(provenance["velocity_seed"]) is not int
                or expected_trial != args.trial or provenance["snapshot"]["sha256"] != source_hash["model"]
                or provenance["potential_sha256"] != source_hash["potential"]):
            raise ValueError("calibration trial evidence disagrees with the frozen-source files")
        trial_evidence = {"path": str(args.trial_evidence.resolve()),
                          "sha256": sha256(args.trial_evidence), "seed": provenance["velocity_seed"]}
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=args.gpu, CUDA_DEVICE_ORDER="PCI_BUS_ID",
               DMGMD_COMM_BACKEND="HostStaged", LAMMPS_RANK_GPU_UUIDS=args.gpu,
               OMP_NUM_THREADS="1")
    output = args.output.resolve()
    output.mkdir(parents=True)
    engines = {}
    guards, pressure_factor = guard_bands()
    for engine in ENGINE_NAMES:
        binary = getattr(args, f"{engine}_bin").resolve(strict=True)
        directory = output / engine
        directory.mkdir()
        (directory / "model.xyz").symlink_to(model)
        command = [str(binary)]
        if engine == "lammps":
            converted, conversion = lammps_inputs.write_matpl_potentials(directory, nep)
            # GPUMD evaluates force at setup and once more for its zero-dt output step.
            data, faces = lammps_inputs.model_data(model.read_text(), converted, active_species=species,
                                                   gpumd_wraps=2 if args.lammps_geometry != "native" else 0)
            (directory / "data.lmp").write_text(data)
            del data
            conversion["initial_face_counts"] = {"upper_xyz": list(map(len, faces[:3])),
                                                 "lower_xyz": list(map(len, faces[3:]))}
            (directory / "in.lmp").write_text(lammps_inputs.input_file(
                converted, 0, 0, snapshot="static.lammpstrj", active_species=species,
                full_virial=True, geometry=args.lammps_geometry, initial_faces=faces))
            command = ["mpirun", "-n", "1", sys.executable,
                       str(ROOT / "tests/lammps_reference/launch_rank.py"),
                       "--binary", str(binary)]
        else:
            (directory / "nep.txt").symlink_to(potential)
            (directory / "run.in").write_text(GPUMD_RUN)
            if engine == "dmgmd":
                command = ["mpirun", "-n", "1", *command]
        log_path = directory / "run.log"
        with log_path.open("x") as log:
            subprocess.run(command, cwd=directory, env=env, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=300)
        verify_log(engine, log_path, args.gpu, args.lammps_geometry)
        engines[engine] = {"binary": str(binary), "sha256": sha256(binary), "argv": command}
        if engine == "lammps":
            engines[engine]["geometry"] = args.lammps_geometry
        # The loaded WPE shared library is an input as well as the executable.
        if engine == "wpe":
            library = wpe_library(binary, env)
            engines[engine]["library"] = {"path": str(library), "sha256": sha256(library)}
    reference = output / "gpumd/static.xyz"
    with ProcessPoolExecutor(max_workers=len(ENGINE_NAMES) - 1) as workers:
        jobs = {}
        for engine in ENGINE_NAMES[1:]:
            filename = "static.lammpstrj" if engine == "lammps" else "static.xyz"
            jobs[engine] = workers.submit(compare, model, reference, output / engine / filename,
                                          species, guards[engine], pressure_factor, engine=engine)
        comparisons = {engine: job.result() for engine, job in jobs.items()}
    if source_hash != {"model": sha256(model), "potential": sha256(potential)}:
        raise ValueError("static source changed while the cross-engine comparisons ran")
    if trial_evidence and "water_selection" in trial_evidence:
        approved = water_selection_provenance(Path(trial_evidence["path"]).parents[1], source_hash["model"])
        check_holdout_selection(trial_evidence, started, approved)
    measurement = {
        "schema_version": 1, "started_utc": started, "created_utc": datetime.now(timezone.utc).isoformat(),
        "material": args.material, "trial": args.trial, "source": source_hash,
        "source_paths": {"model": str(model), "potential": str(potential)},
        "gpu_uuid": args.gpu, "engines": engines, "lammps_conversion": conversion,
        "trial_evidence": trial_evidence,
        "guards": guards, "comparisons": comparisons,
    }
    record(output / "measurement.json", measurement)
    if args.check_guards:
        within_bounds = all(field["max_guard_fraction"] <= 1.0 for result in comparisons.values()
                            for field in result["quantities"].values())
        return 0 if within_bounds and lammps_total_energy_guard([measurement])["pass"] else 1


def read_measurements(args):
    measurements = [json.loads(path.read_text()) for path in args.frames]
    paths = [str(path.resolve()) for path in args.frames]
    return measurements, dict(zip(paths, map(sha256, args.frames), strict=True))


def compatible(left, right):
    if (left["material"] != right["material"] or left["source"]["potential"] != right["source"]["potential"]
            or left["gpu_uuid"] != right["gpu_uuid"]
            or left["guards"] != right["guards"]):
        raise ValueError("measurement material, source potential, GPU or guards differ")
    if (left["engines"]["lammps"].get("geometry", "native")
            != right["engines"]["lammps"].get("geometry", "native")):
        raise ValueError("MatPL pair geometry changed during static acceptance")
    for name in ENGINE_NAMES:
        if (left["engines"][name]["sha256"] != right["engines"][name]["sha256"]
                or left["engines"][name].get("library") != right["engines"][name].get("library")):
            raise ValueError(f"{name} build changed during static acceptance")
    for name in ENGINE_NAMES[1:]:
        if left["comparisons"][name]["atoms"] != right["comparisons"][name]["atoms"]:
            raise ValueError("static acceptance atom count changed")


def lammps_total_energy_guard(frames):
    contract = json.loads((ROOT / "tests/lammps_reference/manifest.json").read_text())
    limit = contract["tolerances"]["total_energy_per_atom_eV"]["atol"]
    maximum = max(frame["comparisons"]["lammps"]["total_energy_difference_per_atom_eV"]
                  for frame in frames)
    return {"max_abs_eV_per_atom": maximum, "atol_eV_per_atom": limit, "pass": maximum <= limit}


def evidence_file(path, expected, bundle_files):
    if bundle_files is not None:
        if expected not in bundle_files:
            raise ValueError(f"evidence missing from the handoff: {expected}")
        path = bundle_files[expected]
    path = Path(path)
    if sha256(path) != expected:
        raise ValueError(f"evidence changed: {path}")
    return path


def calibration_data(frames, bundle_files=None):
    if (len(frames) < 3 or any(frame["trial_evidence"] is None for frame in frames)
            or len({frame["trial_evidence"]["seed"] for frame in frames}) != len(frames)
            or len({frame["source"]["model"] for frame in frames}) != len(frames)):
        raise ValueError("need at least three distinct, independent calibration trials and snapshots")
    for frame in frames:
        evidence = frame["trial_evidence"]
        evidence_file(evidence["path"], evidence["sha256"], bundle_files)
        compatible(frame, frames[0])
    thresholds = {}
    for engine in ENGINE_NAMES[1:]:
        thresholds[engine] = {}
        for name, _, _ in QUANTITIES:
            fields = [frame["comparisons"][engine]["quantities"][name] for frame in frames]
            largest = max(field["max_abs"] for field in fields)
            guard_fraction = max(field["max_guard_fraction"] for field in fields)
            thresholds[engine][name] = {
                "largest_observed_abs": largest, "atol": CALIBRATION_MULTIPLIER * largest,
                "max_guard_fraction": guard_fraction, "calibration_guard_pass": guard_fraction <= 1.0}
    total_energy = lammps_total_energy_guard(frames)
    passed = total_energy["pass"] and all(field["calibration_guard_pass"]
        for values in thresholds.values() for field in values.values())
    return {"calibration_status": "PASS" if passed else "FAIL",
            "material": frames[0]["material"], "thresholds": thresholds,
            "lammps_total_energy_guard": total_energy,
            "snapshot_sha256": [frame["source"]["model"] for frame in frames],
            "trials": [frame["trial"] for frame in frames], "baseline": frames[0],
            "method": f"{CALIBRATION_MULTIPLIER:g} × largest calibration difference, with independent guard bands"}


def registered_wpe_diagnosis(path, baseline, bundle_files=None):
    path = path.resolve(strict=True)
    evidence = json.loads(path.read_text())
    identity = evidence["sha256"]
    engines = baseline["engines"]
    if (evidence["release_qualification_status"] != "PASS"
            or identity["gpumd_5_9_1"] != engines["gpumd"]["sha256"]
            or identity["wpe"] != engines["wpe"]["sha256"]
            or identity["wpe_library"] != engines["wpe"]["library"]["sha256"]
            or evidence["gpu"]["uuid"] != baseline["gpu_uuid"]
            or evidence["material_results"][baseline["material"]]["potential_sha256"]
                != baseline["source"]["potential"]
            or not evidence["evidence"]):
        raise ValueError("WPE diagnosis has not qualified the measured engine and GPU")
    for name, expected in evidence["evidence"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"invalid relative WPE diagnosis evidence path: {name}")
        source = path.parent / relative
        if bundle_files is None and not source.resolve(strict=True).is_relative_to(path.parent):
            raise ValueError(f"WPE diagnosis evidence points outside its directory: {name}")
        evidence_file(source, expected, bundle_files)
    return {"path": str(path), "sha256": sha256(path)}


def calibrate(args):
    frames, paths = read_measurements(args)
    data = calibration_data(frames)
    diagnosis = registered_wpe_diagnosis(args.wpe_diagnosis, data["baseline"])
    record(args.output, {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                          "measurements": paths, "wpe_diagnosis": diagnosis, **data})
    return 0 if data["calibration_status"] == "PASS" else 1


def checked_calibration(path, bundle_files=None):
    freeze = json.loads(path.read_text())
    frames = []
    for name, digest in freeze["measurements"].items():
        measurement = evidence_file(name, digest, bundle_files)
        frames.append(json.loads(measurement.read_text()))
    expected = calibration_data(frames, bundle_files)
    diagnosis = freeze["wpe_diagnosis"]
    diagnosis_path = evidence_file(diagnosis["path"], diagnosis["sha256"], bundle_files)
    registered_wpe_diagnosis(diagnosis_path, expected["baseline"], bundle_files)
    if any(freeze[key] != value for key, value in expected.items()):
        raise ValueError("frozen calibration differs from registered measurements")
    return freeze


def bundled_water_selection(bundle, manifest):
    index = manifest["evidence_sha256"]
    for name in WATER_SELECTION_FILES:
        relative = f"evidence/{name}"
        if relative not in index:
            raise ValueError(f"water selection evidence missing from the handoff: {relative}")
        evidence_file(bundle / relative, index[relative], None)
    return water_selection_provenance(bundle / "evidence", manifest["model"]["sha256"])


def verify_holdout_preparation(candidate, execution, bundle, manifest, approved_water=None):
    from prepare import SCR_EXTENSIONS, check_preparation, preparation_stages

    material = candidate["material"]
    evidence = candidate["trial_evidence"]
    bundled_stage = manifest.get("preparation_stage") if manifest else None
    stage = evidence.get("preparation_stage", "scr" if bundled_stage == "scr" else None)
    if stage is None and bundled_stage is None:
        if material == "water" or execution.parent.name in SCR_EXTENSIONS:
            raise ValueError("SCR extension holdout is missing its preparation stage")
        # Previously published bundles do not declare a preparation endpoint.
        return
    evidence_dir = bundle / "evidence" if bundle else execution.parents[1]
    stages = preparation_stages(material, evidence_dir=evidence_dir)
    if stage != stages[-1]:
        raise ValueError("holdout does not reference the selected preparation endpoint")
    if bundle:
        if manifest["material"] != material or manifest["preparation_stage"] != stage:
            raise ValueError("handoff preparation stage differs from static holdout")
        names = ["source.json"]
        for step in stages:
            names.extend(f"{step}/{filename}" for filename in
                         ("run.in", "execution.json", "thermo.out", "summary.json"))
        index = manifest["evidence_sha256"]
        for name in names:
            relative = f"evidence/{name}"
            if relative not in index:
                raise ValueError(f"preparation evidence missing from the handoff: {relative}")
            evidence_file(bundle / relative, index[relative], None)
        if evidence["sha256"] != index[f"evidence/{stage}/execution.json"]:
            raise ValueError("holdout execution differs from the packaged preparation endpoint")
        source, frame, reference = check_preparation(bundle, material, packaged=True)
        if manifest["model"] != frame or manifest["potential_sha256"] != source["potential_sha256"]:
            raise ValueError("handoff manifest differs from the packaged SCR input")
    else:
        source, frame, reference = check_holdout_execution(execution, material)
    if material == "water":
        approval = approved_water
        if approval is None:
            approval = (bundled_water_selection(bundle, manifest) if bundle
                        else water_selection_provenance(execution.parents[1], frame["sha256"]))
        check_holdout_selection(evidence, candidate["started_utc"], approval)
    if (candidate["source"] != {"model": frame["sha256"], "potential": source["potential_sha256"]}
            or candidate["engines"]["gpumd"]["sha256"] != reference):
        raise ValueError("holdout differs from the verified SCR preparation")


def verify(args):
    frame_path, calibration_path = args.frame, args.calibration
    bundle_files = base = manifest = approved_water = None
    if args.bundle:
        if calibration_path:
            raise ValueError("use either --bundle or the original --frame and --calibration")
        base = args.bundle.resolve(strict=True)
        manifest = json.loads((base / "manifest.json").read_text())
        index = manifest["evidence_sha256"]
        bundle_files = {}
        for name, digest in index.items():
            path = (base / name).resolve(strict=True)
            if not path.is_relative_to(base / "evidence"):
                raise ValueError(f"evidence points outside the handoff: {name}")
            bundle_files[digest] = path
        if manifest.get("material") == "water":
            approved_water = bundled_water_selection(base, manifest)
        frame_path = evidence_file(None, index["evidence/static-measurement.json"], bundle_files)
        calibration_path = evidence_file(None, index["evidence/static-calibration.json"], bundle_files)
    elif not calibration_path:
        raise ValueError("--frame requires --calibration")
    candidate = json.loads(frame_path.read_text())
    freeze = checked_calibration(calibration_path, bundle_files)
    if candidate["trial"] != "holdout":
        raise ValueError("holdout measurement must be labeled --trial holdout")
    if candidate["source"]["model"] in freeze["snapshot_sha256"]:
        raise ValueError("holdout was used for calibration")
    evidence = candidate["trial_evidence"]
    if (not evidence or evidence["calibration_sha256"] != sha256(calibration_path)
            or evidence["model_sha256"] != candidate["source"]["model"]):
        raise ValueError("holdout SCR or calibration evidence changed")
    execution = evidence_file(evidence["path"], evidence["sha256"], bundle_files)
    verify_holdout_preparation(candidate, execution, base, manifest, approved_water)
    compatible(candidate, freeze["baseline"])
    if candidate["started_utc"] <= freeze["created_utc"]:
        raise ValueError("holdout static measurement predates tolerance freeze")
    results = {}
    for engine in ENGINE_NAMES[1:]:
        results[engine] = {}
        for name, _, _ in QUANTITIES:
            field = candidate["comparisons"][engine]["quantities"][name]
            limit = freeze["thresholds"][engine][name]
            results[engine][name] = {
                **field, "frozen_atol": limit["atol"],
                "pass": (limit["calibration_guard_pass"] and field["max_guard_fraction"] <= 1.0
                         and field["max_abs"] <= limit["atol"])}
    total_energy = lammps_total_energy_guard([candidate])
    passed = freeze["calibration_status"] == "PASS" and total_energy["pass"] and all(
        field["pass"] for values in results.values() for field in values.values())
    record(args.output, {"schema_version": 1, "material": candidate["material"],
                          "lammps_total_energy_guard": total_energy,
                          "calibration_sha256": sha256(calibration_path),
                          "holdout_measurement_sha256": sha256(frame_path),
                          "numeric_status": "PASS" if passed else "FAIL", "results": results})
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("measure")
    run.add_argument("--material", required=True, choices=MATERIALS)
    for name in ("model", "potential", *(f"{engine}-bin" for engine in ENGINE_NAMES)):
        run.add_argument(f"--{name}", required=True, type=Path)
    run.add_argument("--lammps-geometry", choices=lammps_inputs.GEOMETRY_MODES, default="native")
    run.add_argument("--gpu", required=True, help="full NVIDIA GPU UUID")
    run.add_argument("--trial", required=True, help="independent source run identifier")
    run.add_argument("--calibration", type=Path, help="passing frozen calibration required for holdout")
    run.add_argument("--check-guards", action="store_true", help="return 1 if any existing guard fails")
    run.add_argument("--trial-evidence", type=Path, help="execution.json from the independent source job")
    run.set_defaults(function=measure)

    freeze = sub.add_parser("calibrate")
    freeze.add_argument("--frames", nargs="+", type=Path, required=True)
    freeze.add_argument("--wpe-diagnosis", type=Path, required=True)
    freeze.set_defaults(function=calibrate)
    holdout = sub.add_parser("verify")
    source = holdout.add_mutually_exclusive_group(required=True)
    source.add_argument("--frame", type=Path)
    source.add_argument("--bundle", type=Path, help="published handoff directory, including manifest.json")
    holdout.add_argument("--calibration", type=Path)
    holdout.set_defaults(function=verify)
    for command in (run, freeze, holdout):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return args.function(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
