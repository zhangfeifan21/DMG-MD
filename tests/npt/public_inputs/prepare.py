#!/usr/bin/env python3
"""Generate and prepare the three public V1.0 NPT systems with GPUMD 5.9.1."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = (str(ROOT / "tests/long_nve"), str(ROOT / "tests/baseline"))
from analyze_preparation import CONDITIONS, MANIFEST, STAGES, check_stage, read_thermo, thermo_schedule
from long_nve_common import diamond_atoms, water_atoms
from run_baselines import sha256

FIXTURES = MANIFEST["materials"]
GPUMD_VERSION = MANIFEST["gpumd_version"]
INITIAL_VELOCITY_SEED = CONDITIONS["initial_velocity_seed"]
CALIBRATION_SEEDS = tuple(STAGES["calibration"]["velocity_seeds"])
CALIBRATION_STEPS = STAGES["calibration"]["segments"][0]["steps"]
NPT_PROFILES = {
    "ber": "berendsen",
    "scr": "scr",
    "scr-extension": "scr-extension",
    "scr-extension-2": "scr-extension",
}
SCR_STAGES = ("scr", "scr-extension", "scr-extension-2")
SCR_EXTENSIONS = SCR_STAGES[1:]
LEGACY_ESS_THRESHOLD = 100
LEGACY_ESS_KEY = f"volume_effective_samples_gt_{LEGACY_ESS_THRESHOLD}"


def preparation_stages(material: str, through_stage: str | None = None,
                       *, evidence_dir: Path | None = None) -> tuple[str, ...]:
    stages = SCR_STAGES if material == "water" else ("scr",)
    if through_stage is None:
        through_stage = stages[0]
        if material == "water":
            latest = evidence_dir / SCR_EXTENSIONS[-1] if evidence_dir is not None else None
            # An incomplete stage or a dangling link must fail validation instead of falling back.
            through_stage = (SCR_EXTENSIONS[-1]
                             if latest is not None and (latest.exists() or latest.is_symlink())
                             else SCR_EXTENSIONS[0])
    if through_stage not in stages:
        raise ValueError(f"unsupported preparation endpoint for {material}: {through_stage}")
    return ("ber", *stages[:stages.index(through_stage) + 1])


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def fcc_atoms(fixture: dict):
    nx, ny, nz = fixture["cells"]
    a = fixture["lattice_constant_A"]
    mass = fixture["masses_amu"]["Cu"]
    basis = ((0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0))
    atoms = [
        ("Cu", (i + x) * a, (j + y) * a, (k + z) * a, mass)
        for i in range(nx) for j in range(ny) for k in range(nz) for x, y, z in basis
    ]
    return atoms, [n * a for n in fixture["cells"]]


def generate_start(args: argparse.Namespace, output: Path, fixture: dict) -> dict:
    source = output / "source.json"
    model = output / "public-model.xyz"
    potential = output / "nep.txt"
    if source.is_file():
        record = json.loads(source.read_text())
        expected = {
            **CONDITIONS,
            **{key: value for key, value in fixture.items() if key != "public_model_sha256"},
            "material": args.material,
            "calibration_seeds": list(CALIBRATION_SEEDS),
            "calibration_steps": CALIBRATION_STEPS,
        }
        if (any(record.get(key) != value for key, value in expected.items())
                or record["model_sha256"] != fixture["public_model_sha256"]
                or sha256(model) != fixture["public_model_sha256"]
                or sha256(potential) != fixture["potential_sha256"]):
            raise ValueError(f"public inputs changed in {output}")
        return record
    if args.material == "copper":
        if args.copper_potential is None:
            raise ValueError(f"provide --copper-potential from {fixture['potential_source']}")
        original = args.copper_potential
        atoms, lengths = fcc_atoms(fixture)
    elif args.material == "water":
        original = ROOT / fixture["potential_source"]
        atoms, lengths = water_atoms(fixture, fixture["geometry_seed"])
    else:
        original = ROOT / fixture["potential_source"]
        atoms, lengths = diamond_atoms(fixture, INITIAL_VELOCITY_SEED)
    if sha256(original) != fixture["potential_sha256"]:
        raise ValueError(f"unexpected SHA-256 for {original}")
    shutil.copyfile(original, potential)
    lx, ly, lz = lengths
    with model.open("w", encoding="ascii") as stream:
        stream.write(
            f'{len(atoms)}\npbc="T T T" Lattice="{lx:.17g} 0 0 0 {ly:.17g} 0 0 0 {lz:.17g}" '
            "Properties=species:S:1:pos:R:3:mass:R:1\n"
        )
        for species, x, y, z, mass in atoms:
            stream.write(f"{species} {x:.17g} {y:.17g} {z:.17g} {mass:.17g}\n")
    digest = sha256(model)
    if digest != fixture["public_model_sha256"]:
        raise ValueError(f"generated model SHA-256 differs from the registered fixture: {model}")
    record = {
        **fixture,
        **CONDITIONS,
        "material": args.material,
        "atoms": len(atoms),
        "box_lengths_A": lengths,
        "model_sha256": digest,
        "calibration_seeds": CALIBRATION_SEEDS,
        "calibration_steps": CALIBRATION_STEPS,
        "potential_path": str(original.resolve()),
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "command": sys.argv,
    }
    write_json(source, record)
    return record


def pressure_ensemble(fixture: dict, variant: str) -> str:
    name = STAGES[NPT_PROFILES[variant]]["ensemble"]
    temperature = CONDITIONS["temperature_K"]
    tdamp = CONDITIONS["temperature_damping_steps"]
    pressure = CONDITIONS["pressure_GPa"]
    pdamp = CONDITIONS["pressure_damping_steps"]
    return (
        f"ensemble {name} {temperature:g} {temperature:g} {tdamp:g} "
        f"{pressure:g} {fixture['bulk_modulus_GPa']:g} {pdamp:g}"
    )


def input_script(fixture: dict, stage: str, seed: int | None) -> str:
    lines = ["potential nep.txt", f"time_step {fixture['time_step_fs']:g}"]
    temperature = CONDITIONS["temperature_K"]
    if seed is not None:
        lines.append(f"velocity {temperature:g} seed {seed}")
    profile = NPT_PROFILES.get(stage, "calibration")
    if profile == "calibration":
        name = STAGES[profile]["ensemble"]
        tdamp = CONDITIONS["temperature_damping_steps"]
        ensemble = f"ensemble {name} {temperature:g} {temperature:g} {tdamp:g}"
    else:
        ensemble = pressure_ensemble(fixture, stage)
    for steps, interval in thermo_schedule(profile):
        lines.extend([ensemble, f"dump_thermo {interval}", f"dump_restart {steps}", f"run {steps}"])
    return "\n".join(lines) + "\n"


def snapshot_record(path: Path, initial_model: Path, fixture: dict) -> dict:
    """Check GPUMD's restart against the starting element, count and mass order."""
    expected = "Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3"
    masses = fixture["masses_amu"]
    counts = dict.fromkeys(masses, 0)
    nonzero = 0
    with path.open() as frame, initial_model.open() as initial:
        count = int(next(frame))
        header = next(frame)
        initial_count = int(next(initial))
        next(initial)
        if count != initial_count or 'pbc="T T T"' not in header or expected not in header:
            raise ValueError(f"invalid restart header: {path}")
        lattice = [float(value) for value in header.split('Lattice="')[1].split('"')[0].split()]
        if len(lattice) != 9 or any(lattice[index] != 0 for index in (1, 2, 3, 5, 6, 7)):
            raise ValueError(f"restart cell is not orthorhombic: {path}")
        lengths = [lattice[0], lattice[4], lattice[8]]
        if any(not math.isfinite(value) or value <= 0 for value in lengths):
            raise ValueError(f"invalid restart cell lengths: {path}")
        for number, (row, original) in enumerate(zip(frame, initial, strict=True), start=1):
            original_species = original.split(maxsplit=1)[0]
            species, *values = row.split()
            x, y, z, mass, vx, vy, vz = map(float, values)
            if species != original_species or mass != masses[species]:
                raise ValueError(f"restart atom {number} changed species or mass: {path}")
            if not all(math.isfinite(value) for value in (x, y, z, vx, vy, vz)):
                raise ValueError(f"nonfinite restart atom {number}: {path}")
            counts[species] += 1
            nonzero += any(value != 0 for value in (vx, vy, vz))
    if sum(counts.values()) != count or nonzero != count:
        raise ValueError(f"restart missing atoms or velocities: {path}")
    return {
        "atoms": count,
        "species_counts": counts,
        "nonzero_velocity_atoms": nonzero,
        "box_lengths_A": lengths,
        "sha256": sha256(path),
        "volume_A3": math.prod(lengths),
    }


def device_sample(uuid: str) -> str:
    return subprocess.check_output(
        ["nvidia-smi", "-i", uuid,
         "--query-gpu=timestamp,index,uuid,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
        text=True,
    )


def check_thermo(path: Path, stage: str, fixture: dict, expected_atoms: int) -> None:
    schedule = thermo_schedule(NPT_PROFILES.get(stage, "calibration"))
    atoms, _ = read_thermo(path, schedule, fixture["time_step_fs"])
    if atoms != expected_atoms:
        raise ValueError(f"unexpected atom count in {path}: {atoms}, expected {expected_atoms}")


def check_completed_stage(directory: Path, stage: str, fixture: dict, seed: int | None,
                          model_sha: str, binary_sha: str,
                          *, resumes_execution_sha256: str | None = None,
                          packaged: bool = False) -> dict:
    record = json.loads((directory / "execution.json").read_text())
    if record["status"] != "PASS" or record["exit_code"] != 0:
        raise ValueError(f"{stage} has not completed: {directory}")
    expected = {
        "velocity_seed": seed,
        "model_in_sha256": model_sha,
        "gpumd_binary_sha256": binary_sha,
        "potential_sha256": fixture["potential_sha256"],
        "run_in_sha256": hashlib.sha256(input_script(fixture, stage, seed).encode("ascii")).hexdigest(),
    }
    if stage in SCR_EXTENSIONS:
        expected["resumes_execution_sha256"] = resumes_execution_sha256
        if resumes_execution_sha256 is None:
            raise ValueError("the SCR extension requires its predecessor execution identity")
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError(f"recorded configuration differs from requested {stage}: {directory}")
    hashes = {"run.in": expected["run_in_sha256"]}
    if not packaged:
        hashes.update({
            "model.xyz": model_sha,
            "nep.txt": fixture["potential_sha256"],
            "restart.xyz": record["snapshot"]["sha256"],
        })
    if stage in SCR_EXTENSIONS or "thermo_sha256" in record:
        hashes["thermo.out"] = record["thermo_sha256"]
    if any(sha256(directory / filename) != digest for filename, digest in hashes.items()):
        raise ValueError(f"recorded input or output has changed for {stage}: {directory}")
    check_thermo(directory / "thermo.out", stage, fixture, record["snapshot"]["atoms"])
    return record


def check_analysis(thermo: Path, summary_path: Path, stage: str, fixture: dict,
                   expected_atoms: int) -> None:
    profile = NPT_PROFILES[stage]
    schedule = thermo_schedule(profile)
    dt = fixture["time_step_fs"]
    atoms, samples = read_thermo(thermo, schedule, dt)
    summary = json.loads(summary_path.read_text())
    measured = check_stage(profile, samples)
    checks = measured["checks"]
    if stage == "scr" and LEGACY_ESS_KEY in summary["checks"]:
        # The frozen 20k records used this extra gate. Recompute its original verdict.
        volume_ess = measured["effective_samples"]["volume_A3"]["effective_samples"]
        checks[LEGACY_ESS_KEY] = volume_ess > LEGACY_ESS_THRESHOLD
    status = "PASS" if all(checks.values()) else "FAIL"
    expected = {
        **measured,
        "stage": profile,
        "thermo_sha256": sha256(thermo),
        "atoms": expected_atoms,
        "time_step_fs": dt,
        "schedule_steps_stride": [list(segment) for segment in schedule],
        "observed_samples": len(samples),
        "status": status,
    }
    # The recorded source path is informational so copied evidence can be replayed.
    recorded_thermo = summary.pop("thermo", None)
    if atoms != expected_atoms or not isinstance(recorded_thermo, str) or not recorded_thermo or summary != expected:
        raise ValueError(f"{stage} preparation analysis is stale: {summary_path}")
    if status != "PASS":
        raise ValueError(f"{stage} preparation failed the recorded criteria: {summary_path}")


def check_preparation(base: Path, material: str, *,
                      through_stage: str | None = None, packaged: bool = False) -> tuple[dict, dict, str]:
    """Bind the selected restart to the public input and each recorded stage."""
    fixture = FIXTURES[material]
    evidence = base / "evidence" if packaged else base
    stages = preparation_stages(material, through_stage, evidence_dir=evidence)
    source = json.loads((evidence / "source.json").read_text())
    model = base / "public-model.xyz"
    if (source["material"] != material
            or source["model_sha256"] != fixture["public_model_sha256"]
            or source["potential_sha256"] != fixture["potential_sha256"]
            or (not packaged and sha256(model) != source["model_sha256"])
            or sha256(base / "nep.txt") != fixture["potential_sha256"]):
        raise ValueError("source identity mismatch")
    ref_sha = json.loads((evidence / "ber/execution.json").read_text())["gpumd_binary_sha256"]
    previous = source["model_sha256"]
    previous_execution = None
    for stage in stages:
        job = evidence / stage
        seed = INITIAL_VELOCITY_SEED if stage == "ber" else None
        record = check_completed_stage(job, stage, fixture, seed, previous, ref_sha,
                                       resumes_execution_sha256=previous_execution, packaged=packaged)
        if record["snapshot"]["atoms"] != source["atoms"]:
            raise ValueError(f"{stage} snapshot atom count differs from the public input")
        summary = (job if packaged else job / "analysis") / "summary.json"
        check_analysis(job / "thermo.out", summary, stage, fixture, source["atoms"])
        previous = record["snapshot"]["sha256"]
        previous_execution = sha256(job / "execution.json")
    if packaged:
        frame = record["snapshot"]
        if sha256(base / "model.xyz") != frame["sha256"]:
            raise ValueError("packaged SCR snapshot changed")
    else:
        frame = snapshot_record(base / stages[-1] / "restart.xyz", model, fixture)
        if frame != record["snapshot"]:
            raise ValueError("SCR snapshot changed")
    return source, frame, ref_sha


def run_stage(args: argparse.Namespace, output: Path, fixture: dict, stage: str,
              model: Path, seed: int | None, expected_model_sha: str,
              *, resumes_execution_sha256: str | None = None) -> None:
    directory = output / stage
    result = directory / "execution.json"
    source = output / "nep.txt"
    if sha256(model) != expected_model_sha or sha256(source) != fixture["potential_sha256"]:
        raise ValueError(f"input hash mismatch for {stage}")
    binary_sha = sha256(args.gpumd_bin)
    if directory.exists():
        check_completed_stage(directory, stage, fixture, seed, expected_model_sha, binary_sha,
                              resumes_execution_sha256=resumes_execution_sha256)
        return
    directory.mkdir()
    shutil.copyfile(model, directory / "model.xyz")
    shutil.copyfile(source, directory / "nep.txt")
    if sha256(directory / "model.xyz") != expected_model_sha:
        raise ValueError(f"copied snapshot hash mismatch for {stage}")
    script = directory / "run.in"
    script.write_text(input_script(fixture, stage, seed), encoding="ascii")
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = args.device
    env["CUDA_CACHE_PATH"] = str(output / "cuda-cache")
    (output / "cuda-cache").mkdir(exist_ok=True)
    command = ["stdbuf", "-oL", "-eL", str(args.gpumd_bin)]
    record = {
        "status": "RUNNING",
        "command": command,
        "gpumd_binary_sha256": binary_sha,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_in_sha256": expected_model_sha,
        "potential_sha256": fixture["potential_sha256"],
        "run_in_sha256": sha256(script),
        "cuda_visible_devices": args.device,
        "velocity_seed": seed,
    }
    if resumes_execution_sha256 is not None:
        record["resumes_execution_sha256"] = resumes_execution_sha256
    write_json(result, record)
    started = time.monotonic()
    with ((directory / "stdout.log").open("w") as stdout,
          (directory / "stderr.log").open("w") as stderr,
          (directory / "gpu.csv").open("w") as telemetry):
        telemetry.write("timestamp,gpu_index,uuid,memory_used_MiB,utilization_percent\n")
        with subprocess.Popen(command, cwd=directory, env=env, stdout=stdout, stderr=stderr) as process:
            record["pid"] = process.pid
            write_json(result, record)
            while process.poll() is None:
                telemetry.write(device_sample(args.device))
                telemetry.flush()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            record["exit_code"] = process.returncode
    record["elapsed_seconds"] = time.monotonic() - started
    try:
        log = (directory / "stdout.log").read_text()
        required_segments = len(STAGES[NPT_PROFILES.get(stage, "calibration")]["segments"])
        version = re.escape(GPUMD_VERSION)
        version_banner = rf"(?i)(?:version\s*[:=]?\s*|gpumd-v){version}\b"
        neptype = rf"Use the NEP4 potential with\s+{len(fixture['masses_amu'])} atom type"
        # 'PASS' here records process/output integrity. Equilibration is a separate analysis.
        if (record["exit_code"] != 0 or not re.search(r"number of GPUs\s*=\s*1\b", log)
                or "Try to use" in log or "Wisevolve Potential Engine" in log
                or not re.search(neptype, log) or log.count("Time used for this run") != required_segments
                or not re.search(version_banner, log)):
            raise ValueError(f"GPUMD {GPUMD_VERSION} execution or identity check failed: {directory}")
        if args.material == "copper" and not re.search(r"ZBL", log):
            raise ValueError(f"expected a ZBL potential: {directory}")
        velocity_message = f"Initialized velocities with input T = {CONDITIONS['temperature_K']:g} K."
        if seed is not None and velocity_message not in log:
            raise ValueError(f"GPUMD did not initialize velocities from run.in: {directory}")
        if seed is None and "Initialized velocities with input T" in log:
            raise ValueError(f"GPUMD reinitialized restart velocities: {directory}")
        thermo = directory / "thermo.out"
        record["snapshot"] = snapshot_record(directory / "restart.xyz", output / "public-model.xyz", fixture)
        check_thermo(thermo, stage, fixture, record["snapshot"]["atoms"])
        record["thermo_sha256"] = sha256(thermo)
        record["status"] = "PASS"
    except (OSError, StopIteration, ValueError) as error:
        record["status"] = "FAIL"
        record["error"] = str(error)
        raise
    finally:
        write_json(result, record)


def analyze_stage(output: Path, fixture: dict, stage: str, analysis: str) -> None:
    directory = output / stage
    subprocess.run([
        sys.executable, str(Path(__file__).with_name("analyze_preparation.py")),
        analysis, "--thermo", str(directory / "thermo.out"),
        "--time-step-fs", str(fixture["time_step_fs"]),
        "--output-dir", str(directory / "analysis"),
    ], check=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def prepare(args: argparse.Namespace, output: Path) -> None:
    fixture = FIXTURES[args.material]
    if args.stage in SCR_EXTENSIONS:
        predecessor = SCR_STAGES[SCR_STAGES.index(args.stage) - 1]
        _, frame, ref_sha = check_preparation(output, args.material, through_stage=predecessor)
        if sha256(args.gpumd_bin) != ref_sha:
            raise ValueError("the SCR extension requires the same GPUMD binary as preparation")
        original = output / predecessor
        run_stage(args, output, fixture, args.stage, original / "restart.xyz", None, frame["sha256"],
                  resumes_execution_sha256=sha256(original / "execution.json"))
        analyze_stage(output, fixture, args.stage, NPT_PROFILES[args.stage])
        return
    source = generate_start(args, output, fixture)
    if args.stage == "generate":
        print(json.dumps(source, indent=2))
        return
    model = output / "public-model.xyz"
    model_sha = source["model_sha256"]
    if args.stage == "calibration":
        for seed in CALIBRATION_SEEDS:
            run_stage(args, output, fixture, f"calibration-{seed}", model, seed, model_sha)
    elif args.stage == "ber":
        run_stage(args, output, fixture, "ber", model, INITIAL_VELOCITY_SEED, model_sha)
    else:
        ber = check_completed_stage(output / "ber", "ber", fixture, INITIAL_VELOCITY_SEED,
                                    model_sha, sha256(args.gpumd_bin))
        analyze_stage(output, fixture, "ber", "berendsen")
        run_stage(args, output, fixture, "scr", output / "ber" / "restart.xyz",
                  None, ber["snapshot"]["sha256"])
        analyze_stage(output, fixture, "scr", "scr")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("generate", "calibration", "ber", *SCR_STAGES, "check"))
    parser.add_argument("--material", choices=FIXTURES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpumd-bin", type=Path)
    parser.add_argument("--device", help="GPU UUID from nvidia-smi")
    parser.add_argument("--copper-potential", type=Path)
    parser.add_argument("--through-stage", choices=SCR_STAGES, help="endpoint for offline check")
    args = parser.parse_args()
    if args.stage == "check":
        evidence = args.output.resolve(strict=True)
        source, frame, ref_sha = check_preparation(evidence, args.material, through_stage=args.through_stage)
        print(json.dumps({"stages": preparation_stages(args.material, args.through_stage, evidence_dir=evidence),
                          "source": source, "snapshot": frame, "gpumd_binary_sha256": ref_sha}, indent=2))
        return
    if args.through_stage:
        parser.error("--through-stage is only valid with check")
    if args.stage in SCR_EXTENSIONS and args.material != "water":
        parser.error(f"{args.stage} is registered for water only")
    if args.stage != "generate":
        if args.gpumd_bin is None or not args.device or not args.device.startswith("GPU-"):
            parser.error("running a stage requires --gpumd-bin and a --device GPU UUID")
        args.gpumd_bin = args.gpumd_bin.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare(args, output)
    except BlockingIOError as error:
        raise ValueError(f"another preparation process is using {output}") from error
    finally:
        os.close(directory)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
