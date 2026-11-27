#!/usr/bin/env python3
"""Run a two-step Berendsen restart and record GPU capacity in a fresh job directory."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/benchmark"))
import run_benchmark as bench
import lammps_inputs
from prepare import CONDITIONS, FIXTURES, pressure_ensemble

ENGINES = ("gpumd", "dmgmd", "lammps", "wpe")


def write_job(directory, material, engine, ranks, lammps_geometry):
    fixture = FIXTURES[material]
    dt = fixture["time_step_fs"]
    bulk = fixture["bulk_modulus_GPa"]
    if engine == "lammps":
        source = (directory / "nep.txt").read_bytes().decode()
        converted, identity = lammps_inputs.write_matpl_potentials(directory, source)
        active = ["Cu"] if material == "copper" else None
        model = (directory / "model.xyz").read_text()
        data, faces = lammps_inputs.model_data(model, converted, active_species=active,
                                               gpumd_wraps=1 if lammps_geometry != "native" else 0)
        (directory / "data.lmp").write_text(data)
        del data
        identity["initial_face_counts"] = {"upper_xyz": list(map(len, faces[:3])),
                                           "lower_xyz": list(map(len, faces[3:]))}
        bench.save(directory / "conversion.json", identity)
        # Two steps exercise read_data, force evaluation and box updates. Output matches
        # the benchmark reader's two finite thermo segments. LAMMPS metal uses ps and bar.
        temperature = CONDITIONS["temperature_K"]
        pressure_bar = CONDITIONS["pressure_GPa"] * 10000
        tdamp_ps = CONDITIONS["temperature_damping_steps"] * dt / 1000
        pdamp_ps = CONDITIONS["pressure_damping_steps"] * dt / 1000
        fixes = (
            f"fix thermostat all temp/berendsen/kk {temperature:g} {temperature:g} {tdamp_ps:g}",
            f"fix barostat all press/berendsen iso {pressure_bar:g} {pressure_bar:g} "
            f"{pdamp_ps:g} modulus {bulk * 10000:g}",
        )
        script = lammps_inputs.input_file(converted, dt, 1, warmup=1, active_species=active,
                                          geometry=lammps_geometry, initial_faces=faces,
                                          y_ranks=ranks, integration_fixes=fixes)
        (directory / "in.lmp").write_text(script)
        return
    axis = " y" if engine == "gpumd" and ranks > 1 else ""
    ensemble = pressure_ensemble(fixture, "ber") + "\n"
    (directory / "run.in").write_text(
        f"potential nep.txt{axis}\ntime_step {dt}\n{ensemble}run 1\n{ensemble}dump_thermo 1\nrun 1\n"
    )


def command_for(engine, binary, uuids, env):
    ranks = len(uuids)
    if engine == "dmgmd":
        env.update(DMGMD_DOMAIN_NPT="affine", DMGMD_COMM_BACKEND="HostStaged")
        return ["mpirun", "-n", str(ranks), str(binary)]
    if engine == "lammps":
        env["LAMMPS_RANK_GPU_UUIDS"] = ",".join(uuids)
        return ["mpirun", "-n", str(ranks), sys.executable,
                str(ROOT / "tests/lammps_reference/launch_rank.py"), "--binary", str(binary)]
    return [str(binary)]


def wpe_library_identity(binary, env):
    linkage = subprocess.check_output(["ldd", str(binary)], text=True, env=env)
    match = re.search(r"libwisevolve_potential\.so\S* => (\S+)", linkage)
    if not match:
        raise ValueError("unable to resolve the WPE shared library")
    path = Path(match[1]).resolve(strict=True)
    return {"path": str(path), "sha256": bench.sha(path)}


def verify_job(directory, engine, uuids, atoms, lammps_geometry="native"):
    log = (directory / "stdout.txt").read_text()
    ranks = len(uuids)
    bench.parse_timing(log, engine, ranks, atoms, 1, 1, "HostStaged", uuids)
    if engine == "lammps":
        script = (directory / "in.lmp").read_text()
        lammps_inputs.verify_geometry(log, lammps_geometry, initial_faces=" initial_face_images " in script)
        bench.parse_lammps_thermo(log, 2, atoms)
        if not re.search(rf"\b1 by {ranks} by 1 MPI processor grid\b", log):
            raise ValueError("LAMMPS y processor grid differs from the requested grid")
    else:
        bench.parse_thermo((directory / "thermo.out").read_text())
    if engine == "dmgmd":
        bindings = [bench.fields(line) for line in log.splitlines() if line.startswith("DMGMD_MPI rank=")]
        if (set(b["cuda_uuid"].lower() for b in bindings)
                != {u[4:].replace("-", "").lower() for u in uuids}):
            raise ValueError("DMG-MD GPU identities differ from selected devices")
        if ranks > 1 and "DMGMD_DOMAIN_NPT mode=affine" not in log:
            raise ValueError("missing local affine NPT evidence")
    elif engine in ("gpumd", "wpe"):
        if re.findall(r"^number of GPUs = (\d+)$", log, re.M) != [str(ranks)]:
            raise ValueError("GPUMD visible GPU count mismatch")
        if f"Number of atoms is {atoms}." not in log:
            raise ValueError("GPUMD atom count mismatch")
        versions = bench.reported_versions(engine, log)
        if engine == "gpumd" and versions.get("GPUMD") != "5.9.1":
            raise ValueError("ordinary GPUMD 5.9.1 required")
        if engine == "wpe" and versions.get("WPE") != "1.0.0":
            raise ValueError("expected Wisevolve Potential Engine 1.0.0")
    if engine == "dmgmd":
        fields = next(bench.fields(line) for line in log.splitlines() if line.startswith("DMGMD_DOMAIN mode="))
        routing = {"domain": fields, "backend": "HostStaged", "npt": "affine" if ranks > 1 else "single GPU"}
    elif engine == "lammps":
        routing = {"pair_style": "matpl/nep/kk", "processor_grid": [1, ranks, 1]}
    else:
        routing = {"multi_gpu_nep": ranks > 1, "gpu_count": ranks}
        if ranks > 1:
            if (directory / "run.in").read_text().splitlines()[0] != "potential nep.txt y":
                raise ValueError("expected explicit GPUMD y-axis partition")
            routing["requested_partition_axis"] = "y"
    return {"versions": bench.reported_versions(engine, log), "routing": routing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material", choices=FIXTURES, required=True)
    parser.add_argument("--engine", choices=ENGINES, required=True)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--lammps-geometry", choices=lammps_inputs.GEOMETRY_MODES, default="native")
    parser.add_argument("--devices", required=True, help="comma-separated indices or UUIDs")
    parser.add_argument("--model", required=True, type=Path, help="common velocity-bearing snapshot")
    parser.add_argument("--potential", required=True, type=Path)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--potential-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path, help="new job directory")
    args = parser.parse_args()
    if args.engine != "lammps" and args.lammps_geometry != "native":
        parser.error("--lammps-geometry requires --engine lammps")
    directory = args.output.resolve()
    binary = bench.installed_binary(args.binary, args.engine)
    before = bench.snapshot()
    uuids = bench.resolve_devices(args.devices.split(","), before)
    if len(uuids) not in (1, 2, 4) or (args.engine == "wpe" and len(uuids) != 1):
        parser.error("use 1, 2 or 4 GPUs, and exactly one for WPE")
    if any(int(gpu["memory.used"]) > 0 for gpu in before["gpus"] if gpu["uuid"] in uuids):
        parser.error("selected GPUs are in use")
    directory.mkdir(parents=True, exist_ok=False)
    result = {"material": args.material, "engine": args.engine, "binary": str(binary),
              "binary_sha256": bench.sha(binary), "devices": uuids, "ensemble": "npt_ber", "steps": 2}
    try:
        for source, name, digest in ((args.model, "model.xyz", args.model_sha256),
                                     (args.potential, "nep.txt", args.potential_sha256)):
            shutil.copyfile(source, directory / name)
            actual = bench.sha(directory / name)
            if actual != digest:
                raise ValueError(f"{name} SHA-256 mismatch: {actual}")
            result[name + "_sha256"] = actual
        with (directory / "model.xyz").open() as stream:
            result["atoms"] = int(stream.readline())
            if "vel:R:3" not in stream.readline():
                raise ValueError("snapshot must include atom velocities")
        write_job(directory, args.material, args.engine, len(uuids), args.lammps_geometry)
        if args.engine == "lammps":
            result["geometry"] = args.lammps_geometry
        env = {k: v for k, v in os.environ.items() if not k.startswith("DMGMD_")}
        env.update(CUDA_VISIBLE_DEVICES=",".join(uuids), CUDA_DEVICE_ORDER="PCI_BUS_ID", OMP_NUM_THREADS="1")
        if args.engine == "wpe":
            result["library"] = wpe_library_identity(binary, env)
        command = command_for(args.engine, binary, uuids, env)
        result["command"] = command
        code, _, timeout = bench.execute(command, directory, env, 900, 0.2)
        result.update(returncode=code, timed_out=timeout)
        peaks = dict.fromkeys(uuids, 0)
        for line in (directory / "telemetry.jsonl").read_text().splitlines():
            for gpu in json.loads(line)["gpus"]:
                if gpu["uuid"] in peaks:
                    peaks[gpu["uuid"]] = max(peaks[gpu["uuid"]], int(gpu["memory.used"]))
        result["sampled_peak_MiB"] = peaks
        result["total_MiB"] = {g["uuid"]: int(g["memory.total"]) for g in before["gpus"] if g["uuid"] in peaks}
        if code or timeout:
            raise ValueError("engine did not complete both steps; see stdout.txt and stderr.txt")
        if min(peaks.values()) == 0:
            raise ValueError("missing selected-device allocation evidence")
        result.update(verify_job(directory, args.engine, uuids, result["atoms"], args.lammps_geometry))
        result["status"] = "PASS"
    except (ValueError, OSError, KeyError) as error:
        result.update(status="FAIL", error=str(error))
        if result.get("returncode") and not result.get("timed_out"):
            log = "\n".join((directory / name).read_text(errors="replace") for name in ("stdout.txt", "stderr.txt"))
            oom = re.search(r"(?im)^.*(?:cudaErrorMemoryAllocation|(?:CUDA|cudaMalloc).*out of memory).*$", log)
            if oom:
                result.update(status="CAPACITY_LIMIT", diagnostic=oom.group().strip())
    bench.save(directory / "result.json", result)
    print(json.dumps(result, indent=2))
    return int(result["status"] != "PASS")


if __name__ == "__main__":
    sys.exit(main())
