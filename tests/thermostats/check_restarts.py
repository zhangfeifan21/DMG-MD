#!/usr/bin/env python3
"""Smoke-test new thermostat jobs initialized from locked prior restart snapshots."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]
sys.path.insert(0, str(ROOT / "tests/baseline"))
sys.path.insert(0, str(ROOT / "tests/mpi"))
sys.path.insert(0, str(SUITE))
import run_baselines as baseline
import check_environment
import run_thermostats as thermostats


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_results(source: Path) -> Path:
    source = source.resolve()
    return source / "results.json" if source.is_dir() else source


def records_from(source: Path) -> tuple[Path, dict, list[dict]]:
    results_path = resolve_results(source)
    payload = json.loads(results_path.read_text())
    records = payload.get("records", payload.get("runner_records"))
    if not isinstance(records, list):
        raise RuntimeError(f"{results_path}: no records/runner_records array")
    return results_path, payload, records


def candidate_sources(sources: list[Path]) -> list[dict]:
    selected: dict[tuple, dict] = {}
    for source in sources:
        results_path, payload, records = records_from(source)
        for record in records:
            label = str(record.get("label", ""))
            if record.get("status") != "PASS" or record.get("reference"):
                continue
            if not (label.endswith("repeat-a") or re.search(r"-a(?:$|-)", label)
                    or label.startswith("short-")):
                continue
            if record.get("resumed") or record.get("negative") or "oracle" in label.lower():
                continue
            # Comparison records share the short-* prefix but have no job
            # directory or inputs; only actual successful executions are sources.
            if not record.get("cwd") or not record.get("input_sha256"):
                continue
            cwd = Path(record.get("cwd", ""))
            if not cwd.is_dir():
                continue
            input_hashes = record.get("input_sha256", {})
            if not all(name in input_hashes and (cwd / name).is_file()
                       and sha256(cwd / name) == input_hashes[name]
                       for name in ("model.xyz", "run.in", "nep.txt")):
                raise RuntimeError(f"{label}: source input hash verification failed")
            output_hashes = dict(record.get("output_sha256", {}))
            output_hashes.update(record.get("required_output_sha256", {}))
            if "restart.xyz" not in output_hashes:
                raise RuntimeError(f"{label}: source has no recorded restart hash")
            for name, expected in output_hashes.items():
                path = cwd / name
                if not path.is_file() or sha256(path) != expected:
                    raise RuntimeError(f"{label}: source output hash verification failed: {name}")
            if record.get("mode") not in ("m1-fallback", "m2a"):
                continue
            signature = (
                record.get("ensemble"), input_hashes.get("nep.txt"),
                record.get("geometry"), int(record.get("ranks", 0)),
                record.get("backend"),
            )
            if not all(signature):
                continue
            selected.setdefault(signature, {
                "source_results": str(results_path),
                "source_results_sha256": sha256(results_path),
                "source_payload_candidate": payload.get("candidate"),
                "source_payload_candidate_sha256": payload.get("candidate_sha256"),
                "record": record,
                "stage": cwd,
                "signature": signature,
                "restart_sha256": output_hashes["restart.xyz"],
                "output_sha256": output_hashes,
                "input_sha256": input_hashes,
            })
    return list(selected.values())


def parse_source_run(path: Path) -> tuple[str, str]:
    lines = path.read_text().splitlines()
    timestep = [line.split() for line in lines if line.strip().startswith("time_step ")]
    ensemble = [line.split() for line in lines if line.strip().startswith("ensemble ")]
    if len(timestep) != 1 or len(timestep[0]) != 2:
        raise RuntimeError(f"{path}: expected one two-token time_step command")
    if len(ensemble) != 1 or len(ensemble[0]) != 5:
        raise RuntimeError(f"{path}: expected one five-token ensemble command")
    if ensemble[0][1] not in ("nvt_nhc", "nvt_bdp"):
        raise RuntimeError(f"{path}: unsupported thermostat {ensemble[0][1]}")
    return " ".join(timestep[0]), " ".join(ensemble[0])


def restart_run(stage: Path, steps: int = 5) -> tuple[str, str, str]:
    timestep, ensemble = parse_source_run(stage / "run.in")
    ensemble_name = ensemble.split()[1]
    run_text = "\n".join((
        "potential nep.txt", timestep, ensemble,
        "dump_thermo 1",
        "dump_xyz 1 trajectory.xyz precision double velocity force potential virial",
        f"dump_restart {steps}", f"run {steps}", "",
    ))
    return run_text, ensemble_name, ensemble


def main_run(args: argparse.Namespace) -> int:
    args.candidate = args.candidate.resolve()
    if not args.candidate.is_file():
        raise RuntimeError(f"candidate binary does not exist: {args.candidate}")
    if args.work_root.exists():
        raise RuntimeError(f"work root must be a new directory: {args.work_root}")
    if len(set(args.devices)) < 4:
        raise RuntimeError("--devices must list at least four unique device IDs")
    sources = candidate_sources(args.sources)
    if not sources:
        raise RuntimeError("no eligible successful source snapshots were found")

    manifest = json.loads((SUITE / "manifest.json").read_text())
    runner_args = SimpleNamespace(
        candidate=args.candidate, reference=ROOT.parent / "gpumd-reference/src/gpumd",
        devices=args.devices, ranks=[1, 2, 4], backends=["HostStaged", "CudaAware"],
        work_root=args.work_root.resolve(), timeout=1800, parallel_seeds=1,
    )
    runner = thermostats.Runner(runner_args, manifest)
    payload = {
        "schema": "dmgmd.thermostat-restarts.v1", "status": "IN_PROGRESS",
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "candidate": str(args.candidate), "candidate_sha256": sha256(args.candidate),
        "manifest_sha256": sha256(SUITE / "manifest.json"),
        "sources": [str(resolve_results(path)) for path in args.sources],
        "source_count": len(sources), "checks": [], "runner_records": runner.records,
        "scope": "new restart-initialized jobs; no continuity or RNG equivalence claim",
    }
    output = runner.root / "results.json"

    def save(status: str, error: str | None = None) -> None:
        payload["status"] = status
        payload["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if error:
            payload["error"] = error
        temp = output.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
        temp.replace(output)

    save("IN_PROGRESS")
    try:
        for item in sources:
            source_record, source_stage = item["record"], item["stage"]
            run_text, ensemble_name, ensemble_line = restart_run(source_stage)
            target_ranks = int(source_record["ranks"])
            backend = source_record["backend"]
            source_mode = source_record["mode"]
            route_case = "domain" if source_mode == "m2a" else "fallback"
            label = f"resume-{source_record['label']}-p{target_ranks}-{backend}"
            stage = runner.execute(
                label, ensemble_name, route_case, target_ranks, backend,
                {"steps": 5, "dt_fs": 0.0, "damping_steps": 0.0,
                 "thermo_interval": 1, "trajectory_interval": 1,
                 "communication_interval": 1, "statistics": False},
                seed=int(source_record.get("seed", 1)), model=(source_stage / "restart.xyz").read_text(),
                run=run_text, potential=(source_stage / "nep.txt").read_text(),
            )
            record = next(row for row in runner.records if row.get("cwd") == str(stage))
            record["potential"] = source_record.get("potential", "NEP4")
            record["source_label"] = source_record["label"]
            record["source_restart_sha256"] = item["restart_sha256"]
            record["source_input_sha256"] = item["input_sha256"]
            record["source_output_sha256"] = item["output_sha256"]
            record["source_rank"] = int(source_record["ranks"])
            record["target_rank"] = target_ranks
            record["source_mode"] = source_mode
            record["resumed"] = True
            record["source_ensemble_line"] = ensemble_line
            record["required_output_sha256"] = {
                name: sha256(stage / name) for name in ("thermo.out", "trajectory.xyz", "restart.xyz")
            }
            payload["checks"].append({
                "type": "same-rank-restart-smoke", "source_label": source_record["label"],
                "source_rank": int(source_record["ranks"]), "target_rank": target_ranks,
                "backend": backend, "ensemble": ensemble_name,
                "potential_sha256": item["input_sha256"]["nep.txt"],
                "source_restart_sha256": item["restart_sha256"],
                "target_cwd": str(stage), "target_command": record["command"],
                "target_input_sha256": record["input_sha256"],
                "target_output_sha256": record["required_output_sha256"],
                "route": record.get("mode"), "status": "PASS",
            })
            runner.save()
            save("IN_PROGRESS")

        # Cross-rank restarts are deliberately new jobs and are not compared as
        # continuations. Use one NEP4 P1 snapshot per thermostat when available.
        for ensemble_name in ("nvt_nhc", "nvt_bdp"):
            options = [item for item in sources
                       if item["record"].get("ensemble") == ensemble_name
                       and int(item["record"].get("ranks", 0)) == 1
                       and item["input_sha256"].get("nep.txt") == manifest["potential"]["sha256"]]
            if not options:
                raise RuntimeError(f"no P1 NEP4 snapshot found for cross-rank {ensemble_name}")
            item = options[0]
            source_record, source_stage = item["record"], item["stage"]
            run_text, _, ensemble_line = restart_run(source_stage)
            label = f"crossrank-resume-{ensemble_name}-p1-to-p4-{source_record['backend']}"
            backend = source_record["backend"]
            route_case = "domain" if source_record["mode"] == "m2a" else "fallback"
            stage = runner.execute(
                label, ensemble_name, route_case, 4, backend,
                {"steps": 5, "dt_fs": 0.0, "damping_steps": 0.0,
                 "thermo_interval": 1, "trajectory_interval": 1,
                 "communication_interval": 1, "statistics": False},
                seed=int(source_record.get("seed", 1)), model=(source_stage / "restart.xyz").read_text(),
                run=run_text, potential=(source_stage / "nep.txt").read_text(),
            )
            record = next(row for row in runner.records if row.get("cwd") == str(stage))
            record.update(potential="NEP4", source_label=source_record["label"],
                          source_restart_sha256=item["restart_sha256"], source_rank=1,
                          target_rank=4, resumed=True, source_ensemble_line=ensemble_line)
            record["required_output_sha256"] = {
                name: sha256(stage / name) for name in ("thermo.out", "trajectory.xyz", "restart.xyz")
            }
            payload["checks"].append({
                "type": "cross-rank-new-job-restart-smoke", "source_label": source_record["label"],
                "source_rank": 1, "target_rank": 4, "backend": backend,
                "ensemble": ensemble_name, "potential_sha256": item["input_sha256"]["nep.txt"],
                "source_restart_sha256": item["restart_sha256"], "target_cwd": str(stage),
                "target_command": record["command"], "target_input_sha256": record["input_sha256"],
                "target_output_sha256": record["required_output_sha256"],
                "route": record.get("mode"), "status": "PASS",
            })
            runner.save()
            save("IN_PROGRESS")
        save("PASS")
        print(f"PASS {len(payload['checks'])} restart smoke jobs; evidence {runner.root}", flush=True)
        return 0
    except Exception as error:
        save("FAIL", str(error))
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, nargs="+", required=True,
                        help="Runner results.json files or evidence root directories")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--devices", type=lambda value: value.split(","), default=["2", "4", "5", "7"])
    parser.add_argument("--work-root", type=Path, required=True, help="new evidence directory")
    return main_run(parser.parse_args())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, baseline.BaselineError,
            check_environment.EnvironmentError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
