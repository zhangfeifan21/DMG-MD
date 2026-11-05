#!/usr/bin/env python3
"""Strict analyzer for the independently preregistered SCR MPI confirmation.

Formal analysis requires an approved registration. The input bundle is assembled
from immutable runner results; no original B3 records are rewritten or pooled.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import datetime as dt
import hashlib
import importlib.util
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_MANIFEST = HERE / "independent-confirmation-manifest.json"
DEFAULT_PROTOCOL = HERE / "independent-confirmation-protocol.md"
METRICS = ("temperature_K", "pressure_GPa", "volume_A3")
UNITS = {"temperature": "K", "pressure": "GPa", "volume": "angstrom^3",
         "kinetic_energy": "eV"}
BOOTSTRAP_REPLICATES = 20_000
BOOTSTRAP_SEED = 2026110301
INPUT_SCHEMA = "dmgmd.npt-scr-independent-confirmation-input.v1"
_FORK_ANALYSIS_CONTEXT: tuple[dict, dict] | None = None


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load analysis dependency: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PRIMARY = _load_module("dmgmd_original_npt_statistics", HERE / "analyze_statistics.py")
_MATH = _load_module("dmgmd_paired_confirmation_math", HERE / "paired_confirmation_math.py")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _require_hash(label: str, path: Path, expected: str) -> None:
    if not path.is_file():
        raise ValueError(f"{label} file is missing: {path}")
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def require_global_reference_seconds(seconds: list[int]) -> None:
    if len(seconds) != 64 or len(seconds) != len(set(seconds)):
        raise ValueError("all 64 reference launches must have globally distinct UTC start seconds")


def _identity(manifest: dict, key: str) -> dict:
    try:
        return manifest["confirmation_registration"]["analysis_software_identity"][key]
    except KeyError as error:
        raise ValueError(f"manifest is missing analysis identity {key}") from error


def require_approved_registration(manifest: dict) -> None:
    accepted_status = "APPROVED_INDEPENDENT_CONFIRMATION"
    if manifest.get("sampling_authorized") is not True or \
            manifest.get("status") != accepted_status:
        raise ValueError("independent confirmation manifest is not approved for formal analysis")
    if manifest.get("confirmation_registration", {}).get("status") != accepted_status:
        raise ValueError("confirmation registration is not approved")


def validate_software_identity(manifest: dict) -> dict[str, str]:
    """Check the analyzer and every imported source helper against registration."""
    source_files = {
        "primary_analyzer": ROOT / "tests/npt/analyze_statistics.py",
        "shared_statistics_primitives": ROOT / "tests/thermostats/sample_statistics.py",
        "native_acf_helper": ROOT / "tests/thermostats/autocorrelation.cpp",
        "paired_math_draft": HERE / "paired_confirmation_math.py",
        "paired_math_tests_draft": HERE / "test_paired_confirmation_math.py",
    }
    expected_cli = _identity(manifest, "paired_cli")
    relative_cli = Path(__file__).resolve().relative_to(ROOT).as_posix()
    actual_cli = sha256(Path(__file__).resolve())
    if expected_cli.get("path") != relative_cli or expected_cli.get("sha256") != actual_cli:
        raise ValueError("paired analyzer identity differs from the manifest")
    integration_test = ROOT / "tests/npt/test_paired_confirmation_integration.py"
    expected_test = _identity(manifest, "paired_integration_tests")
    if expected_test.get("path") != integration_test.relative_to(ROOT).as_posix() or \
            expected_test.get("sha256") != sha256(integration_test):
        raise ValueError("paired integration-test identity differs from the manifest")
    identities = {"paired_cli": actual_cli,
                  "paired_integration_tests": sha256(integration_test)}
    for key, path in source_files.items():
        expected = _identity(manifest, key)
        actual = sha256(path)
        if expected.get("sha256") != actual:
            raise ValueError(f"analysis dependency hash mismatch for {key}")
        identities[key] = actual
    helper = os.environ.get("DMGMD_ACF_HELPER")
    if not helper or not Path(helper).is_file() or not os.access(helper, os.X_OK):
        raise ValueError("DMGMD_ACF_HELPER must name the frozen executable for formal ESS analysis")
    expected_helper = _identity(manifest, "native_acf_helper_executable")
    relative_helper = Path(expected_helper.get("path", ""))
    actual_helper_path = Path(helper).resolve()
    if not relative_helper.is_absolute():
        expected_helper_path = (ROOT / relative_helper).resolve()
    else:
        expected_helper_path = relative_helper.resolve()
    if actual_helper_path != expected_helper_path:
        raise ValueError("DMGMD_ACF_HELPER path differs from the registered executable")
    actual_helper_sha = sha256(actual_helper_path)
    if expected_helper.get("sha256") != actual_helper_sha:
        raise ValueError("native ACF executable hash differs from the manifest")
    identities["acf_helper_executable"] = actual_helper_sha
    return identities


def validate_run_identity(
    run: dict, *, expected: dict[str, Any], base: Path, manifest: dict,
    context: str,
) -> tuple[Path, Path]:
    """Validate one run's immutable inputs, executable, metadata, and thermo bytes."""
    for key, value in expected.items():
        if key in ("model_sha256", "run_sha256"):
            continue
        if run.get(key) != value:
            raise ValueError(f"{context}: {key} mismatch (expected {value!r}, got {run.get(key)!r})")
    if run.get("status") != "PASS":
        raise ValueError(f"{context}: run status is not PASS")
    if run.get("units") != UNITS:
        raise ValueError(f"{context}: units do not match the registered output units")
    if run.get("steps") != expected["steps"]:
        raise ValueError(f"{context}: actual steps do not match the registered full chain")
    try:
        dt_fs = float(run.get("dt_fs"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context}: invalid dt_fs") from error
    if not math.isfinite(dt_fs) or not math.isclose(
            dt_fs, expected["dt_fs"], rel_tol=0, abs_tol=1e-12):
        raise ValueError(f"{context}: dt_fs does not match the registered profile")
    if run.get("executable_sha256") != expected["executable_sha256"]:
        raise ValueError(f"{context}: executable identity mismatch")
    executable_path = _resolve(base, run.get("executable_path", ""))
    _require_hash(f"{context} executable", executable_path, expected["executable_sha256"])

    expected_inputs = {
        "model.xyz": expected["model_sha256"],
        "run.in": expected["run_sha256"],
        "nep.txt": manifest["source_contract"]["potential_sha256"],
    }
    if run.get("input_sha256") != expected_inputs:
        raise ValueError(f"{context}: input SHA map differs from the registered model/run/potential")
    paths = run.get("input_paths")
    if not isinstance(paths, dict) or set(paths) != set(expected_inputs):
        raise ValueError(f"{context}: input_paths must identify model.xyz, run.in and nep.txt")
    for name, digest in expected_inputs.items():
        _require_hash(f"{context} input {name}", _resolve(base, paths[name]), digest)

    thermo_path = _resolve(base, run.get("thermo_path", ""))
    if not thermo_path.is_file() or run.get("thermo_sha256") != sha256(thermo_path):
        raise ValueError(f"{context}: thermo output is missing or its SHA-256 mismatches")
    return executable_path, thermo_path


def _profile_map(manifest: dict) -> dict[str, dict]:
    registration = manifest["confirmation_registration"]
    result = {item["cohort"]: item for item in registration["primary_profiles"]}
    bridge = dict(registration["bridge_profile"])
    base = result["primary_base"]
    for key in ("burn_fraction", "block_steps", "block_fs"):
        bridge.setdefault(key, base[key])
    result[bridge["cohort"]] = bridge
    return result


def _same_inputs(left: dict, right: dict, cohort: str, seed: int) -> None:
    if left["input_sha256"] != right["input_sha256"]:
        raise ValueError(f"{cohort} seed {seed}: paired records do not share byte-identical inputs")


def load_bundle(records_path: Path, manifest_path: Path,
                protocol_path: Path) -> tuple[dict, dict, dict, dict]:
    """Load and validate the approved one-look, 160-chain records bundle."""
    records_path = records_path.resolve()
    manifest_path, protocol_path = manifest_path.resolve(), protocol_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    protocol_text = protocol_path.read_text(encoding="utf-8")
    bundle = json.loads(records_path.read_text(encoding="utf-8"))
    if bundle.get("schema") != INPUT_SCHEMA:
        raise ValueError(f"records schema must be {INPUT_SCHEMA}")
    if bundle.get("manifest_sha256") != sha256(manifest_path):
        raise ValueError("records manifest SHA-256 mismatch")
    if bundle.get("protocol_sha256") != sha256(protocol_path):
        raise ValueError("records protocol SHA-256 mismatch")
    require_approved_registration(manifest)
    if not protocol_text.strip():
        raise ValueError("independent confirmation protocol is empty")
    software_identity = validate_software_identity(manifest)

    identities = manifest["confirmation_registration"]["analysis_software_identity"]
    expected_top = {
        "candidate_executable_sha256": identities["candidate_binary"]["sha256"],
        "reference_executable_sha256": identities["reference_binary"]["sha256"],
        "candidate_source_digest_sha256": identities["candidate_source_digest"]["sha256"],
        "potential_sha256": manifest["source_contract"]["potential_sha256"],
    }
    for key, value in expected_top.items():
        if bundle.get(key) != value:
            raise ValueError(f"bundle {key} differs from the approved manifest")
    rng_audit = bundle.get("bridge_rng_source_audit")
    expected_rng = identities.get("bridge_rng_source_audit")
    if not isinstance(rng_audit, dict) or rng_audit.get("status") != "PASS" or not expected_rng:
        raise ValueError("registered bridge RNG source audit is missing or not PASS")
    if rng_audit.get("sha256") != expected_rng.get("sha256") or \
            not _valid_sha(rng_audit.get("sha256")):
        raise ValueError("bridge RNG source audit identity differs from the manifest")
    _require_hash("bridge RNG source audit", _resolve(manifest_path.parent, rng_audit.get("path", "")),
                  expected_rng["sha256"])

    profiles = _profile_map(manifest)
    seed_models = manifest["input_locking"]["confirmation_model_xyz_sha256_by_seed"]
    run_hashes = manifest["input_locking"]["confirmation_run_in_sha256"]
    atoms = manifest["fixtures"]["statistics"]["atoms"]
    raw_runs = bundle.get("runs")
    if not isinstance(raw_runs, list) or len(raw_runs) != 160:
        raise ValueError("records must contain exactly 160 independent full-length chains")

    expected_keys = set()
    for cohort in ("primary_base", "primary_half"):
        profile = profiles[cohort]
        for engine in ("candidate", "reference"):
            ranks = profile["candidate"]["ranks"] if engine == "candidate" else 1
            backend = profile["candidate"]["backend"] if engine == "candidate" else "HostStaged"
            for seed in profile["seed_set"]:
                expected_keys.add((cohort, engine, seed, ranks, backend))
    bridge = profiles["bridge_base"]
    for condition in bridge["candidate_conditions"]:
        for seed in bridge["seed_set"]:
            expected_keys.add(("bridge_base", "candidate", seed,
                               condition["ranks"], condition["backend"]))

    keyed: dict[tuple, dict] = {}
    series: dict[tuple, dict[str, list[float]]] = {}
    executable_hashes = {"candidate": set(), "reference": set()}
    reference_seconds: list[int] = []
    for index, run in enumerate(raw_runs):
        if not isinstance(run, dict):
            raise ValueError(f"run record {index} is not an object")
        key = (run.get("cohort"), run.get("engine"), run.get("seed"),
               run.get("ranks"), run.get("backend"))
        if key not in expected_keys or key in keyed:
            raise ValueError(f"duplicate or unregistered run identity: {key!r}")
        cohort, engine, seed, ranks, backend = key
        profile = profiles[cohort]
        expected_steps, expected_dt = profile["steps"], profile["dt_fs"]
        binary_sha = expected_top[
            "candidate_executable_sha256" if engine == "candidate"
            else "reference_executable_sha256"]
        expected = {"cohort": cohort, "engine": engine, "seed": seed, "ranks": ranks,
                    "backend": backend, "steps": expected_steps, "dt_fs": expected_dt,
                    "executable_sha256": binary_sha,
                    "model_sha256": seed_models[str(seed)],
                    "run_sha256": run_hashes[
                        "base_dt_0.25_160000" if cohort != "primary_half"
                        else "half_dt_0.125_320000"]}
        if engine == "candidate":
            expected["ensemble_seed"] = str(seed)
            if cohort == "bridge_base":
                draw_evidence = run.get("rng_draw_evidence")
                if not isinstance(draw_evidence, dict) or \
                        draw_evidence.get("status") != "PASS" or \
                        draw_evidence.get("source_audit_sha256") != expected_rng.get("sha256"):
                    raise ValueError(f"{key!r}: bridge record lacks registered same-stream RNG evidence")
        elif cohort == "bridge_base":
            raise ValueError("bridge profile must contain only candidate MPI runs")
        _, thermo_path = validate_run_identity(
            run, expected=expected, base=records_path.parent, manifest=manifest,
            context=f"{key!r}")
        executable_hashes[engine].add(binary_sha)
        if engine == "candidate":
            route_lines = run.get("route_lines", [])
            route_text = "\n".join(route_lines) if isinstance(route_lines, list) else str(route_lines)
            if "mode=m1" not in route_text or "reason=npt-m1-only" not in route_text:
                raise ValueError(f"{key!r}: missing explicit NPT M1 route evidence")
        if engine == "reference":
            if "system_clock" not in str(run.get("rng_initialization_evidence", "")):
                raise ValueError(f"{key!r}: missing independent GPUMD RNG evidence")
            try:
                started = dt.datetime.fromisoformat(run["started_at_utc"].replace("Z", "+00:00"))
            except (KeyError, AttributeError, ValueError) as error:
                raise ValueError(f"{key!r}: invalid reference start timestamp") from error
            if started.tzinfo is None or started.utcoffset() != dt.timedelta(0):
                raise ValueError(f"{key!r}: reference timestamp must be UTC")
            try:
                ended = dt.datetime.fromisoformat(run["ended_at_utc"].replace("Z", "+00:00"))
                pid = int(run["pid"])
            except (KeyError, AttributeError, TypeError, ValueError) as error:
                raise ValueError(f"{key!r}: invalid reference end timestamp or PID") from error
            if ended.tzinfo is None or ended.utcoffset() != dt.timedelta(0) or ended < started or pid <= 0:
                raise ValueError(f"{key!r}: invalid reference end timestamp or PID")
            reference_seconds.append(int(started.timestamp()))
        else:
            try:
                started = dt.datetime.fromisoformat(run["started_at_utc"].replace("Z", "+00:00"))
                ended = dt.datetime.fromisoformat(run["ended_at_utc"].replace("Z", "+00:00"))
                pid = int(run["pid"])
            except (KeyError, AttributeError, TypeError, ValueError) as error:
                raise ValueError(f"{key!r}: invalid candidate timestamps or PID") from error
            if started.tzinfo is None or started.utcoffset() != dt.timedelta(0) or \
                    ended.tzinfo is None or ended.utcoffset() != dt.timedelta(0) or \
                    ended < started or pid <= 0:
                raise ValueError(f"{key!r}: invalid candidate timestamps or PID")
        thermo = _PRIMARY._thermo_rows(
            thermo_path, expected_steps, atoms, expected_dt)
        trim = math.ceil(expected_steps * profile["burn_fraction"])
        values = {metric: chain[trim:] for metric, chain in thermo.items()}
        for metric, chain in values.items():
            if len(chain) % profile["block_steps"]:
                raise ValueError(f"{key!r} {metric}: burn-trimmed samples leave a partial block")
            if len(chain) // profile["block_steps"] < \
                    manifest["confirmation_registration"]["precision_gates"]["complete_blocks_per_seed"]:
                raise ValueError(f"{key!r} {metric}: fewer than registered complete blocks")
        keyed[key], series[key] = run, values
    if set(keyed) != expected_keys:
        raise ValueError(f"missing registered run identities: {sorted(expected_keys - set(keyed))[:5]!r}")
    if len(executable_hashes["candidate"]) != 1 or len(executable_hashes["reference"]) != 1:
        raise ValueError("executable identity changes within an engine's registered chains")
    require_global_reference_seconds(reference_seconds)
    for cohort in ("primary_base", "primary_half"):
        for seed in profiles[cohort]["seed_set"]:
            _same_inputs(keyed[(cohort, "candidate", seed, 1, "HostStaged")],
                         keyed[(cohort, "reference", seed, 1, "HostStaged")], cohort, seed)
    for condition in bridge["candidate_conditions"]:
        for seed in bridge["seed_set"]:
            _same_inputs(keyed[("bridge_base", "candidate", seed,
                                condition["ranks"], condition["backend"])],
                         keyed[("primary_base", "candidate", seed, 1, "HostStaged")],
                         "bridge_base", seed)
    return bundle, manifest, profiles, {
        "runs": keyed, "series": series, "software_identity": software_identity,
        "input_sha256": sha256(records_path)}


def _chains(loaded: dict, cohort: str, engine: str, seeds: list[int],
            metric: str, *, ranks: int = 1, backend: str = "HostStaged") -> dict[int, list[float]]:
    return {seed: loaded["series"][(cohort, engine, seed, ranks, backend)][metric]
            for seed in seeds}


def _ess_summary(chains: dict[int, list[float]]) -> dict:
    pooled = [value for chain in chains.values() for value in chain]
    mean = math.fsum(pooled) / len(pooled)

    def ess(group: dict[int, list[float]]) -> float:
        return math.fsum(
            len(chain) / _PRIMARY._statistics._integrated_autocorrelation_time(
                chain, min(len(chain) // 10, 2048))
            for chain in group.values())

    squared = {seed: [(value - mean) ** 2 for value in chain]
               for seed, chain in chains.items()}
    return {
        "effective_samples_mean_pooled": ess(chains),
        "effective_samples_squared_deviation_pooled": ess(squared),
        "pooled_sample_count": len(pooled),
        "scope": "pooled over seeds for this engine and metric",
    }


def _metric_bounds(metric: str, reference_mean: float, manifest: dict) -> tuple[list[float], list[float]]:
    bounds = manifest["confirmation_registration"]["unchanged_final_bounds"]
    ratio = list(bounds["variance_ratio"])
    if metric == "temperature_K":
        return list(bounds["temperature_mean_K"]), ratio
    if metric == "pressure_GPa":
        return list(bounds["pressure_mean_GPa"]), ratio
    relative = float(bounds["volume_mean_relative"])
    if not math.isfinite(reference_mean) or reference_mean <= 0:
        raise ValueError("volume reference mean must be finite and positive")
    return [-relative * reference_mean, relative * reference_mean], ratio


def _decision(interval: list[float], bounds: list[float], enough: bool) -> str:
    if not enough:
        return "INCONCLUSIVE"
    if interval[0] >= bounds[0] and interval[1] <= bounds[1]:
        return "PASS"
    if interval[1] < bounds[0] or interval[0] > bounds[1]:
        return "FAIL"
    return "INCONCLUSIVE"


def _flatten(chains: dict[int, list[float]]) -> list[float]:
    return [value for seed in sorted(chains) for value in chains[seed]]


def _primary_metric(loaded: dict, profile: dict, cohort: str, metric: str,
                    confidence_z: float) -> dict:
    seeds = profile["seed_set"]
    block_length = profile["block_steps"]
    candidate = _chains(loaded, cohort, "candidate", seeds, metric)
    reference = _chains(loaded, cohort, "reference", seeds, metric)
    candidate_stats = _PRIMARY._mean_ci(list(candidate.values()), block_length, confidence_z)
    reference_stats = _PRIMARY._mean_ci(list(reference.values()), block_length, confidence_z)
    mean_difference = _PRIMARY._paired_difference_ci(
        candidate, reference, block_length, confidence_z)
    cdf_distance = _PRIMARY._ecdf_distance(_flatten(candidate), _flatten(reference))
    return {
        "candidate_chains": candidate,
        "reference_chains": reference,
        "candidate_stats": candidate_stats,
        "reference_stats": reference_stats,
        "mean_difference": mean_difference,
        "cdf_distance": cdf_distance,
        "candidate_ess": _ess_summary(candidate),
        "reference_ess": _ess_summary(reference),
    }


def _variance_job(job: tuple) -> tuple[tuple, dict]:
    """Run one bootstrap component using the fork-inherited read-only data."""
    if _FORK_ANALYSIS_CONTEXT is None:
        raise RuntimeError("variance worker has no shared analysis context")
    loaded, profiles = _FORK_ANALYSIS_CONTEXT
    kind = job[0]
    if kind == "primary":
        _, cohort, metric, replicates = job
        profile = profiles[cohort]
        candidate = _chains(loaded, cohort, "candidate", profile["seed_set"], metric)
        reference = _chains(loaded, cohort, "reference", profile["seed_set"], metric)
        original_replicates = _PRIMARY.BOOTSTRAP_REPLICATES
        _PRIMARY.BOOTSTRAP_REPLICATES = replicates
        try:
            result = _PRIMARY._variance_ratio_ci(
                candidate, reference, profile["block_steps"], profile["block_fs"],
                _FORK_ANALYSIS_CONTEXT[1]["_confidence_z"])
        finally:
            _PRIMARY.BOOTSTRAP_REPLICATES = original_replicates
        return ("primary", cohort, metric), result
    if kind == "bridge":
        _, ranks, backend, metric, replicates, bootstrap_seed = job
        profile = profiles["bridge_base"]
        seeds = profile["seed_set"]
        anchor = _chains(loaded, "primary_base", "candidate", seeds, metric)
        mpi = _chains(loaded, "bridge_base", "candidate", seeds, metric,
                      ranks=ranks, backend=backend)
        z = _FORK_ANALYSIS_CONTEXT[1]["_confidence_z"]
        result = _MATH.paired_global_variance_ratio_ci(
            mpi, anchor, block_length=profile["block_steps"], confidence_z=z,
            bootstrap_replicates=replicates, bootstrap_seed=bootstrap_seed)
        return ("bridge", ranks, backend, metric), result
    raise ValueError(f"unknown variance job: {kind}")


def _compute_variance_jobs(jobs: list[tuple], loaded: dict, profiles: dict,
                           confidence_z: float, workers: int) -> dict[tuple, dict]:
    if not isinstance(workers, int) or not 1 <= workers <= 4:
        raise ValueError("workers must be an integer from 1 to 4")
    global _FORK_ANALYSIS_CONTEXT
    context_profiles = dict(profiles)
    context_profiles["_confidence_z"] = confidence_z
    _FORK_ANALYSIS_CONTEXT = (loaded, context_profiles)
    if workers == 1 or len(jobs) < 2:
        results = map(_variance_job, jobs)
        return {key: value for key, value in results}
    try:
        fork_context = multiprocessing.get_context("fork")
    except ValueError:
        results = map(_variance_job, jobs)
        return {key: value for key, value in results}
    with ProcessPoolExecutor(max_workers=workers, mp_context=fork_context) as pool:
        results = pool.map(_variance_job, jobs, chunksize=1)
        return {key: value for key, value in results}


def _has_precision(primary: dict, anchor: dict[int, list[float]],
                   bridge: dict[int, list[float]], profile: dict,
                   gates: dict) -> bool:
    summaries = (primary["candidate_ess"], primary["reference_ess"],
                 _ess_summary(anchor), _ess_summary(bridge))
    if any(item["effective_samples_mean_pooled"] < gates["pooled_ess_per_engine_metric_mean"] or
           item["effective_samples_squared_deviation_pooled"] <
           gates["pooled_ess_per_engine_metric_squared_deviation"]
           for item in summaries):
        return False
    minimum_blocks = gates["complete_blocks_per_seed"]
    return all(len(chain) // profile["block_steps"] >= minimum_blocks
               for group in (anchor, bridge) for chain in group.values())


def analyze_data(bundle: dict, manifest: dict, profiles: dict, loaded: dict,
                 *, workers: int = 1, bootstrap_replicates: int = BOOTSTRAP_REPLICATES) -> dict:
    registration = manifest["confirmation_registration"]
    confidence = registration["confidence_composition"]
    precision_gates = registration["precision_gates"]
    z = confidence["component_z"]
    seeds32 = profiles["primary_base"]["seed_set"]
    seeds8 = profiles["bridge_base"]["seed_set"]
    report: dict[str, Any] = {
        "schema": "dmgmd.npt-scr-independent-confirmation-report.v1",
        "status": "ANALYZED",
        "records_sha256": loaded["input_sha256"],
        "manifest_sha256": bundle["manifest_sha256"],
        "protocol_sha256": bundle["protocol_sha256"],
        "software_identity": loaded["software_identity"],
        "chain_count": len(loaded["runs"]),
        "independent_chain_counts": {"primary_base": 64, "primary_half": 64,
                                     "bridge_base": 32},
        "confidence": {
            "component_z": z,
            "component_alpha": confidence["component_two_sided_alpha"],
            "bonferroni_sum_alpha": confidence["bonferroni_sum_alpha"],
            "minimum_composite_coverage": confidence["minimum_component_composite_coverage"],
            "no_independence_assumption": True,
        },
        "profiles": {},
        "execution": {"variance_bootstrap_workers": workers,
                      "worker_model": "fork-inherited read-only loaded chains; summaries returned",
                      "bootstrap_replicates_per_component": bootstrap_replicates,
                      "bootstrap_analysis_seed": BOOTSTRAP_SEED},
    }
    primary_cache: dict[tuple[str, str], dict] = {}
    variance_jobs = []
    # Compute means/ESS and retain the loaded chains in the parent. Bootstrap
    # workers inherit this read-only cache by fork and return only summaries.
    for cohort in ("primary_base", "primary_half"):
        profile = profiles[cohort]
        for metric in METRICS:
            primary_cache[(cohort, metric)] = _primary_metric(loaded, profile, cohort, metric, z)
            variance_jobs.append(("primary", cohort, metric, bootstrap_replicates))
    for condition in profiles["bridge_base"]["candidate_conditions"]:
        for metric in METRICS:
            variance_jobs.append(("bridge", condition["ranks"], condition["backend"],
                                  metric, bootstrap_replicates, BOOTSTRAP_SEED))
    variance_results = _compute_variance_jobs(variance_jobs, loaded, profiles, z, workers)
    for cohort in ("primary_base", "primary_half"):
        for metric in METRICS:
            primary_cache[(cohort, metric)]["variance_ratio"] = variance_results[
                ("primary", cohort, metric)]

    # Primary summaries are reused across all four bridge conditions.
    for cohort in ("primary_base", "primary_half"):
        profile = profiles[cohort]
        profile_out = {"dt_fs": profile["dt_fs"], "steps": profile["steps"],
                       "block_length_steps": profile["block_steps"], "primary": {}}
        for metric in METRICS:
            primary = primary_cache[(cohort, metric)]
            ref_mean = primary["reference_stats"]["estimate"]
            mean_bounds, ratio_bounds = _metric_bounds(metric, ref_mean, manifest)
            enough = all(
                summary["effective_samples_mean_pooled"] >=
                precision_gates["pooled_ess_per_engine_metric_mean"] and
                summary["effective_samples_squared_deviation_pooled"] >=
                precision_gates["pooled_ess_per_engine_metric_squared_deviation"]
                for summary in (primary["candidate_ess"], primary["reference_ess"]))
            enough &= all(
                len(chain) // profile["block_steps"] >= precision_gates["complete_blocks_per_seed"]
                for engine in ("candidate_chains", "reference_chains")
                for chain in primary[engine].values())
            mean = dict(primary["mean_difference"])
            mean["allowed_interval"] = mean_bounds
            mean["decision"] = _decision(mean["confidence_interval"], mean_bounds, enough)
            ratio = dict(primary["variance_ratio"])
            ratio["allowed_interval"] = ratio_bounds
            ratio["decision"] = _decision(ratio["confidence_interval"], ratio_bounds, enough)
            reference_sd = math.sqrt(primary["reference_stats"]["sample_variance"])
            if not math.isfinite(reference_sd) or reference_sd <= 0:
                raise ValueError(f"{cohort} {metric}: reference standard deviation must be finite and positive")
            cdf_limit = registration["unchanged_final_bounds"]["volume_ecdf_triangle_sum_max"]
            cdf_decision = "REPORT_ONLY"
            if metric == "volume_A3":
                cdf_decision = ("INCONCLUSIVE" if not enough else
                                "PASS" if primary["cdf_distance"] <= cdf_limit else "FAIL")
            profile_out["primary"][metric] = {
                "candidate": primary["candidate_stats"],
                "reference": primary["reference_stats"],
                "candidate_minus_reference_mean": mean,
                "candidate_over_reference_variance": ratio,
                "empirical_cdf_distance": primary["cdf_distance"],
                "empirical_cdf_diagnostics": _PRIMARY._cdf_uncertainty(
                    primary["candidate_chains"], primary["reference_chains"],
                    profile["block_steps"], z),
                "empirical_cdf_decision": cdf_decision,
                "ess": {"candidate": primary["candidate_ess"],
                        "reference": primary["reference_ess"]},
                "precision_gate": "PASS" if enough else "INCONCLUSIVE",
                "quantiles": {
                    str(q): {
                        "candidate": _PRIMARY._quantile(_flatten(primary["candidate_chains"]), q),
                        "reference": _PRIMARY._quantile(_flatten(primary["reference_chains"]), q),
                        "normalized_candidate_minus_reference": (
                            _PRIMARY._quantile(_flatten(primary["candidate_chains"]), q) -
                            _PRIMARY._quantile(_flatten(primary["reference_chains"]), q)) /
                            reference_sd,
                        "block_seed_uncertainty": _PRIMARY._quantile_difference_ci(
                            primary["candidate_chains"], primary["reference_chains"], q,
                            profile["block_steps"], z),
                    } for q in (0.05, 0.5, 0.95)
                },
            }
        report["profiles"][cohort] = profile_out

        # Preserve the original frozen analyzer's 3N kinetic-energy theory
        # diagnostic. It remains separate from the compatibility decision.
        kinetic = {}
        for engine in ("candidate", "reference"):
            chains = _chains(loaded, cohort, engine, profile["seed_set"], "kinetic_energy_eV")
            flat = _flatten(chains)
            mean = _PRIMARY._mean_ci(list(chains.values()), profile["block_steps"], z)
            variance = _PRIMARY._statistics._sample_variance(flat)
            atoms = manifest["fixtures"]["statistics"]["atoms"]
            kb = manifest["source_contract"]["units"]["boltzmann_eV_per_K"]
            target_mean = 1.5 * atoms * kb * 300.0
            target_variance = 1.5 * atoms * (kb * 300.0) ** 2
            kinetic[engine] = {
                "mean_eV": mean, "mean_target_eV": target_mean,
                "mean_offset_eV": mean["estimate"] - target_mean,
                "variance_eV2": variance, "variance_target_eV2": target_variance,
                "variance_ratio_to_canonical": variance / target_variance,
                "variance_offset_eV2": variance - target_variance,
            }
        atoms = manifest["fixtures"]["statistics"]["atoms"]
        kb = manifest["source_contract"]["units"]["boltzmann_eV_per_K"]
        profile_out["kinetic_energy_theory_diagnostic"] = {
            "status": "THEORY_TARGET_INCONCLUSIVE", "dof": 3 * atoms,
            "target_mean_eV": 1.5 * atoms * kb * 300.0,
            "target_variance_eV2": 1.5 * atoms * (kb * 300.0) ** 2,
            "engines": kinetic,
            "gate_effect": "diagnostic only; does not override compatibility decisions",
        }
        profile_out["temperature_pressure_target_diagnostics"] = {}
        for metric, target in (("temperature_K", 300.0), ("pressure_GPa", 0.0)):
            profile_out["temperature_pressure_target_diagnostics"][metric] = {
                engine: {"mean": profile_out["primary"][metric][engine],
                         "target": target,
                         "offset": profile_out["primary"][metric][engine]["estimate"] - target}
                for engine in ("candidate", "reference")}

    bridge_profile = profiles["bridge_base"]
    bridge_out = {"dt_fs": bridge_profile["dt_fs"], "steps": bridge_profile["steps"],
                  "block_length_steps": bridge_profile["block_steps"], "conditions": {}}
    final_bounds = registration["unchanged_final_bounds"]
    for condition in bridge_profile["candidate_conditions"]:
        ranks, backend = condition["ranks"], condition["backend"]
        name = f"P{ranks}/{backend}"
        result = {"ranks": ranks, "backend": backend, "metrics": {}}
        for metric in METRICS:
            primary = primary_cache[("primary_base", metric)]
            anchor = _chains(loaded, "primary_base", "candidate", seeds8, metric)
            mpi = _chains(loaded, "bridge_base", "candidate", seeds8, metric,
                          ranks=ranks, backend=backend)
            paired_mean = _MATH.paired_mean_difference_ci(
                mpi, anchor, block_length=bridge_profile["block_steps"], confidence_z=z)
            paired_variance = variance_results[("bridge", ranks, backend, metric)]
            mean = _MATH.combine_mean_intervals(primary["mean_difference"], paired_mean)
            variance = _MATH.combine_variance_ratio_intervals(
                primary["variance_ratio"], paired_variance)
            reference_mean = primary["reference_stats"]["estimate"]
            mean_bounds, ratio_bounds = _metric_bounds(metric, reference_mean, manifest)
            enough = _has_precision(primary, anchor, mpi, bridge_profile, precision_gates)
            mean_decision = _decision(mean["confidence_interval"], mean_bounds, enough)
            variance_decision = _decision(variance["confidence_interval"], ratio_bounds, enough)
            cdf = _MATH.cdf_triangle_bound(
                _flatten(mpi), _flatten(anchor),
                _flatten(primary["candidate_chains"]), _flatten(primary["reference_chains"]))
            cdf_decision = "REPORT_ONLY" if metric != "volume_A3" else \
                _MATH.cdf_triangle_decision(
                    cdf["triangle_sum"], final_bounds["volume_ecdf_triangle_sum_max"],
                    enough=enough)
            combined_decision = ("PASS" if mean_decision == variance_decision == "PASS" and
                                 cdf_decision in ("PASS", "REPORT_ONLY") else
                                 "FAIL" if "FAIL" in (mean_decision, variance_decision, cdf_decision)
                                 else "INCONCLUSIVE")
            result["metrics"][metric] = {
                "primary_p1_minus_gpumd": {
                    "mean": primary["mean_difference"],
                    "variance_ratio": primary["variance_ratio"],
                },
                "paired_bridge_mpi_minus_p1": {
                    "mean": paired_mean,
                    "variance_ratio": paired_variance,
                    "pairing": "same initial model/ensemble seed and same time-block index",
                },
                "composite_mean": {**mean, "allowed_interval": mean_bounds,
                                   "decision": mean_decision},
                "composite_variance_ratio": {**variance, "allowed_interval": ratio_bounds,
                                             "decision": variance_decision},
                "cdf_triangle": cdf,
                "cdf_decision": cdf_decision,
                "ess_gate": "PASS" if enough else "INCONCLUSIVE",
                "ess": {
                    "p1_anchor_candidate_full32": primary["candidate_ess"],
                    "p1_anchor_candidate_same8": _ess_summary(anchor),
                    "mpi_candidate_same8": _ess_summary(mpi),
                },
                "decision": combined_decision,
            }
        bridge_out["conditions"][name] = result
    report["profiles"]["bridge_base"] = bridge_out
    outcomes = [
        metric["decision"]
        for condition in bridge_out["conditions"].values()
        for metric in condition["metrics"].values()
    ] + [
        metric[key]["decision"]
        for cohort in ("primary_base", "primary_half")
        for metric in report["profiles"][cohort]["primary"].values()
        for key in ("candidate_minus_reference_mean", "candidate_over_reference_variance")
    ] + [
        metric["empirical_cdf_decision"]
        for cohort in ("primary_base", "primary_half")
        for metric in report["profiles"][cohort]["primary"].values()
        if metric["empirical_cdf_decision"] != "REPORT_ONLY"
    ]
    report["status"] = (
        "PASS" if outcomes and all(value == "PASS" for value in outcomes) else
        "FAIL" if "FAIL" in outcomes else "INCONCLUSIVE")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True,
                        help="assembled strict v1 confirmation records bundle")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4,
                        help="CPU workers for independent bootstrap components (default: 4)")
    args = parser.parse_args()
    bundle, manifest, profiles, loaded = load_bundle(args.records, args.manifest, args.protocol)
    report = analyze_data(bundle, manifest, profiles, loaded, workers=args.workers)
    report["analyzed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(f"{report['status']}: {args.output}")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
