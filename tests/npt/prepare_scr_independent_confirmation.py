#!/usr/bin/env python3
"""CPU-only dry-run preparation from the single audit-owned manifest."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import generate_inputs
import run_npt_ber


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def registered_jobs(manifest: dict, input_root: Path, output_root: Path,
                    input_hashes: dict[str, str]) -> list[dict]:
    reg = manifest["confirmation_registration"]
    jobs: list[dict] = []
    for profile_spec in reg["primary_profiles"]:
        cohort = profile_spec["cohort"]
        profile = "base" if cohort == "primary_base" else "half"
        steps = profile_spec["steps"]
        ranks = profile_spec["candidate"]["ranks"]
        backend = profile_spec["candidate"]["backend"]
        for engine in ("candidate", "reference"):
            for seed in profile_spec["seed_set"]:
                key = f"{cohort}|{engine}|P1|HostStaged|seed{seed}|steps{steps}"
                source = input_root / profile / str(steps) / f"seed-{seed}"
                relative = source.relative_to(input_root).as_posix()
                job_hashes = {name: input_hashes[f"{relative}/{name}"]
                              for name in ("model.xyz", "run.in", "nep.txt")}
                result_root = output_root / "results" / key.replace("|", "__")
                jobs.append({"key": key, "cohort": cohort, "profile": profile,
                             "engine": engine, "ranks": 1, "backend": "HostStaged",
                             "seed": seed, "actual_steps": steps, "status": "planned",
                             "input_source_dir": str(source), "input_sha256": job_hashes,
                             "result_root": str(result_root),
                             "result_file": str(result_root / "results.json")})
    bridge = reg["bridge_profile"]
    for condition in bridge["candidate_conditions"]:
        for seed in bridge["seed_set"]:
            ranks, backend, steps = condition["ranks"], condition["backend"], bridge["steps"]
            key = f"bridge_base|candidate|P{ranks}|{backend}|seed{seed}|steps{steps}"
            source = input_root / "base" / str(steps) / f"seed-{seed}"
            relative = source.relative_to(input_root).as_posix()
            job_hashes = {name: input_hashes[f"{relative}/{name}"]
                          for name in ("model.xyz", "run.in", "nep.txt")}
            result_root = output_root / "results" / key.replace("|", "__")
            jobs.append({"key": key, "cohort": "bridge_base", "profile": "base",
                         "engine": "candidate", "ranks": ranks, "backend": backend,
                         "seed": seed, "actual_steps": steps,
                         "input_source_dir": str(source), "input_sha256": job_hashes,
                         "result_root": str(result_root),
                         "result_file": str(result_root / "results.json"),
                         "anchor_key": f"primary_base|candidate|P1|HostStaged|seed{seed}|steps{steps}",
                         "status": "planned"})
    keys = [job["key"] for job in jobs]
    expected = manifest["budget"]["total_independent_full_chains"]
    if len(jobs) != expected or len(keys) != len(set(keys)):
        raise RuntimeError(f"expected {expected} unique jobs, got {len(jobs)}")
    return jobs


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, default=HERE / "independent-confirmation-manifest.json")
    p.add_argument("--output-root", type=Path, required=True,
                   help="fresh, nonexistent directory; command only prepares a CPU dry-run")
    p.add_argument("--dry-run", action="store_true", required=True)
    a = p.parse_args()
    manifest_path = a.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "dmgmd.npt-scr-independent-confirmation.preregistration.v1":
        raise SystemExit("unexpected audit-owned independent confirmation manifest schema")
    state = (manifest.get("status"), manifest.get("sampling_authorized"))
    if state not in {
            ("DRAFT_FOR_ROOT_REVIEW_NO_SAMPLING_AUTHORIZED", False),
            ("APPROVED_INDEPENDENT_CONFIRMATION", True)}:
        raise SystemExit(f"unrecognized registration state: {state!r}")
    output = a.output_root.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite output root: {output}")
    identity = manifest["confirmation_registration"]["analysis_software_identity"]
    candidate = Path(identity["candidate_binary"]["path"])
    reference = Path(identity["reference_binary"]["path"])
    potential = (ROOT / manifest["source_contract"]["potential_path"]).resolve()
    for name, path, expected in (("candidate", candidate, identity["candidate_binary"]["sha256"]),
                                 ("reference", reference, identity["reference_binary"]["sha256"]),
                                 ("potential", potential, manifest["source_contract"]["potential_sha256"])):
        if not path.is_file() or sha(path) != expected:
            raise SystemExit(f"{name} missing or SHA differs from audit-owned manifest: {path}")

    input_root = output / "registered-inputs"
    input_root.mkdir(parents=True)
    inputs: dict[str, str] = {}
    potential_bytes = potential.read_bytes()
    fixture = manifest["fixtures"]["statistics"]
    lock = manifest["input_locking"]
    expected_models = lock["confirmation_model_xyz_sha256_by_seed"]
    profiles = {"base": manifest["confirmation_registration"]["primary_profiles"][0],
                "half": manifest["confirmation_registration"]["primary_profiles"][1]}
    expected_runs = lock["confirmation_run_in_sha256"]
    run_keys = {"base": "base_dt_0.25_160000", "half": "half_dt_0.125_320000"}
    for profile, spec in profiles.items():
        steps, dt_fs = spec["steps"], spec["dt_fs"]
        for seed in spec["seed_set"]:
            stage = input_root / profile / str(steps) / f"seed-{seed}"
            stage.mkdir(parents=True)
            model = generate_inputs.model_text(tuple(fixture["cells"]), seed, scale=1.0023)
            model_sha = generate_inputs.sha256_bytes(model.encode())
            if model_sha != expected_models[str(seed)]:
                raise SystemExit(f"seed {seed} model hash differs from audit-owned registration")
            run = generate_inputs.scr_run(steps, dt_fs, spec["tdamp_steps"], spec["tau_p_steps"])
            run_sha = generate_inputs.sha256_bytes(run.encode())
            if run_sha != expected_runs[run_keys[profile]]:
                raise SystemExit(f"{profile} run.in hash differs from audit-owned registration")
            (stage / "model.xyz").write_text(model)
            (stage / "run.in").write_text(run)
            (stage / "nep.txt").write_bytes(potential_bytes)
            for file in sorted(stage.iterdir()):
                inputs[file.relative_to(input_root).as_posix()] = sha(file)

    jobs = registered_jobs(manifest, input_root, output, inputs)
    ref_state = run_npt_ber.reference_status_snapshot()
    is_approved = state[1] is True
    ledger = {"schema": "dmgmd.npt-scr-independent-confirmation-ledger.v1",
              "protocol_status": manifest["status"], "sampling_authorized": is_approved,
              "manifest_sha256": sha(manifest_path), "candidate_sha256": sha(candidate),
              "reference_sha256": sha(reference), "input_root": str(input_root),
              "input_inventory_sha256": inputs, "jobs": jobs}
    write_json(output / "execution-ledger.draft.json", ledger)
    counts = {name: sum(job["cohort"] == name for job in jobs)
              for name in ("primary_base", "primary_half", "bridge_base")}
    plan = {"schema": "dmgmd.npt-scr-independent-confirmation-dry-run.v1",
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "manifest_path": str(manifest_path), "manifest_sha256": sha(manifest_path),
            "protocol_status": manifest["status"], "sampling_authorized": is_approved,
            "input_root": str(input_root), "input_file_count": len(inputs),
            "input_inventory_sha256": inputs, "candidate_sha256": sha(candidate),
            "reference_sha256": sha(reference), "potential_sha256": sha(potential),
            "reference_checkout_snapshot": ref_state,
            "reference_checkout_snapshot_sha256": hashlib.sha256(
                json.dumps(ref_state, sort_keys=True).encode()).hexdigest(),
            "job_count": len(jobs), "cohort_counts": counts, "jobs": jobs,
            "note": "CPU-only planning artifact. No executable was launched; no GPU data was sampled."}
    write_json(output / "dry-run-plan.json", plan)
    print(json.dumps({"output_root": str(output), "plan": str(output / "dry-run-plan.json"),
                      "plan_sha256": sha(output / "dry-run-plan.json"),
                      "ledger": str(output / "execution-ledger.draft.json"),
                      "ledger_sha256": sha(output / "execution-ledger.draft.json"),
                      "input_files": len(inputs), "jobs": len(jobs), "cohort_counts": counts,
                      "sampling_authorized": is_approved}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
