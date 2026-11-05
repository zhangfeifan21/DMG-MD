#!/usr/bin/env python3
"""Replay the locked Ber 200-step GPUMD golden on the final B3 candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tests/mpi"))
import check_environment
import generate_inputs
import run_npt_ber


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=ROOT.parent / "gpumd-reference/src/gpumd")
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--devices", default="4,5,6,7")
    parser.add_argument("--work-root", type=Path, required=True)
    a = parser.parse_args()
    a.candidate, a.reference, a.freeze = a.candidate.resolve(), a.reference.resolve(), a.freeze.resolve()
    freeze = json.loads(a.freeze.read_text())
    if run_npt_ber.sha(a.candidate) != freeze["candidate_sha256"]:
        raise RuntimeError("candidate SHA differs from B3 freeze")
    if run_npt_ber.sha(a.reference) != freeze["reference_sha256"]:
        raise RuntimeError("GPUMD SHA differs from B3 freeze")
    a.devices = a.devices.split(",")
    runner = run_npt_ber.Runner(a, json.loads((HERE / "manifest.json").read_text()), freeze)
    runner.mpiexec = check_environment.validate_environment(
        a.candidate, "mpiexec", a.devices, 4, 180)
    results = []
    for name, cells, seed in (("216", (3, 3, 3), 101), ("2016", (7, 6, 6), 101)):
        model = generate_inputs.model_text(cells, seed)
        run_text = generate_inputs.ber_run(200, trajectory_interval=20, restart_interval=200)
        ref, _ = runner.execute(f"b3-final-ber-{name}-reference", "reference", model, run_text)
        cand, _ = runner.execute(f"b3-final-ber-{name}-candidate", "candidate", model, run_text,
                                 ranks=1, backend="HostStaged", devices=[a.devices[0]])
        runner.ensure_success_outputs(ref, ("thermo.out", "trajectory.xyz", "restart.xyz"))
        runner.ensure_success_outputs(cand, ("thermo.out", "trajectory.xyz", "restart.xyz"))
        diff = runner.compare(ref, cand, f"final-b3-ber-{name}")
        results.append({"fixture": name, "reference": str(ref), "candidate": str(cand),
                        "comparison": diff})
    print(json.dumps({"results": str(runner.result_path), "candidate_sha256": freeze["candidate_sha256"],
                      "fixtures": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
