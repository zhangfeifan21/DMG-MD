#!/usr/bin/env python3
"""B4 output/restart contracts and short multi-element NPT checks.

This runner is intentionally separate from the B2/B3 preregistered suites.
Run --prepare first, freeze the resulting directory, then use --run only with
the exact candidate and input inventory recorded by the freeze file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
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
sys.path.insert(0, str(ROOT / "tests/baseline"))
sys.path.insert(0, str(ROOT / "tests/mpi"))
sys.path.insert(0, str(ROOT / "tests/long_nve"))
import run_baselines as baseline
import long_nve_common as long_nve
import check_environment
import generate_inputs as npt_inputs

ENSEMBLES = ("npt_ber", "npt_scr")
CASES = ("dense_water", "batio3_zbl")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    tmp.replace(path)


def trajectory_without_unwrapped(source: Path, target: Path) -> None:
    """Apply the locked baseline comparator to its supported XYZ property subset."""
    frames = baseline.parse_xyz(source)
    with target.open("w") as output:
        for frame in frames:
            props = frame["properties"]
            offset = 0
            keep = []
            for name, _kind, width in props:
                if name != "unwrapped_position":
                    keep.extend(range(offset, offset + width))
                offset += width
            if "unwrapped_position" not in {name for name, _, _ in props}:
                raise RuntimeError(f"{source}: expected unwrapped_position property")
            comment = frame["comment"].replace(":unwrapped_position:R:3", "")
            output.write(f"{frame['natoms']}\n{comment}\n")
            for row in frame["rows"]:
                output.write(" ".join(row[index] for index in keep) + "\n")


def run_text(ensemble: str, steps: int, dt_fs: float, xyz_interval: int = 0,
             restart_interval: int = 0) -> str:
    if ensemble == "npt_ber":
        control = "ensemble npt_ber 300 300 40 0 425 200"
    else:
        control = "ensemble npt_scr 300 300 40 0 425 200"
    lines = ["potential nep.txt", f"time_step {dt_fs:g}", control,
             "dump_thermo 1"]
    if xyz_interval:
        lines.append(f"dump_xyz {xyz_interval} trajectory.xyz precision double velocity unwrapped_position")
    if restart_interval:
        lines.append(f"dump_restart {restart_interval}")
    lines.append(f"run {steps}")
    return "\n".join(lines) + "\n"


def control_run(ensemble: str, bulk_modulus: float) -> str:
    return ("potential nep.txt\ntime_step 0.25\n"
            f"ensemble {ensemble} 300 300 40 0 {bulk_modulus:g} 200\n"
            "dump_thermo 1\n"
            "dump_xyz 1 trajectory.xyz precision double velocity force potential virial unwrapped_position\n"
            "dump_restart 1\nrun 1\n")


def prepare(args: argparse.Namespace) -> None:
    out = args.freeze_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = long_nve.load_manifest()
    fixture_records = {}
    for case_name in CASES:
        case = long_nve.profile_case(manifest, "smoke", case_name)
        text = long_nve.generate_model(case, 0)
        long_nve.validate_generated_model(case, 0, text)
        case_dir = out / "fixtures" / case_name
        case_dir.mkdir(parents=True)
        (case_dir / "model.xyz").write_text(text)
        potential = ROOT / case["potential"]
        if sha(potential) != case["potential_sha256"]:
            raise RuntimeError(f"{case_name}: pinned potential hash mismatch")
        shutil.copyfile(potential, case_dir / "nep.txt")
        fixture_records[case_name] = {
            "model_sha256": sha(case_dir / "model.xyz"),
            "potential_sha256": sha(case_dir / "nep.txt"),
            "model_sha256_registered": case["model_sha256"]["0"],
            "potential_source": str(potential),
            "potential_source_sha256": case["potential_sha256"],
            "atoms": case["atoms"], "dt_fs": case["time_step_fs"],
        }
        for ensemble in ENSEMBLES:
            (case_dir / f"{ensemble}.in").write_text(
                run_text(ensemble, 100, float(case["time_step_fs"]), 50, 100))
            (case_dir / f"{ensemble}-short.in").write_text(
                run_text(ensemble, 20, float(case["time_step_fs"]), 5, 20))
            (case_dir / f"{ensemble}-resume.in").write_text(
                run_text(ensemble, 5, float(case["time_step_fs"]), 5, 5))
    control_dir = out / "fixtures" / "output-timepoint"
    control_dir.mkdir(parents=True)
    (control_dir / "model.xyz").write_text(npt_inputs.model_text((3, 3, 3), 101))
    shutil.copyfile(ROOT / "tests/baseline/inputs/potentials/nep_C.txt", control_dir / "nep.txt")
    for ensemble in ENSEMBLES:
        (control_dir / f"{ensemble}-moving.in").write_text(control_run(ensemble, 425.0))
        (control_dir / f"{ensemble}-fixed.in").write_text(control_run(ensemble, 2001.0))
    (control_dir / "npt_scr-fixed-negative20.in").write_text(
        "potential nep.txt\ntime_step 0.25\n"
        "ensemble npt_scr 300 300 40 -1 2001 200\n"
        "dump_thermo 1\n"
        "dump_xyz 5 trajectory.xyz precision double velocity unwrapped_position\n"
        "dump_restart 20\nrun 20\n")
    (control_dir / "npt_scr-long40.in").write_text(
        run_text("npt_scr", 40, 0.25, 10, 20))
    common = ["potential nep.txt", "time_step 0.25",
              "ensemble npt_scr 300 300 40 0 425 200", "dump_thermo 1",
              "dump_xyz 10 trajectory.xyz precision double velocity unwrapped_position",
              "dump_restart 20"]
    measurements = common[3:]
    (control_dir / "npt_scr-split20x2.in").write_text(
        "\n".join(common + ["run 20"] + measurements + ["run 20"]) + "\n")
    files = {p.relative_to(out).as_posix(): sha(p)
             for p in sorted(out.rglob("*")) if p.is_file()}
    candidate = args.candidate.resolve()
    reference = args.reference.resolve()
    freeze = {
        "schema": "dmgmd.npt-output-contract-freeze.v1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidate": str(candidate), "candidate_sha256": sha(candidate),
        "reference": str(reference), "reference_sha256": sha(reference),
        "script_sha256": sha(Path(__file__).resolve()),
        "long_nve_manifest_sha256": sha(HERE.parents[1] / "tests/long_nve/manifest.json"),
        "baseline_manifest_sha256": sha(ROOT / "tests/baseline/manifest.json"),
        "fixtures": fixture_records, "files_sha256": files,
        "reference_heads": reference_heads(),
        "test_contract": {
            "ber_reference_steps": 100, "scr_repeat_steps": 20,
            "ranks_and_backends": "selected at run time; exact inventory recorded per run",
            "restart_precision": "C printf %g; six significant digits",
        },
    }
    write_json(out / "freeze.json", freeze)
    print(json.dumps({"freeze": str(out / "freeze.json"),
                      "candidate_sha256": freeze["candidate_sha256"],
                      "fixtures": fixture_records}, indent=2, sort_keys=True))


def reference_heads() -> dict:
    results = {}
    for name in ("gpumd-reference", "lammps-reference", "lammps-matpl-reference"):
        path = ROOT.parent / name
        head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(path), "status", "--short"],
                                capture_output=True, text=True, check=True).stdout
        ignored = subprocess.run(["git", "-C", str(path), "ls-files", "--others",
                                  "--ignored", "--exclude-standard", "-z"],
                                 capture_output=True, check=True).stdout
        ignored_paths = sorted(item.decode() for item in ignored.split(b"\0") if item)
        results[name] = {"head": head, "status_short": status,
                         "ignored_paths_sha256": hashlib.sha256(
                             "\n".join(ignored_paths).encode()).hexdigest(),
                         "ignored_path_count": len(ignored_paths)}
    return results


class ContractRunner:
    def __init__(self, args: argparse.Namespace, freeze: dict):
        self.args, self.freeze = args, freeze
        self.root = args.work_root.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.candidate = args.candidate.resolve()
        self.reference = args.reference.resolve()
        self.devices = args.devices.split(",")
        self.records: list[dict] = []
        self.results = self.root / "results.json"
        self.mpiexec = check_environment.validate_environment(
            self.candidate, "mpiexec", self.devices, max(args.ranks), args.timeout)
        self.save()

    def save(self) -> None:
        write_json(self.results, {
            "schema": "dmgmd.npt-output-contract-results.v1",
            "started_at_utc": self.started,
            "freeze_sha256": sha(self.args.freeze.resolve()),
            "candidate_sha256": sha(self.candidate),
            "records": self.records,
        })

    @property
    def started(self) -> str:
        if not hasattr(self, "_started"):
            self._started = dt.datetime.now(dt.timezone.utc).isoformat()
        return self._started

    def execute(self, label: str, case: str, ensemble: str, engine: str,
                ranks: int, backend: str, source_input: str,
                expected_failure: str | None = None,
                env_override: dict[str, str] | None = None,
                model_override: Path | None = None) -> tuple[Path, dict]:
        fixture = self.args.freeze.resolve().parent / "fixtures" / case
        stage = self.root / label
        stage.mkdir(parents=True)
        for name in ("model.xyz", "nep.txt"):
            src = model_override if name == "model.xyz" and model_override else fixture / name
            shutil.copyfile(src, stage / name)
        shutil.copyfile(fixture / source_input, stage / "run.in")
        env = os.environ.copy()
        env.update(CUDA_VISIBLE_DEVICES=",".join(self.devices[:ranks]),
                   CUDA_DEVICE_ORDER="PCI_BUS_ID", DMGMD_COMM_BACKEND=backend,
                   DMGMD_DOMAIN_DIAGNOSTICS="1", DMGMD_COMM_LOG_INTERVAL="1",
                   OMP_NUM_THREADS="1", DMGMD_ENSEMBLE_SEED="12345678")
        if engine == "reference":
            env.pop("DMGMD_ENSEMBLE_SEED", None)
        if env_override:
            env.update(env_override)
        executable = self.reference if engine == "reference" else self.candidate
        command = ([str(executable)] if engine == "reference" else
                   [str(self.mpiexec), "-n", str(ranks), str(executable)])
        record = {"label": label, "case": case, "ensemble": ensemble,
                  "engine": engine, "ranks": ranks, "backend": backend,
                  "command": command, "cwd": str(stage),
                  "inputs_sha256": {n: sha(stage / n) for n in ("model.xyz", "run.in", "nep.txt")},
                  "candidate_sha256": sha(executable),
                  "cuda_visible_devices": env["CUDA_VISIBLE_DEVICES"],
                  "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        self.records.append(record)
        self.save()
        begin = time.monotonic()
        with (stage / "stdout.log").open("w") as stdout, (stage / "stderr.log").open("w") as stderr:
            proc = subprocess.Popen(command, cwd=stage, env=env, stdout=stdout, stderr=stderr,
                                    start_new_session=True)
            try:
                rc = proc.wait(timeout=self.args.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                record.update(status="TIMEOUT", returncode=None,
                              duration_s=time.monotonic() - begin)
                self.save()
                raise RuntimeError(f"{label}: timeout, retained at {stage}")
        logs = (stage / "stdout.log").read_text(errors="replace") + "\n" + \
               (stage / "stderr.log").read_text(errors="replace")
        record.update(returncode=rc, duration_s=time.monotonic() - begin,
                      output_sha256={p.name: sha(p) for p in stage.iterdir() if p.is_file()
                                     and p.name not in ("model.xyz", "run.in", "nep.txt")},
                      route_lines=[line for line in logs.splitlines() if "DMGMD_DOMAIN" in line])
        if expected_failure:
            if rc == 0 or not re.search(expected_failure, logs, re.IGNORECASE):
                record["status"] = "FAIL"
                self.save()
                raise RuntimeError(f"{label}: expected failed exit with /{expected_failure}/; rc={rc}")
            if any((stage / name).exists() and (stage / name).stat().st_size > 0
                   for name in ("trajectory.xyz", "restart.xyz")):
                record["status"] = "FAIL"
                self.save()
                raise RuntimeError(f"{label}: failure produced success output")
            thermo_path = stage / "thermo.out"
            numeric_rows = []
            if thermo_path.exists():
                numeric_rows = [row for section in baseline.parse_thermo(thermo_path)
                                for row in section["rows"]]
            if numeric_rows:
                record["status"] = "FAIL"
                self.save()
                raise RuntimeError(f"{label}: failure left {len(numeric_rows)} numeric thermo rows")
            record["numeric_thermo_rows_after_failure"] = 0
            record["status"] = "EXPECTED_FAILURE"
        else:
            if rc != 0:
                record["status"] = "FAIL"
                self.save()
                raise RuntimeError(f"{label}: exit {rc}; inspect {stage}/stderr.log")
            record["status"] = "PASS"
        self.save()
        return stage, record

    @staticmethod
    def check_outputs(stage: Path, natoms: int, steps: int) -> dict:
        thermo = baseline.parse_thermo(stage / "thermo.out")
        rows = [row for segment in thermo for row in segment["rows"]]
        frames = baseline.parse_xyz(stage / "trajectory.xyz")
        restart_frames = baseline.parse_xyz(stage / "restart.xyz")
        if len(rows) != steps:
            raise RuntimeError(f"{stage}: expected {steps} thermo snapshots, got {len(rows)}")
        if [frame["natoms"] for frame in frames] != [natoms] * len(frames):
            raise RuntimeError(f"{stage}: trajectory atom count changed")
        if len(restart_frames) != 1 or restart_frames[0]["natoms"] != natoms:
            raise RuntimeError(f"{stage}: restart shape mismatch")
        final = frames[-1]
        restart = restart_frames[0]
        names = {name for name, _, _ in final["properties"]}
        if not {"pos", "vel", "unwrapped_position"}.issubset(names):
            raise RuntimeError(f"{stage}: missing pos/vel/unwrapped_position in dump properties: {names}")
        if final["fields"].get("Lattice") is None:
            raise RuntimeError(f"{stage}: trajectory lacks final lattice")
        if [format(float(x), ".6g") for x in final["fields"]["Lattice"].split()] != \
                restart["fields"]["Lattice"].split():
            raise RuntimeError(f"{stage}: restart lattice does not match final snapshot %g serialization")
        for prop in ("pos", "vel"):
            ti = next(i for i, (name, _, _) in enumerate(final["properties"]) if name == prop)
            ri = next(i for i, (name, _, _) in enumerate(restart["properties"]) if name == prop)
            toff = sum(w for _, _, w in final["properties"][:ti])
            roff = sum(w for _, _, w in restart["properties"][:ri])
            width = final["properties"][ti][2]
            for tr, rr in zip(final["rows"], restart["rows"]):
                if tr[0] != rr[0]:
                    raise RuntimeError(f"{stage}: trajectory/restart atom identity mismatch")
                if [format(float(x), ".6g") for x in tr[toff:toff + width]] != rr[roff:roff + width]:
                    raise RuntimeError(f"{stage}: restart {prop} does not match snapshot %g serialization")
        box_xyz = [float(x) for x in final["fields"]["Lattice"].split()]
        box_thermo = rows[-1][9:18]
        if not all(math.isfinite(value) for value in box_xyz + box_thermo):
            raise RuntimeError(f"{stage}: non-finite box in output")
        if max(abs(a - b) for a, b in zip(box_xyz, box_thermo)) > 1e-7:
            raise RuntimeError(f"{stage}: final thermo box does not match its post-scale XYZ snapshot")
        return {"thermo_rows": len(rows), "trajectory_frames": len(frames),
                "restart_frames": len(restart_frames), "restart_percent_g_matches_snapshot": True,
                "last_thermo_box": box_thermo, "last_snapshot_box": box_xyz,
                "thermo_and_snapshot_box_delta_max": max(abs(a - b) for a, b in zip(box_thermo, box_xyz))}

    def run(self) -> None:
        fixture_manifest = long_nve.load_manifest()
        for case in CASES:
            spec = long_nve.profile_case(fixture_manifest, "smoke", case)
            for ensemble in ENSEMBLES:
                # Short duplicate SCR confirms deterministic fixed-seed output.
                steps = 100 if ensemble == "npt_ber" else 20
                input_name = f"{ensemble}.in" if steps == 100 else f"{ensemble}-short.in"
                checkpoints = {}
                for ranks in self.args.ranks:
                    for backend in self.args.backends:
                        label = f"{case}-{ensemble}-p{ranks}-{backend}"
                        stage, record = self.execute(label, case, ensemble, "candidate", ranks,
                                                     backend, input_name)
                        record["contract"] = self.check_outputs(stage, int(spec["atoms"]), steps)
                        if not any("mode=m1" in line and "reason=npt-m1-only" in line
                                   for line in record["route_lines"]):
                            raise RuntimeError(f"{label}: missing explicit M1-only route record")
                        if ensemble == "npt_ber" and ranks == 1 and backend == self.args.backends[0]:
                            ref, refrec = self.execute(f"{label}-gpumd", case, ensemble,
                                                       "reference", 1, "HostStaged", input_name)
                            refrec["contract"] = self.check_outputs(ref, int(spec["atoms"]), steps)
                            metrics = baseline.DiffCollector(
                                json.loads((ROOT / "tests/baseline/manifest.json").read_text())["tolerances"],
                                enforce=True)
                            baseline.compare_thermo(ref / "thermo.out", stage / "thermo.out", metrics, label)
                            ref_xyz = ref / "trajectory-compatible.xyz"
                            cand_xyz = stage / "trajectory-compatible.xyz"
                            trajectory_without_unwrapped(ref / "trajectory.xyz", ref_xyz)
                            trajectory_without_unwrapped(stage / "trajectory.xyz", cand_xyz)
                            baseline.compare_xyz(ref_xyz, cand_xyz,
                                                 {"kind": "xyz"}, metrics, label + "/trajectory")
                            baseline.compare_xyz(ref / "restart.xyz", stage / "restart.xyz",
                                                 {"kind": "restart"}, metrics, label + "/restart")
                            record["gpumd_comparison"] = metrics.stats
                        if ensemble == "npt_scr":
                            repeat, rep = self.execute(label + "-repeat", case, ensemble,
                                                       "candidate", ranks, backend, input_name)
                            rep["contract"] = self.check_outputs(repeat, int(spec["atoms"]), steps)
                            if sha(stage / "thermo.out") != sha(repeat / "thermo.out"):
                                raise RuntimeError(f"{label}: fixed-seed SCR repeated thermo differs")
                        checkpoints[(ranks, backend)] = stage / "restart.xyz"
                        self.save()
                # New-job restart at a different rank count is a classification
                # check only; the contract explicitly makes no trajectory-
                # continuity promise across process layouts.
                if len(self.args.ranks) > 1:
                    for (source_ranks, source_backend), checkpoint in checkpoints.items():
                        target_ranks = next((n for n in self.args.ranks if n != source_ranks), None)
                        if target_ranks is None:
                            continue
                        target_backend = self.args.backends[0]
                        label = (f"{case}-{ensemble}-restart-p{source_ranks}-to-p{target_ranks}-"
                                 f"{source_backend}-to-{target_backend}")
                        resumed, record = self.execute(
                            label, case, ensemble, "candidate", target_ranks, target_backend,
                            f"{ensemble}-resume.in", model_override=checkpoint)
                        record["contract"] = self.check_outputs(resumed, int(spec["atoms"]), 5)
                        if not any("mode=m1" in line and "reason=npt-m1-only" in line
                                   for line in record["route_lines"]):
                            raise RuntimeError(f"{label}: missing explicit M1-only route record")
                        record["restart_classification"] = "NEW_JOB_CROSS_RANK; continuity not promised"
                        self.save()
        self.run_output_timepoint_pair()

    def run_output_timepoint_pair(self) -> None:
        """Compare post-scale snapshots with a same-step fixed-box control."""
        fixture = "output-timepoint"
        case_spec = {"atoms": 216}
        for ensemble in ENSEMBLES:
            moving, moving_record = self.execute(
                f"output-timepoint-{ensemble}-moving", fixture, ensemble, "candidate", 1,
                self.args.backends[0], f"{ensemble}-moving.in")
            fixed, fixed_record = self.execute(
                f"output-timepoint-{ensemble}-fixed", fixture, ensemble, "candidate", 1,
                self.args.backends[0], f"{ensemble}-fixed.in")
            moving_record["contract"] = self.check_outputs(moving, case_spec["atoms"], 1)
            fixed_record["contract"] = self.check_outputs(fixed, case_spec["atoms"], 1)
            moving_xyz = baseline.parse_xyz(moving / "trajectory.xyz")[-1]
            fixed_xyz = baseline.parse_xyz(fixed / "trajectory.xyz")[-1]
            moving_thermo = [r for s in baseline.parse_thermo(moving / "thermo.out") for r in s["rows"]][-1]
            fixed_thermo = [r for s in baseline.parse_thermo(fixed / "thermo.out") for r in s["rows"]][-1]
            if len(moving_thermo) != 18 or len(fixed_thermo) != 18:
                raise RuntimeError(f"{ensemble}: one-step thermo output shape mismatch")
            thermo_delta = max(abs(a - b) for a, b in zip(moving_thermo[:9], fixed_thermo[:9]))
            if thermo_delta > 1e-10:
                raise RuntimeError(f"{ensemble}: pre-scale thermodynamics differ from fixed-box control: {thermo_delta:g}")
            lattice_m = [float(v) for v in moving_xyz["fields"]["Lattice"].split()]
            lattice_f = [float(v) for v in fixed_xyz["fields"]["Lattice"].split()]
            moving_lengths = [lattice_m[i] for i in (0, 4, 8)]
            fixed_lengths = [lattice_f[i] for i in (0, 4, 8)]
            factors = [m / f for m, f in zip(moving_lengths, fixed_lengths)]
            if max(factors) - min(factors) > 1e-11 or abs(factors[0] - 1.0) < 1e-12:
                raise RuntimeError(f"{ensemble}: expected non-unit isotropic box scaling, got {factors}")
            if max(abs(a - b) for a, b in zip(moving_thermo[9:18], lattice_m)) > 1e-7:
                raise RuntimeError(f"{ensemble}: moving thermo box does not match post-scale snapshot")
            if max(abs(a - b) for a, b in zip(fixed_thermo[9:18], lattice_f)) > 1e-7:
                raise RuntimeError(f"{ensemble}: fixed thermo box does not match fixed snapshot")
            properties = {name: (sum(width for _, _, width in moving_xyz["properties"][:i]), width)
                          for i, (name, _kind, width) in enumerate(moving_xyz["properties"])}
            fixed_props = {name: (sum(width for _, _, width in fixed_xyz["properties"][:i]), width)
                           for i, (name, _kind, width) in enumerate(fixed_xyz["properties"])}
            if set(properties) != set(fixed_props):
                raise RuntimeError(f"{ensemble}: fixed/moving snapshot property sets differ")
            scalar_max = {}
            for prop in ("vel", "unwrapped_position", "forces", "energy_atom", "virial"):
                om, width = properties[prop]
                of, width_f = fixed_props[prop]
                if width != width_f:
                    raise RuntimeError(f"{ensemble}: property width mismatch for {prop}")
                delta = max(abs(float(a) - float(b))
                            for ra, rb in zip(moving_xyz["rows"], fixed_xyz["rows"])
                            for a, b in zip(ra[om:om + width], rb[of:of + width]))
                scalar_max[prop] = delta
                if delta > 1e-10:
                    raise RuntimeError(f"{ensemble}: {prop} changed during box scale: {delta:g}")
            pos_m, width = properties["pos"]
            pos_f, width_f = fixed_props["pos"]
            if width != width_f:
                raise RuntimeError(f"{ensemble}: position property width mismatch")
            position_delta = max(abs(float(a) - factors[0] * float(b))
                                 for ra, rb in zip(moving_xyz["rows"], fixed_xyz["rows"])
                                 for a, b in zip(ra[pos_m:pos_m + width], rb[pos_f:pos_f + width]))
            if position_delta > 1e-9:
                raise RuntimeError(f"{ensemble}: snapshot positions are not post-scale: {position_delta:g}")
            moving_record["output_timepoint_pair"] = {
                "fixed_control": str(fixed), "isotropic_scale": factors[0],
                "thermo_pre_scale_first9_max_delta": thermo_delta,
                "thermo_post_scale_box_matches_snapshot": True,
                "snapshot_position_scaled_from_fixed_max_delta": position_delta,
                "unchanged_snapshot_fields_max_delta": scalar_max,
                "interpretation": "thermo and force/virial/energy are pre-box-scale; trajectory/restart pos and lattice post-scale; thermostat velocity and unwrapped coordinates unchanged by box scaling",
            }
            self.save()
        # Same-process SCR segment reset classification. This is a semantic
        # classification, not a promise that split and continuous trajectories
        # are byte-identical.
        long, long_record = self.execute(
            "scr-segment-classification-long40", fixture, "npt_scr", "candidate", 1,
            self.args.backends[0], "npt_scr-long40.in")
        split, split_record = self.execute(
            "scr-segment-classification-split20x2", fixture, "npt_scr", "candidate", 1,
            self.args.backends[0], "npt_scr-split20x2.in")
        long_thermo = [r for s in baseline.parse_thermo(long / "thermo.out") for r in s["rows"]]
        split_thermo = [r for s in baseline.parse_thermo(split / "thermo.out") for r in s["rows"]]
        if len(long_thermo) != 40 or len(split_thermo) != 40:
            raise RuntimeError("SCR same-process segment classification has unexpected thermo row count")
        first_diff = None
        for i, (left, right) in enumerate(zip(long_thermo, split_thermo)):
            for j, (a, b) in enumerate(zip(left, right)):
                if a != b:
                    first_diff = {"row": i, "column": j, "continuous": a, "split": b}
                    break
            if first_diff:
                break
        self.records.append({
            "label": "scr-same-process-segment-reset-classification",
            "status": "PASS", "continuous_thermo_sha256": sha(long / "thermo.out"),
            "split_thermo_sha256": sha(split / "thermo.out"),
            "continuous_restart_sha256": sha(long / "restart.xyz"),
            "split_restart_sha256": sha(split / "restart.xyz"),
            "first_thermo_difference": first_diff,
            "contract": "classification only; each run segment reinitializes SCR controller state; no trajectory continuity promise",
        })
        self.save()
        fixed_negative, record = self.execute(
            "scr-fixed-box-negative-pressure-20", fixture, "npt_scr", "candidate", 1,
            self.args.backends[0], "npt_scr-fixed-negative20.in")
        contract = self.check_outputs(fixed_negative, 216, 20)
        model_initial = baseline.parse_xyz(fixture_dir(self.args.freeze) / "model.xyz")[0]
        initial_lattice = [float(v) for v in model_initial["fields"]["Lattice"].split()]
        thermo_rows = [r for section in baseline.parse_thermo(fixed_negative / "thermo.out")
                       for r in section["rows"]]
        max_box_delta = max(
            abs(value - initial_lattice[i])
            for row in [*thermo_rows, *[[float(v) for v in frame["fields"]["Lattice"].split()]
                                       for frame in baseline.parse_xyz(fixed_negative / "trajectory.xyz")]]
            for i, value in enumerate(row[9:18] if len(row) == 18 else row))
        if max_box_delta > 1e-12:
            raise RuntimeError(f"SCR B>2000 negative-pressure branch changed fixed box by {max_box_delta:g}")
        record["contract"] = {**contract, "target_pressure_gpa": -1.0,
                               "bulk_modulus_gpa": 2001.0,
                               "max_box_delta_from_initial": max_box_delta,
                               "classification": "negative target pressure accepted; B>2000 fixed-box branch"}
        if not any("reason=npt-m1-only" in line for line in record["route_lines"]):
            raise RuntimeError("SCR fixed-box negative-pressure case lacks explicit M1 route")
        self.save()


def fixture_dir(freeze_path: Path) -> Path:
    return freeze_path.resolve().parent / "fixtures" / "output-timepoint"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prepare", action="store_true")
    p.add_argument("--run", action="store_true")
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--reference", type=Path,
                   default=ROOT.parent / "gpumd-reference/src/gpumd")
    p.add_argument("--freeze-dir", type=Path)
    p.add_argument("--freeze", type=Path)
    p.add_argument("--work-root", type=Path)
    p.add_argument("--devices", default="5")
    p.add_argument("--ranks", default="1")
    p.add_argument("--backends", default="HostStaged")
    p.add_argument("--timeout", type=int, default=900)
    args = p.parse_args()
    args.candidate = args.candidate.resolve()
    args.reference = args.reference.resolve()
    if args.prepare == args.run:
        p.error("select exactly one of --prepare or --run")
    if args.prepare:
        if args.freeze_dir is None:
            p.error("--prepare requires --freeze-dir")
        prepare(args)
        return 0
    if not args.freeze or not args.work_root:
        p.error("--run requires --freeze and --work-root")
    freeze = json.loads(args.freeze.resolve().read_text())
    if freeze["candidate_sha256"] != sha(args.candidate):
        raise RuntimeError("candidate binary does not match the frozen SHA-256")
    if freeze["reference_sha256"] != sha(args.reference):
        raise RuntimeError("GPUMD binary does not match the frozen SHA-256")
    if freeze["script_sha256"] != sha(Path(__file__).resolve()):
        raise RuntimeError("runner script differs from the frozen SHA-256")
    input_root = args.freeze.resolve().parent
    actual = {p.relative_to(input_root).as_posix(): sha(p)
              for p in sorted(input_root.rglob("*")) if p.is_file() and p.name != "freeze.json"}
    if actual != freeze["files_sha256"]:
        raise RuntimeError("frozen input file inventory/hash mismatch")
    if freeze["reference_heads"] != reference_heads():
        raise RuntimeError("reference checkout identity/status changed after freeze")
    args.ranks = [int(v) for v in args.ranks.split(",")]
    args.backends = args.backends.split(",")
    ContractRunner(args, freeze).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
