#!/usr/bin/env python3
"""Freeze B3 candidate/reference and preregistered SCR inputs before sampling."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import generate_inputs
import run_npt_ber


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def capture(argv: list[str]) -> str:
    return subprocess.run(argv, check=True, text=True, capture_output=True).stdout


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--reference", type=Path, default=ROOT.parent / "gpumd-reference/src/gpumd")
    p.add_argument("--input-root", type=Path, required=True)
    p.add_argument("--freeze", type=Path, required=True)
    p.add_argument("--build-log", type=Path, required=True)
    p.add_argument("--acf-helper", type=Path, required=True)
    a = p.parse_args()
    candidate, reference = a.candidate.resolve(), a.reference.resolve()
    input_root, freeze_path = a.input_root.resolve(), a.freeze.resolve()
    if not candidate.is_file() or not candidate.stat().st_mode & 0o111:
        raise SystemExit(f"candidate missing/not executable: {candidate}")
    expected_ref = json.loads((ROOT / "tests/baseline/manifest.json").read_text())["reference"]["executable_sha256"]
    if not reference.is_file() or sha(reference) != expected_ref:
        raise SystemExit("GPUMD executable SHA differs from pinned baseline manifest")
    if input_root.exists() or freeze_path.exists():
        raise SystemExit("refusing to overwrite existing B3 input root or freeze")
    if not a.acf_helper.is_file() or not a.acf_helper.stat().st_mode & 0o111:
        raise SystemExit("native autocorrelation helper is missing/not executable")

    manifest_path = HERE / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    potential_source = ROOT / manifest["source_contract"]["potential_path"]
    if sha(potential_source) != manifest["source_contract"]["potential_sha256"]:
        raise SystemExit("NEP potential differs from preregistered SHA")
    input_root.mkdir(parents=True)
    inputs: dict[str, str] = {}
    # Materialize every preregistered statistical input once, byte-identically
    # across engines. Long GPUMD runs share the registered max-budget run.in.
    for profile, dt_fs, tdamp, tau, budgets, reference_steps in (
        ("base", .25, 40, 200, (20000, 40000, 80000), 80000),
        ("half", .125, 80, 400, (40000, 80000, 160000), 160000),
    ):
        fixture = manifest["fixtures"]["statistics"]
        for steps in budgets:
            for seed in fixture["seeds"]:
                model = generate_inputs.model_text((3, 3, 3), seed)
                expected = fixture["model_xyz_sha256_by_seed"][str(seed)]
                if generate_inputs.sha256_bytes(model.encode()) != expected:
                    raise SystemExit(f"seed {seed} model hash differs from manifest")
                run_steps = reference_steps if steps == reference_steps else steps
                run_text = generate_inputs.scr_run(run_steps, dt_fs, tdamp, tau)
                stage = input_root / profile / str(steps) / f"seed-{seed}"
                stage.mkdir(parents=True)
                (stage / "model.xyz").write_text(model)
                (stage / "run.in").write_text(run_text)
                (stage / "nep.txt").write_bytes(potential_source.read_bytes())
                for file in sorted(stage.iterdir()):
                    inputs[file.relative_to(input_root).as_posix()] = sha(file)
    short_run = generate_inputs.scr_run(20, .25, 40, 200)
    for seed in fixture["seeds"]:
        short = input_root / "short" / f"seed-{seed}"
        short.mkdir(parents=True)
        (short / "model.xyz").write_text(generate_inputs.model_text((3, 3, 3), seed))
        (short / "run.in").write_text(short_run)
        (short / "nep.txt").write_bytes(potential_source.read_bytes())
        for file in sorted(short.iterdir()):
            inputs[file.relative_to(input_root).as_posix()] = sha(file)
    zero = input_root / "short" / "zero-ke"
    zero.mkdir(parents=True)
    (zero / "model.xyz").write_text(generate_inputs.zero_ke_model())
    (zero / "run.in").write_text(short_run)
    (zero / "nep.txt").write_bytes(potential_source.read_bytes())
    for file in sorted(zero.iterdir()):
        inputs[file.relative_to(input_root).as_posix()] = sha(file)

    before = run_npt_ber.reference_status_snapshot()
    expected_heads = {
        "gpumd-reference": "9d23496e41319b9e2af5221a7df6285387401d1e",
        "lammps-reference": "611ca3b7f8525ba802373b04f9e3d632d515f7e3",
        "lammps-matpl-reference": "21fabf34eadc57a717f4c7fbce9bb80aad5fd991",
    }
    for name, head in expected_heads.items():
        if before[name]["head"] != head or before[name]["tracked_status"]:
            raise SystemExit(f"reference checkout changed: {name}: {before[name]}")

    sym = capture(["nm", "-C", str(reference)])
    if "Ensemble_NPT_SCR::initialize_rng()" not in sym:
        raise SystemExit("pinned GPUMD binary lacks SCR initialize_rng symbol")
    disasm = capture(["objdump", "-d", "-C", "--disassemble=Ensemble_NPT_SCR::initialize_rng()", str(reference)])
    if "system_clock::now()" not in disasm:
        raise SystemExit("pinned GPUMD SCR RNG binary does not show system_clock::now call")
    src = ROOT.parent / "gpumd-reference/src/integrate/ensemble_npt_scr.cu"
    source_text = src.read_text()
    if "std::chrono::system_clock::now().time_since_epoch().count()" not in source_text:
        raise SystemExit("pinned GPUMD source RNG contract differs")

    scripts = ["tests/npt/freeze_b3.py", "tests/npt/generate_inputs.py",
               "tests/npt/run_npt_scr.py", "tests/npt/build_records.py",
               "tests/npt/replay_b2_golden.py", "tests/npt/run_npt_ber.py",
               "tests/npt/manifest.json", "tests/npt/analyze_statistics.py",
               "tests/npt/test_statistics.py", "tests/thermostats/sample_statistics.py",
               "tests/thermostats/autocorrelation.cpp", "tests/baseline/manifest.json",
               "tests/mpi/check_environment.py"]
    payload = {
        "schema": "dmgmd.npt-b3-freeze.v1",
        "frozen_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "manifest_sha256": sha(manifest_path),
        "candidate_path": str(candidate), "candidate_sha256": sha(candidate),
        "reference_path": str(reference), "reference_sha256": sha(reference),
        "reference_scr_rng": {
            "source_path": str(src), "source_sha256": sha(src),
            "source_contract": "system_clock::now().time_since_epoch().count() in release branch",
            "nm_output": sym, "objdump_output": disasm,
            "binary_call_verified": True,
        },
        "potential_path": str(potential_source), "potential_sha256": sha(potential_source),
        "baseline_manifest_sha256": sha(ROOT / "tests/baseline/manifest.json"),
        "analysis_helper": {"path": str(a.acf_helper.resolve()), "sha256": sha(a.acf_helper.resolve()),
                            "source_path": str(ROOT / "tests/thermostats/autocorrelation.cpp"),
                            "source_sha256": sha(ROOT / "tests/thermostats/autocorrelation.cpp")},
        "generated_input_root": str(input_root), "generated_inputs_sha256": inputs,
        "script_sha256": {name: sha(ROOT / name) for name in scripts},
        "reference_status_before": before,
        "reference_status_before_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        "build_log": str(a.build_log.resolve()), "build_log_sha256": sha(a.build_log.resolve()),
        "scientific_parameters_frozen": True,
        "formal_results_seen_before_freeze": False,
        "utc_second_serialization_isolation_only": True,
    }
    freeze_path.parent.mkdir(parents=True, exist_ok=True)
    freeze_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"freeze": str(freeze_path), "freeze_sha256": sha(freeze_path),
                      "candidate_sha256": payload["candidate_sha256"],
                      "reference_sha256": payload["reference_sha256"],
                      "input_files": len(inputs), "manifest_sha256": payload["manifest_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
