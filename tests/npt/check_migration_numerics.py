#!/usr/bin/env python3
"""Prepare, run, and compare a non-formal SCR migration prefix diagnostic.

This diagnostic is deliberately limited to legacy pilot seeds 101 and 102.
It never reads the independent-confirmation seed range and never writes into
the formal confirmation tree.  A 200-step run is compared with the first 200
rows of the corresponding archived candidate chain.  Only the single `run N`
budget line may differ between archived and diagnostic inputs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import capture_environment_proof
import generate_inputs
import run_npt_scr

DIAGNOSTIC_SCHEMA = "dmgmd.npt-scr-migration-prefix-diagnostic.v1"
NPT_B3_ROOT_PART = "npt-b3"
PREFIX_ROWS = 200
SEEDS = (101, 102)
POTENTIAL_SHA = "add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623"
EXPECTED_CANDIDATE_SHA = "c3c9cd0b81a3f18ebbd8f4379dfbd3dbf929785eb5aa883fa4730b025d70bcdf"

# These are the completed legacy pilot result bundles selected for the
# migration diagnostic.  Their trajectories are old B3 pilot evidence only.
OLD_RESULT_RELATIVE: dict[tuple[str, int, int, str], str] = {}
for _seed in SEEDS:
    OLD_RESULT_RELATIVE[("base", _seed, 1, "HostStaged")] = (
        "base-candidate-p1-host-80k-gpu5-v1/results.json")
    OLD_RESULT_RELATIVE[("half", _seed, 1, "HostStaged")] = (
        f"half-candidate-p1-gpu{'5' if _seed == 101 else '6'}-160k/results.json")
    OLD_RESULT_RELATIVE[("base", _seed, 2, "HostStaged")] = (
        "base-candidate-p2-host-gpu4-7-80k-s101-104-v1/results.json")
    OLD_RESULT_RELATIVE[("base", _seed, 4, "HostStaged")] = (
        "base-candidate-p4-host-gpu0-3-80k-v1/results.json")
    OLD_RESULT_RELATIVE[("base", _seed, 2, "CudaAware")] = (
        f"base-candidate-p2-cudaaware-gpu{'0-3' if _seed == 101 else '5-6'}-seed{_seed}/results.json")
    OLD_RESULT_RELATIVE[("base", _seed, 4, "CudaAware")] = (
        f"base-candidate-p4-cudaaware-gpu0-3-seed{_seed}/results.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _resolve_archived_path(raw: str, root: Path) -> Path:
    """Resolve an archived /tmp absolute path under a relocated npt-b3 root."""
    path = Path(raw)
    if path.is_file():
        return path.resolve()
    parts = path.parts
    try:
        index = parts.index(NPT_B3_ROOT_PART)
    except ValueError:
        candidate = root / path.name
    else:
        candidate = root.joinpath(*parts[index + 1:])
    if not candidate.is_file():
        raise FileNotFoundError(f"cannot resolve archived path {raw!r} under {root}")
    return candidate.resolve()


def _record_for(result_path: Path, seed: int, ranks: int, backend: str) -> dict[str, Any]:
    payload = _json(result_path)
    matches = [record for record in payload.get("records", [])
               if record.get("engine") == "candidate" and
               record.get("seed") == seed and record.get("ranks") == ranks and
               record.get("backend") == backend and record.get("status") == "PASS"]
    if len(matches) != 1:
        raise ValueError(f"{result_path}: expected one PASS candidate record for "
                         f"seed={seed}, P{ranks}/{backend}; found {len(matches)}")
    return matches[0]


def _read_run_controls(path: Path) -> dict[str, Any]:
    values: dict[str, list[str]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        fields = raw_line.split()
        if fields:
            values.setdefault(fields[0], []).append(" ".join(fields[1:]))
    if any(len(values.get(key, [])) != 1 for key in ("time_step", "ensemble", "run")):
        raise ValueError(f"run.in must have exactly one time_step, ensemble, and run: {path}")
    try:
        dt_fs = float(values["time_step"][0])
        ensemble = values["ensemble"][0].split()
        steps = int(values["run"][0])
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid run.in controls: {path}") from error
    if len(ensemble) != 7 or ensemble[0] != "npt_scr":
        raise ValueError(f"expected one seven-argument npt_scr ensemble in {path}")
    try:
        t1, t2 = float(ensemble[1]), float(ensemble[2])
        tdamp, pressure, bulk, tau = int(ensemble[3]), float(ensemble[4]), \
            float(ensemble[5]), int(ensemble[6])
    except ValueError as error:
        raise ValueError(f"invalid npt_scr controls in {path}") from error
    if not all(math.isfinite(value) for value in (dt_fs, t1, t2, pressure, bulk)):
        raise ValueError(f"non-finite npt_scr controls in {path}")
    if (t1, t2, pressure, bulk) != (300.0, 300.0, 0.0, 425.0):
        raise ValueError(f"unexpected SCR parameters in {path}")
    if steps <= 0:
        raise ValueError(f"run budget must be positive in {path}")
    profile = "base" if (dt_fs, tdamp, tau) == (0.25, 40, 200) else \
        "half" if (dt_fs, tdamp, tau) == (0.125, 80, 400) else None
    if profile is None:
        raise ValueError(f"unregistered base/half SCR timestep controls in {path}")
    return {"profile": profile, "dt_fs": dt_fs, "tdamp_steps": tdamp,
            "tau_p_steps": tau, "steps": steps, "normalized_text": _normalized_run_text(path)}


def _normalized_run_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^run [0-9]+\r?$", text))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one standalone run budget line: {path}")
    return re.sub(r"(?m)^run [0-9]+(?=\r?$)", "run <BUDGET>", text, count=1)


def numeric_thermo_rows(path: Path) -> list[list[float]]:
    rows: list[list[float]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 18:
            if fields[0][0].isdigit() or fields[0][0] in "+-.":
                raise ValueError(f"{path}:{line_number}: numeric-looking thermo row has {len(fields)} columns")
            continue
        try:
            row = [float(value) for value in fields]
        except ValueError:
            if fields[0][0].isdigit() or fields[0][0] in "+-.":
                raise ValueError(f"{path}:{line_number}: malformed numeric thermo row")
            continue
        if not all(math.isfinite(value) for value in row):
            raise ValueError(f"{path}:{line_number}: non-finite thermo value")
        rows.append(row)
    return rows


def _volume(row: list[float]) -> float:
    a, b, c, d, e, f, g, h, i = row[9:18]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def _pressure(row: list[float]) -> float:
    return math.fsum(row[3:6]) / 3.0


def _tolerance_categories() -> list[str]:
    return ["temperature", "energy", "energy"] + ["thermo_stress"] * 6 + ["lattice"] * 9


def _compare_rows(reference: list[list[float]], actual: list[list[float]],
                  tolerances: dict[str, Any], prefix_rows: int) -> dict[str, Any]:
    if len(reference) < prefix_rows or len(actual) < prefix_rows:
        raise ValueError(f"both thermo outputs need at least {prefix_rows} numeric rows")
    by_column: list[dict[str, Any]] = []
    categories = _tolerance_categories()
    for column, category in enumerate(categories):
        tol = tolerances[category]
        abs_diffs = []
        ratios = []
        exceeded = 0
        first = None
        for row_index in range(prefix_rows):
            left, right = reference[row_index][column], actual[row_index][column]
            absolute = abs(right - left)
            allowed = float(tol["atol"]) + float(tol["rtol"]) * abs(left)
            ratio = absolute / allowed if allowed > 0 else (0.0 if absolute == 0 else math.inf)
            abs_diffs.append(absolute)
            ratios.append(ratio)
            if absolute > allowed:
                exceeded += 1
                if first is None:
                    first = {"row": row_index + 1, "reference": left, "actual": right,
                             "abs_diff": absolute, "allowed": allowed}
        by_column.append({"column": column, "category": category,
                          "comparisons": prefix_rows, "exceeded_tolerance": exceeded,
                          "max_abs_diff": max(abs_diffs), "max_allowed_ratio": max(ratios),
                          "first_exceedance": first})
    derived = {}
    for name, fn in (("pressure_GPa", _pressure), ("volume_A3", _volume)):
        differences = [abs(fn(actual[index]) - fn(reference[index])) for index in range(prefix_rows)]
        rel = [differences[index] / abs(fn(reference[index]))
               if abs(fn(reference[index])) > 1e-12 else None for index in range(prefix_rows)]
        valid_rel = [value for value in rel if value is not None]
        derived[name] = {"max_abs_diff": max(differences),
                         "max_rel_diff_when_reference_nonzero": max(valid_rel) if valid_rel else None,
                         "comparison_count": prefix_rows,
                         "gate": "descriptive only; no new tolerance"}
    return {"prefix_rows": prefix_rows, "columns": by_column, "derived": derived,
            "within_existing_manifest_tolerances": all(x["exceeded_tolerance"] == 0 for x in by_column)}


def _old_result_path(old_root: Path, profile: str, seed: int, ranks: int, backend: str) -> Path:
    relative = OLD_RESULT_RELATIVE[(profile, seed, ranks, backend)]
    path = old_root / relative
    if not path.is_file():
        raise FileNotFoundError(f"required archived pilot record missing: {path}")
    return path


def prepare(old_root: Path, work_root: Path) -> dict[str, Any]:
    old_root, work_root = old_root.resolve(), work_root.resolve()
    if work_root.exists():
        raise FileExistsError(f"refusing to overwrite migration diagnostic root: {work_root}")
    baseline_manifest = _json(ROOT / "tests/baseline/manifest.json")
    potential = ROOT / "tests/baseline/inputs/potentials/nep_C.txt"
    if sha256(potential) != POTENTIAL_SHA:
        raise ValueError("locked NEP-C potential hash differs from expected B3 identity")
    tasks: list[dict[str, Any]] = []
    for profile in ("base", "half"):
        for seed in SEEDS:
            ranks_backends = [(1, "HostStaged")] if profile == "half" else [
                (1, "HostStaged"), (2, "HostStaged"), (2, "CudaAware"),
                (4, "HostStaged"), (4, "CudaAware")]
            for ranks, backend in ranks_backends:
                old_result = _old_result_path(old_root, profile, seed, ranks, backend)
                old_record = _record_for(old_result, seed, ranks, backend)
                old_controls, _old_inputs = _verify_record(old_record, old_root, profile, ranks, backend)
                anchor_result = _old_result_path(old_root, profile, seed, 1, "HostStaged")
                anchor_record = _record_for(anchor_result, seed, 1, "HostStaged")
                anchor_controls, _anchor_inputs = _verify_record(
                    anchor_record, old_root, profile, 1, "HostStaged")
                for name in ("model.xyz", "nep.txt"):
                    if old_record["input_sha256"][name] != anchor_record["input_sha256"][name]:
                        raise ValueError(f"legacy {name} differs between P1 anchor and route: {old_result}")
                if old_controls["normalized_text"] != anchor_controls["normalized_text"]:
                    raise ValueError(f"legacy run.in controls differ from P1 anchor: {old_result}")
                if old_controls["steps"] < PREFIX_ROWS:
                    raise ValueError(f"old run too short for prefix comparison: {old_result}")
                repeats = 2 if ranks == 1 else 1
                for repeat in range(1, repeats + 1):
                    task_id = f"{profile}-P{ranks}-{backend}-seed{seed}-repeat{repeat}"
                    tasks.append({"task_id": task_id, "profile": profile, "seed": seed,
                                  "ranks": ranks, "backend": backend, "repeat": repeat,
                                  "old_result_relative": old_result.relative_to(old_root).as_posix(),
                                  "old_label": old_record.get("label"),
                                  "old_input_sha256": old_record["input_sha256"],
                                  "old_thermo_sha256": old_record["thermo_sha256"],
                                  "old_executable_sha256": old_record["executable_sha256"],
                                  "old_controls": old_controls,
                                  "new_input_sha256": None,
                                  "new_run_budget_only_change": True,
                                  "prefix_rows": PREFIX_ROWS})
    # Both repeated P1 jobs and every MPI case stage the exact same model,
    # potential, and run controls; only the run budget is shortened to 200.
    work_root.mkdir(parents=True)
    input_root = work_root / "inputs"
    for profile in ("base", "half"):
        for seed in SEEDS:
            source_result = _old_result_path(old_root, profile, seed, 1, "HostStaged")
            record = _record_for(source_result, seed, 1, "HostStaged")
            _, source_paths = _verify_record(record, old_root, profile, 1, "HostStaged")
            source_run = source_paths["run.in"]
            original = source_run.read_text(encoding="utf-8")
            short = re.sub(r"(?m)^run [0-9]+(?=\r?$)", f"run {PREFIX_ROWS}", original, count=1)
            if short == original or len(re.findall(r"(?m)^run [0-9]+(?=\r?$)", original)) != 1:
                raise ValueError(f"could not shorten exactly one run budget line in {source_run}")
            source_controls, _ = _verify_record(record, old_root, profile, 1, "HostStaged")
            expected = generate_inputs.scr_run(PREFIX_ROWS, source_controls["dt_fs"],
                                               source_controls["tdamp_steps"],
                                               source_controls["tau_p_steps"])
            if short != expected:
                raise ValueError(f"shortened run.in differs outside run budget line: {source_run}")
            target = input_root / profile / str(PREFIX_ROWS) / f"seed-{seed}"
            target.mkdir(parents=True)
            shutil.copyfile(source_paths["model.xyz"], target / "model.xyz")
            shutil.copyfile(potential, target / "nep.txt")
            (target / "run.in").write_text(short, encoding="utf-8")
    inventory = {path.relative_to(input_root).as_posix(): sha256(path)
                 for path in sorted(input_root.rglob("*")) if path.is_file()}
    for task in tasks:
        input_dir = input_root / task["profile"] / str(PREFIX_ROWS) / f"seed-{task['seed']}"
        task["new_input_sha256"] = {name: sha256(input_dir / name)
                                    for name in ("model.xyz", "run.in", "nep.txt")}
        old_run = _resolve_archived_path(
            _record_for(old_root / task["old_result_relative"], task["seed"],
                        task["ranks"], task["backend"])["input_paths"]["run.in"], old_root)
        if _normalized_run_text(old_run) != task["old_controls"]["normalized_text"]:
            raise ValueError("old run.in normalization changed during preparation")
    freeze = {"schema": "dmgmd.npt-scr-migration-diagnostic-freeze.v1",
              "status": "DIAGNOSTIC_ONLY_NOT_FORMAL_SAMPLING",
              "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "candidate_sha256_expected": EXPECTED_CANDIDATE_SHA,
              "potential_sha256": POTENTIAL_SHA,
              "npt_manifest_sha256": sha256(HERE / "manifest.json"),
              "baseline_manifest_sha256": sha256(ROOT / "tests/baseline/manifest.json"),
              "baseline_tolerances": baseline_manifest["tolerances"],
              "input_root": str(input_root), "input_inventory_sha256": inventory,
              "prefix_rows": PREFIX_ROWS,
              "seed_scope": list(SEEDS),
              "registered_confirmation_data_read": False,
              "candidate_route_matrix": ["P1/HostStaged base+half repeated",
                                         "P2/P4 HostStaged/CudaAware base"],
              "tasks": tasks}
    (work_root / "diagnostic-freeze.json").write_text(
        json.dumps(freeze, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return {"work_root": str(work_root), "freeze": str(work_root / "diagnostic-freeze.json"),
            "task_count": len(tasks), "input_count": len(inventory),
            "prefix_rows": PREFIX_ROWS, "status": freeze["status"]}


def _verify_record(record: dict[str, Any], root: Path, expected_profile: str,
                   ranks: int, backend: str) -> tuple[dict[str, Any], dict[str, Path]]:
    if record.get("status") != "PASS" or record.get("engine") != "candidate":
        raise ValueError("only successful candidate records are valid migration inputs")
    if record.get("ranks") != ranks or record.get("backend") != backend:
        raise ValueError("record route differs from expected route")
    if record.get("ensemble_seed") != str(record.get("seed")):
        raise ValueError("candidate record lacks explicit matching DMGMD_ENSEMBLE_SEED")
    inputs = record.get("input_paths")
    hashes = record.get("input_sha256")
    if not isinstance(inputs, dict) or not isinstance(hashes, dict) or \
            set(inputs) != {"model.xyz", "run.in", "nep.txt"}:
        raise ValueError("candidate record lacks exact model/run/potential paths and hashes")
    resolved = {name: _resolve_archived_path(inputs[name], root) for name in inputs}
    for name, path in resolved.items():
        if sha256(path) != hashes.get(name):
            raise ValueError(f"recorded {name} hash does not match staged file: {path}")
    if hashes["nep.txt"] != POTENTIAL_SHA:
        raise ValueError("record potential is not the locked NEP-C fixture")
    controls = _read_run_controls(resolved["run.in"])
    if controls["profile"] != expected_profile:
        raise ValueError(f"record run.in profile is {controls['profile']}, expected {expected_profile}")
    if record.get("steps") != controls["steps"]:
        raise ValueError("record step count differs from run.in")
    thermo = _resolve_archived_path(record.get("thermo_path", ""), root)
    if sha256(thermo) != record.get("thermo_sha256"):
        raise ValueError(f"thermo hash mismatch: {thermo}")
    if record.get("executable_sha256") != EXPECTED_CANDIDATE_SHA:
        raise ValueError("candidate executable hash differs from preserved formal candidate")
    route_text = "\n".join(record.get("route_lines", []))
    if "mode=m1" not in route_text or "reason=npt-m1-only" not in route_text:
        raise ValueError("record lacks explicit M1 NPT route evidence")
    return controls, resolved | {"thermo.out": thermo}


def run_diagnostic(work_root: Path, candidate: Path, reference: Path,
                   proof_path: Path, devices: str, timeout: int) -> dict[str, Any]:
    work_root, candidate, reference, proof_path = map(Path.resolve,
                                                        (work_root, candidate, reference, proof_path))
    freeze_path = work_root / "diagnostic-freeze.json"
    freeze = _json(freeze_path)
    if freeze.get("status") != "DIAGNOSTIC_ONLY_NOT_FORMAL_SAMPLING":
        raise ValueError("diagnostic freeze status is invalid")
    if sha256(candidate) != freeze["candidate_sha256_expected"]:
        raise ValueError("candidate executable hash differs from diagnostic registration")
    if sha256(candidate) != EXPECTED_CANDIDATE_SHA:
        raise ValueError("candidate executable is not the preserved B3 binary")
    proof = _json(proof_path)
    device_ids = [item.strip() for item in devices.split(",") if item.strip()]
    if len(device_ids) != 4 or len(set(device_ids)) != 4:
        raise ValueError("--devices must name four unique physical GPU IDs")
    if proof.get("schema") != "dmgmd.npt-b3-environment-proof.v1" or \
            proof.get("status") != "PASS" or proof.get("candidate_sha256") != sha256(candidate):
        raise ValueError("new-server environment proof does not cover the preserved candidate")
    if proof.get("gate_ranks") != 4 or proof.get("probed_backend") != "CudaAware" or \
            set(proof.get("physical_devices", [])) != set(device_ids):
        raise ValueError("environment proof does not cover the selected four-GPU group")
    if proof.get("environment_fingerprint") != capture_environment_proof.environment_fingerprint():
        raise ValueError("current CUDA/MPI/UCX environment differs from approved proof")
    inventory = capture_environment_proof.gpu_inventory()
    if proof.get("gpu_identity") != {device: inventory.get(device) for device in proof["physical_devices"]}:
        raise ValueError("GPU UUID/driver inventory differs from approved proof")
    expected_inventory = freeze["input_inventory_sha256"]
    input_root = Path(freeze["input_root"])
    actual_inventory = {path.relative_to(input_root).as_posix(): sha256(path)
                        for path in sorted(input_root.rglob("*")) if path.is_file()}
    if actual_inventory != expected_inventory:
        raise ValueError("diagnostic input tree changed after prepare")
    run_root = work_root / "runs"
    args = argparse.Namespace(work_root=run_root, candidate=candidate, reference=reference,
                              devices=devices, schedule=None, timeout=timeout,
                              freeze=freeze_path, reference_launch_registry=None)
    manifest = _json(HERE / "manifest.json")
    runner = run_npt_scr.ScrRunner(args, manifest,
                                   {"generated_input_root": str(input_root),
                                    "generated_inputs_sha256": expected_inventory,
                                    "manifest_sha256": freeze["npt_manifest_sha256"],
                                    "candidate_sha256": sha256(candidate),
                                    "reference_sha256": sha256(reference)})
    runner.mpiexec = Path(proof["mpiexec_path"]).resolve()
    runner.payload["migration_environment_proof"] = {
        "path": str(proof_path), "sha256": sha256(proof_path),
        "environment_fingerprint": proof["environment_fingerprint"],
        "physical_devices": proof["physical_devices"], "gpu_identity": proof["gpu_identity"]}
    runner.payload["runtime_linkage"] = _runtime_linkage(candidate)
    runner.save()
    for task in freeze["tasks"]:
        controls = task["old_controls"]
        runner.a.dt_fs = controls["dt_fs"]
        runner.a.tdamp_steps = controls["tdamp_steps"]
        runner.a.tau_p_steps = controls["tau_p_steps"]
        record = runner.run_one("candidate", task["profile"], PREFIX_ROWS, task["seed"],
                                PREFIX_ROWS, task["ranks"], task["backend"], task["task_id"],
                                ensemble_seed="registered")
        record.update({"profile": task["profile"], "tdamp_steps": controls["tdamp_steps"],
                       "tau_p_steps": controls["tau_p_steps"], "diagnostic_only": True,
                       "task_id": task["task_id"], "environment_proof_sha256": sha256(proof_path)})
        runner.save()
    return {"results": str(runner.results), "record_count": len(runner.records),
            "environment_proof_sha256": sha256(proof_path), "status": "RUNS_COMPLETE"}


def _runtime_linkage(candidate: Path) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for command, key in ((["ldd", "--version"], "ldd_version"), (["ldd", str(candidate)], "ldd_candidate")):
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
            results[key] = {"returncode": proc.returncode,
                            "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
        except (OSError, subprocess.TimeoutExpired) as error:
            results[key] = {"error": f"{type(error).__name__}: {error}"}
    ldd_text = results.get("ldd_candidate", {}).get("stdout", "")
    match = re.search(r"libstdc\+\+\.so\.6\s+=>\s+(\S+)", ldd_text)
    if match:
        library = Path(match.group(1)).resolve()
        if library.is_file():
            try:
                strings = subprocess.run(["strings", str(library)], capture_output=True,
                                         text=True, check=False, timeout=30)
                versions = sorted(set(re.findall(r"^GLIBCXX_[0-9.]+$", strings.stdout, re.M)),
                                  key=lambda value: tuple(int(x) for x in value.split("_")[1].split(".")))
                results["libstdcxx"] = {"path": str(library), "sha256": sha256(library),
                                        "strings_returncode": strings.returncode,
                                        "glibcxx_symbol_versions": versions}
            except (OSError, subprocess.TimeoutExpired) as error:
                results["libstdcxx"] = {"path": str(library),
                                        "error": f"{type(error).__name__}: {error}"}
        else:
            results["libstdcxx"] = {"path": str(library), "error": "resolved library path missing"}
    else:
        results["libstdcxx"] = {"error": "ldd output did not identify libstdc++.so.6"}
    return results


def analyze(work_root: Path, old_root: Path, output: Path) -> dict[str, Any]:
    work_root, old_root, output = work_root.resolve(), old_root.resolve(), output.resolve()
    freeze = _json(work_root / "diagnostic-freeze.json")
    results_path = work_root / "runs" / "results.json"
    results = _json(results_path)
    if len(results.get("records", [])) != len(freeze["tasks"]):
        raise ValueError("new diagnostic run bundle does not contain every registered task")
    records = {record.get("task_id"): record for record in results["records"]}
    if len(records) != len(freeze["tasks"]) or set(records) != {x["task_id"] for x in freeze["tasks"]}:
        raise ValueError("new diagnostic task IDs are missing or duplicated")
    tolerance_path = ROOT / "tests/baseline/manifest.json"
    if sha256(tolerance_path) != freeze["baseline_manifest_sha256"]:
        raise ValueError("baseline tolerance manifest changed since diagnostic prepare")
    report: dict[str, Any] = {
        "schema": DIAGNOSTIC_SCHEMA,
        "status": "DIAGNOSTIC_ONLY_NO_FORMAL_ACCEPTANCE_DECISION",
        "prefix_rows": PREFIX_ROWS, "seeds": list(SEEDS),
        "confirmation_seed_range_read": False,
        "npt_manifest_sha256": freeze["npt_manifest_sha256"],
        "baseline_manifest_sha256": freeze["baseline_manifest_sha256"],
        "tolerance_rule": "abs(actual-reference) <= atol + rtol*abs(reference)",
        "run_in_rule": "all run.in bytes identical after replacing the single run N budget line",
        "comparisons": [], "new_environment_runtime_linkage": results.get("runtime_linkage"),
        "environment_proof": results.get("migration_environment_proof"),
        "interpretation": "Finite 200-row migration diagnostic only; it does not enter the 160-chain confirmation or establish long-run equivalence.",
    }
    task_by_id = {task["task_id"]: task for task in freeze["tasks"]}
    new_rows: dict[str, list[list[float]]] = {}
    old_rows_cache: dict[str, list[list[float]]] = {}
    for task_id, task in task_by_id.items():
        new_record = records[task_id]
        if new_record.get("status") != "PASS" or new_record.get("steps") != PREFIX_ROWS:
            raise ValueError(f"new task did not complete exactly {PREFIX_ROWS} steps: {task_id}")
        new_controls, new_paths = _verify_record(new_record, work_root, task["profile"],
                                                  task["ranks"], task["backend"])
        if new_controls["steps"] != PREFIX_ROWS:
            raise ValueError(f"new run.in budget is not the diagnostic prefix: {task_id}")
        if new_record.get("environment_proof_sha256") != results.get("migration_environment_proof", {}).get("sha256"):
            raise ValueError(f"new task proof identity mismatch: {task_id}")
        old_result = old_root / task["old_result_relative"]
        old_record = _record_for(old_result, task["seed"], task["ranks"], task["backend"])
        old_controls, old_paths = _verify_record(old_record, old_root, task["profile"],
                                                  task["ranks"], task["backend"])
        if old_controls["normalized_text"] != new_controls["normalized_text"]:
            raise ValueError(f"run.in differs beyond the single budget line: {task_id}")
        for name in ("model.xyz", "nep.txt"):
            if old_record["input_sha256"][name] != new_record["input_sha256"][name]:
                raise ValueError(f"old/new {name} bytes differ: {task_id}")
        if old_record.get("executable_sha256") != new_record.get("executable_sha256"):
            raise ValueError(f"old/new candidate executable SHA differs: {task_id}")
        key = f"{task['profile']}|P{task['ranks']}|{task['backend']}|seed{task['seed']}"
        if key not in old_rows_cache:
            old_rows_cache[key] = numeric_thermo_rows(old_paths["thermo.out"])
        new_rows[task_id] = numeric_thermo_rows(new_paths["thermo.out"])
        result = _compare_rows(old_rows_cache[key], new_rows[task_id],
                               freeze["baseline_tolerances"], PREFIX_ROWS)
        report["comparisons"].append({"task_id": task_id, "profile": task["profile"],
                                      "seed": task["seed"], "ranks": task["ranks"],
                                      "backend": task["backend"], "old_thermo_sha256": old_record["thermo_sha256"],
                                      "new_thermo_sha256": new_record["thermo_sha256"],
                                      "old_run_sha256": old_record["input_sha256"]["run.in"],
                                      "new_run_sha256": new_record["input_sha256"]["run.in"],
                                      "model_sha256": new_record["input_sha256"]["model.xyz"],
                                      "potential_sha256": new_record["input_sha256"]["nep.txt"],
                                      "candidate_executable_sha256": new_record["executable_sha256"],
                                      "route_lines": new_record.get("route_lines", []),
                                      "old_steps": old_controls["steps"], "new_steps": PREFIX_ROWS,
                                      "result": result})
    # Same-seed P1 reruns must remain byte-identical on the new host.  Also
    # compare each newly run MPI route with the first new P1 route under the
    # same immutable baseline tolerances.
    for profile in ("base", "half"):
        for seed in SEEDS:
            p1_ids = [f"{profile}-P1-HostStaged-seed{seed}-repeat{i}" for i in (1, 2)]
            hashes = [records[key]["thermo_sha256"] for key in p1_ids]
            input_hash_maps = [records[key]["input_sha256"] for key in p1_ids]
            report.setdefault("new_host_repeat_checks", []).append({
                "profile": profile, "seed": seed, "thermo_sha256": hashes,
                "input_hash_maps": input_hash_maps,
                "inputs_identical": input_hash_maps[0] == input_hash_maps[1],
                "byte_identical": len(set(hashes)) == 1})
            anchor = new_rows[p1_ids[0]]
            for task_id, task in task_by_id.items():
                if task["profile"] != profile or task["seed"] != seed or task["ranks"] == 1:
                    continue
                same_host = _compare_rows(anchor, new_rows[task_id],
                                          freeze["baseline_tolerances"], PREFIX_ROWS)
                report.setdefault("new_host_route_checks", []).append({
                    "task_id": task_id, "anchor_task_id": p1_ids[0], "result": same_host})
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite diagnostic report: {output}")
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                      encoding="utf-8")
    return {"output": str(output), "comparisons": len(report["comparisons"]),
            "status": report["status"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    p_prepare = subparsers.add_parser("prepare", help="freeze old-pilot inputs for the diagnostic")
    p_prepare.add_argument("--old-root", type=Path, required=True,
                           help="relocated archived npt-b3 directory")
    p_prepare.add_argument("--work-root", type=Path, required=True,
                           help="new, previously nonexistent migration diagnostic directory")
    p_run = subparsers.add_parser("run", help="run only the prepared 200-step diagnostics")
    p_run.add_argument("--work-root", type=Path, required=True)
    p_run.add_argument("--candidate", type=Path, required=True)
    p_run.add_argument("--reference", type=Path, required=True,
                       help="preserved GPUMD path required by ScrRunner; this command does not launch it")
    p_run.add_argument("--environment-proof", type=Path, required=True)
    p_run.add_argument("--devices", default="4,5,6,7")
    p_run.add_argument("--timeout", type=int, default=600)
    p_analyze = subparsers.add_parser("analyze", help="compare completed new prefixes with old pilot prefixes")
    p_analyze.add_argument("--work-root", type=Path, required=True)
    p_analyze.add_argument("--old-root", type=Path, required=True)
    p_analyze.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.old_root, args.work_root)
    elif args.command == "run":
        result = run_diagnostic(args.work_root, args.candidate, args.reference,
                                args.environment_proof, args.devices, args.timeout)
    else:
        result = analyze(args.work_root, args.old_root, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
