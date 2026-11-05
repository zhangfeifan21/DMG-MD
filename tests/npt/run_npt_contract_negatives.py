#!/usr/bin/env python3
"""Freeze and run the B4 fail-closed NPT contract cases on replicated-full.

The B2 runner already owns the Berendsen golden/error matrix. This supplement
adds SCR parameter/geometry/scale failures and the environment/step-option
guards for both NPT modes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tests/mpi"))
sys.path.insert(0, str(ROOT / "tests/baseline"))
sys.path.insert(0, str(HERE))
import check_environment
import generate_inputs
import run_baselines as baseline


RANK_ENV_WRAPPER = '''#!/usr/bin/env python3
"""Set one rank's environment and exec the frozen candidate binary."""
import os
import sys

candidate, scenario, mismatch_rank = sys.argv[1], sys.argv[2], int(sys.argv[3])
rank = int(os.environ["OMPI_COMM_WORLD_RANK"])
for name in ("DMGMD_ENSEMBLE_SEED", "DMGMD_DOMAIN_PARTITION"):
    os.environ.pop(name, None)
if scenario == "seed":
    os.environ["DMGMD_ENSEMBLE_SEED"] = "12345679" if rank == mismatch_rank else "12345678"
elif scenario == "partition":
    if rank == mismatch_rank:
        os.environ["DMGMD_DOMAIN_PARTITION"] = "serial"
else:
    raise SystemExit("unknown mismatch scenario")
os.execv(candidate, [candidate])
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    tmp.replace(path)


def reference_state() -> dict:
    result = {}
    for name in ("gpumd-reference", "lammps-reference", "lammps-matpl-reference"):
        path = ROOT.parent / name
        head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(path), "status", "--short"],
                                check=True, capture_output=True, text=True).stdout
        ignored = subprocess.run(["git", "-C", str(path), "ls-files", "--others", "--ignored",
                                  "--exclude-standard", "-z"], check=True,
                                 capture_output=True).stdout
        items = sorted(x.decode() for x in ignored.split(b"\0") if x)
        result[name] = {"head": head, "status": status,
                        "ignored_inventory_sha256": hashlib.sha256("\n".join(items).encode()).hexdigest()}
    return result


def freeze(args: argparse.Namespace) -> None:
    root = args.freeze_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    inp = root / "inputs"
    inp.mkdir()
    base = generate_inputs.model_text((3, 3, 3), 101)
    variants = {
        "base": base,
        "nonperiodic": base.replace('pbc="T T T"', 'pbc="T T F"', 1),
        "triclinic": re.sub(r'Lattice="[^"]+"',
                             'Lattice="10.736433 0 0 0.1 10.736433 0 0 0 10.736433"',
                             base, count=1),
        "zero-ke": generate_inputs.zero_ke_model(),
    }
    zero_velocity = []
    for line in base.splitlines():
        columns = line.split()
        if columns and columns[0] == "C" and len(columns) == 8:
            columns[5:8] = ["0", "0", "0"]
            zero_velocity.append(" ".join(columns))
        else:
            zero_velocity.append(line)
    variants["zero-ke-216"] = "\n".join(zero_velocity) + "\n"
    for key, text in variants.items():
        (inp / f"{key}.xyz").write_text(text)
    potential = ROOT / "tests/baseline/inputs/potentials/nep_C.txt"
    if sha(potential) != "add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623":
        raise RuntimeError("registered carbon NEP potential hash changed")
    shutil.copyfile(potential, inp / "nep.txt")
    definitions = [
        ("nan-temperature", "ensemble {kind} nan 300 40 0 425 200", "base",
         r"initial temperature should be a finite real number"),
        ("nan-pressure", "ensemble {kind} 300 300 40 nan 425 200", "base",
         r"target pressure \(GPa\) should be a finite real number"),
        ("bulk-zero", "ensemble {kind} 300 300 40 0 0 200", "base",
         r"bulk modulus should be > 0"),
        ("tau-zero", "ensemble {kind} 300 300 40 0 425 0", "base",
         r"pressure coupling time should be >= 1"),
        ("tokens-12", "ensemble {kind} 300 300 40 0 425 200 0 0 0 200", "base",
         r"v1 supports isotropic 8-token syntax only"),
        ("tokens-18", "ensemble {kind} 300 300 40 0 425 200 0 0 0 200 0 0 0 200 0 0", "base",
         r"v1 supports isotropic 8-token syntax only"),
        ("nonperiodic", "ensemble {kind} 300 300 40 0 425 200", "nonperiodic",
         r"initial box validation failed: isotropic box scaling requires periodicity in all directions"),
        ("triclinic", "ensemble {kind} 300 300 40 0 425 200", "triclinic",
         r"initial box validation failed: isotropic box scaling requires an orthogonal box"),
        ("unsafe-scale", "ensemble {kind} 300 300 40 10000 1 1", "base",
         r"invalid npt_(?:ber|scr) box factor step=\d+ P_GPa=10000"),
        ("zero-ke", "ensemble {kind} 300 300 40 0 425 200", "zero-ke",
         r"(?:NPT Berendsen instantaneous temperature|BDP global temperature) must be finite and positive"),
        ("correct-velocity", "ensemble {kind} 300 300 40 0 425 200", "base",
         r"does not support correct_velocity"),
        ("adaptive-step", "ensemble {kind} 300 300 40 0 425 200", "base",
         r"does not support adaptive time_step"),
    ]
    cases = []
    for kind in ("npt_ber", "npt_scr"):
        for name, ens, model, diagnostic in definitions:
            if kind == "npt_ber" and name in {"nan-temperature", "nan-pressure", "bulk-zero", "tau-zero",
                                              "tokens-12", "tokens-18", "nonperiodic", "triclinic",
                                              "unsafe-scale", "zero-ke"}:
                # B2 already froze and passed these exact Ber diagnostics.
                continue
            if kind == "npt_scr" and name == "nan-temperature":
                diagnostic = r"initial temperature should be a finite real number"
            label = f"{kind}-{name}"
            ensemble_line = ens.format(kind=kind)
            dt_line = "time_step 0.25 0.01" if name == "adaptive-step" else "time_step 0.25"
            pre = "correct_velocity 10\n" if name == "correct-velocity" else ""
            run = (f"potential nep.txt\n{dt_line}\n{ensemble_line}\n"
                   "dump_thermo 1\ndump_xyz 1 trajectory.xyz precision double velocity\n"
                   "dump_restart 1\n" + pre + "run 1\n")
            run_path = inp / f"{label}.in"
            run_path.write_text(run)
            cases.append({"label": label, "kind": kind, "model": model,
                          "run": run_path.name, "diagnostic_regex": diagnostic,
                          "environment": {}})
        for option, value, diagnostic in (
            ("DMGMD_DOMAIN_PARTITION", "serial", r"NPT requires DMGMD_DOMAIN_PARTITION=off"),
            ("DMGMD_DOMAIN_OVERLAP", "wait", r"NPT requires DMGMD_DOMAIN_OVERLAP=off"),
            ("DMGMD_DOMAIN_SCHEDULE", "producer", r"NPT requires DMGMD_DOMAIN_SCHEDULE=split"),
            ("DMGMD_OVERLAP_TRACE", "1", r"NPT requires DMGMD_OVERLAP_TRACE=0"),
        ):
            label = f"{kind}-environment-{option.lower()}"
            run_path = inp / f"{label}.in"
            run_path.write_text(run_text_for(kind))
            cases.append({"label": label, "kind": kind, "model": "base",
                          "run": run_path.name, "diagnostic_regex": diagnostic,
                          "environment": {option: value}})
    seed_run = inp / "npt_scr-seed-mismatch.in"
    seed_run.write_text(run_text_for("npt_scr"))
    (inp / "rank_env_wrapper.py").write_text(RANK_ENV_WRAPPER)
    records = {p.relative_to(root).as_posix(): sha(p)
               for p in sorted(root.rglob("*")) if p.is_file()}
    candidate, reference = args.candidate.resolve(), args.reference.resolve()
    payload = {"schema": "dmgmd.npt-contract-negatives-freeze.v1",
               "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "candidate": str(candidate), "candidate_sha256": sha(candidate),
               "reference": str(reference), "reference_sha256": sha(reference),
               "script_sha256": sha(Path(__file__).resolve()),
               "manifest_sha256": sha(HERE / "manifest.json"),
               "baseline_manifest_sha256": sha(ROOT / "tests/baseline/manifest.json"),
               "potential_sha256": sha(potential), "files_sha256": records,
               "reference_state": reference_state(), "cases": cases,
               "seed_mismatch": {"model": "base", "run": seed_run.name,
                                 "diagnostic_regex": r"DMGMD_ENSEMBLE_SEED differs between MPI ranks",
                                 "ranks": 2}}
    write_json(root / "freeze.json", payload)
    print(json.dumps({"freeze": str(root / "freeze.json"), "cases": len(cases),
                      "candidate_sha256": payload["candidate_sha256"]}, indent=2))


def run_text_for(kind: str) -> str:
    return ("potential nep.txt\ntime_step 0.25\n"
            f"ensemble {kind} 300 300 40 0 425 200\n"
            "dump_thermo 1\ndump_xyz 1 trajectory.xyz precision double velocity\n"
            "dump_restart 1\nrun 1\n")


def run(args: argparse.Namespace) -> None:
    freeze_path = args.freeze.resolve()
    f = json.loads(freeze_path.read_text())
    if sha(args.candidate.resolve()) != f["candidate_sha256"]:
        raise RuntimeError("candidate SHA differs from freeze")
    if sha(args.reference.resolve()) != f["reference_sha256"]:
        raise RuntimeError("reference executable SHA differs from freeze")
    if sha(Path(__file__).resolve()) != f["script_sha256"]:
        raise RuntimeError("negative runner changed after freeze")
    if sha(HERE / "manifest.json") != f["manifest_sha256"] or \
            sha(ROOT / "tests/baseline/manifest.json") != f["baseline_manifest_sha256"]:
        raise RuntimeError("NPT or baseline manifest changed after freeze")
    root = freeze_path.parent
    files = {p.relative_to(root).as_posix(): sha(p)
             for p in sorted(root.rglob("*")) if p.is_file() and p.name != "freeze.json"}
    if files != f["files_sha256"]:
        raise RuntimeError("frozen input inventory differs")
    if reference_state() != f["reference_state"]:
        raise RuntimeError("reference checkout changed after freeze")
    records = []
    work = args.work_root.resolve()
    work.mkdir(parents=True, exist_ok=False)
    devices = args.devices.split(",")
    required_ranks = args.collective_ranks if (args.seed_mismatch or args.env_mismatch) else 1
    mpiexec = check_environment.validate_environment(args.candidate.resolve(), "mpiexec",
                                                      devices, required_ranks, args.timeout)
    for case in ([] if args.seed_mismatch_only else f["cases"]):
        stage = work / case["label"]
        stage.mkdir()
        for name in ("nep.txt", f"{case['model']}.xyz"):
            shutil.copyfile(root / "inputs" / name, stage / ("model.xyz" if name.endswith(".xyz") else name))
        shutil.copyfile(root / "inputs" / case["run"], stage / "run.in")
        env = os.environ.copy()
        env.update(CUDA_VISIBLE_DEVICES=devices[0], CUDA_DEVICE_ORDER="PCI_BUS_ID",
                   DMGMD_COMM_BACKEND="HostStaged", DMGMD_DOMAIN_DIAGNOSTICS="1",
                   DMGMD_COMM_LOG_INTERVAL="1", OMP_NUM_THREADS="1",
                   DMGMD_ENSEMBLE_SEED="12345678")
        env.update(case["environment"])
        command = [str(mpiexec), "-n", "1", str(args.candidate.resolve())]
        with (stage / "stdout.log").open("w") as stdout, (stage / "stderr.log").open("w") as stderr:
            started = time.monotonic()
            proc = subprocess.Popen(command, cwd=stage, env=env, stdout=stdout, stderr=stderr,
                                    start_new_session=True)
            try:
                rc = proc.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL); proc.wait()
                rc = None
        logs = (stage / "stdout.log").read_text(errors="replace") + "\n" + \
               (stage / "stderr.log").read_text(errors="replace")
        success_files = [n for n in ("trajectory.xyz", "restart.xyz")
                         if (stage / n).exists() and (stage / n).stat().st_size]
        thermo_rows = []
        if (stage / "thermo.out").exists():
            thermo_rows = [r for s in baseline.parse_thermo(stage / "thermo.out") for r in s["rows"]]
        matched = bool(re.search(case["diagnostic_regex"], logs, re.IGNORECASE))
        passed = (rc not in (0, None) and matched and not success_files and not thermo_rows)
        record = {"label": case["label"], "returncode": rc,
                  "expected_diagnostic_regex": case["diagnostic_regex"],
                  "diagnostic_matched": matched, "success_artifacts": success_files,
                  "numeric_thermo_rows": len(thermo_rows),
                  "duration_seconds": time.monotonic() - started,
                  "input_sha256": {n: sha(stage / n) for n in ("model.xyz", "run.in", "nep.txt")},
                  "output_sha256": {p.name: sha(p) for p in stage.iterdir()
                                    if p.is_file() and p.name not in ("model.xyz", "run.in", "nep.txt")},
                  "status": "EXPECTED_FAILURE" if passed else "FAIL"}
        records.append(record)
        write_json(work / "results.json", {
            "schema": "dmgmd.npt-contract-negatives-results.v1",
            "candidate_sha256": f["candidate_sha256"],
            "freeze_sha256": sha(freeze_path), "records": records})
        if not passed:
            raise RuntimeError(f"{case['label']}: failed negative contract; evidence {stage}; record={record}")
    if args.seed_mismatch or args.env_mismatch:
        case = f["seed_mismatch"]
        n = args.collective_ranks
        def stage_collective(label: str) -> Path:
            stage_dir = work / label
            stage_dir.mkdir()
            for name in ("nep.txt", "base.xyz"):
                shutil.copyfile(root / "inputs" / name, stage_dir / ("model.xyz" if name.endswith(".xyz") else name))
            shutil.copyfile(root / "inputs" / case["run"], stage_dir / "run.in")
            return stage_dir

        env = os.environ.copy()
        env.update(CUDA_VISIBLE_DEVICES=",".join(devices[:n]), CUDA_DEVICE_ORDER="PCI_BUS_ID",
                   DMGMD_COMM_BACKEND="HostStaged", DMGMD_DOMAIN_DIAGNOSTICS="1",
                   DMGMD_COMM_LOG_INTERVAL="1", OMP_NUM_THREADS="1")

        def collective_check(label: str, scenario: str, expected: str) -> None:
            stage = stage_collective(label)
            wrapper = stage / "rank_env_wrapper.py"
            shutil.copyfile(root / "inputs" / "rank_env_wrapper.py", wrapper)
            command = [str(mpiexec), "-n", str(n), sys.executable, str(wrapper),
                       str(args.candidate.resolve()), scenario, str(n - 1)]
            for name in ("DMGMD_ENSEMBLE_SEED", "DMGMD_DOMAIN_PARTITION",
                         "DMGMD_DOMAIN_OVERLAP", "DMGMD_DOMAIN_SCHEDULE",
                         "DMGMD_OVERLAP_TRACE"):
                env.pop(name, None)
            with (stage / "stdout.log").open("w") as stdout, (stage / "stderr.log").open("w") as stderr:
                started = time.monotonic()
                proc = subprocess.Popen(command, cwd=stage, env=env, stdout=stdout, stderr=stderr,
                                        start_new_session=True)
                try:
                    rc = proc.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL); proc.wait()
                    rc = None
            logs = (stage / "stdout.log").read_text(errors="replace") + "\n" + \
                   (stage / "stderr.log").read_text(errors="replace")
            matching_ranks = sorted({int(value) for value in re.findall(
                r"DMGMD_ERROR rank=(\d+).*" + expected, logs)})
            success_files = [name for name in ("trajectory.xyz", "restart.xyz")
                             if (stage / name).exists() and (stage / name).stat().st_size]
            thermo_rows = []
            if (stage / "thermo.out").exists():
                thermo_rows = [r for s in baseline.parse_thermo(stage / "thermo.out") for r in s["rows"]]
            passed = rc not in (0, None) and matching_ranks == list(range(n)) and \
                     not success_files and not thermo_rows
            record = {"label": label, "command": command, "returncode": rc,
                      "diagnostic_regex": expected, "matching_error_ranks": matching_ranks,
                      "success_artifacts": success_files, "numeric_thermo_rows": len(thermo_rows),
                      "duration_seconds": time.monotonic() - started,
                      "input_sha256": {name: sha(stage / name) for name in
                                       ("model.xyz", "run.in", "nep.txt", "rank_env_wrapper.py")},
                      "output_sha256": {p.name: sha(p) for p in stage.iterdir()
                                        if p.is_file() and p.name not in
                                        ("model.xyz", "run.in", "nep.txt", "rank_env_wrapper.py")},
                      "status": "EXPECTED_FAILURE" if passed else "FAIL"}
            records.append(record)
            write_json(work / "results.json", {"schema": "dmgmd.npt-contract-negatives-results.v1",
                        "candidate_sha256": f["candidate_sha256"], "freeze_sha256": sha(freeze_path),
                        "records": records})
            if not passed:
                raise RuntimeError(f"{label} failed collective contract; evidence {stage}; {record}")

        if args.seed_mismatch:
            collective_check(f"npt_scr-seed-mismatch-p{n}", "seed",
                             re.escape(case["diagnostic_regex"]))
        if args.env_mismatch:
            collective_check(f"npt_scr-domain-partition-mismatch-p{n}", "partition",
                             r"DMGMD_DOMAIN_PARTITION differs between MPI ranks")
    print(f"PASS {len(records)} NPT negative contracts; evidence {work}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=ROOT.parent / "gpumd-reference/src/gpumd")
    parser.add_argument("--freeze-dir", type=Path)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--devices", default="5")
    parser.add_argument("--seed-mismatch", action="store_true",
                        help="also run the MPMD P2 seed-disagreement collective check")
    parser.add_argument("--seed-mismatch-only", action="store_true",
                        help="skip P1 cases and run only the two-rank seed disagreement check")
    parser.add_argument("--env-mismatch", action="store_true",
                        help="run an MPMD NPT environment-disagreement collective check")
    parser.add_argument("--collective-ranks", type=int, default=2,
                        help="rank count for seed/environment mismatch checks")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    args.candidate = args.candidate.resolve()
    args.reference = args.reference.resolve()
    if args.prepare == args.run:
        parser.error("select exactly one of --prepare or --run")
    if args.seed_mismatch_only and not args.seed_mismatch:
        parser.error("--seed-mismatch-only requires --seed-mismatch")
    if args.prepare:
        if not args.freeze_dir: parser.error("--prepare requires --freeze-dir")
        freeze(args)
    else:
        if not args.freeze or not args.work_root: parser.error("--run requires --freeze and --work-root")
        run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
