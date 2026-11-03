#!/usr/bin/env python3
"""Check per-step ensemble scalar broadcasts and communication accounting."""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import math
from pathlib import Path
import re
import struct
import sys


SUITE = Path(__file__).resolve().parent
sys.path.insert(0, str(SUITE))
import run_thermostats as thermostat_runner


FACTOR_PATTERN = re.compile(
    r"DMGMD_ENSEMBLE_FACTOR rank=(\d+) sequence=(\d+) "
    r"status_bits=([0-9a-fA-F]{1,16}) factor_bits=([0-9a-fA-F]{1,16})"
)
EXPECTED_STATUS_BITS = struct.unpack(">Q", struct.pack(">d", 1.0))[0]
COMM_COUNTERS = (
    "collective_calls",
    "mpi_input_bytes_global",
    "mpi_output_bytes_global",
    "device_to_host_bytes_global",
    "host_to_device_bytes_global",
)


def parse_key_values(line: str, prefix: str) -> dict[str, str]:
    if not line.startswith(prefix):
        raise ValueError(f"unexpected telemetry line: {line}")
    fields = {}
    for item in line[len(prefix) :].split():
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        fields[key] = value
    return fields


def read_comm_records(stage: Path, expected_count: int) -> list[dict[str, str]]:
    stdout_path = stage / "stdout.log"
    lines = [line for line in stdout_path.read_text(errors="replace").splitlines()
             if line.startswith("DMGMD_COMM step=")]
    records = [parse_key_values(line, "DMGMD_COMM ") for line in lines]
    if len(records) != expected_count:
        raise ValueError(f"{stage}: expected {expected_count} DMGMD_COMM rows, found {len(records)}")
    steps = [int(record["step"]) for record in records]
    if any(right != left + 1 for left, right in zip(steps, steps[1:])):
        raise ValueError(f"{stage}: DMGMD_COMM step sequence is not contiguous: {steps}")
    return records


def read_domain_comm_records(stage: Path) -> dict[tuple[int, int], dict[str, str]]:
    lines = [line for line in (stage / "stdout.log").read_text(errors="replace").splitlines()
             if line.startswith("DMGMD_DOMAIN_COMM ")]
    records: dict[tuple[int, int], dict[str, str]] = {}
    for line in lines:
        fields = parse_key_values(line, "DMGMD_DOMAIN_COMM ")
        key = (int(fields["rank"]), int(fields["step"]))
        if key in records:
            raise ValueError(f"{stage}: duplicate DMGMD_DOMAIN_COMM row {key}")
        records[key] = fields
    return records


def check_probe(stage: Path, ranks: int, ensemble: str, steps: int) -> dict:
    stderr = (stage / "stderr.log").read_text(errors="replace")
    events = []
    for match in FACTOR_PATTERN.finditer(stderr):
        rank, sequence, status_bits, factor_bits = match.groups()
        events.append({
            "rank": int(rank),
            "sequence": int(sequence),
            "status_bits": int(status_bits, 16),
            "factor_bits": int(factor_bits, 16),
        })

    broadcasts_per_step = 2 if ensemble == "nvt_nhc" else 1
    expected_per_rank = steps * broadcasts_per_step
    errors = []
    marker_count = stderr.count("DMGMD_ENSEMBLE_FACTOR")
    if marker_count != len(events):
        errors.append(f"found {marker_count} probe markers but parsed {len(events)} complete records")
    by_rank: dict[int, list[dict]] = {rank: [] for rank in range(ranks)}
    for event in events:
        if event["rank"] not in by_rank:
            errors.append(f"probe emitted out-of-range rank {event['rank']}")
            continue
        by_rank[event["rank"]].append(event)
        if event["status_bits"] != EXPECTED_STATUS_BITS:
            errors.append(
                f"rank {event['rank']} sequence {event['sequence']} has status_bits "
                f"{event['status_bits']:016x}, expected {EXPECTED_STATUS_BITS:016x}"
            )

    per_sequence_factors: dict[int, set[int]] = {}
    for rank, rank_events in by_rank.items():
        sequences = [event["sequence"] for event in rank_events]
        if sequences != list(range(expected_per_rank)):
            errors.append(
                f"rank {rank} sequence is {sequences}, expected 0..{expected_per_rank - 1}"
            )
        for event in rank_events:
            per_sequence_factors.setdefault(event["sequence"], set()).add(event["factor_bits"])
    for sequence in range(expected_per_rank):
        factors = per_sequence_factors.get(sequence, set())
        if len(factors) != 1:
            errors.append(
                f"sequence {sequence} factor bit patterns differ across ranks: "
                f"{sorted(f'{value:016x}' for value in factors)}"
            )
        elif factors:
            factor = struct.unpack(">d", struct.pack(">Q", next(iter(factors))))[0]
            if not math.isfinite(factor) or factor <= 0.0:
                errors.append(f"sequence {sequence} factor is not finite and positive: {factor}")

    return {
        "expected_per_rank": expected_per_rank,
        "observed_per_rank": {str(rank): len(values) for rank, values in by_rank.items()},
        "events": events,
        "errors": errors,
        "status": "PASS" if not errors else "FAIL",
    }


def compare_communication(
    nhc_stage: Path, ber_stage: Path, ensemble: str, ranks: int, backend: str, steps: int
) -> dict:
    nhc = read_comm_records(nhc_stage, steps)
    ber = read_comm_records(ber_stage, steps)
    if ensemble == "nvt_nhc":
        expected = {
            "collective_calls": 3,
            "mpi_input_bytes_global": 32 + 8 * ranks,
            "mpi_output_bytes_global": 40 * ranks,
            "device_to_host_bytes_global": 8 * ranks if backend == "HostStaged" else 0,
            "host_to_device_bytes_global": 8 * ranks if backend == "HostStaged" else 0,
        }
    else:
        expected = {
            "collective_calls": 1,
            "mpi_input_bytes_global": 16,
            "mpi_output_bytes_global": 16 * ranks,
            "device_to_host_bytes_global": 0,
            "host_to_device_bytes_global": 0,
        }
    per_step = []
    errors = []
    for index, (nhc_row, ber_row) in enumerate(zip(nhc, ber)):
        if nhc_row.get("step") != ber_row.get("step"):
            errors.append(
                f"record {index} step mismatch NHC={nhc_row.get('step')} Ber={ber_row.get('step')}"
            )
        if nhc_row.keys() != ber_row.keys():
            errors.append(
                f"step {nhc_row.get('step')}: telemetry fields differ; "
                f"NHC-only={sorted(nhc_row.keys() - ber_row.keys())}, "
                f"Ber-only={sorted(ber_row.keys() - nhc_row.keys())}"
            )
        differences = {}
        for field in COMM_COUNTERS:
            if field not in nhc_row or field not in ber_row:
                errors.append(f"step {nhc_row.get('step')}: missing counter {field}")
                continue
            differences[field] = int(nhc_row[field]) - int(ber_row[field])
            if differences[field] != expected[field]:
                errors.append(
                    f"step {nhc_row.get('step')}: {field} delta {differences[field]}, "
                    f"expected {expected[field]}"
                )
        extras = {}
        all_numeric = (set(nhc_row) & set(ber_row)) - {"step", "backend", *COMM_COUNTERS}
        for field in sorted(all_numeric):
            try:
                delta = int(nhc_row[field]) - int(ber_row[field])
            except ValueError:
                continue
            if delta != 0:
                extras[field] = delta
        per_step.append({"step": nhc_row.get("step"), "expected_counter_deltas": differences,
                         "additional_deltas": extras})
        if extras:
            errors.append(
                f"step {nhc_row.get('step')}: unmodeled communication deltas {extras}; "
                "possible halo or layout change"
            )

    halo_differences = []
    if ranks > 1 and "domain" in nhc_stage.name:
        nhc_domain = read_domain_comm_records(nhc_stage)
        ber_domain = read_domain_comm_records(ber_stage)
        expected_records = ranks * steps
        if len(nhc_domain) != expected_records or len(ber_domain) != expected_records:
            errors.append(
                f"M2a expected {expected_records} domain records per ensemble, "
                f"found NHC={len(nhc_domain)} Ber={len(ber_domain)}"
            )
        if nhc_domain.keys() != ber_domain.keys():
            errors.append("M2a DMGMD_DOMAIN_COMM rank/step layouts differ between ensembles")
        for key in sorted(nhc_domain.keys() & ber_domain.keys()):
            nrow, brow = nhc_domain[key], ber_domain[key]
            if nrow.keys() != brow.keys():
                errors.append(f"M2a rank/step {key}: DMGMD_DOMAIN_COMM fields differ")
            diffs = {}
            for field in sorted((set(nrow) & set(brow)) - {"rank", "step", "backend"}):
                try:
                    delta = int(nrow[field]) - int(brow[field])
                except ValueError:
                    continue
                if delta:
                    diffs[field] = delta
            if diffs:
                halo_differences.append({"rank": key[0], "step": key[1], "deltas": diffs})
        if halo_differences:
            errors.append(
                "M2a halo/domain communication differs between NHC and Berendsen; "
                "kept expected scalar deltas fixed and recorded the layout deltas"
            )

    return {
        "expected_per_step_deltas": expected,
        "per_step": per_step,
        "halo_domain_differences": halo_differences,
        "errors": errors,
        "status": "PASS" if not errors else "FAIL",
    }


def run_pair(runner, probe: Path, ensemble: str, case: str, ranks: int, backend: str, profile: dict) -> dict:
    model = protocol_model(case)
    run = thermostat_runner.run_text(ensemble, profile)
    label = f"protocol-{ensemble}-{case}-p{ranks}-{backend}"
    extra_env = {"LD_PRELOAD": str(probe)}
    instrumented_stage = runner.execute(
        label + "-instrumented", ensemble, case, ranks, backend, profile, seed=1,
        model=model, run=run, extra_env=extra_env,
    )
    ber_stage = runner.execute(
        label + "-ber", "nvt_ber", case, ranks, backend, profile, seed=1,
        model=model, run=thermostat_runner.run_text("nvt_ber", profile),
    )
    protocol = check_probe(instrumented_stage, ranks, ensemble, profile["steps"])
    communication = compare_communication(
        instrumented_stage, ber_stage, ensemble, ranks, backend, profile["steps"]
    )
    return {
        "label": label,
        "ensemble": ensemble,
        "data_plane_case": case,
        "ranks": ranks,
        "backend": backend,
        "instrumented_stage": str(instrumented_stage),
        "berendsen_stage": str(ber_stage),
        "logs": {
            "instrumented_stdout": str(instrumented_stage / "stdout.log"),
            "instrumented_stderr": str(instrumented_stage / "stderr.log"),
            "berendsen_stdout": str(ber_stage / "stdout.log"),
            "berendsen_stderr": str(ber_stage / "stderr.log"),
        },
        "probe": protocol,
        "communication": communication,
        "status": "PASS" if protocol["status"] == "PASS" and communication["status"] == "PASS" else "FAIL",
    }


def protocol_model(case: str) -> str:
    if case == "fallback":
        x_second, lengths = 22.0, (30.0, 24.0, 24.0)
    else:
        x_second, lengths = 48.0, (64.0, 24.0, 24.0)
    lx, ly, lz = lengths
    return (
        "2\n"
        f'pbc="T T T" Lattice="{lx:g} 0 0 0 {ly:g} 0 0 0 {lz:g}" '
        "Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3\n"
        "C 2 12 12 12.011 0.001 0.002 -0.001\n"
        f"C {x_second:g} 12 12 12.011 0.001 0.002 -0.001\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--devices", type=lambda text: text.split(","), default=["2", "4", "5", "7"])
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--ensemble", choices=("nvt_nhc", "nvt_bdp"), default="nvt_nhc")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    args.candidate = args.candidate.resolve(strict=True)
    args.probe = args.probe.resolve(strict=True)
    if not args.candidate.is_file() or not args.probe.is_file():
        parser.error("--candidate and --probe must be files")
    if len(args.devices) < 4 or len(set(args.devices)) != len(args.devices):
        parser.error("--devices must list at least four unique devices")
    args.ranks = [1, 2, 4]
    args.work_root = args.work_root.resolve()
    manifest = json.loads((SUITE / "manifest.json").read_text())
    runner = thermostat_runner.Runner(args, manifest)
    result_path = runner.root / "protocol-results.json"
    profile = {
        "steps": 8,
        "dt_fs": 0.1,
        "damping_steps": 1000,
        "thermo_interval": 1,
        "communication_interval": 1,
    }
    results = {
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "candidate": str(args.candidate),
        "probe": str(args.probe),
        "candidate_sha256": runner.binary_hash,
        "probe_sha256": thermostat_runner.sha(args.probe.read_bytes()),
        "manifest_sha256": runner.manifest_hash,
        "runner_results": str(runner.root / "results.json"),
        "profile": profile,
        "rank_counts": args.ranks,
        "backends": ["HostStaged", "CudaAware"],
        "data_plane_cases": ["fallback", "domain"],
        "records": [],
        "status": "INCOMPLETE",
    }

    for case in ("fallback", "domain"):
        for ranks in args.ranks:
            for backend in ("HostStaged", "CudaAware"):
                label = f"{args.ensemble}-{case}-p{ranks}-{backend}"
                try:
                    record = run_pair(runner, args.probe, args.ensemble, case, ranks, backend, profile)
                except Exception as error:
                    record = {
                        "label": label,
                        "ensemble": args.ensemble,
                        "data_plane_case": case,
                        "ranks": ranks,
                        "backend": backend,
                        "status": "FAIL",
                        "error": str(error),
                        "runner_records": runner.records[-2:],
                    }
                results["records"].append(record)
                results["status"] = "PASS" if all(item["status"] == "PASS" for item in results["records"]) else "FAIL"
                result_path.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")

    print(f"protocol status: {results['status']} (evidence: {result_path})", flush=True)
    return 0 if results["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, thermostat_runner.baseline.BaselineError,
            thermostat_runner.check_environment.EnvironmentError) as error:
        print(f"FAIL protocol check: {error}", file=sys.stderr)
        raise SystemExit(1)
