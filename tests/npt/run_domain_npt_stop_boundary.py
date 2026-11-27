#!/usr/bin/env python3
"""Verify production local-NPT startup recovery and the last valid output."""
import argparse
import itertools
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess

import run_domain_npt_contracts as contract
import run_domain_npt as domain
import generate_inputs
import check_environment


OUTPUTS = ("thermo.out", "trajectory.xyz", "restart.xyz")
REQUIRED_SLAB = 16.0  # NEP4-C radial reach 7 Å in both hops, plus two 1 Å skins.
LAST_VALID_STEP = 2


def sparse_carbon(ranks, slab):
    """Separate all carbon atoms by more than the NEP cutoff."""
    length = ranks * slab
    lines = [
        str(ranks),
        f'pbc="T T T" Lattice="{length} 0 0 0 24 0 0 0 24" '
        'Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3',
    ]
    for rank in range(ranks):
        vx = 0.003 if rank % 2 else -0.003
        lines.append(f"C {(rank + 0.5) * slab} 12 12 12.011 {vx} 0.001 0")
    return "\n".join(lines) + "\n"


def launch(stage, model, run, command, environment, timeout):
    stage.mkdir()
    (stage / "model.xyz").write_text(model)
    (stage / "run.in").write_text(run)
    shutil.copyfile(contract.ROOT / "tests/baseline/inputs/potentials/nep_C.txt", stage / "nep.txt")
    with (stage / "stdout.log").open("w") as out, (stage / "stderr.log").open("w") as err:
        proc = subprocess.Popen(
            command, cwd=stage, env=environment, stdout=out, stderr=err, start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
    log = "\n".join((stage / name).read_text(errors="replace") for name in ("stdout.log", "stderr.log"))
    return {
        "command": command,
        "environment": {
            key: environment[key] for key in sorted(environment)
            if key.startswith("DMGMD_") or key in (
                "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "OMP_NUM_THREADS",
            )
        },
        "returncode": proc.returncode,
        "files_sha256": {path.name: contract.sha(path) for path in sorted(stage.iterdir()) if path.is_file()},
    }, log


def startup_case(stage, candidate, mpi_command, gpu_uuids, backend, env, timeout, evidence):
    ranks = len(gpu_uuids)
    model = sparse_carbon(2, REQUIRED_SLAB * 0.985)
    run = generate_inputs.ber_run(2, trajectory_interval=1, restart_interval=1)
    rejected, log = launch(stage / "rejected", model, run, mpi_command, env, timeout)
    evidence["rejected"] = rejected
    rejected["bindings"] = contract.verify_binding(log, gpu_uuids, backend)
    domain.check_route(log, ranks, "off")
    if (
        rejected["returncode"] != 1 or "MPI_ABORT was invoked" not in log
        or "Reason: slab-width-below-d-coord" not in log
        or "DMGMD_DOMAIN mode=m2a" in log or "DMGMD_DOMAIN_LAYOUT " in log
        or any(name in rejected["files_sha256"] for name in OUTPUTS)
    ):
        raise AssertionError("expected collective initial slab rejection before scientific outputs")
    match = re.search(r"^    (mpirun -n 1 .+)$", log, re.MULTILINE)
    if not match:
        raise AssertionError("initial rejection omitted the single-GPU relaunch command")
    diagnostic = match[1]
    recovery_words = shlex.split(diagnostic)
    if len(recovery_words) != 4 or recovery_words[:3] != ["mpirun", "-n", "1"]:
        raise AssertionError(f"invalid recovery command: {diagnostic}")
    executable = recovery_words[3]
    if not Path(executable).is_absolute() or Path(executable).resolve() != candidate.resolve():
        raise AssertionError(f"recovery command points at a different binary: {diagnostic}")
    quoted_forms = {shlex.quote(executable), "'" + executable.replace("'", "'\\''") + "'"}
    if diagnostic.removeprefix("mpirun -n 1 ") not in quoted_forms:
        raise AssertionError(f"recovery command does not safely quote the executable: {diagnostic}")
    env = dict(env, CUDA_VISIBLE_DEVICES=gpu_uuids[0])
    recovered, recovered_log = launch(
        stage / "recovered", model, run, ["/bin/sh", "-ec", diagnostic], env, timeout,
    )
    evidence["recovered"] = recovered
    if recovered["returncode"] != 0:
        raise AssertionError(f"the printed recovery command exited {recovered['returncode']}: {diagnostic}")
    recovered["bindings"] = contract.verify_binding(recovered_log, gpu_uuids[:1], backend)
    domain.check_route(recovered_log, 1, "off")
    recovered["outputs"] = contract.validate_outputs(stage / "recovered", 2)


def preflight_error_ranks(log):
    """Open MPI may prefix a forwarded stderr line with NUL during MPI_Abort."""
    return sorted({
        int(rank) for rank in re.findall(
            r'^\x00*DMGMD_ERROR rank=(\d+).*domain NPT box preflight failed', log, re.MULTILINE,
        )
    })


def runtime_case(stage, mpi_command, gpu_uuids, backend, mode, env, timeout, evidence):
    ranks = len(gpu_uuids)
    model = sparse_carbon(ranks, REQUIRED_SLAB * 1.025)
    # Berendsen's 2,550 GPa target induces about 1% linear compression per step.
    # Starting 2.5% above the local-slab limit puts the first failure at step 3.
    def run_text(steps):
        return generate_inputs.ber_run(
            steps, target_pressure_gpa=2550, trajectory_interval=1, restart_interval=1,
        )

    valid, valid_log = launch(stage / "valid", model, run_text(LAST_VALID_STEP), mpi_command, env, timeout)
    evidence["valid"] = valid
    if valid["returncode"] != 0:
        raise AssertionError("last-valid prefix failed")
    valid["bindings"] = contract.verify_binding(valid_log, gpu_uuids, backend)
    domain.check_route(valid_log, ranks, mode)
    valid["outputs"] = contract.validate_outputs(stage / "valid", LAST_VALID_STEP)
    aborted, log = launch(stage / "aborted", model, run_text(4), mpi_command, env, timeout)
    evidence["aborted"] = aborted
    aborted["bindings"] = contract.verify_binding(log, gpu_uuids, backend)
    domain.check_route(log, ranks, mode)
    diagnostic = re.search(
        r"domain NPT box preflight failed step=(\d+) lx=(\S+) ly=(\S+) lz=(\S+) "
        r"axis=(\d+) slab_width=(\S+) required=([\d.]+)", log,
    )
    if aborted["returncode"] != 1 or "MPI_ABORT was invoked" not in log or not diagnostic:
        raise AssertionError("expected collective in-run box preflight failure")
    step_text, lx_text, ly_text, lz_text, axis_text, slab_text, required_text = diagnostic.groups()
    step, axis = int(step_text), int(axis_text)
    lx, ly, lz, slab, required = map(float, (lx_text, ly_text, lz_text, slab_text, required_text))
    aborted["preflight"] = dict(step=step, lx=lx, ly=ly, lz=lz, axis=axis, slab=slab, required=required)
    if (
        step != LAST_VALID_STEP + 1 or axis != 0 or not 0 < slab < required == REQUIRED_SLAB
        or abs(lx / ranks - slab) > 1e-4 or min(ly, lz) <= 20
    ):
        raise AssertionError(f"unexpected candidate geometry: {aborted['preflight']}")
    aborted["reported_error_ranks"] = preflight_error_ranks(log)
    aborted["outputs"] = contract.validate_outputs(stage / "aborted", LAST_VALID_STEP)
    restart = contract.baseline.parse_xyz(stage / "aborted/restart.xyz")[0]
    last_lx = float(restart["fields"]["Lattice"].split()[0])
    aborted["last_valid_slab"] = last_lx / ranks
    if not required <= aborted["last_valid_slab"] < REQUIRED_SLAB * 1.025:
        raise AssertionError(f"restart slab was not the last eligible compressed slab: {last_lx / ranks}")
    aborted["output_matches_valid_prefix"] = all(
        valid["files_sha256"][name] == aborted["files_sha256"][name] for name in OUTPUTS
    )
    if not aborted["output_matches_valid_prefix"]:
        raise AssertionError("scientific outputs differ from the independently stopped last-valid prefix")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--devices", required=True)
    parser.add_argument("--ranks", default="2,4")
    parser.add_argument("--backends", default="HostStaged,CudaAware")
    parser.add_argument("--modes", default="rebuild,affine")
    parser.add_argument("--scenario", choices=("all", "startup", "runtime"), default="all")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    ranks = tuple(map(int, args.ranks.split(",")))
    backends, modes, devices = (arg.split(",") for arg in (args.backends, args.modes, args.devices))
    if (
        not set(ranks) <= {2, 4}
        or not set(backends) <= {"HostStaged", "CudaAware"}
        or not set(modes) <= {"rebuild", "affine"}
        or len(devices) < max(ranks) or len(set(devices)) != len(devices)
    ):
        parser.error("expect P2/P4, supported backends/modes, and enough distinct GPUs")
    candidate, root = args.candidate.absolute(), args.work_root.resolve()
    gpu_uuids = contract.physical_gpu_uuids(devices[:max(ranks)])
    mpirun = check_environment.validate_environment(candidate, "mpirun", gpu_uuids, max(ranks), args.timeout)
    root.mkdir(parents=True, exist_ok=False)
    records = []
    results = {"candidate_sha256": contract.sha(candidate), "records": records, "status": "RUNNING"}
    try:
        for rank_count, backend, mode in itertools.product(ranks, backends, modes):
            stage = root / f"p{rank_count}-{backend}-{mode}"
            stage.mkdir()
            assigned_gpus = gpu_uuids[:rank_count]
            env = dict(
                os.environ, CUDA_VISIBLE_DEVICES=",".join(assigned_gpus),
                CUDA_DEVICE_ORDER="PCI_BUS_ID", DMGMD_COMM_BACKEND=backend, DMGMD_DOMAIN_NPT=mode,
                DMGMD_DOMAIN_PARTITION="off", DMGMD_DOMAIN_OVERLAP="off",
                DMGMD_DOMAIN_SCHEDULE="split", DMGMD_OVERLAP_TRACE="0",
                DMGMD_FORCE_M1_REPLICATED="0", DMGMD_ENSEMBLE_SEED="12345678", OMP_NUM_THREADS="1",
            )
            command = [str(mpirun), "-n", str(rank_count), str(candidate)]
            if args.scenario != "runtime":
                startup = stage / "startup"
                startup.mkdir()
                record = {"scenario": "startup", "case": stage.name}
                records.append(record)
                startup_case(startup, candidate, command, assigned_gpus, backend, env, args.timeout, record)
                record["status"] = "PASS"
            if args.scenario != "startup":
                runtime = stage / "runtime"
                runtime.mkdir()
                record = {"scenario": "runtime", "case": stage.name}
                records.append(record)
                runtime_case(runtime, command, assigned_gpus, backend, mode, env, args.timeout, record)
                record["status"] = "PASS"
        results["status"] = "PASS"
        print(f"{len(records)} stop-boundary cases passed: {root / 'results.json'}")
    except Exception as error:
        results.update(status="FAIL", error=str(error))
        raise
    finally:
        (root / "results.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
