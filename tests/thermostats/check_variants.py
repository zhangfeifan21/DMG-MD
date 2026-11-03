#!/usr/bin/env python3
"""Short NHC/BDP acceptance across NEP5 routes and a locked NEP-ZBL fixture."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[1]
sys.path.insert(0, str(ROOT / "tests/baseline"))
sys.path.insert(0, str(ROOT / "tests/mpi"))
sys.path.insert(0, str(ROOT / "tests/long_nve"))
sys.path.insert(0, str(SUITE))
import run_baselines as baseline
import check_environment
import long_nve_common as long_nve
import run_thermostats as thermostats

BACKENDS = ("HostStaged", "CudaAware")
ENSEMBLES = ("nvt_nhc", "nvt_bdp")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def profile_and_run(dt_fs: float, damping_steps: float, steps: int = 100) -> tuple[dict, str]:
    profile = {
        "steps": steps,
        "dt_fs": dt_fs,
        "damping_steps": damping_steps,
        "thermo_interval": 10,
        "trajectory_interval": 10,
        "communication_interval": 10,
        "statistics": False,
    }
    run_texts = {}
    for ensemble in ENSEMBLES:
        run_texts[ensemble] = "\n".join((
            "potential nep.txt",
            f"time_step {dt_fs:.12g}",
            f"ensemble {ensemble} 300 300 {damping_steps:.12g}",
            "dump_thermo 10",
            "dump_xyz 10 trajectory.xyz precision double velocity force potential virial",
            f"dump_restart {steps}",
            f"run {steps}",
            "",
        ))
    return profile, run_texts


def make_runner_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        candidate=args.candidate.resolve(),
        reference=ROOT.parent / "gpumd-reference/src/gpumd",
        devices=args.devices,
        ranks=[1, 2, 4],
        backends=list(BACKENDS),
        work_root=args.work_root.resolve(),
        timeout=1800,
        parallel_seeds=1,
    )


def load_fixtures() -> tuple[dict, dict, dict, dict]:
    thermostat_manifest = json.loads((SUITE / "manifest.json").read_text())
    long_manifest = long_nve.load_manifest()
    baseline_manifest = baseline.load_manifest()

    nep5_case = long_nve.profile_case(long_manifest, "smoke", "carbon_nep5")
    nep5_text = long_nve.generate_potential(nep5_case)
    long_nve.validate_generated_potential(nep5_case, nep5_text)
    carbon_case = long_nve.profile_case(long_manifest, "smoke", "carbon_crystal")
    carbon_model = long_nve.generate_model(carbon_case, 0)
    long_nve.validate_generated_model(carbon_case, 0, carbon_model)

    domain_case = thermostat_manifest["geometries"]["domain"]
    domain_model = thermostats.model_text(domain_case, 1)
    if thermostats.sha(domain_model) != thermostat_manifest["input_sha256"]["domain"]["1"]:
        raise RuntimeError("locked thermostat domain model hash mismatch")

    zbl_case = long_nve.profile_case(long_manifest, "smoke", "batio3_zbl")
    zbl_text = long_nve.generate_potential(zbl_case)
    long_nve.validate_generated_potential(zbl_case, zbl_text)
    zbl_model = long_nve.generate_model(zbl_case, 0)
    long_nve.validate_generated_model(zbl_case, 0, zbl_model)

    fixtures = {
        "nep5-carbon-fallback": {
            "model": carbon_model,
            "model_sha256": long_nve.text_sha256(carbon_model),
            "model_lock": carbon_case["model_sha256"]["0"],
            "model_source": "tests/long_nve/long_nve_common.py:generate_model(carbon_crystal, seed=0)",
            "geometry": {"cells": carbon_case["cells"], "atoms": carbon_case["atoms"]},
            "dt_fs": float(carbon_case["time_step_fs"]),
            "damping_steps": float(long_manifest["profiles"]["smoke"]["nvt_temperature_coupling"]),
            "potential": nep5_text,
            "potential_name": "NEP5",
            "potential_lock": nep5_case["potential_sha256"],
            "potential_source": nep5_case["potential"],
            "route_case": "fallback",
            "expected_route_by_ranks": {"1": "m1-fallback", "2": "m1-fallback", "4": "m1-fallback"},
        },
        "nep5-carbon-domain": {
            "model": domain_model,
            "model_sha256": thermostats.sha(domain_model),
            "model_lock": thermostat_manifest["input_sha256"]["domain"]["1"],
            "model_source": "tests/thermostats/run_thermostats.py:model_text(domain, seed=1)",
            "geometry": {"cells": domain_case["cells"], "box_A": domain_case["box_A"], "atoms": 8 * 18 * 6 * 6},
            "dt_fs": float(carbon_case["time_step_fs"]),
            "damping_steps": float(long_manifest["profiles"]["smoke"]["nvt_temperature_coupling"]),
            "potential": nep5_text,
            "potential_name": "NEP5",
            "potential_lock": nep5_case["potential_sha256"],
            "potential_source": nep5_case["potential"],
            "route_case": "domain",
            "expected_route_by_ranks": {"1": "m1-fallback", "2": "m2a", "4": "m2a"},
        },
        "nep-zbl-batio3": {
            "model": zbl_model,
            "model_sha256": long_nve.text_sha256(zbl_model),
            "model_lock": zbl_case["model_sha256"]["0"],
            "model_source": "tests/long_nve/long_nve_common.py:generate_model(batio3_zbl, seed=0)",
            "geometry": {"cells": zbl_case["cells"], "atoms": zbl_case["atoms"]},
            "dt_fs": float(zbl_case["time_step_fs"]),
            "damping_steps": float(long_manifest["profiles"]["smoke"]["nvt_temperature_coupling"]),
            "potential": zbl_text,
            "potential_name": "NEP-ZBL",
            "potential_lock": zbl_case["potential_sha256"],
            "potential_source": zbl_case["potential"],
            "route_case": None,
            "expected_route_by_ranks": {str(rank): zbl_case["domain_mode_by_rank"][str(rank)]
                                         for rank in (1, 2, 4)},
        },
    }
    return thermostat_manifest, long_manifest, baseline_manifest, fixtures


def attach_run_evidence(runner: thermostats.Runner, stage: Path, fixture_name: str,
                        fixture: dict, ensemble: str) -> dict:
    record = next(record for record in runner.records if record.get("cwd") == str(stage))
    record["fixture"] = fixture_name
    record["potential"] = fixture["potential_name"]
    record["potential_sha256"] = record["input_sha256"]["nep.txt"]
    record["model_sha256"] = record["input_sha256"]["model.xyz"]
    record["model_fixture_lock_sha256"] = fixture["model_lock"]
    record["model_source"] = fixture["model_source"]
    record["ensemble"] = ensemble
    record["stdout_path"] = str(stage / "stdout.log")
    record["stderr_path"] = str(stage / "stderr.log")
    record["stdout_sha256"] = sha256(stage / "stdout.log")
    record["stderr_sha256"] = sha256(stage / "stderr.log")
    record["all_output_sha256"] = {
        path.name: sha256(path)
        for path in stage.iterdir()
        if path.is_file() and path.name not in {"model.xyz", "run.in", "nep.txt", "stdout.log", "stderr.log"}
    }
    record["route_claim"] = record.get("mode")
    runner.save()
    return record


def require_standard_outputs(stage: Path) -> dict[str, str]:
    names = ("thermo.out", "trajectory.xyz", "restart.xyz")
    missing = [name for name in names if not (stage / name).is_file()]
    if missing:
        raise RuntimeError(f"{stage}: missing required outputs {missing}")
    return {name: sha256(stage / name) for name in names}


def run_candidate(runner: thermostats.Runner, ensemble: str, fixture_name: str,
                  fixture: dict, ranks: int, backend: str, suffix: str = "a") -> tuple[Path, dict]:
    route_case = fixture["route_case"]
    if route_case is None:
        expected_mode = fixture["expected_route_by_ranks"][str(ranks)]
        route_case = "domain" if ranks > 1 and expected_mode == "m2a" else "fallback"
    profile, run_texts = profile_and_run(fixture["dt_fs"], fixture["damping_steps"])
    label = f"{fixture_name}-{ensemble}-p{ranks}-{backend}-{suffix}"
    stage = runner.execute(
        label, ensemble, route_case, ranks, backend, profile,
        seed=1,
        model=fixture["model"],
        run=run_texts[ensemble],
        potential=fixture["potential"],
    )
    record = attach_run_evidence(runner, stage, fixture_name, fixture, ensemble)
    expected_mode = fixture["expected_route_by_ranks"][str(ranks)]
    if record.get("mode") != expected_mode:
        raise RuntimeError(f"{label}: actual route {record.get('mode')} != expected {expected_mode}")
    record["required_output_sha256"] = require_standard_outputs(stage)
    runner.save()
    return stage, record


def add_comparison(payload: dict, runner: thermostats.Runner, source: Path, target: Path,
                   label: str, exact: bool) -> dict:
    before = len(runner.records)
    runner.compare(source, target, label, exact=exact)
    comparison = runner.records[before]
    payload["comparisons"].append({
        "label": label,
        "status": comparison["status"],
        "kind": "byte-identical" if exact else "registered-baseline-tolerance",
        "result": comparison["comparison"],
        "source_dir": str(source),
        "target_dir": str(target),
    })
    return comparison


def save_final(runner: thermostats.Runner, payload: dict, status: str, error: str | None = None) -> None:
    payload["status"] = status
    payload["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if error:
        payload["error"] = error
    output = runner.root / "results.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    temporary.replace(output)


def run(args: argparse.Namespace) -> int:
    args.candidate = args.candidate.resolve()
    if not args.candidate.is_file():
        raise RuntimeError(f"candidate binary does not exist: {args.candidate}")
    if args.work_root.exists():
        raise RuntimeError(f"work root must be a new directory: {args.work_root}")
    if len(set(args.devices)) != len(args.devices) or len(args.devices) < 4:
        raise RuntimeError("--devices must contain at least four unique device IDs")

    thermostat_manifest, long_manifest, base_manifest, fixtures = load_fixtures()
    parsed = make_runner_args(args)
    runner = thermostats.Runner(parsed, thermostat_manifest)
    payload = {
        "schema": "dmgmd.thermostat-variants.v1",
        "status": "IN_PROGRESS",
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "candidate": str(args.candidate),
        "candidate_sha256": sha256(args.candidate),
        "thermostat_manifest_sha256": sha256(SUITE / "manifest.json"),
        "long_nve_manifest_sha256": sha256(ROOT / "tests/long_nve/manifest.json"),
        "baseline_manifest_sha256": sha256(ROOT / "tests/baseline/manifest.json"),
        "run_thermostats_sha256": sha256(SUITE / "run_thermostats.py"),
        "long_nve_common_sha256": sha256(ROOT / "tests/long_nve/long_nve_common.py"),
        "reference": str(parsed.reference),
        "reference_executable_sha256": sha256(parsed.reference) if parsed.reference.is_file() else None,
        "fixtures": {
            name: {key: value for key, value in fixture.items() if key not in ("model", "potential")}
            for name, fixture in fixtures.items()
        },
        "matrix": [],
        "comparisons": [],
        "runner_records": runner.records,
        "acceptance_source": "existing baseline manifest tolerances; no new thresholds",
    }
    save_final(runner, payload, "IN_PROGRESS")
    try:
        executed: dict[tuple[str, str, int, str], Path] = {}
        for ensemble in ENSEMBLES:
            configurations = []
            for fixture_name in ("nep5-carbon-fallback", "nep5-carbon-domain"):
                fixture = fixtures[fixture_name]
                configurations.append((fixture_name, fixture, 1, "HostStaged"))
                for ranks in (2, 4):
                    for backend in BACKENDS:
                        configurations.append((fixture_name, fixture, ranks, backend))
            zbl = fixtures["nep-zbl-batio3"]
            configurations.append(("nep-zbl-batio3", zbl, 1, "HostStaged"))
            for ranks in (2, 4):
                for backend in BACKENDS:
                    configurations.append(("nep-zbl-batio3", zbl, ranks, backend))

            for fixture_name, fixture, ranks, backend in configurations:
                first, first_record = run_candidate(
                    runner, ensemble, fixture_name, fixture, ranks, backend, "repeat-a"
                )
                second, second_record = run_candidate(
                    runner, ensemble, fixture_name, fixture, ranks, backend, "repeat-b"
                )
                comparison = add_comparison(
                    payload, runner, first, second,
                    f"repeat-{fixture_name}-{ensemble}-p{ranks}-{backend}", exact=True,
                )
                if fixture_name == "nep5-carbon-fallback" and ranks == 1:
                    baseline_key = fixture_name, ensemble, ranks, "HostStaged"
                else:
                    baseline_key = fixture_name, ensemble, ranks, backend
                executed[baseline_key] = first
                payload["matrix"].append({
                    "case": fixture_name,
                    "ensemble": ensemble,
                    "potential": fixture["potential_name"],
                    "ranks": ranks,
                    "data_plane": first_record.get("mode"),
                    "backend": backend,
                    "steps": first_record.get("steps"),
                    "seed": 1,
                    "status": "PASS",
                    "evidence": str(first),
                    "repeat_evidence": str(second),
                    "repeat_comparison": comparison["comparison"],
                    "mode_detail": first_record.get("mode_detail"),
                    "layouts": first_record.get("layouts", []),
                    "input_sha256": first_record["input_sha256"],
                    "output_sha256": first_record["required_output_sha256"],
                    "commands": [first_record["command"], second_record["command"]],
                    "cwd": [first_record["cwd"], second_record["cwd"]],
                    "environment": [first_record["environment"], second_record["environment"]],
                    "logs": [{"stdout": first_record["stdout_path"], "stderr": first_record["stderr_path"]},
                             {"stdout": second_record["stdout_path"], "stderr": second_record["stderr_path"]}],
                })
                save_final(runner, payload, "IN_PROGRESS")

            # Compare M1/M2a ranks and CudaAware against the HostStaged baseline
            # generated from the exact same locked model and potential fixture.
            for fixture_name in ("nep5-carbon-fallback", "nep5-carbon-domain", "nep-zbl-batio3"):
                p1 = executed[(fixture_name, ensemble, 1, "HostStaged")]
                for ranks in (2, 4):
                    for backend in BACKENDS:
                        target = executed[(fixture_name, ensemble, ranks, backend)]
                        add_comparison(
                            payload, runner, p1, target,
                            f"cross-rank-{fixture_name}-{ensemble}-p{ranks}-{backend}", exact=False,
                        )
                    cuda = executed[(fixture_name, ensemble, ranks, "CudaAware")]
                    host = executed[(fixture_name, ensemble, ranks, "HostStaged")]
                    add_comparison(
                        payload, runner, host, cuda,
                        f"cross-backend-{fixture_name}-{ensemble}-p{ranks}", exact=False,
                    )

            if ensemble == "nvt_nhc":
                fixture = fixtures["nep5-carbon-fallback"]
                profile, run_texts = profile_and_run(fixture["dt_fs"], fixture["damping_steps"])
                oracle = runner.execute(
                    "gpumd-oracle-nep5-carbon-nhc-p1",
                    "nvt_nhc", "fallback", 1, "HostStaged", profile,
                    seed=1, reference=True, model=fixture["model"],
                    run=run_texts["nvt_nhc"], potential=fixture["potential"],
                )
                oracle_record = attach_run_evidence(
                    runner, oracle, "nep5-carbon-fallback", fixture, "nvt_nhc"
                )
                actual = executed[("nep5-carbon-fallback", "nvt_nhc", 1, "HostStaged")]
                before = len(runner.records)
                runner.compare(oracle, actual, "gpumd-nep5-carbon-nhc-p1", exact=False)
                result = runner.records[before]
                payload["comparisons"].append({
                    "label": "gpumd-nep5-carbon-nhc-p1",
                    "status": result["status"],
                    "kind": "independent-GPUMD-oracle-registered-tolerance",
                    "result": result["comparison"],
                    "reference_command": oracle_record["command"],
                    "reference_cwd": oracle_record["cwd"],
                    "reference_input_sha256": oracle_record["input_sha256"],
                    "reference_output_sha256": oracle_record["all_output_sha256"],
                    "reference_logs": {"stdout": oracle_record["stdout_path"], "stderr": oracle_record["stderr_path"]},
                })
                save_final(runner, payload, "IN_PROGRESS")

        payload["status"] = "PASS"
        payload["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save_final(runner, payload, "PASS")
        print(f"PASS {len(payload['matrix'])} variant runs; evidence {runner.root}", flush=True)
        return 0
    except Exception as error:
        save_final(runner, payload, "FAIL", str(error))
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--devices", type=lambda value: value.split(","), default=["2", "4", "5", "7"])
    parser.add_argument("--work-root", type=Path, required=True, help="new evidence directory")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, baseline.BaselineError,
            check_environment.EnvironmentError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        raise SystemExit(1)
