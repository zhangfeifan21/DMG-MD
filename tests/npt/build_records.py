#!/usr/bin/env python3
"""Assemble runner evidence into the frozen analyzer input schema."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEEDS = [101, 102, 103, 104, 105, 106, 107, 108]
UNITS = {"temperature": "K", "pressure": "GPa", "volume": "angstrom^3", "kinetic_energy": "eV"}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def records_from(paths: list[Path]) -> dict[tuple[str, int], dict]:
    result = {}
    for path in paths:
        data = json.loads(path.read_text())
        for run in data["records"]:
            key = (run.get("engine"), run.get("seed"))
            if key in result:
                raise SystemExit(f"duplicate runner record: {key}")
            result[key] = run
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate-results", type=Path, action="append", required=True)
    p.add_argument("--reference-results", type=Path, action="append", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--look-steps", type=int, required=True)
    p.add_argument("--dt-fs", type=float, required=True)
    p.add_argument("--ranks", type=int, required=True)
    p.add_argument("--backend", choices=("HostStaged", "CudaAware"), required=True)
    a = p.parse_args()
    candidate_paths = [x.resolve() for x in a.candidate_results]
    reference_paths = [x.resolve() for x in a.reference_results]
    candidate, reference = records_from(candidate_paths), records_from(reference_paths)
    expected_candidate = {("candidate", seed) for seed in SEEDS}
    expected_reference = {("reference", seed) for seed in SEEDS}
    if set(candidate) != expected_candidate or set(reference) != expected_reference:
        raise SystemExit("candidate/reference records must each contain exactly seeds 101..108")
    runs = []
    for seed in SEEDS:
        for engine, source in (("candidate", candidate[("candidate", seed)]),
                               ("reference", reference[("reference", seed)])):
            if source.get("status") != "PASS" or "thermo_path" not in source:
                raise SystemExit(f"{engine} seed {seed} is not a successful complete run")
            paths = source["input_paths"]
            hashes = source["input_sha256"]
            if set(paths) != {"model.xyz", "run.in", "nep.txt"} or set(hashes) != set(paths):
                raise SystemExit(f"bad input mapping for {engine} seed {seed}")
            actual = {name: sha(Path(paths[name])) for name in paths}
            if actual != hashes:
                raise SystemExit(f"input hash mismatch for {engine} seed {seed}")
            thermo = Path(source["thermo_path"])
            if not thermo.is_file() or sha(thermo) != source["thermo_sha256"]:
                raise SystemExit(f"thermo hash mismatch for {engine} seed {seed}")
            if source["steps"] < a.look_steps:
                raise SystemExit(f"{engine} seed {seed} has fewer than look steps")
            item = {key: source[key] for key in (
                "engine", "seed", "steps", "status", "input_sha256", "input_paths",
                "executable_sha256", "thermo_path", "thermo_sha256", "started_at_utc",
                "ended_at_utc", "pid")}
            item["ranks"] = source["ranks"]
            item["backend"] = source["backend"]
            if engine == "candidate":
                if item["ranks"] != a.ranks or item["backend"] != a.backend:
                    raise SystemExit(f"candidate condition mismatch seed {seed}")
                item["ensemble_seed"] = source.get("ensemble_seed")
                if item["ensemble_seed"] != str(seed):
                    raise SystemExit(f"candidate seed env mismatch seed {seed}")
            else:
                if "rng_initialization_evidence" not in source:
                    raise SystemExit(f"missing reference RNG evidence seed {seed}")
                item["rng_initialization_evidence"] = source["rng_initialization_evidence"]
            runs.append(item)
    output = {
        "schema": "dmgmd.npt-scr-statistics-input.v1",
        "look_steps": a.look_steps, "dt_fs": a.dt_fs, "units": UNITS,
        "ranks": a.ranks, "backend": a.backend,
        "candidate_results_sha256": {str(x): sha(x) for x in candidate_paths},
        "reference_results_sha256": {str(x): sha(x) for x in reference_paths},
        "runs": runs,
    }
    a.output.resolve().write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"records": str(a.output.resolve()), "sha256": sha(a.output.resolve()),
                      "runs": len(runs), "reference_launch_seconds": [
                          int(dt.datetime.fromisoformat(x["started_at_utc"].replace("Z", "+00:00")).timestamp())
                          for x in runs if x["engine"] == "reference"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
