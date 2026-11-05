#!/usr/bin/env python3
"""B2 GPUMD differential, replicated-full routing, output/restart, and fail-closed matrix."""
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
sys.path.insert(0, str(ROOT / "tests/baseline"))
sys.path.insert(0, str(ROOT / "tests/mpi"))
sys.path.insert(0, str(HERE))
import run_baselines as baseline
import check_environment
import generate_inputs as inputs


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def reference_status_snapshot() -> dict:
    references = {}
    for name in ("gpumd-reference", "lammps-reference", "lammps-matpl-reference"):
        path = ROOT.parent / name
        head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                              check=True, text=True, capture_output=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(path), "status", "--short"],
                                check=True, text=True, capture_output=True).stdout
        ignored = subprocess.run(["git", "-C", str(path), "ls-files", "--others", "--ignored",
                                  "--exclude-standard", "-z"], check=True, capture_output=True).stdout
        paths = sorted(item.decode() for item in ignored.split(b"\0") if item)
        references[name] = {"head": head, "tracked_status": status,
                            "ignored_paths": paths,
                            "ignored_paths_sha256": hashlib.sha256("\n".join(paths).encode()).hexdigest()}
    return references


class Runner:
    def __init__(self, args: argparse.Namespace, manifest: dict, freeze: dict):
        self.args = args
        self.manifest = manifest
        self.freeze = freeze
        self.root = args.work_root.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.records: list[dict] = []
        self.tolerances = json.loads((ROOT / "tests/baseline/manifest.json").read_text())["tolerances"]
        self.started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        self.mpiexec: Path | None = None
        self.result_path = self.root / "results.json"
        self.save()

    def save(self) -> None:
        write_json(self.result_path, {
            "schema": "dmgmd.npt-ber-results.v1",
            "started_at_utc": self.started_at,
            "candidate": str(self.args.candidate.resolve()),
            "reference": str(self.args.reference.resolve()),
            "candidate_sha256": sha(self.args.candidate),
            "reference_sha256": sha(self.args.reference),
            "manifest_sha256": self.freeze["manifest_sha256"],
            "freeze_sha256": sha(self.args.freeze),
            "devices": self.args.devices,
            "records": self.records,
        })

    def stage_inputs(self, label: str, model_text: str, run_text: str) -> Path:
        stage = self.root / label
        stage.mkdir(parents=True, exist_ok=False)
        (stage / "model.xyz").write_text(model_text)
        (stage / "run.in").write_text(run_text)
        shutil.copyfile(ROOT / self.manifest["source_contract"]["potential_path"], stage / "nep.txt")
        return stage

    def execute(self, label: str, engine: str, model_text: str, run_text: str,
                ranks: int = 1, backend: str = "HostStaged", devices: list[str] | None = None,
                timeout: int = 600, allow_failure: bool = False) -> tuple[Path, dict]:
        stage = self.stage_inputs(label, model_text, run_text)
        selected = devices or self.args.devices[:ranks]
        environment = os.environ.copy()
        environment["CUDA_VISIBLE_DEVICES"] = ",".join(selected)
        environment["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["DMGMD_COMM_BACKEND"] = backend
        environment["DMGMD_DOMAIN_DIAGNOSTICS"] = "1"
        environment["DMGMD_COMM_LOG_INTERVAL"] = "1"
        executable = self.args.reference if engine == "reference" else self.args.candidate
        command = ([str(executable)] if engine == "reference" else
                   [str(self.mpiexec), "-n", str(ranks), str(executable)])
        input_hashes = {name: sha(stage / name) for name in ("model.xyz", "run.in", "nep.txt")}
        record = {
            "label": label, "engine": engine, "ranks": ranks, "backend": backend,
            "command": command, "cwd": str(stage), "input_sha256": input_hashes,
            "executable_sha256": sha(executable),
            "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "cuda_visible_devices": environment["CUDA_VISIBLE_DEVICES"],
        }
        self.records.append(record)
        self.save()
        start = time.monotonic()
        with (stage / "execution.stdout").open("w") as stdout, (stage / "execution.stderr").open("w") as stderr:
            process = subprocess.Popen(command, cwd=stage, env=environment,
                                       stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                record.update(status="TIMEOUT", duration_seconds=time.monotonic() - start)
                self.save()
                raise RuntimeError(f"{label} timed out after {timeout}s; evidence in {stage}")
        record["duration_seconds"] = time.monotonic() - start
        record["returncode"] = returncode
        record["output_sha256"] = {p.name: sha(p) for p in stage.iterdir()
                                   if p.is_file() and p.name not in ("model.xyz", "run.in", "nep.txt")}
        logs = (stage / "execution.stdout").read_text(errors="replace") + "\n" + \
               (stage / "execution.stderr").read_text(errors="replace")
        record["route_lines"] = [line for line in logs.splitlines() if "DMGMD_DOMAIN" in line]
        record["status"] = ("PASS" if returncode == 0 else
                            ("EXPECTED_FAILURE" if allow_failure else "FAIL"))
        self.save()
        if (returncode == 0) == allow_failure:
            expected = "failure" if allow_failure else "success"
            raise RuntimeError(f"{label}: expected {expected}, got exit {returncode}; evidence {stage}")
        return stage, record

    @staticmethod
    def ensure_success_outputs(stage: Path, required: tuple[str, ...]) -> None:
        missing = [name for name in required if not (stage / name).is_file()]
        if missing:
            raise RuntimeError(f"{stage}: missing expected outputs {missing}")

    def compare(self, reference: Path, candidate: Path, label: str,
                filenames: tuple[str, ...] = ("thermo.out", "trajectory.xyz", "restart.xyz")) -> dict:
        collector = baseline.DiffCollector(self.tolerances, enforce=True)
        try:
            for filename in filenames:
                left, right = reference / filename, candidate / filename
                if filename == "thermo.out":
                    baseline.compare_thermo(left, right, collector, label)
                else:
                    kind = "restart" if filename == "restart.xyz" else "xyz"
                    baseline.compare_xyz(left, right, {"kind": kind}, collector, label + "/" + filename)
        except Exception as error:
            self.records.append({"label": f"comparison/{label}", "status": "FAIL",
                                 "reference": str(reference), "candidate": str(candidate),
                                 "first_mismatch": str(error),
                                 "metrics_before_failure": collector.stats})
            self.save()
            raise
        return collector.stats


def fixed_box_check(stage: Path, start_model: str) -> dict:
    from run_baselines import parse_thermo
    initial_fields = baseline.parse_fields(start_model.splitlines()[1])[1]
    start_lattice = [float(x) for x in initial_fields["Lattice"].split()]
    segments = parse_thermo(stage / "thermo.out")
    rows = [row for segment in segments for row in segment["rows"]]
    end_lattice = rows[-1][9:18]
    max_abs = max(abs(a - b) for a, b in zip(start_lattice, end_lattice))
    if max_abs > 1e-12:
        raise RuntimeError(f"B>2000 fixed-box branch changed thermo lattice by {max_abs:.17g} Å")
    if not all(__import__("math").isfinite(value) for value in end_lattice):
        raise RuntimeError("non-finite thermo lattice in B>2000 output")
    return {"start_lattice": start_lattice, "final_thermo_lattice": end_lattice,
            "max_abs_lattice_delta_A": max_abs}


def restart_semantics_check(stage: Path) -> dict:
    from run_baselines import parse_xyz, parse_thermo
    trajectory = parse_xyz(stage / "trajectory.xyz")[-1]
    restart = parse_xyz(stage / "restart.xyz")[-1]
    thermo = parse_thermo(stage / "thermo.out")[-1]["rows"][-1]
    rlat = [float(x) for x in restart["fields"]["Lattice"].split()]
    if trajectory["natoms"] != restart["natoms"]:
        raise RuntimeError("restart/trajectory atom counts differ at the shared final snapshot")
    canonical_lattice = [format(float(value), ".6g") for value in trajectory["fields"]["Lattice"].split()]
    restart_lattice_tokens = restart["fields"]["Lattice"].split()
    if canonical_lattice != restart_lattice_tokens:
        raise RuntimeError("restart Lattice is not the %g serialization of the final trajectory snapshot")
    def property_offsets(frame: dict) -> dict:
        offsets = {}
        offset = 0
        for name, _kind, width in frame["properties"]:
            offsets[name] = (offset, width)
            offset += width
        return offsets
    trajectory_properties = property_offsets(trajectory)
    restart_properties = property_offsets(restart)
    for prop in ("pos", "vel"):
        if prop not in trajectory_properties or prop not in restart_properties:
            raise RuntimeError(f"shared trajectory/restart snapshot lacks {prop} property")
        start_t, width_t = trajectory_properties[prop]
        start_r, width_r = restart_properties[prop]
        if width_t != width_r:
            raise RuntimeError(f"trajectory/restart {prop} widths differ")
        for atom, (trow, rrow) in enumerate(zip(trajectory["rows"], restart["rows"])):
            if trow[0] != rrow[0]:
                raise RuntimeError(f"trajectory/restart atom order differs at row {atom}")
            expected = [format(float(value), ".6g") for value in trow[start_t:start_t + width_t]]
            actual = rrow[start_r:start_r + width_r]
            if expected != actual:
                raise RuntimeError(f"restart {prop} is not the %g serialization at atom row {atom}: "
                                   f"expected {expected}, got {actual}")
    thermo_lat = thermo[9:18]
    return {"restart_matches_final_trajectory_snapshot": True,
            "restart_format": "C %g, six significant digits",
            "lattice_serialization": "exact .6g token match",
            "position_serialization": "exact .6g token match for every atom",
            "velocity_serialization": "exact .6g token match for every atom",
            "thermo_final_stress_gpa": thermo[3:9],
            "restart_lattice_A": rlat, "thermo_box_A": thermo_lat}


def assert_route(record: dict) -> None:
    if not any(re.search(r"DMGMD_DOMAIN mode=m1 reason=npt-m1-only", line)
               for line in record.get("route_lines", [])):
        raise RuntimeError(f"{record['label']}: missing explicit M1-only NPT route record")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path,
                        default=ROOT.parent / "gpumd-reference" / "src" / "gpumd")
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--devices", required=True, help="physical GPU indices, comma-separated")
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    args.candidate = args.candidate.resolve()
    args.reference = args.reference.resolve()
    args.freeze = args.freeze.resolve()
    args.work_root = args.work_root.resolve()
    manifest = json.loads((HERE / "manifest.json").read_text())
    freeze = json.loads(args.freeze.read_text())
    if sha(HERE / "manifest.json") != freeze["manifest_sha256"]:
        raise RuntimeError("manifest hash differs from frozen B2 execution identity")
    if sha(args.candidate) != freeze["candidate_sha256"]:
        raise RuntimeError("candidate binary differs from frozen B2 execution identity")
    if sha(args.reference) != freeze["reference_sha256"]:
        raise RuntimeError("GPUMD binary differs from frozen B2 execution identity")
    if sha(ROOT / "tests/baseline/manifest.json") != freeze["baseline_manifest_sha256"]:
        raise RuntimeError("baseline/tolerance manifest changed after B2 freeze")
    for name, expected in freeze["script_sha256"].items():
        if sha(ROOT / name) != expected:
            raise RuntimeError(f"test script hash changed after B2 freeze: {name}")
    if reference_status_snapshot() != freeze["reference_status_before"]:
        raise RuntimeError("reference repositories differ from their pre-run frozen state")
    devices = args.devices.split(",")
    if len(devices) < 4:
        raise RuntimeError("P1/P2/P4 route matrix requires four selected physical GPUs")
    runner = Runner(args, manifest, freeze)
    runner.mpiexec = check_environment.validate_environment(
        args.candidate.resolve(), "mpiexec", devices, 4, args.timeout)
    runner.records.append({"label": "environment-gate", "status": "PASS",
                           "devices": devices, "max_ranks": 4})
    runner.save()

    input_root = Path(freeze["generated_input_dir"])
    if not input_root.is_dir():
        raise RuntimeError(f"registered frozen input directory is missing: {input_root}")
    actual_input_hashes = {p.relative_to(input_root).as_posix(): sha(p)
                           for p in sorted(input_root.rglob("*")) if p.is_file()}
    if actual_input_hashes != freeze["generated_input_sha256"]:
        raise RuntimeError("generated model/run input inventory differs from frozen hashes")
    shutil.copytree(input_root, runner.root / "registered-inputs")
    potential = ROOT / manifest["source_contract"]["potential_path"]
    if sha(Path(potential)) != manifest["source_contract"]["potential_sha256"]:
        raise RuntimeError("pinned NEP_C potential hash mismatch")

    # The same explicit model/run/potential bytes feed reference and candidate.
    for label, model_name in (("ber216", "ber216"), ("ber2016", "ber2016")):
        model = (input_root / model_name / "model.xyz").read_text()
        run = (input_root / "ber-golden-200" / "run.in").read_text()
        ref, ref_rec = runner.execute(f"reference-{label}", "reference", model, run,
                                      devices=devices[:1], timeout=args.timeout)
        cand, cand_rec = runner.execute(f"candidate-{label}-p1-host", "candidate", model, run,
                                        ranks=1, backend="HostStaged", devices=devices[:1], timeout=args.timeout)
        runner.ensure_success_outputs(ref, ("thermo.out", "trajectory.xyz", "restart.xyz"))
        runner.ensure_success_outputs(cand, ("thermo.out", "trajectory.xyz", "restart.xyz"))
        stats = runner.compare(ref, cand, label)
        ref_rows = baseline.parse_thermo(ref / "thermo.out")
        cand_rows = baseline.parse_thermo(cand / "thermo.out")
        ref_xyz = baseline.parse_xyz(ref / "trajectory.xyz")
        cand_xyz = baseline.parse_xyz(cand / "trajectory.xyz")
        if len(ref_rows) != 1 or len(cand_rows) != 1 or len(ref_rows[0]["rows"]) != 200 or len(cand_rows[0]["rows"]) != 200:
            raise RuntimeError(f"{label}: expected exactly 200 thermo rows")
        if len(ref_xyz) != 10 or len(cand_xyz) != 10:
            raise RuntimeError(f"{label}: expected 10 trajectory frames at 20-step cadence")
        cand_rec["comparison"] = stats
        runner.save()

    route_run = (input_root / "ber-route-20-target5" / "run.in").read_text()
    backend_names = ["HostStaged", "CudaAware"]
    for fixture_name in ("ber216", "route5184"):
        model = (input_root / fixture_name / "model.xyz").read_text()
        ref_stage, _ = runner.execute(f"reference-route-{fixture_name}", "reference", model,
                                      route_run, devices=devices[:1], timeout=args.timeout)
        for backend in backend_names:
            p1_stage, p1_rec = runner.execute(f"route-{fixture_name}-{backend}-p1", "candidate", model,
                                              route_run, ranks=1, backend=backend,
                                              devices=devices[:1], timeout=args.timeout)
            assert_route(p1_rec)
            runner.ensure_success_outputs(p1_stage, ("thermo.out", "trajectory.xyz", "restart.xyz"))
            runner.compare(ref_stage, p1_stage, f"route/{fixture_name}/{backend}/p1")
            runner.records[-1]["comparison_reference"] = str(ref_stage)
            for ranks in (2, 4):
                stage, record = runner.execute(f"route-{fixture_name}-{backend}-p{ranks}", "candidate", model,
                                               route_run, ranks=ranks, backend=backend,
                                               devices=devices[:ranks], timeout=args.timeout)
                assert_route(record)
                runner.ensure_success_outputs(stage, ("thermo.out", "trajectory.xyz", "restart.xyz"))
                record["comparison_to_gpumd"] = runner.compare(ref_stage, stage,
                                                                f"route/{fixture_name}/{backend}/p{ranks}")
                record["comparison_to_candidate_p1"] = runner.compare(p1_stage, stage,
                                                                        f"route/{fixture_name}/{backend}/p1-v-p{ranks}")
                record["restart_semantics"] = restart_semantics_check(stage)
                runner.save()

    # Direction response, negative target pressure, and B>2000 fixed-box branch.
    for fixture_name, direction in (("compress216", "expand"), ("expand216", "shrink")):
        model = (input_root / fixture_name / "model.xyz").read_text()
        run = (input_root / "ber-pressure-track-20" / "run.in").read_text()
        ref, _ = runner.execute(f"reference-pressure-direction-{fixture_name}", "reference", model, run,
                                devices=devices[:1], timeout=args.timeout)
        stage, record = runner.execute(f"pressure-direction-{fixture_name}", "candidate", model, run,
                                       ranks=1, backend="HostStaged", devices=devices[:1], timeout=args.timeout)
        start_lattice = [float(x) for x in baseline.parse_fields(model.splitlines()[1])[1]["Lattice"].split()]
        frame = baseline.parse_xyz(stage / "trajectory.xyz")[0]
        end_lattice = [float(x) for x in frame["fields"]["Lattice"].split()]
        if direction == "expand" and not end_lattice[0] > start_lattice[0]:
            raise RuntimeError(f"{fixture_name}: P_inst>P_target did not expand the box")
        if direction == "shrink" and not end_lattice[0] < start_lattice[0]:
            raise RuntimeError(f"{fixture_name}: P_inst<P_target did not contract the box")
        row = baseline.parse_thermo(stage / "thermo.out")[0]["rows"][0]
        initial_mean_pressure = sum(row[3:6]) / 3.0
        if direction == "expand" and initial_mean_pressure <= 0:
            raise RuntimeError(f"{fixture_name}: expected compressive P_inst>P_target, observed {initial_mean_pressure} GPa")
        if direction == "shrink" and initial_mean_pressure >= 0:
            raise RuntimeError(f"{fixture_name}: expected tensile P_inst<P_target, observed {initial_mean_pressure} GPa")
        record["comparison_to_gpumd"] = runner.compare(ref, stage,
            f"pressure-direction/{fixture_name}", ("thermo.out", "trajectory.xyz", "restart.xyz"))
        record["first_reported_mean_pressure_GPa"] = initial_mean_pressure
        record["first_step_direction"] = direction
        record["initial_lattice"] = start_lattice
        record["first_output_lattice"] = end_lattice
        runner.save()

    negative_model = (input_root / "ber216" / "model.xyz").read_text()
    negative_pressure_run = (input_root / "ber-negative-pressure-20-target-minus1" / "run.in").read_text()
    negative_ref, _ = runner.execute("reference-negative-pressure-minus1", "reference", negative_model,
                                     negative_pressure_run, devices=devices[:1], timeout=args.timeout)
    neg_stage, neg_record = runner.execute("negative-pressure-minus1", "candidate", negative_model,
                                           negative_pressure_run, devices=devices[:1], timeout=args.timeout)
    runner.ensure_success_outputs(neg_stage, ("thermo.out", "trajectory.xyz"))
    neg_record["comparison_to_gpumd"] = runner.compare(negative_ref, neg_stage,
        "negative-pressure-minus1", ("thermo.out", "trajectory.xyz"))
    neg_record["negative_target_pressure_accepted"] = True
    fixed_run = (input_root / "ber-fixed-box-20-B2001" / "run.in").read_text()
    fixed_ref, _ = runner.execute("reference-fixed-box-B2001", "reference", negative_model,
                                  fixed_run, devices=devices[:1], timeout=args.timeout)
    fixed_stage, fixed_record = runner.execute("fixed-box-B2001", "candidate", negative_model,
                                               fixed_run, devices=devices[:1], timeout=args.timeout)
    runner.ensure_success_outputs(fixed_stage, ("thermo.out", "trajectory.xyz"))
    fixed_record["fixed_box_check"] = fixed_box_check(fixed_stage, negative_model)
    fixed_record["comparison_to_gpumd"] = runner.compare(fixed_ref, fixed_stage, "fixed-box-B2001",
        ("thermo.out", "trajectory.xyz"))
    runner.save()

    # Relaunch from the produced post-scale restart snapshot; only new-job viability is promised.
    resume_run = (input_root / "ber-restart-resume-3" / "run.in").read_text()
    restart_stage = runner.root / "route-ber216-HostStaged-p4"
    if not (restart_stage / "restart.xyz").is_file():
        raise RuntimeError("registered route source for restart was not generated")
    resume_stage, resume_record = runner.execute("restart-resume-p4", "candidate",
                                                  (restart_stage / "restart.xyz").read_text(), resume_run,
                                                  ranks=4, backend="HostStaged", devices=devices[:4],
                                                  timeout=args.timeout)
    runner.ensure_success_outputs(resume_stage, ("thermo.out", "trajectory.xyz", "restart.xyz"))
    resume_record["restart_new_job"] = "PASS"
    runner.save()

    # Fail-closed parser/geometry/scaling matrix: no successful trajectory or restart on rejection.
    errors = [
        ("invalid-t-nan", "ber-invalid-t-nan", "ber216", 1,
         r"initial temperature should be a finite real number"),
        ("invalid-b-zero", "ber-invalid-b-zero", "ber216", 1,
         r"bulk modulus should be > 0"),
        ("invalid-tau-zero", "ber-invalid-tau-zero", "ber216", 1,
         r"pressure coupling time should be >= 1"),
        ("invalid-token-12", "ber-invalid-token-12", "ber216", 1,
         r"npt_ber: v1 supports isotropic 8-token syntax only"),
        ("invalid-token-18", "ber-invalid-token-18", "ber216", 1,
         r"npt_ber: v1 supports isotropic 8-token syntax only"),
        ("unsupported-nonperiodic", "ber-route-20-target5", "negative-nonperiodic", 1,
         r"npt_ber initial box validation failed: isotropic box scaling requires periodicity in all directions"),
        ("unsupported-triclinic", "ber-route-20-target5", "negative-triclinic", 1,
         r"npt_ber initial box validation failed: isotropic box scaling requires an orthogonal box"),
        ("nonpositive-scale-p1", "ber-unsafe-scale-1", "ber216", 1,
         r"invalid npt_ber box factor step=\d+ P_GPa=10000(?:\.0+)? .*factor=-"),
        ("nonpositive-scale-p4", "ber-unsafe-scale-1", "ber216", 4,
         r"invalid npt_ber box factor step=\d+ P_GPa=10000(?:\.0+)? .*factor=-"),
        ("zero-ke-p1", "ber-zero-ke-1", "zero-ke", 1,
         r"NPT Berendsen instantaneous temperature must be finite and positive"),
        ("zero-ke-p4", "ber-zero-ke-1", "zero-ke", 4,
         r"NPT Berendsen instantaneous temperature must be finite and positive"),
    ]
    input_run_map = {
        "ber-invalid-t-nan": (input_root / "ber-invalid-t-nan" / "run.in").read_text(),
        "ber-invalid-b-zero": (input_root / "ber-invalid-b-zero" / "run.in").read_text(),
        "ber-invalid-tau-zero": (input_root / "ber-invalid-tau-zero" / "run.in").read_text(),
        "ber-invalid-token-12": (input_root / "ber-invalid-token-12" / "run.in").read_text(),
        "ber-invalid-token-18": (input_root / "ber-invalid-token-18" / "run.in").read_text(),
        "ber-route-20-target5": route_run,
        "ber-unsafe-scale-1": (input_root / "ber-unsafe-scale-1" / "run.in").read_text(),
        "ber-zero-ke-1": (input_root / "ber-zero-ke-1" / "run.in").read_text(),
    }
    for label, run_key, model_key, ranks, expected_diagnostic in errors:
        model = (input_root / ("ber216" if model_key == "ber216" else model_key) / "model.xyz").read_text()
        stage, record = runner.execute(f"error-{label}", "candidate", model,
                                       input_run_map[run_key], ranks=ranks,
                                       backend="HostStaged", devices=devices[:ranks],
                                       timeout=120, allow_failure=True)
        record["expected_error"] = True
        record["expected_diagnostic_regex"] = expected_diagnostic
        final_artifacts = [name for name in ("trajectory.xyz", "restart.xyz") if (stage / name).exists()]
        if final_artifacts:
            raise RuntimeError(f"{label}: rejected case left success artifacts {final_artifacts}")
        logs = (stage / "execution.stdout").read_text(errors="replace") + \
               (stage / "execution.stderr").read_text(errors="replace")
        record["error_text"] = logs[-2000:]
        match = re.search(expected_diagnostic, logs, re.IGNORECASE | re.MULTILINE)
        record["expected_diagnostic_matched"] = bool(match)
        if not match:
            raise RuntimeError(f"{label}: expected diagnostic /{expected_diagnostic}/ absent; "
                               f"this may be an infrastructure failure, evidence {stage}; "
                               f"log tail={logs[-2000:]!r}")
        if re.search(r"out of memory|CUDA_ERROR_OUT_OF_MEMORY|PMIX ERROR|PMIx_Error|ORTE_ERROR", logs, re.IGNORECASE):
            raise RuntimeError(f"{label}: infrastructure error appeared despite diagnostic match; evidence {stage}")
        thermo_rows = []
        thermo_path = stage / "thermo.out"
        if thermo_path.exists():
            try:
                thermo_rows = [row for block in baseline.parse_thermo(thermo_path) for row in block["rows"]]
            except Exception as error:
                raise RuntimeError(f"{label}: thermo output is not parseable after rejection: {error}; evidence {stage}")
        record["numeric_thermo_rows_after_rejection"] = len(thermo_rows)
        if thermo_rows:
            raise RuntimeError(f"{label}: rejected run left {len(thermo_rows)} numeric thermo rows; evidence {stage}")
        record["success_artifacts_after_rejection"] = final_artifacts
        runner.save()

    after = reference_status_snapshot()
    if after != freeze["reference_status_before"]:
        raise RuntimeError("one or more read-only reference states changed during B2 run")
    runner.records.append({"label": "reference-read-only-audit", "status": "PASS",
                           "before": freeze["reference_status_before"], "after": after})
    runner.save()
    print(f"PASS B2 NPT Ber matrix; evidence {runner.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
