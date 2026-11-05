#!/usr/bin/env python3
"""Run frozen B3 SCR controls or one preregistered candidate/reference condition."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tests/mpi"))
import check_environment
import generate_inputs
import run_npt_ber
import capture_environment_proof


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def write_json(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


class ScrRunner:
    def __init__(self, args: argparse.Namespace, manifest: dict, freeze: dict):
        self.a, self.m, self.f = args, manifest, freeze
        self.root = args.work_root.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.input_root = Path(freeze["generated_input_root"])
        frozen_inventory = freeze["generated_inputs_sha256"]
        actual_inventory = {p.relative_to(self.input_root).as_posix(): sha(p)
                            for p in sorted(self.input_root.rglob("*")) if p.is_file()}
        if actual_inventory != frozen_inventory:
            raise RuntimeError("registered B3 input inventory/hash differs from freeze")
        self.candidate = args.candidate.resolve()
        self.reference = args.reference.resolve()
        self.device_ids = args.devices.split(",")
        self.records: list[dict] = []
        self.schedule_path = args.schedule.resolve() if args.schedule else None
        self.results = self.root / "results.json"
        self.mpiexec: Path | None = None
        self.payload = {"schema": "dmgmd.npt-scr-runner-results.v1",
                        "started_at_utc": utc_now(), "freeze_sha256": sha(args.freeze),
                        "manifest_sha256": freeze["manifest_sha256"],
                        "candidate_sha256": freeze["candidate_sha256"],
                        "reference_sha256": freeze["reference_sha256"],
                        "records": self.records}
        self.save()

    @staticmethod
    def schedule_key(profile: str, engine: str, ranks: int, backend: str,
                     seed: int, steps: int) -> str:
        return f"{profile}|{engine}|P{ranks}|{backend}|seed{seed}|steps{steps}"

    def schedule_update(self, key: str, updates: dict, *, create: bool = False) -> None:
        if self.schedule_path is None:
            raise RuntimeError("formal condition runs require --schedule")
        lock_path = self.schedule_path.with_suffix(self.schedule_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if not self.schedule_path.is_file():
                raise RuntimeError(f"execution schedule does not exist: {self.schedule_path}")
            data = json.loads(self.schedule_path.read_text())
            jobs = data.get("jobs")
            if not isinstance(jobs, list):
                raise RuntimeError("execution schedule jobs must be a list")
            matches = [job for job in jobs if job.get("key") == key]
            if not matches and create:
                job = dict(updates)
                job["key"] = key
                job["status"] = "planned"
                jobs.append(job)
                matches = [job]
            if len(matches) != 1:
                raise RuntimeError(f"schedule key missing or duplicated: {key}")
            job = matches[0]
            if "status" in updates and job.get("status") != updates["status"]:
                allowed = {"planned": {"running", "blocked-before-launch"},
                           "running": {"completed", "failed", "timeout"},
                           "blocked-before-launch": {"planned"}}
                if updates["status"] not in allowed.get(job.get("status"), set()):
                    raise RuntimeError(f"invalid schedule transition {job.get('status')} -> {updates['status']} for {key}")
            job.update(updates)
            data["updated_at_utc"] = utc_now()
            temp = self.schedule_path.with_suffix(self.schedule_path.suffix + f".{os.getpid()}.tmp")
            temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
            temp.replace(self.schedule_path)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def reserve_reference_launch(self, label: str):
        """Reserve a globally unique UTC second across concurrent runners."""
        if self.a.reference_launch_registry is None:
            raise RuntimeError("formal reference runs require --reference-launch-registry")
        path = self.a.reference_launch_registry.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock = lock_path.open("a+")
        while True:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if path.is_file():
                data = json.loads(path.read_text())
            else:
                data = {"schema": "dmgmd.npt-reference-launch-registry.v1", "reservations": []}
            if data.get("schema") != "dmgmd.npt-reference-launch-registry.v1":
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                lock.close()
                raise RuntimeError("reference launch registry schema mismatch")
            used = {entry["utc_second"] for entry in data["reservations"]}
            now = dt.datetime.now(dt.timezone.utc)
            second = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            # Leave enough time to Popen before the UTC second rolls over.
            safe_margin = now.microsecond < 700000
            if second not in used and safe_margin:
                data["reservations"].append({"utc_second": second,
                                              "reserved_at_utc": now.isoformat(),
                                              "started_at_utc": None,
                                              "label": label,
                                              "pid": os.getpid()})
                temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
                temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
                temp.replace(path)
                return lock, second
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            time.sleep(.05)

    def record_reference_launch(self, lock, utc_second: str, started_at: str, pid: int) -> None:
        path = self.a.reference_launch_registry.resolve()
        data = json.loads(path.read_text())
        matches = [x for x in data["reservations"] if x["utc_second"] == utc_second]
        if len(matches) != 1:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
            raise RuntimeError(f"reference launch reservation missing/duplicated: {utc_second}")
        matches[0].update(started_at_utc=started_at, pid=pid)
        temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        temp.replace(path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()

    @staticmethod
    def process_affinity(pid: int) -> dict:
        allowed = None
        command = None
        status = Path(f"/proc/{pid}/status")
        if status.is_file():
            for line in status.read_text(errors="replace").splitlines():
                if line.startswith("Cpus_allowed_list:"):
                    allowed = line.split(":", 1)[1].strip()
                    break
        cmdline = Path(f"/proc/{pid}/cmdline")
        if cmdline.is_file():
            command = cmdline.read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        return {"pid": pid, "cpus_allowed_list": allowed, "command": command}

    @staticmethod
    def process_descendants(pid: int) -> list[int]:
        found = []
        pending = [pid]
        while pending:
            current = pending.pop()
            children_file = Path(f"/proc/{current}/task/{current}/children")
            if children_file.is_file():
                children = [int(x) for x in children_file.read_text().split()]
                found.extend(children)
                pending.extend(children)
        return found

    def save(self) -> None:
        self.payload["updated_at_utc"] = utc_now()
        write_json(self.results, self.payload)

    def stage(self, profile: str, budget_steps: int, seed: int, steps: int,
              label: str) -> Path:
        source = (self.input_root / "short" / f"seed-{seed}" if steps == 20 else
                  self.input_root / profile / str(budget_steps) / f"seed-{seed}")
        if not source.is_dir():
            raise RuntimeError(f"frozen input directory missing: {source}")
        target = self.root / label
        target.mkdir(parents=True, exist_ok=False)
        model = (source / "model.xyz").read_bytes()
        potential = (source / "nep.txt").read_bytes()
        run_text = (source / "run.in").read_text()
        expected_run = generate_inputs.scr_run(steps, self.a.dt_fs, self.a.tdamp_steps, self.a.tau_p_steps)
        if run_text != expected_run:
            raise RuntimeError(f"staged run.in differs from frozen profile: {source / 'run.in'}")
        (target / "model.xyz").write_bytes(model)
        (target / "nep.txt").write_bytes(potential)
        (target / "run.in").write_text(run_text)
        expected = {"model.xyz": sha(source / "model.xyz"), "nep.txt": sha(source / "nep.txt")}
        actual = {name: sha(target / name) for name in expected}
        if actual != expected:
            raise RuntimeError(f"staged SCR inputs differ from frozen input: {target}")
        return target

    def run_one(self, engine: str, profile: str, budget_steps: int, seed: int,
                steps: int, ranks: int, backend: str, label: str,
                ensemble_seed: str | None = "registered",
                schedule_key: str | None = None) -> dict:
        stage = self.stage(profile, budget_steps, seed, steps, label)
        executable = self.reference if engine == "reference" else self.candidate
        env = os.environ.copy()
        comm_log_interval = 1 if steps <= 20 else 1000
        env.update(CUDA_VISIBLE_DEVICES=",".join(self.device_ids[:ranks]),
                   CUDA_DEVICE_ORDER="PCI_BUS_ID", PYTHONDONTWRITEBYTECODE="1",
                   DMGMD_COMM_BACKEND=backend,
                   DMGMD_DOMAIN_DIAGNOSTICS="1" if steps <= 20 else "0",
                   # Runtime requires a positive interval even when verbose
                   # domain diagnostics are disabled for long statistics.
                   DMGMD_COMM_LOG_INTERVAL=str(comm_log_interval),
                   OMP_NUM_THREADS="1")
        if engine == "candidate":
            if ensemble_seed is None:
                env.pop("DMGMD_ENSEMBLE_SEED", None)
            else:
                env["DMGMD_ENSEMBLE_SEED"] = str(seed) if ensemble_seed == "registered" else ensemble_seed
        else:
            env.pop("DMGMD_ENSEMBLE_SEED", None)
        command = ([str(executable)] if engine == "reference" else
                   [str(self.mpiexec), "--bind-to", "none", "-n", str(ranks), str(executable)])
        reserved_second = None
        started_at = utc_now()
        inp = {n: sha(stage / n) for n in ("model.xyz", "run.in", "nep.txt")}
        record = {"label": label, "engine": engine, "seed": seed,
                  "steps": steps, "ranks": ranks, "backend": backend,
                  "ensemble_seed": env.get("DMGMD_ENSEMBLE_SEED"),
                  "command": command, "cwd": str(stage), "input_sha256": inp,
                  "input_paths": {n: str((stage / n).resolve()) for n in inp},
                  "executable_sha256": sha(executable), "started_at_utc": started_at,
                  "pid": None, "cuda_visible_devices": env["CUDA_VISIBLE_DEVICES"],
                  "DMGMD_COMM_LOG_INTERVAL": env["DMGMD_COMM_LOG_INTERVAL"]}
        if engine == "reference":
            record["reserved_utc_second"] = reserved_second
        else:
            record["mpi_binding"] = "--bind-to none"
        if engine == "reference":
            record["rng_initialization_evidence"] = (
                "release initialize_rng system_clock epoch count; nm/objdump evidence in B3 freeze; "
                "internal seed is not directly observed")
        self.records.append(record)
        self.save()
        started = time.monotonic()
        with (stage / "execution.stdout").open("w") as out, (stage / "execution.stderr").open("w") as err:
            registry_lock = None
            if engine == "reference":
                registry_lock, reserved_second = self.reserve_reference_launch(label)
                record["reserved_utc_second"] = reserved_second
            try:
                proc = subprocess.Popen(command, cwd=stage, env=env, stdout=out, stderr=err,
                                        start_new_session=True)
            except BaseException:
                if registry_lock is not None:
                    fcntl.flock(registry_lock.fileno(), fcntl.LOCK_UN)
                    registry_lock.close()
                raise
            record["pid"] = proc.pid
            record["started_at_utc"] = utc_now()
            if engine == "reference":
                self.record_reference_launch(registry_lock, reserved_second,
                                             record["started_at_utc"], proc.pid)
            else:
                deadline = time.monotonic() + 5.0
                descendants = []
                while time.monotonic() < deadline:
                    descendants = self.process_descendants(proc.pid)
                    if descendants or proc.poll() is not None:
                        break
                    time.sleep(.05)
                record["affinity_snapshot"] = [self.process_affinity(x)
                                               for x in [proc.pid] + descendants]
            self.save()
            if schedule_key:
                self.schedule_update(schedule_key, {"status": "running", "pid": proc.pid,
                                    "started_at_utc": record["started_at_utc"],
                                    "result_dir": str(stage.resolve()),
                                    "gpu_ids": self.device_ids[:ranks]})
            try:
                rc = proc.wait(timeout=self.a.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                record.update(status="TIMEOUT", ended_at_utc=utc_now(),
                              duration_seconds=time.monotonic() - started)
                self.save()
                if schedule_key:
                    self.schedule_update(schedule_key, {"status": "timeout",
                                        "ended_at_utc": record["ended_at_utc"]})
                raise RuntimeError(f"{label} timeout; retained at {stage}")
        record.update(ended_at_utc=utc_now(), duration_seconds=time.monotonic() - started,
                      returncode=rc)
        logs = (stage / "execution.stdout").read_text(errors="replace") + "\n" + \
               (stage / "execution.stderr").read_text(errors="replace")
        record["route_lines"] = [line for line in logs.splitlines()
                                 if "DMGMD_DOMAIN" in line or "npt-m1-only" in line]
        record["output_sha256"] = {p.name: sha(p) for p in stage.iterdir()
                                   if p.is_file() and p.name not in ("model.xyz", "run.in", "nep.txt")}
        record["status"] = "PASS" if rc == 0 else "FAIL"
        if rc == 0:
            thermo = stage / "thermo.out"
            if not thermo.is_file():
                record["status"] = "FAIL"
                self.save()
                raise RuntimeError(f"{label}: successful exit without thermo.out")
            record["thermo_path"] = str(thermo.resolve())
            record["thermo_sha256"] = sha(thermo)
        self.save()
        if schedule_key:
            self.schedule_update(schedule_key, {
                "status": "completed" if rc == 0 else "failed",
                "ended_at_utc": record["ended_at_utc"],
                "thermo_sha256": record.get("thermo_sha256"),
                "output_sha256": record.get("output_sha256"),
                "returncode": rc,
            })
        if rc != 0:
            raise RuntimeError(f"{label}: exit {rc}; see {stage}/execution.stderr")
        return record

    def run_condition(self) -> None:
        profile = self.a.profile
        max_steps = 80000 if profile == "base" else 160000
        steps = max_steps if self.a.engine == "reference" else self.a.steps
        if self.a.engine == "reference" and steps != max_steps:
            raise RuntimeError("reference jobs must be one full max-budget segment")
        seeds = self.a.seeds or self.m["fixtures"]["statistics"]["seeds"]
        if self.schedule_path is None:
            raise RuntimeError("formal condition runs require --schedule")
        for seed in seeds:
            # run_one serializes launches and records each actual launch second.
            source_budget = steps if self.a.engine == "candidate" else max_steps
            label = f"{profile}-{self.a.engine}-s{seed}"
            key = self.schedule_key(profile, self.a.engine, self.a.ranks,
                                    self.a.backend, seed, steps)
            plan = {"profile": profile, "engine": self.a.engine,
                    "ranks": self.a.ranks, "backend": self.a.backend,
                    "seed": seed, "actual_steps": steps,
                    "candidate_sha256": self.f["candidate_sha256"],
                    "executable_sha256": sha(self.candidate if self.a.engine == "candidate" else self.reference),
                    "freeze_sha256": self.payload["freeze_sha256"],
                    "result_dir": str((self.root / label).resolve()),
                    "status": "planned"}
            self.schedule_update(key, plan, create=True)
            self.run_one(self.a.engine, profile, source_budget, seed, steps,
                         self.a.ranks, self.a.backend, label, schedule_key=key)

    def run_short(self) -> None:
        # Exact repeat and seed smoke, then physical route checks. The 20-step
        # runs are controls only and never enter statistical records.
        self.a.dt_fs, self.a.tdamp_steps, self.a.tau_p_steps = .25, 40, 200
        self.a.profile = "base"
        self.a.engine = "candidate"
        self.run_one("reference", "base", 20000, 101, 20, 1,
                     "HostStaged", "short-reference-scr-smoke")
        seeds = self.m["fixtures"]["statistics"]["seeds"]
        seed101_stage = None
        for seed in seeds:
            record = self.run_one("candidate", "base", 20000, seed, 20, 1,
                                  "HostStaged", f"short-seed-{seed}")
            if seed == 101:
                seed101_stage = Path(record["thermo_path"]).parent
        defaulted = self.run_one("candidate", "base", 20000, 101, 20, 1,
                                 "HostStaged", "short-default-ensemble-seed",
                                 ensemble_seed=None)
        explicit_default = self.run_one("candidate", "base", 20000, 101, 20, 1,
                                        "HostStaged", "short-explicit-default-ensemble-seed",
                                        ensemble_seed="12345678")
        if defaulted["thermo_sha256"] != explicit_default["thermo_sha256"]:
            raise RuntimeError("unset DMGMD_ENSEMBLE_SEED differs from explicit default 12345678")
        first = self.run_one("candidate", "base", 20000, 101, 20, 1,
                             "HostStaged", "short-repeat-a")
        second = self.run_one("candidate", "base", 20000, 101, 20, 1,
                              "HostStaged", "short-repeat-b")
        if first["thermo_sha256"] != second["thermo_sha256"]:
            raise RuntimeError("same candidate seed/config repeat thermo differs bytewise")
        if seed101_stage is None or first["thermo_sha256"] != sha(seed101_stage / "thermo.out"):
            raise RuntimeError("fixed seed repeat differs from the same-model seed-101 run")
        baseline_path = Path(first["thermo_path"])
        from run_baselines import DiffCollector, compare_thermo
        tolerance = json.loads((ROOT / "tests/baseline/manifest.json").read_text())["tolerances"]
        route_metrics = {}
        for ranks, backend in ((1, "HostStaged"), (2, "HostStaged"), (2, "CudaAware"),
                               (4, "HostStaged"), (4, "CudaAware")):
            a = self.run_one("candidate", "base", 20000, 101, 20, ranks,
                             backend, f"short-route-p{ranks}-{backend}-a")
            b = self.run_one("candidate", "base", 20000, 101, 20, ranks,
                             backend, f"short-route-p{ranks}-{backend}-b")
            if a["thermo_sha256"] != b["thermo_sha256"]:
                raise RuntimeError(f"same route repeat thermo differs: P{ranks}/{backend}")
            route_text = "\n".join(a.get("route_lines", [])) + "\n" + \
                         Path(a["thermo_path"]).parent.joinpath("execution.stdout").read_text(errors="replace")
            route_text += Path(a["thermo_path"]).parent.joinpath("execution.stderr").read_text(errors="replace")
            if "npt-m1-only" not in route_text:
                raise RuntimeError(f"P{ranks}/{backend} route evidence lacks reason=npt-m1-only")
            diff = DiffCollector(tolerance, enforce=True)
            compare_thermo(baseline_path, Path(a["thermo_path"]), diff,
                           f"SCR route P{ranks}/{backend} vs P1")
            route_metrics[f"P{ranks}/{backend}"] = diff.stats
        self.payload["route_comparison_vs_p1"] = route_metrics
        self.save()
        self.run_zero_temperature_collective_negative()

    def run_zero_temperature_collective_negative(self) -> None:
        source = self.input_root / "short" / "zero-ke"
        stage = self.root / "negative-zero-ke-p4"
        stage.mkdir()
        for name in ("model.xyz", "nep.txt", "run.in"):
            (stage / name).write_bytes((source / name).read_bytes())
        env = os.environ.copy()
        env.update(CUDA_VISIBLE_DEVICES=",".join(self.device_ids[:4]),
                   CUDA_DEVICE_ORDER="PCI_BUS_ID", DMGMD_COMM_BACKEND="HostStaged",
                   DMGMD_DOMAIN_DIAGNOSTICS="1", DMGMD_COMM_LOG_INTERVAL="1",
                   DMGMD_ENSEMBLE_SEED="101", OMP_NUM_THREADS="1")
        command = [str(self.mpiexec), "-n", "4", str(self.candidate)]
        record = {"label": "negative-zero-ke-p4", "engine": "candidate",
                  "seed": 101, "ranks": 4, "backend": "HostStaged", "command": command,
                  "cwd": str(stage), "ensemble_seed": "101",
                  "input_paths": {n: str((stage / n).resolve())
                                  for n in ("model.xyz", "run.in", "nep.txt")},
                  "input_sha256": {n: sha(stage / n) for n in ("model.xyz", "run.in", "nep.txt")},
                  "executable_sha256": sha(self.candidate),
                  "expected_diagnostic_regex":
                      "BDP global temperature must be finite and positive",
                  "started_at_utc": utc_now(), "pid": None}
        self.records.append(record)
        self.save()
        with (stage / "execution.stdout").open("w") as out, (stage / "execution.stderr").open("w") as err:
            proc = subprocess.Popen(command, cwd=stage, env=env, stdout=out, stderr=err,
                                    start_new_session=True)
            record["pid"] = proc.pid
            self.save()
            try:
                rc = proc.wait(timeout=self.a.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                record.update(status="TIMEOUT", ended_at_utc=utc_now())
                self.save()
                raise RuntimeError("P4 zero-kinetic-energy negative case hung")
        logs = (stage / "execution.stdout").read_text(errors="replace") + "\n" + \
               (stage / "execution.stderr").read_text(errors="replace")
        if rc == 0 or not re.search(record["expected_diagnostic_regex"], logs):
            record.update(status="FAIL", returncode=rc, ended_at_utc=utc_now())
            self.save()
            raise RuntimeError(f"P4 zero-kinetic-energy negative missed expected diagnostic; evidence {stage}")
        thermo = stage / "thermo.out"
        numeric_rows = []
        if thermo.is_file():
            for line in thermo.read_text(errors="replace").splitlines():
                fields = line.split()
                if len(fields) == 18:
                    try:
                        [float(v) for v in fields]
                    except ValueError:
                        continue
                    numeric_rows.append(line)
        forbidden = [name for name in ("trajectory.xyz", "restart.xyz") if (stage / name).exists()]
        if numeric_rows or forbidden:
            record.update(status="FAIL", numeric_thermo_rows=len(numeric_rows),
                          forbidden_artifacts=forbidden, ended_at_utc=utc_now())
            self.save()
            raise RuntimeError(f"P4 zero-kinetic-energy failure left output artifacts: {stage}")
        record.update(status="EXPECTED_FAILURE", returncode=rc, ended_at_utc=utc_now(),
                      numeric_thermo_rows=0, forbidden_artifacts=[],
                      stdout_sha256=sha(stage / "execution.stdout"),
                      stderr_sha256=sha(stage / "execution.stderr"))
        self.save()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--reference", type=Path, default=ROOT.parent / "gpumd-reference/src/gpumd")
    p.add_argument("--freeze", type=Path, required=True)
    p.add_argument("--acf-helper", type=Path, required=True)
    p.add_argument("--devices", required=True, help="approved free physical GPU ids, e.g. 4,5,6,7")
    p.add_argument("--work-root", type=Path, required=True)
    p.add_argument("--mode", choices=("short", "condition"), required=True)
    p.add_argument("--engine", choices=("candidate", "reference"), default="candidate")
    p.add_argument("--profile", choices=("base", "half"), default="base")
    p.add_argument("--steps", type=int, default=20000)
    p.add_argument("--ranks", type=int, default=1)
    p.add_argument("--backend", choices=("HostStaged", "CudaAware"), default="HostStaged")
    p.add_argument("--seeds", help="optional subset of registered seeds for execution sharding")
    p.add_argument("--environment-proof", type=Path,
                   help="validated same-binary/toolchain/physical-GPU four-rank gate proof")
    p.add_argument("--schedule", type=Path,
                   help="shared atomic execution schedule for formal SCR jobs")
    p.add_argument("--reference-launch-registry", type=Path,
                   help="global lock-protected registry preventing same-second GPUMD launches")
    p.add_argument("--timeout", type=int, default=3600)
    a = p.parse_args()
    a.seeds = [int(x) for x in a.seeds.split(",")] if a.seeds else None
    a.candidate, a.reference, a.freeze = a.candidate.resolve(), a.reference.resolve(), a.freeze.resolve()
    a.acf_helper = a.acf_helper.resolve()
    if not a.acf_helper.is_file() or sha(a.acf_helper) != json.loads(a.freeze.read_text())["analysis_helper"]["sha256"]:
        raise RuntimeError("native ACF helper missing or differs from B3 freeze")
    os.environ["DMGMD_ACF_HELPER"] = str(a.acf_helper)
    manifest = json.loads((HERE / "manifest.json").read_text())
    freeze = json.loads(a.freeze.read_text())
    if sha(HERE / "manifest.json") != freeze["manifest_sha256"]:
        raise RuntimeError("manifest SHA differs from B3 freeze")
    if sha(a.candidate) != freeze["candidate_sha256"] or sha(a.reference) != freeze["reference_sha256"]:
        raise RuntimeError("candidate/reference binary differs from B3 freeze")
    for name, digest in freeze["script_sha256"].items():
        if sha(ROOT / name) != digest:
            raise RuntimeError(f"frozen script changed: {name}")
    if a.engine == "reference":
        registry_frozen = freeze.get("execution_amendment", {}).get("reference_launch_registry")
        if a.reference_launch_registry is None or not registry_frozen:
            raise RuntimeError("formal GPUMD jobs require a frozen reference launch registry")
        registry_path = a.reference_launch_registry.resolve()
        if registry_frozen.get("path") != str(registry_path):
            raise RuntimeError("reference launch registry path differs from execution freeze")
        registry_data = json.loads(registry_path.read_text())
        current_seconds = [x.get("utc_second") for x in registry_data.get("reservations", [])]
        if registry_data.get("schema") != "dmgmd.npt-reference-launch-registry.v1" or \
                len(current_seconds) != len(set(current_seconds)) or \
                not set(registry_frozen.get("initial_utc_seconds", [])) <= set(current_seconds):
            raise RuntimeError("reference launch registry lost frozen entries or has duplicate seconds")
    if run_npt_ber.reference_status_snapshot() != freeze["reference_status_before"]:
        raise RuntimeError("read-only reference checkout state changed")
    if len(a.devices.split(",")) < 4:
        raise RuntimeError("B3 P1/P2/P4 jobs require four assigned physical GPUs")
    if a.seeds is not None:
        registered = set(manifest["b3_scr_statistics"]["seeds"])
        if len(a.seeds) != len(set(a.seeds)) or not set(a.seeds) <= registered:
            raise RuntimeError("--seeds must be a unique subset of registered seeds")
    runner = ScrRunner(a, manifest, freeze)
    if a.environment_proof:
        proof_path = a.environment_proof.resolve()
        proof = json.loads(proof_path.read_text())
        frozen_proof = freeze.get("execution_amendment", {}).get("environment_proof")
        if not frozen_proof or frozen_proof.get("path") != str(proof_path) or \
                frozen_proof.get("sha256") != sha(proof_path):
            raise RuntimeError("environment proof path/SHA differs from execution freeze")
        requested_devices = a.devices.split(",")
        if proof.get("schema") != "dmgmd.npt-b3-environment-proof.v1" or \
                proof.get("status") != "PASS" or proof.get("gate_ranks") != 4 or \
                proof.get("probed_backend") != "CudaAware":
            raise RuntimeError("environment proof schema/status/backend is invalid")
        if proof.get("candidate_sha256") != sha(a.candidate) or \
                proof.get("environment_fingerprint") != capture_environment_proof.environment_fingerprint():
            raise RuntimeError("environment proof candidate or toolchain fingerprint differs")
        if set(proof.get("physical_devices", [])) != set(requested_devices[:4]):
            raise RuntimeError("environment proof does not cover the selected physical GPU group")
        current_gpu_identity = capture_environment_proof.gpu_inventory()
        if proof.get("gpu_identity") != {device: current_gpu_identity.get(device)
                                          for device in proof["physical_devices"]}:
            raise RuntimeError("environment proof GPU UUID/driver identity differs")
        runner.mpiexec = Path(proof["mpiexec_path"])
        runner.payload["environment_proof"] = {
            "path": str(proof_path), "sha256": sha(proof_path),
            "candidate_sha256": proof["candidate_sha256"],
            "physical_devices": requested_devices[:4],
            "probed_backend": proof["probed_backend"],
            "environment_fingerprint": proof["environment_fingerprint"],
            "captured_at_utc": proof["captured_at_utc"],
        }
        runner.save()
    else:
        runner.mpiexec = check_environment.validate_environment(
            a.candidate, "mpiexec", a.devices.split(","), 4, min(a.timeout, 180))
    if a.mode == "short":
        runner.run_short()
    else:
        config = manifest["b3_scr_statistics"]
        if a.profile == "base":
            a.dt_fs, a.tdamp_steps, a.tau_p_steps = .25, 40, 200
            allowed = (20000, 40000, 80000)
        else:
            a.dt_fs, a.tdamp_steps, a.tau_p_steps = .125, 80, 400
            allowed = (40000, 80000, 160000)
        if a.steps not in allowed and a.engine == "candidate":
            raise RuntimeError(f"candidate steps {a.steps} not registered in {allowed}")
        conditions = (config["candidate_conditions_base_dt"] if a.profile == "base"
                      else config["candidate_conditions_half_dt"])
        if a.engine == "candidate" and {"ranks": a.ranks, "backend": a.backend} not in conditions:
            raise RuntimeError("candidate condition is not preregistered")
        if a.engine == "reference" and (a.ranks != 1 or a.backend != "HostStaged"):
            raise RuntimeError("GPUMD reference runs are single process")
        if a.engine == "reference" and a.reference_launch_registry is None:
            raise RuntimeError("formal GPUMD jobs require --reference-launch-registry")
        runner.run_condition()
    print(json.dumps({"results": str(runner.results), "records": len(runner.records),
                      "statuses": [x["status"] for x in runner.records]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
