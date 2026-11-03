#!/usr/bin/env python3
"""NHC/BDP GPU acceptance; manifest.json is the frozen input/threshold contract.

Run from the supported env/md-mpi.sh environment. Every invocation creates a
new evidence directory; reference executables run only in its temporary stages.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import time
import threading
import queue
from concurrent.futures import ThreadPoolExecutor

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]
sys.path.insert(0, str(ROOT / "tests/baseline"))
sys.path.insert(0, str(ROOT / "tests/mpi"))
import run_baselines as baseline
import check_environment
import importlib.util

spec = importlib.util.spec_from_file_location("thermostat_statistics", SUITE / "sample_statistics.py")
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def sha(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def model_text(case, seed):
    """Explicit independent Gaussian velocities; no velocity command/COM removal."""
    rng = random.Random(seed)
    nx, ny, nz = case["cells"]
    lengths = case["box_A"]
    mass, kb, conversion = 12.011, 8.617343e-5, 1.018051e1
    std = math.sqrt(kb * 300.0 / mass) / conversion
    basis = ((0, 0, 0), (.25, .25, .25), (.5, .5, 0), (.75, .75, .25),
             (.5, 0, .5), (.75, .25, .75), (0, .5, .5), (.25, .75, .75))
    lines = [str(8 * nx * ny * nz),
             'pbc="T T T" Lattice="%s 0 0 0 %s 0 0 0 %s" '
             'Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3' % tuple(lengths)]
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                for x, y, z in basis:
                    position = (1 + 3.57 * (i + x), 1 + 3.57 * (j + y), 1 + 3.57 * (k + z))
                    velocity = [rng.gauss(0, std) for _ in range(3)]
                    lines.append("C " + " ".join(format(v, ".17g") for v in (*position, mass, *velocity)))
    return "\n".join(lines) + "\n"


def run_text(ensemble, profile, half_dt=False, ramp=False):
    dt = profile["dt_fs"] / (2 if half_dt else 1)
    damping = profile["damping_steps"] * (2 if half_dt else 1)
    steps = profile["steps"] * (2 if half_dt else 1)
    target2 = 450 if ramp else 300
    text = (f"potential nep.txt\ntime_step {dt:.17g}\n"
            f"ensemble {ensemble} 300 {target2} {damping:.17g}\n"
            f"dump_thermo {profile['thermo_interval']}\n")
    if profile.get("trajectory_interval"):
        text += (f"dump_xyz {profile['trajectory_interval']} trajectory.xyz precision double "
                 "velocity force potential virial\n")
    text += f"dump_restart {steps}\nrun {steps}\n"
    return text


class Runner:
    def __init__(self, args, manifest):
        self.args, self.manifest = args, manifest
        self.root = args.work_root.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.records = []
        self.lock = threading.RLock()
        self.manifest_hash = sha(json.dumps(manifest, indent=2) + "\n")
        self.binary_hash = baseline.sha256(args.candidate)
        self.potential = (ROOT / manifest["potential"]["path"]).read_text()
        if sha(self.potential) != manifest["potential"]["sha256"]:
            raise RuntimeError("potential hash differs from manifest")
        self.env = os.environ.copy()
        # Do not silently clear experimental options: production preflight
        # must reject them. Acceptance inputs require the documented defaults.
        self.env["CUDA_VISIBLE_DEVICES"] = ",".join(args.devices)
        self.env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.started = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
        (self.root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        self.mpirun = check_environment.validate_environment(
            args.candidate, "mpiexec", args.devices, max(args.ranks), args.timeout)
        self.save()

    def save(self):
        with self.lock:
            temporary = self.root / "results.json.tmp"
            temporary.write_text(json.dumps({
                "started_at": self.started, "candidate": str(self.args.candidate),
                "candidate_sha256": self.binary_hash, "manifest_sha256": self.manifest_hash,
                "records": self.records}, indent=2, allow_nan=False) + "\n")
            temporary.replace(self.root / "results.json")

    def execute(self, label, ensemble, case, ranks, backend, profile, seed=1,
                reference=False, half_dt=False, ramp=False, model=None, run=None,
                extra_env=None, expect_error=None, potential=None):
        stage = self.root / label
        stage.mkdir(parents=True, exist_ok=False)
        generated = model is None
        model = model if model is not None else model_text(self.manifest["geometries"][case], seed)
        if generated:
            locked = self.manifest["input_sha256"][case][str(seed)]
            if sha(model) != locked:
                raise RuntimeError(f"unregistered model: {case}/{seed}")
        run = run if run is not None else run_text(ensemble, profile, half_dt, ramp)
        for name, text in {"model.xyz": model, "run.in": run, "nep.txt": potential or self.potential}.items():
            (stage / name).write_text(text)
        env = self.env.copy()
        if reference:
            # GPUMD auto-enables all visible devices; this oracle is single GPU.
            env["CUDA_VISIBLE_DEVICES"] = self.args.devices[0]
        env["DMGMD_COMM_BACKEND"] = backend
        env["DMGMD_ENSEMBLE_SEED"] = str(seed)
        env["DMGMD_COMM_LOG_INTERVAL"] = str(profile.get("communication_interval", 1))
        env["DMGMD_DOMAIN_DIAGNOSTICS"] = "0" if profile.get("statistics") else "1"
        if extra_env:
            for name, value in extra_env.items():
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value
        executable = self.args.reference if reference else self.args.candidate
        command = [str(executable)] if reference else [str(self.mpirun), "-n", str(ranks), str(executable)]
        record = {"label": label, "ensemble": ensemble, "geometry": case, "potential": "NEP4",
                  "ranks": ranks, "backend": backend, "seed": seed, "reference": reference,
                  "command": command, "cwd": str(stage), "steps": profile["steps"] * (2 if half_dt else 1),
                  "input_sha256": {n: baseline.sha256(stage / n) for n in ("model.xyz", "run.in", "nep.txt")},
                  "executable_sha256": baseline.sha256(executable),
                  "environment": {k: v for k, v in env.items() if k.startswith(("DMGMD_", "CUDA_", "OMPI_", "UCX_", "PMIX_"))},
                  "started_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()}
        with self.lock:
            self.records.append(record)
            self.save()
        start = time.monotonic()
        with (stage / "stdout.log").open("w") as out, (stage / "stderr.log").open("w") as err:
            try:
                result = subprocess.run(command, cwd=stage, env=env, stdout=out, stderr=err, timeout=self.args.timeout)
            except subprocess.TimeoutExpired:
                record.update(status="FAIL", reason="timeout", duration_seconds=time.monotonic() - start)
                self.save()
                raise RuntimeError(f"{label}: timed out")
        with self.lock:
            record.update(returncode=result.returncode, duration_seconds=time.monotonic() - start)
            logs = (stage / "stdout.log").read_text() + (stage / "stderr.log").read_text()
            record["output_sha256"] = {p.name: baseline.sha256(p) for p in stage.iterdir()
                                      if p.name in ("thermo.out", "trajectory.xyz", "restart.xyz", "neighbor.out")}
            if expect_error:
                if result.returncode == 0 or expect_error not in logs:
                    raise RuntimeError(f"{label}: expected nonzero error containing {expect_error!r}")
                if (stage / "restart.xyz").exists() or (stage / "trajectory.xyz").exists():
                    raise RuntimeError(f"{label}: failed input produced a final scientific snapshot")
                record.update(status="PASS", expected_error=expect_error, termination="launcher returned before timeout")
            else:
                if result.returncode != 0:
                    raise RuntimeError(f"{label}: exit {result.returncode}; see {stage / 'stderr.log'}")
                if not reference:
                    mode = re.search(r"DMGMD_DOMAIN mode=(\S+) (.*)", logs)
                    if not mode:
                        raise RuntimeError(f"{label}: missing data-plane proof")
                    record.update(mode=mode[1], mode_detail=mode[2])
                    expected = "m2a" if case in ("domain", "cluster_domain") and ranks > 1 else "m1-fallback"
                    if mode[1] != expected:
                        raise RuntimeError(f"{label}: route {mode[1]}, expected {expected}")
                    rank_logs = [line for line in logs.splitlines() if line.startswith("DMGMD_MPI rank=")]
                    if len(rank_logs) != ranks or any(f"backend={backend}" not in line for line in rank_logs):
                        raise RuntimeError(f"{label}: backend/rank binding proof missing")
                    record["layouts"] = [line for line in logs.splitlines() if line.startswith("DMGMD_DOMAIN_LAYOUT ")]
                for name in ("thermo.out", "restart.xyz"):
                    if not (stage / name).is_file():
                        raise RuntimeError(f"{label}: missing {name}")
                record["status"] = "PASS"
        self.save()
        print(f"PASS {label} ({record['duration_seconds']:.2f}s)", flush=True)
        return stage

    def compare(self, reference, actual, label, exact=False):
        files = [n for n in ("thermo.out", "trajectory.xyz", "restart.xyz") if (reference / n).exists()]
        if exact:
            for name in files:
                if baseline.sha256(reference / name) != baseline.sha256(actual / name):
                    raise RuntimeError(f"{label}: repeated output differs: {name}")
            stats = {"exact_files": files}
        else:
            collector = baseline.DiffCollector(baseline.load_manifest()["tolerances"], True)
            for name in files:
                if name == "thermo.out":
                    baseline.compare_thermo(reference / name, actual / name, collector, label)
                else:
                    baseline.compare_xyz(reference / name, actual / name,
                                         {"kind": "restart" if name == "restart.xyz" else "xyz"}, collector, label)
            stats = collector.stats
        self.records.append({"label": label, "status": "PASS", "comparison": stats,
                             "reference_dir": str(reference), "actual_dir": str(actual)})
        self.save()


def short_matrix(runner, ensembles):
    profile = runner.manifest["short"]
    for ensemble in ensembles:
        for case in ("fallback", "domain"):
            oracle = None
            if ensemble == "nvt_nhc":
                baseline.validate_reference(runner.args.reference, baseline.load_manifest())
                oracle = runner.execute(f"oracle-{ensemble}-{case}", ensemble, case, 1, "HostStaged", profile, reference=True)
            p1 = None
            for ranks in runner.args.ranks:
                if case == "domain" and ranks == 1:
                    selected_backends = ["HostStaged"]
                else:
                    selected_backends = runner.args.backends
                for backend in selected_backends:
                    label = f"short-{ensemble}-{case}-p{ranks}-{backend}"
                    first = runner.execute(label + "-a", ensemble, case, ranks, backend, profile)
                    second = runner.execute(label + "-b", ensemble, case, ranks, backend, profile)
                    runner.compare(first, second, label + "-repeat", True)
                    if ranks == 1 and backend == "HostStaged":
                        p1 = first
                        if oracle: runner.compare(oracle, first, label + "-gpumd")
                    elif p1:
                        runner.compare(p1, first, label + "-p1")
                    # Restart is a fresh controller/RNG, never a continuity assertion.
                    if backend == "HostStaged":
                        resumed = runner.execute(label + "-restart", ensemble, case, ranks, backend,
                            {**profile, "steps": 5, "trajectory_interval": 5},
                            model=(first / "restart.xyz").read_text())
                        runner.records[-1]["restart_contract"] = "fresh chain/RNG; continuity not asserted"
                        runner.save()


def negative_matrix(runner, ensembles):
    profile = {**runner.manifest["short"], "steps": 2}
    for ensemble in ensembles:
        base_run = run_text(ensemble, profile)
        for ranks in runner.args.ranks:
            for setting, expected in (("correct_velocity 10", "does not support correct_velocity"),
                                      ("time_step 0.25 0.1", "does not support adaptive time_step")):
                run = base_run.replace("dump_thermo", setting + "\ndump_thermo", 1)
                runner.execute(f"negative-{ensemble}-{setting.split()[0]}-p{ranks}", ensemble,
                               "domain", ranks, "HostStaged", profile, run=run, expect_error=expected)
            for flag, value, expected in (
                ("DMGMD_DOMAIN_PARTITION", "serial", "DMGMD_DOMAIN_PARTITION=off"),
                ("DMGMD_DOMAIN_OVERLAP", "wait", "DMGMD_DOMAIN_OVERLAP=off"),
                ("DMGMD_DOMAIN_SCHEDULE", "producer", "DMGMD_DOMAIN_SCHEDULE=split"),
                ("DMGMD_OVERLAP_TRACE", "1", "DMGMD_OVERLAP_TRACE=0")):
                runner.execute(f"negative-{ensemble}-{flag}-p{ranks}", ensemble, "domain", ranks,
                               "HostStaged", profile, extra_env={flag: value}, expect_error=expected)
            # Two mutually isolated atoms remain force-free through VV2. A crystal
            # with zero initial velocities need not have zero KE at the BDP
            # post-force hook, because its first force update can accelerate it.
            model = ('2\npbc="T T T" Lattice="64 0 0 0 24 0 0 0 24" '
                     'Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3\n'
                     'C 2 2 2 12.011 0 0 0\nC 32 2 2 12.011 0 0 0\n')
            runner.execute(f"negative-{ensemble}-zero-ke-p{ranks}", ensemble, "domain", ranks,
                           "HostStaged", profile, model=model,
                           expect_error="kinetic energy" if ensemble == "nvt_nhc" else "BDP global temperature")


def statistical_matrix(runner, ensembles):
    profile = runner.manifest["statistics"]
    seeds = profile["seeds"]
    for ensemble in ensembles:
        selected_seeds = seeds[:3] if ensemble == "nvt_nhc" else seeds
        groups = []
        for ranks in runner.args.ranks:
            for case in (("cluster_domain",) if ranks == 1 else ("cluster_fallback", "cluster_domain")):
                for backend in runner.args.backends:
                    groups.append((case, ranks, backend, False, False))
        if 1 in runner.args.ranks:
            groups.append(("cluster_domain", 1, "HostStaged", True, False))
            if ensemble == "nvt_bdp":
                groups.append(("cluster_domain", 1, "HostStaged", False, True))
        for case, ranks, backend, half_dt, reference in groups:
            group = f"statistics-{ensemble}-{case}-p{ranks}-{backend}" + ("-half-dt" if half_dt else "") + ("-gpumd" if reference else "")
            if runner.args.statistics_filter and not re.search(runner.args.statistics_filter, group):
                continue
            for budget in profile["analysis_looks"]:
                if budget < runner.args.minimum_statistics_steps:
                    continue
                active_profile = {**profile, "steps": budget}
                chains = []
                device_groups = [runner.args.devices[i:i + ranks]
                                 for i in range(0, len(runner.args.devices) - ranks + 1, ranks)]
                worker_count = min(runner.args.parallel_seeds, len(selected_seeds), len(device_groups))
                available = queue.Queue()
                for devices in device_groups[:worker_count]:
                    available.put(devices)
                def sample(seed):
                    devices = available.get()
                    try:
                        return runner.execute(group + f"-look{budget}-seed{seed}", ensemble, case, ranks, backend,
                            active_profile, seed, reference, half_dt,
                            extra_env={"CUDA_VISIBLE_DEVICES": ",".join(devices)})
                    finally:
                        available.put(devices)
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    stages = list(executor.map(sample, selected_seeds))
                burn = (budget // 10) * (2 if half_dt else 1)
                for stage in stages:
                    rows = [r for segment in baseline.parse_thermo(stage / "thermo.out") for r in segment["rows"]]
                    expected_rows = budget * (2 if half_dt else 1)
                    if len(rows) != expected_rows:
                        raise RuntimeError(f"{stage}: incorrect thermo row count")
                    chains.append([row[1] for row in rows[burn:]])
                natoms = math.prod(runner.manifest["geometries"][case]["cells"]) * 8
                kt = 8.617343e-5 * 300
                config = dict(profile["acceptance"])
                if half_dt: config["block_length"] *= 2
                result = analysis.analyze_series(chains, 3 * natoms * kt / 2, 3 * natoms * kt * kt / 2, config)
                record = {"label": group + f"-look{budget}", "status": result["status"], "statistics": result,
                          "seeds": selected_seeds, "ndeg": 3 * natoms, "discard_steps": burn,
                          "sampling_contract": "independent Gaussian input momenta; no COM correction; interacting NEP cluster"}
                runner.records.append(record)
                runner.save()
                print(json.dumps(record, allow_nan=False), flush=True)
                if result["status"] == "PASS":
                    break
                if result["status"] == "FAIL" or budget == profile["maximum_steps"]:
                    raise RuntimeError(f"{group}: statistical result {result['status']}; do not widen thresholds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=ROOT.parent / "gpumd-reference/src/gpumd")
    parser.add_argument("--devices", type=lambda s: s.split(","), default=["0", "1", "2", "3"])
    parser.add_argument("--ranks", type=lambda s: [int(v) for v in s.split(",")], default=[1, 2, 4])
    parser.add_argument("--backends", nargs="+", default=["HostStaged", "CudaAware"])
    parser.add_argument("--ensembles", nargs="+", choices=["nvt_nhc", "nvt_bdp"], required=True)
    parser.add_argument("--sections", nargs="+", choices=["short", "negative", "statistics"], default=["short", "negative"])
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--parallel-seeds", type=int, default=1, help="concurrent independent seeds on disjoint GPU groups")
    parser.add_argument("--statistics-filter", help="select named statistical groups for a preregistered continuation")
    parser.add_argument("--minimum-statistics-steps", type=int, choices=[20000, 40000, 80000], default=20000)
    args = parser.parse_args()
    if args.parallel_seeds < 1:
        parser.error("--parallel-seeds must be positive")
    args.candidate, args.reference = args.candidate.resolve(), args.reference.resolve()
    manifest = json.loads((SUITE / "manifest.json").read_text())
    runner = Runner(args, manifest)
    for section in args.sections:
        {"short": short_matrix, "negative": negative_matrix, "statistics": statistical_matrix}[section](runner, args.ensembles)
    print(f"PASS {len(runner.records)} records; evidence {runner.root}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, baseline.BaselineError, check_environment.EnvironmentError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
