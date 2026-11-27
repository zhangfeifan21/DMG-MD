#!/usr/bin/env python3
"""Record and verify the reviewed 60 ps or 110 ps water preparation endpoint.

Keep both JSON records together with execution.json and thermo.out in scr,
scr-extension, and scr-extension-2 when a second continuation exists.
A live selection also checks the last restart on disk. Verification takes the
actual frame SHA-256 so the same evidence can be reviewed in a publication bundle.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from statistics import mean, stdev
from tempfile import TemporaryDirectory

from analyze_preparation import SCHEDULE, Sample, check_stage, read_thermo
from prepare import FIXTURES, input_script, sha256, write_json


DIAGNOSTICS_FILENAME = "water-diagnostics.json"
SELECTION_FILENAME = "water-selection.json"
DIAGNOSTICS_SCHEMA = "dmgmd.water-preparation-diagnostics.v1"
SELECTION_SCHEMA = "dmgmd.water-preparation-selection.v1"
STAGE_NAMES = ("scr", "scr-extension")
SECOND_EXTENSION = "scr-extension-2"
WINDOW_PS = 10
EXTENSION_PS = 50
OBSERVABLES = (
    "potential_energy_eV_per_atom", "volume_A3", "temperature_K", "pressure_GPa",
)


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError(f"invalid {name} SHA-256")
    return value


def _decode_record(raw: bytes, path: Path) -> dict:
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return record


def _read_stage(directory: Path, stage: str) -> tuple[dict, dict, list[Sample], list[float]]:
    fixture = FIXTURES["water"]
    profile = "scr" if stage == "scr" else "scr-extension"
    execution_path = directory / "execution.json"
    execution_bytes = execution_path.read_bytes()
    execution = _decode_record(execution_bytes, execution_path)
    if execution.get("status") != "PASS" or execution.get("exit_code") != 0:
        raise ValueError(f"{stage} execution is incomplete or failed")

    expected_run = hashlib.sha256(input_script(fixture, profile, None).encode("ascii")).hexdigest()
    if (execution.get("run_in_sha256") != expected_run
            or execution.get("potential_sha256") != fixture["potential_sha256"]
            or "velocity_seed" not in execution
            or execution["velocity_seed"] is not None):
        raise ValueError(f"{stage} execution does not use the registered water protocol")
    _digest(execution.get("gpumd_binary_sha256"), f"{stage} GPUMD binary")
    snapshot = execution.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError(f"{stage} execution is missing its restart record")
    restart_sha = _digest(snapshot.get("sha256"), f"{stage} restart")

    thermo_path = directory / "thermo.out"
    thermo_bytes = thermo_path.read_bytes()
    thermo_sha = hashlib.sha256(thermo_bytes).hexdigest()
    if thermo_sha != execution.get("thermo_sha256"):
        raise ValueError(f"{stage} thermo differs from the execution record")
    schedule = SCHEDULE[profile]
    if len(schedule) != 1:
        raise ValueError(f"expected one thermo segment in {stage}")
    atoms, samples = read_thermo(thermo_path, schedule, fixture["time_step_fs"])
    if atoms != snapshot.get("atoms"):
        raise ValueError(f"{stage} thermo and restart atom counts disagree")
    if sha256(thermo_path) != thermo_sha:
        raise ValueError(f"{stage} thermo changed during review")

    # The shared parser validates the schedule and rows. GPUMD writes total PE
    # in the third column, which is divided by the recorded number of atoms.
    energies = [
        float(row.split()[2]) / atoms
        for row in thermo_bytes.decode("ascii").splitlines()[5:] if row.strip()
    ]
    sources = {
        "execution_sha256": hashlib.sha256(execution_bytes).hexdigest(),
        "thermo_sha256": thermo_sha,
        "restart_sha256": restart_sha,
    }
    return execution, sources, samples, energies


def _window(rows: list[tuple], start_ps: int, end_ps: int) -> dict:
    selected = [row for row in rows if start_ps < row[0] <= end_ps]
    if len(selected) < 2:
        raise ValueError(f"insufficient samples in ({start_ps}, {end_ps}] ps")
    metrics = {}
    for column, quantity in enumerate(OBSERVABLES, start=2):
        values = [row[column] for row in selected]
        metrics[quantity] = {
            "mean": mean(values),
            "sample_sd": stdev(values),
            "min": min(values),
            "max": max(values),
        }
    return {
        "start_ps_exclusive": start_ps,
        "end_ps_inclusive": end_ps,
        "first_sample_step": selected[0][1],
        "last_sample_step": selected[-1][1],
        "sample_count": len(selected),
        "metrics": metrics,
    }


def _mean_difference(later: dict, earlier: dict) -> dict:
    return {
        name: later["metrics"][name]["mean"] - earlier["metrics"][name]["mean"]
        for name in OBSERVABLES
    }


def build_diagnostics(directory: Path) -> dict:
    """Recompute descriptive windows and the existing preparation checks."""
    second = directory / SECOND_EXTENSION
    stages = (*STAGE_NAMES, SECOND_EXTENSION) if second.exists() or second.is_symlink() else STAGE_NAMES
    dt_fs = FIXTURES["water"]["time_step_fs"]
    dt_ps = dt_fs / 1000
    sources = {}
    registered_checks = {}
    rows = []
    prior_execution = None
    total_steps = 0
    for stage in stages:
        execution, stage_sources, samples, energies = _read_stage(directory / stage, stage)
        profile = "scr" if prior_execution is None else "scr-extension"
        if prior_execution is not None:
            if (execution.get("resumes_execution_sha256") != prior_sources["execution_sha256"]
                    or execution.get("model_in_sha256") != prior_sources["restart_sha256"]
                    or execution["gpumd_binary_sha256"] != prior_execution["gpumd_binary_sha256"]
                    or execution["snapshot"]["atoms"] != prior_execution["snapshot"]["atoms"]):
                raise ValueError(f"{stage} does not resume the original {prior_stage} execution and restart")
        latest_start_ps = int(total_steps * dt_ps)
        duration_ps = WINDOW_PS if profile == "scr" else EXTENSION_PS
        if samples[-1].step * dt_ps != duration_ps:
            raise ValueError(f"expected {duration_ps} ps of water preparation in {stage}")
        rows.extend(
            ((total_steps + sample.step) * dt_ps, total_steps + sample.step, energy,
             sample.volume_A3, sample.temperature_K, sample.pressure_GPa)
            for sample, energy in zip(samples, energies, strict=True)
        )
        total_steps += samples[-1].step
        sources[stage] = stage_sources
        registered_checks[stage] = check_stage(profile, samples)
        prior_execution, prior_stage, prior_sources = execution, stage, stage_sources

    final_time_ps = int(total_steps * dt_ps)
    windows = [
        _window(rows, start, start + WINDOW_PS)
        for start in range(0, final_time_ps, WINDOW_PS)
    ]
    halfway = (latest_start_ps + final_time_ps) // 2
    first_half = _window(rows, latest_start_ps, halfway)
    second_half = _window(rows, halfway, final_time_ps)
    return {
        "schema": DIAGNOSTICS_SCHEMA,
        "material": "water",
        "time_step_fs": dt_fs,
        "atom_count": prior_execution["snapshot"]["atoms"],
        "endpoint_step": total_steps,
        "endpoint_time_ps": final_time_ps,
        "sources": sources,
        "registered_preparation_checks": registered_checks,
        "consecutive_10_ps_windows": windows,
        "continuation_halves": {
            "first": first_half,
            "second": second_half,
        },
        "mean_differences": {
            "second_half_minus_first_half": _mean_difference(second_half, first_half),
            "last_window_minus_previous_window": _mean_difference(windows[-1], windows[-2]),
            "last_window_minus_original_scr": _mean_difference(windows[-1], windows[0]),
        },
    }


def _checked_diagnostics(path: Path) -> tuple[str, dict]:
    raw = path.read_bytes()
    record = _decode_record(raw, path)
    measured = build_diagnostics(path.parent)
    if record != measured:
        raise ValueError(f"saved water diagnostics differ from recomputation: {path}")
    return hashlib.sha256(raw).hexdigest(), measured


def _final_stage(diagnostics: dict) -> str:
    return SECOND_EXTENSION if SECOND_EXTENSION in diagnostics["sources"] else "scr-extension"


def _check_frame(diagnostics: dict, frame_sha256: str) -> None:
    stage = _final_stage(diagnostics)
    if frame_sha256 != diagnostics["sources"][stage]["restart_sha256"]:
        endpoint_ps = diagnostics["endpoint_time_ps"]
        raise ValueError(f"water frame SHA-256 differs from the registered {endpoint_ps} ps endpoint")


def _check_registered_criteria(diagnostics: dict) -> None:
    if any(record["status"] != "PASS"
           for record in diagnostics["registered_preparation_checks"].values()):
        raise ValueError("water cannot be selected because a registered preparation check failed")


def record_decision(directory: Path, decision: str, reason: str) -> dict:
    """Save an explicit judgement against the reviewed data and actual restart."""
    if decision not in ("select", "reject") or not reason.strip():
        raise ValueError("record an explicit select or reject decision with a reason")
    diagnostics_sha, diagnostics = _checked_diagnostics(directory / DIAGNOSTICS_FILENAME)
    frame_sha = sha256(directory / _final_stage(diagnostics) / "restart.xyz")
    _check_frame(diagnostics, frame_sha)
    if decision == "select":
        _check_registered_criteria(diagnostics)

    selection = {
        "schema": SELECTION_SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "reason": reason.strip(),
        "diagnostics_sha256": diagnostics_sha,
        "frame_sha256": frame_sha,
        "selected_step": diagnostics["endpoint_step"],
        "selected_time_ps": diagnostics["endpoint_time_ps"],
    }
    payload = json.dumps(selection, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with TemporaryDirectory(dir=directory) as temporary:
        pending = Path(temporary) / SELECTION_FILENAME
        pending.write_text(payload, encoding="utf-8")
        (directory / SELECTION_FILENAME).hardlink_to(pending)
    return selection


def check_selection(selection: Path, diagnostics: Path, frame_sha256: str) -> dict:
    """Verify a positive, recorded endpoint selection against the supplied frame."""
    selection = Path(selection)
    diagnostics = Path(diagnostics)
    try:
        if selection.parent.resolve() != diagnostics.parent.resolve():
            raise ValueError("water selection and diagnostics must share an evidence directory")
        record = _decode_record(selection.read_bytes(), selection)
        if record.get("schema") != SELECTION_SCHEMA or record.get("decision") != "select":
            raise ValueError("water endpoint has no affirmative selection")
        reason = record.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("water selection is missing a review reason")
        created = record.get("created_utc")
        if not isinstance(created, str) or datetime.fromisoformat(created).utcoffset() != timedelta(0):
            raise ValueError("water selection is missing a UTC decision time")
        if _digest(frame_sha256, "water frame") != record.get("frame_sha256"):
            raise ValueError("water frame SHA-256 does not match the selection record")
        diagnostics_sha, measured = _checked_diagnostics(diagnostics)
        if diagnostics_sha != record.get("diagnostics_sha256"):
            raise ValueError("water diagnostics SHA-256 does not match the selection record")
        _check_frame(measured, frame_sha256)
        _check_registered_criteria(measured)
        if (record.get("selected_step") != measured["endpoint_step"]
                or record.get("selected_time_ps") != measured["endpoint_time_ps"]):
            raise ValueError("water selection does not identify the registered endpoint time")
    except OSError as error:
        raise ValueError(f"missing or unreadable water evidence: {error}") from error
    return record


def _print_windows(diagnostics: dict) -> None:
    print("Window (ps)       Samples    PE (eV/atom)       V (A^3)          T (K)     P (GPa)")
    windows = [
        ("10 ps", window) for window in diagnostics["consecutive_10_ps_windows"]
    ] + [("half", window) for window in diagnostics["continuation_halves"].values()]
    for label, window in windows:
        start = window["start_ps_exclusive"]
        end = window["end_ps_inclusive"]
        pe, volume, temp, pressure = (window["metrics"][name]["mean"] for name in OBSERVABLES)
        print(f"({start:2}, {end:2}] {label:5}  {window['sample_count']:>6}    "
              f"{pe:>12.8f}    {volume:>13.3f}    {temp:>9.4f}  {pressure:>10.6f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    diagnose = commands.add_parser("diagnose", help="Save and display the 10 ps windows and halves")
    record = commands.add_parser("record", help="Record one explicit, evidence-bound decision")
    verify = commands.add_parser("verify", help="Recompute the evidence for a selected frame")
    for command in (diagnose, record, verify):
        command.add_argument("--root", required=True, type=Path, help="Water evidence directory")
    record.add_argument("--decision", choices=("select", "reject"), required=True)
    record.add_argument("--reason", required=True)
    verify.add_argument("--frame-sha256", required=True)
    args = parser.parse_args()
    diagnostics_path = args.root / DIAGNOSTICS_FILENAME
    try:
        if args.command == "diagnose":
            diagnostics = build_diagnostics(args.root)
            write_json(diagnostics_path, diagnostics)
            _print_windows(diagnostics)
        elif args.command == "record":
            print(json.dumps(record_decision(args.root, args.decision, args.reason), ensure_ascii=False))
        else:
            result = check_selection(args.root / SELECTION_FILENAME, diagnostics_path, args.frame_sha256)
            print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
