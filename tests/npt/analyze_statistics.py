#!/usr/bin/env python3
"""Frozen, dependency-free NPT SCR candidate/GPUMD ensemble comparison.

This analyzes only registered prefixes.  It never changes the preregistered
thresholds and makes no iid-KS or universal-sampling claim.  The records.json
interface is documented in ``--help`` and validated before any statistics run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MANIFEST_PATH = HERE / "manifest.json"
STATISTICS_PATH = ROOT / "tests/thermostats/sample_statistics.py"
METRICS = ("temperature_K", "pressure_GPa", "volume_A3")
ENGINE_NAMES = ("candidate", "reference")
UNITS = {"temperature": "K", "pressure": "GPa", "volume": "angstrom^3",
         "kinetic_energy": "eV"}
BOOTSTRAP_REPLICATES = 20_000
BOOTSTRAP_SEED = 2026110301

_spec = importlib.util.spec_from_file_location("dmgmd_sample_statistics", STATISTICS_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load existing statistics helper: {STATISTICS_PATH}")
_statistics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_statistics)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sample_variance(values: list[float], mean: float | None = None) -> float:
    return _statistics._sample_variance(values, mean)


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _mean_ci(chains: list[list[float]], block_length: int, z: float) -> dict[str, Any]:
    """Geyer ESS plus the existing helper's wider block/seed Student-t CI."""
    flat = [value for chain in chains for value in chain]
    estimate = math.fsum(flat) / len(flat)
    block_means, _ = _statistics._block_values(chains, estimate, block_length)
    chain_means = [math.fsum(chain) / len(chain) for chain in chains]
    block_se = math.sqrt(_sample_variance(block_means) / len(block_means))
    seed_se = math.sqrt(_sample_variance(chain_means) / len(chain_means))
    block_critical = _statistics._student_t_critical(z, len(block_means) - 1)
    seed_critical = _statistics._student_t_critical(z, len(chain_means) - 1)
    half_width = max(block_se * block_critical, seed_se * seed_critical)
    pooled_ess = 0.0
    pooled_variance_ess = 0.0
    acf = []
    for chain in chains:
        max_lag = min(len(chain) // 10, 2048)
        tau_mean = _statistics._integrated_autocorrelation_time(chain, max_lag)
        squared = [(x - estimate) ** 2 for x in chain]
        tau_variance = _statistics._integrated_autocorrelation_time(squared, max_lag)
        ess_mean = len(chain) / tau_mean
        ess_variance = len(chain) / tau_variance
        pooled_ess += ess_mean
        pooled_variance_ess += ess_variance
        acf.append({"tau_mean": tau_mean, "ess_mean": ess_mean,
                    "tau_squared_deviation": tau_variance,
                    "ess_squared_deviation": ess_variance})
    return {
        "estimate": estimate,
        "confidence_interval": [estimate - half_width, estimate + half_width],
        "block_standard_error": block_se,
        "seed_standard_error": seed_se,
        "block_t_critical": block_critical,
        "seed_t_critical": seed_critical,
        "effective_samples_mean": pooled_ess,
        "effective_samples_squared_deviation": pooled_variance_ess,
        "per_seed_acf": acf,
        "complete_blocks": len(block_means),
        "sample_variance": _sample_variance(flat, estimate),
    }


def _paired_difference_ci(
    candidate: dict[int, list[float]], reference: dict[int, list[float]],
    block_length: int, z: float,
) -> dict[str, Any]:
    """Mean-difference CI using same-initial-state seed and block estimates.

    Pairing is only by registered initial-model seed.  No random-stream or
    stepwise stochastic-path pairing is assumed.
    """
    seeds = sorted(candidate)
    cand_means = {seed: math.fsum(candidate[seed]) / len(candidate[seed]) for seed in seeds}
    ref_means = {seed: math.fsum(reference[seed]) / len(reference[seed]) for seed in seeds}
    seed_differences = [cand_means[s] - ref_means[s] for s in seeds]
    estimate = (math.fsum(x for s in seeds for x in candidate[s]) /
                sum(len(candidate[s]) for s in seeds) -
                math.fsum(x for s in seeds for x in reference[s]) /
                sum(len(reference[s]) for s in seeds))
    cand_blocks = [math.fsum(candidate[s][i:i + block_length]) / block_length
                   for s in seeds for i in range(0, len(candidate[s]) - block_length + 1, block_length)]
    ref_blocks = [math.fsum(reference[s][i:i + block_length]) / block_length
                  for s in seeds for i in range(0, len(reference[s]) - block_length + 1, block_length)]
    block_se = math.sqrt(_sample_variance(cand_blocks) / len(cand_blocks)) + math.sqrt(
        _sample_variance(ref_blocks) / len(ref_blocks))
    seed_se = math.sqrt(_sample_variance(seed_differences) / len(seed_differences))
    block_df = min(len(cand_blocks), len(ref_blocks)) - 1
    block_critical = _statistics._student_t_critical(z, block_df)
    seed_critical = _statistics._student_t_critical(z, len(seeds) - 1)
    half_width = max(block_se * block_critical, seed_se * seed_critical)
    return {
        "estimate": estimate,
        "confidence_interval": [estimate - half_width, estimate + half_width],
        "block_standard_error_conservative_sum": block_se,
        "seed_standard_error_paired_by_initial_model_only": seed_se,
        "block_t_critical": block_critical,
        "seed_t_critical": seed_critical,
        "pairing": "registered initial-model seed only; no timestep/RNG stream pairing",
    }


def _variance_ratio_ci(
    candidate: dict[int, list[float]], reference: dict[int, list[float]],
    block_length: int, block_length_fs: float, z: float,
) -> dict[str, Any]:
    """Hierarchical seed/block bootstrap CI for a ratio of global variances.

    Each replicate resamples the eight seed clusters, then independently
    resamples complete blocks within each selected seed and engine. Each block
    is represented by n/sum/sumsq relative to one common reference center;
    resampled blocks are combined into a single global centered second moment.
    This avoids averaging local block variances or blockwise ratios and does
    not presume either equal IATs or paired random streams.
    """
    seeds = sorted(candidate)
    ref_flat = [x for seed in seeds for x in reference[seed]]
    common_center = math.fsum(ref_flat) / len(ref_flat)

    def block_summaries(chains: dict[int, list[float]]) -> dict[int, list[tuple[int, float, float]]]:
        summaries = {}
        for seed in seeds:
            values = chains[seed]
            blocks = []
            for start in range(0, len(values) - block_length + 1, block_length):
                block = values[start:start + block_length]
                deviations = [x - common_center for x in block]
                block_sum = math.fsum(deviations)
                block_m2 = math.fsum(x * x for x in deviations) - block_sum * block_sum / len(block)
                blocks.append((len(block), block_sum / len(block), max(0.0, block_m2)))
            summaries[seed] = blocks
        return summaries

    block_stats = {"candidate": block_summaries(candidate),
                   "reference": block_summaries(reference)}
    cand_flat = [x for seed in seeds for x in candidate[seed]]
    cand_mean = math.fsum(cand_flat) / len(cand_flat)
    cand_var = _sample_variance(cand_flat, cand_mean)
    ref_var = _sample_variance(ref_flat, common_center)
    if cand_var <= 0.0 or ref_var <= 0.0:
        raise ValueError("variance ratio is undefined for a zero sample variance")
    ratio = cand_var / ref_var
    rng = random.Random(BOOTSTRAP_SEED)
    ratios = []
    for _ in range(BOOTSTRAP_REPLICATES):
        selected_seeds = [rng.choice(seeds) for _ in seeds]
        variances = []
        for engine in ENGINE_NAMES:
            merged = (0, 0.0, 0.0)
            summaries = block_stats[engine]
            for seed in selected_seeds:
                blocks = summaries[seed]
                for _ in blocks:
                    item = rng.choice(blocks)
                    n_a, mean_a, m2_a = merged
                    n_b, mean_b, m2_b = item
                    if n_a == 0:
                        merged = item
                    else:
                        total = n_a + n_b
                        delta = mean_b - mean_a
                        merged = (total, mean_a + delta * n_b / total,
                                  m2_a + m2_b + delta * delta * n_a * n_b / total)
            if merged[2] > 0.0:
                variances.append(merged[2] / (merged[0] - 1))
            else:
                variances.append(0.0)
        if variances[0] > 0.0 and variances[1] > 0.0:
            ratios.append(variances[0] / variances[1])
    alpha = math.erfc(z / math.sqrt(2.0))
    if len(ratios) < int(0.99 * BOOTSTRAP_REPLICATES):
        raise ValueError("too many degenerate variance bootstrap replicates")
    return {
        "estimate": ratio,
        "confidence_interval": [_quantile(ratios, alpha / 2.0),
                                _quantile(ratios, 1.0 - alpha / 2.0)],
        "estimator": "ratio of global unbiased sample variances",
        "ci_method": "hierarchical seed then independent within-seed complete-block percentile bootstrap",
        "confidence_z": z,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "analysis_seed": BOOTSTRAP_SEED,
        "alpha_two_sided": alpha,
        "common_reference_center": common_center,
        "block_length_steps": block_length,
        "block_length_fs": block_length_fs,
        "finite_sample_correction": "same pooled N/(N-1) from centered M2/(N-1) for both engines",
        "resampling": "seed clusters with replacement, then each engine's blocks independently with replacement",
    }


def _ecdf_distance(left: list[float], right: list[float]) -> float:
    a, b = sorted(left), sorted(right)
    i = j = 0
    distance = 0.0
    while i < len(a) or j < len(b):
        value = min(a[i] if i < len(a) else math.inf,
                    b[j] if j < len(b) else math.inf)
        while i < len(a) and a[i] <= value:
            i += 1
        while j < len(b) and b[j] <= value:
            j += 1
        distance = max(distance, abs(i / len(a) - j / len(b)))
    return distance


def _cdf_uncertainty(
    candidate: dict[int, list[float]], reference: dict[int, list[float]],
    block_length: int, z: float,
) -> dict[str, Any]:
    """Report seed- and block-level Student-t intervals for ECDF sup distance.

    These intervals describe the variation in seed-pair and complete-block
    ECDF sup estimates.  The separately reported pooled-sample sup is the
    acceptance statistic; the grid intervals are diagnostic uncertainty.
    """
    seeds = sorted(candidate)
    seed_estimates, block_estimates = [], []
    for seed in seeds:
        seed_estimates.append(_ecdf_distance(candidate[seed], reference[seed]))
        count = min(len(candidate[seed]), len(reference[seed])) // block_length
        for index in range(count):
            c = candidate[seed][index * block_length:(index + 1) * block_length]
            r = reference[seed][index * block_length:(index + 1) * block_length]
            # Same seed denotes the same initial model only; blocks are not
            # treated as paired stochastic samples.  This block ECDF distance
            # is a descriptive scale for finite-block distribution noise.
            block_estimates.append(_ecdf_distance(c, r))
    block_mean = math.fsum(block_estimates) / len(block_estimates)
    seed_mean = math.fsum(seed_estimates) / len(seed_estimates)
    block_half = _statistics._student_t_critical(z, len(block_estimates) - 1) * math.sqrt(
        _sample_variance(block_estimates) / len(block_estimates))
    seed_half = _statistics._student_t_critical(z, len(seed_estimates) - 1) * math.sqrt(
        _sample_variance(seed_estimates) / len(seed_estimates))
    return {
        "seed_level_mean_sup": seed_mean,
        "seed_level_confidence_interval": [max(0.0, seed_mean - seed_half), seed_mean + seed_half],
        "block_level_mean_sup": block_mean,
        "block_level_confidence_interval": [max(0.0, block_mean - block_half), block_mean + block_half],
        "interval_method": "separate block-level and between-seed Student-t intervals; diagnostic only",
        "block_length_steps": block_length,
    }


def _quantile_difference_ci(
    candidate: dict[int, list[float]], reference: dict[int, list[float]],
    probability: float, block_length: int, z: float,
) -> dict[str, Any]:
    """Seed-paired and conservative independent-block CI for quantile delta."""
    seeds = sorted(candidate)
    cand_q = {s: _quantile(candidate[s], probability) for s in seeds}
    ref_q = {s: _quantile(reference[s], probability) for s in seeds}
    seed_delta = [cand_q[s] - ref_q[s] for s in seeds]
    seed_mean = math.fsum(seed_delta) / len(seed_delta)
    seed_se = math.sqrt(_sample_variance(seed_delta) / len(seed_delta))
    block_c, block_r = [], []
    for seed in seeds:
        count = min(len(candidate[seed]), len(reference[seed])) // block_length
        for index in range(count):
            start = index * block_length
            block_c.append(_quantile(candidate[seed][start:start + block_length], probability))
            block_r.append(_quantile(reference[seed][start:start + block_length], probability))
    block_se = math.sqrt(_sample_variance(block_c) / len(block_c)) + math.sqrt(
        _sample_variance(block_r) / len(block_r))
    block_critical = _statistics._student_t_critical(z, min(len(block_c), len(block_r)) - 1)
    seed_critical = _statistics._student_t_critical(z, len(seeds) - 1)
    half = max(block_se * block_critical, seed_se * seed_critical)
    return {
        "estimate_candidate_minus_reference": seed_mean,
        "confidence_interval": [seed_mean - half, seed_mean + half],
        "block_se_conservative_sum": block_se,
        "seed_se_paired_by_initial_model_only": seed_se,
        "interval_method": "wider independent-block or initial-model-seed Student-t interval; diagnostic",
    }


def _thermo_rows(path: Path, expected_steps: int, atoms: int, expected_dt_fs: float) -> dict[str, list[float]]:
    """Read strict thermo.out and derive T, isotropic pressure, and det(box)."""
    rows: list[list[float]] = []
    headers: list[str] = []
    segments = 0
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            line = line.rstrip("\n")
            if line.startswith("# dump_thermo "):
                segments += 1
                if rows and len(rows) != expected_steps:
                    raise ValueError(f"{path}: prior thermo segment has {len(rows)} rows, expected {expected_steps}")
                if segments > 1:
                    raise ValueError(f"{path}: expected one thermo segment, found more than one")
                rows = []
                headers = [line]
            elif line.startswith("#"):
                if not headers:
                    raise ValueError(f"{path}:{line_number}: orphan thermo header")
                headers.append(line)
            elif line.strip():
                if len(headers) != 5:
                    raise ValueError(f"{path}:{line_number}: data before complete five-line header")
                fields = line.split()
                if len(fields) != 18:
                    raise ValueError(f"{path}:{line_number}: expected 18 columns, found {len(fields)}")
                try:
                    row = [float(field) for field in fields]
                except ValueError as error:
                    raise ValueError(f"{path}:{line_number}: nonnumeric thermo row") from error
                if not all(math.isfinite(value) for value in row):
                    raise ValueError(f"{path}:{line_number}: non-finite thermo row")
                rows.append(row)
    if len(headers) != 5 or rows and len(rows) != expected_steps:
        raise ValueError(f"{path}: expected {expected_steps} rows in one complete segment; found {len(rows)}")
    if len(rows) != expected_steps:
        raise ValueError(f"{path}: found {len(rows)} thermo rows, expected {expected_steps}")
    if headers[2] != f"# num_atoms {atoms}" or headers[4] != (
            "# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz"):
        raise ValueError(f"{path}: unexpected thermo atoms/column contract")
    try:
        output_dt = float(headers[3].split()[2])
    except (IndexError, ValueError) as error:
        raise ValueError(f"{path}: invalid thermo output interval") from error
    if not headers[3].endswith(" fs") or not math.isclose(output_dt, expected_dt_fs, rel_tol=0, abs_tol=1e-10):
        raise ValueError(f"{path}: thermo interval/units do not match expected {expected_dt_fs} fs")
    temperatures, kinetic_energies, pressures, volumes = [], [], [], []
    for row in rows:
        # Output lattice is column-major (a,b,c vectors); determinant is
        # equivalent to the orthogonal Lx*Ly*Lz used by the locked fixture.
        ax, ay, az, bx, by, bz, cx, cy, cz = row[9:18]
        volume = (ax * (by * cz - bz * cy) - bx * (ay * cz - az * cy)
                  + cx * (ay * bz - az * by))
        if not math.isfinite(volume) or volume <= 0:
            raise ValueError(f"{path}: nonpositive/non-finite box volume")
        temperatures.append(row[0])
        kinetic_energies.append(row[1])
        pressures.append(math.fsum(row[3:6]) / 3.0)
        volumes.append(volume)
    return dict(temperature_K=temperatures, kinetic_energy_eV=kinetic_energies,
                pressure_GPa=pressures, volume_A3=volumes)


def _expected_run_hash(steps: int, dt_fs: float, manifest: dict) -> str:
    protocol = manifest["b3_scr_statistics"]
    if dt_fs == protocol["configuration"]["dt_fs"]:
        tdamp, tau_p = protocol["configuration"]["Tdamp_steps"], protocol["configuration"]["tau_p_steps"]
    else:
        tdamp, tau_p = protocol["half_dt"]["Tdamp_steps"], protocol["half_dt"]["tau_p_steps"]
    line = f"ensemble {protocol['ensemble']} 300 300 {tdamp} 0 425 {tau_p}"
    text = ("potential nep.txt\n" f"time_step {dt_fs:g}\n"
            f"{line}\n" "dump_thermo 1\n" f"run {steps}\n")
    return hashlib.sha256(text.encode()).hexdigest()


def _profile_for_dt(protocol: dict, dt_fs: float) -> tuple[bool, dict[str, Any]]:
    """Select the registered profile by explicit timestep, never by look size."""
    if dt_fs == protocol["configuration"]["dt_fs"]:
        return False, protocol["configuration"]
    if dt_fs == protocol["half_dt"]["dt_fs"]:
        return True, protocol["half_dt"]
    raise ValueError("records timestep does not match either preregistered profile")


def _load_records(records_path: Path, manifest: dict, look_steps: int,
                  ranks: int, backend: str) -> tuple[dict[int, dict[str, dict[str, list[float]]]], dict[str, Any]]:
    records = json.loads(records_path.read_text(encoding="utf-8"))
    if records.get("schema") != "dmgmd.npt-scr-statistics-input.v1":
        raise ValueError("records schema must be dmgmd.npt-scr-statistics-input.v1")
    if records.get("look_steps") != look_steps:
        raise ValueError("records look_steps does not match requested preregistered look")
    protocol = manifest["b3_scr_statistics"]
    half, config = _profile_for_dt(protocol, records.get("dt_fs"))
    registered_looks = (config["budget_steps_per_seed"] if half
                        else protocol["budget_steps_per_seed"])
    dt_fs = config["dt_fs"]
    if look_steps not in registered_looks:
        raise ValueError("requested look is not registered for the selected timestep profile")
    if ranks not in (1, 2, 4) or backend not in ("HostStaged", "CudaAware"):
        raise ValueError("condition must use a registered rank count/backend")
    if half and (ranks != 1 or backend != "HostStaged"):
        raise ValueError("half-dt analysis is preregistered only for P1 HostStaged")
    if records.get("ranks") != ranks or records.get("backend") != backend:
        raise ValueError("records condition does not match requested ranks/backend")
    if records.get("units") != UNITS:
        raise ValueError("records units differ from the preregistered contract")
    seeds = manifest["b3_scr_statistics"]["seeds"]
    atoms = manifest["fixtures"]["statistics"]["atoms"]
    potential_hash = manifest["source_contract"]["potential_sha256"]
    maximum_steps = max(registered_looks)
    if len(records.get("runs", [])) != len(seeds) * len(ENGINE_NAMES):
        raise ValueError("records must contain exactly one candidate and reference run per seed")
    result: dict[int, dict[str, dict[str, list[float]]]] = {seed: {} for seed in seeds}
    executable_hashes = {engine: set() for engine in ENGINE_NAMES}
    for run in records["runs"]:
        engine, seed = run.get("engine"), run.get("seed")
        condition_matches = (engine == "reference" or
                             (run.get("ranks") == ranks and run.get("backend") == backend))
        if engine not in ENGINE_NAMES or seed not in result or engine in result[seed] or not condition_matches:
            raise ValueError("duplicate, unknown engine, or unregistered seed in runs")
        actual_steps = run.get("steps")
        if (run.get("status") != "PASS" or not isinstance(actual_steps, int) or
                (engine == "candidate" and
                 (actual_steps not in registered_looks or actual_steps < look_steps)) or
                (engine == "reference" and actual_steps != maximum_steps)):
            raise ValueError(f"{engine} seed {seed}: run status or actual-step profile mismatch")
        hashes = run.get("input_sha256", {})
        expected = {
            "model.xyz": manifest["fixtures"]["statistics"]["model_xyz_sha256_by_seed"][str(seed)],
            "run.in": _expected_run_hash(actual_steps, dt_fs, manifest),
            "nep.txt": potential_hash,
        }
        if hashes != expected:
            raise ValueError(f"{engine} seed {seed}: input hashes do not match approved fixture/run/potential")
        input_paths = run.get("input_paths")
        if not isinstance(input_paths, dict) or set(input_paths) != set(expected):
            raise ValueError(f"{engine} seed {seed}: input_paths must identify model.xyz/run.in/nep.txt")
        for name, digest in expected.items():
            input_path = Path(input_paths[name])
            if not input_path.is_absolute():
                input_path = (records_path.parent / input_path).resolve()
            if not input_path.is_file() or _sha256(input_path) != digest:
                raise ValueError(f"{engine} seed {seed}: actual {name} bytes do not match locked hash")
        executable_hash = run.get("executable_sha256")
        if not isinstance(executable_hash, str) or len(executable_hash) != 64 or any(
                char not in "0123456789abcdef" for char in executable_hash.lower()):
            raise ValueError(f"{engine} seed {seed}: missing executable_sha256")
        executable_hashes[engine].add(executable_hash)
        thermo_path = Path(run["thermo_path"])
        if not thermo_path.is_absolute():
            thermo_path = (records_path.parent / thermo_path).resolve()
        if not thermo_path.is_file():
            raise ValueError(f"{engine} seed {seed}: thermo file is missing: {thermo_path}")
        if run.get("thermo_sha256") != _sha256(thermo_path):
            raise ValueError(f"{engine} seed {seed}: thermo_sha256 mismatch")
        series = _thermo_rows(thermo_path, actual_steps, atoms, dt_fs)
        prefix = {metric: values[:look_steps] for metric, values in series.items()}
        burn = math.ceil(look_steps * _registered_parameters(manifest)["burn_fraction"])
        result[seed][engine] = {metric: values[burn:] for metric, values in prefix.items()}
        start_text, end_text = run.get("started_at_utc"), run.get("ended_at_utc")
        try:
            started = dt.datetime.fromisoformat(start_text.replace("Z", "+00:00"))
            ended = dt.datetime.fromisoformat(end_text.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise ValueError(f"{engine} seed {seed}: invalid UTC execution timestamps") from error
        if (started.tzinfo is None or ended.tzinfo is None or
                started.utcoffset() != dt.timedelta(0) or ended.utcoffset() != dt.timedelta(0) or
                ended <= started or not isinstance(run.get("pid"), int) or run["pid"] <= 0):
            raise ValueError(f"{engine} seed {seed}: invalid execution interval or PID")
        run["_parsed_started"] = started
        if engine == "reference" and not str(run.get("rng_initialization_evidence", "")).strip():
            raise ValueError(f"reference seed {seed}: missing RNG initialization evidence")
    if any(len(hashes) != 1 for hashes in executable_hashes.values()):
        raise ValueError("executable identity changes within an engine's eight-seed set")
    if executable_hashes["candidate"] == executable_hashes["reference"]:
        raise ValueError("candidate and GPUMD reference executable hashes are identical")
    candidate_runs = [run for run in records["runs"] if run["engine"] == "candidate"]
    if len({run["steps"] for run in candidate_runs}) != 1:
        raise ValueError("candidate seeds do not share one actual run length")
    reference_runs = [run for run in records["runs"] if run["engine"] == "reference"]
    reference_seconds = [run["_parsed_started"].replace(microsecond=0) for run in reference_runs]
    if len(set(reference_seconds)) != len(reference_seconds):
        raise ValueError("GPUMD reference launches must have distinct UTC start seconds")
    for seed in seeds:
        if set(result[seed]) != set(ENGINE_NAMES):
            raise ValueError(f"missing engine result for seed {seed}")
        for metric in METRICS:
            blocks = len(result[seed]["candidate"][metric]) // (
                manifest["b3_scr_statistics"]["block_length_steps"]["dt_0.125_fs" if half else "dt_0.25_fs"])
            minimum = manifest["b3_scr_statistics"]["minimum_complete_blocks_per_seed"]
            if blocks < minimum:
                raise ValueError(f"seed {seed} {metric}: only {blocks} complete blocks, need {minimum}")
    return result, {"ranks": ranks, "backend": backend,
                    "candidate_actual_steps": sorted({run["steps"] for run in records["runs"]
                                                        if run["engine"] == "candidate"}),
                    "reference_actual_steps": maximum_steps,
                    "reference_prefix_reused": True}


def _metric_bounds(metric: str, reference_mean: float,
                   parameters: dict[str, Any]) -> tuple[float, list[float]]:
    if metric == "temperature_K":
        mean_bound = parameters["temperature_abs"]
    elif metric == "pressure_GPa":
        mean_bound = parameters["pressure_abs"]
    else:
        mean_bound = abs(reference_mean) * parameters["volume_relative"]
    return mean_bound, parameters["variance_ratio"]


def _registered_parameters(manifest: dict) -> dict[str, Any]:
    stats = manifest["b3_scr_statistics"]["statistics"]

    def match(pattern: str, text: str, label: str) -> tuple[float, ...]:
        found = re.search(pattern, text)
        if not found:
            raise ValueError(f"cannot parse approved {label} from manifest")
        return tuple(float(group) for group in found.groups())

    temp_rel, temp_target = match(
        r"within ([0-9.]+)% of ([0-9.]+) K", stats["temperature_mean"], "temperature bound")
    pressure_bound, = match(r"\+/-([0-9.]+) GPa", stats["pressure_mean"], "pressure bound")
    volume_rel, = match(r"\+/-([0-9.]+)%", stats["volume_mean"], "volume bound")
    variance_low, variance_high = match(r"\[([0-9.]+),([0-9.]+)\]", stats["variance"], "variance ratio bound")
    cdf_limit, = match(r"<=([0-9.]+)", stats["distribution"], "volume CDF bound")
    z, = match(r"z=([0-9]+(?:\.[0-9]+)?)\b", stats["confidence"], "confidence z")
    burn_percent, = match(r"first ([0-9.]+)%", manifest["b3_scr_statistics"]["burn_in"], "burn-in fraction")
    return {"temperature_abs": temp_rel * 0.01 * temp_target,
            "pressure_abs": pressure_bound, "volume_relative": volume_rel * 0.01,
            "variance_ratio": [variance_low, variance_high],
            "volume_cdf": cdf_limit, "confidence_z": z,
            "burn_fraction": burn_percent * 0.01, "temperature_target": temp_target}


def _decision(interval: list[float], lower: float, upper: float, enough: bool) -> str:
    if not enough:
        return "INCONCLUSIVE"
    if interval[0] >= lower and interval[1] <= upper:
        return "PASS"
    if interval[1] < lower or interval[0] > upper:
        return "FAIL"
    return "INCONCLUSIVE"


def analyze(records_path: Path, look_steps: int, ranks: int, backend: str,
            manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    series, condition = _load_records(records_path.resolve(), manifest, look_steps, ranks, backend)
    params = _registered_parameters(manifest)
    z = params["confidence_z"]
    records_metadata = json.loads(records_path.read_text(encoding="utf-8"))
    half = records_metadata.get("dt_fs") == manifest["b3_scr_statistics"]["half_dt"]["dt_fs"]
    block_key = "dt_0.125_fs" if half else "dt_0.25_fs"
    block_length = manifest["b3_scr_statistics"]["block_length_steps"][block_key]
    seeds = manifest["b3_scr_statistics"]["seeds"]
    minimum_ess = manifest["b3_scr_statistics"]["minimum_pooled_effective_samples_per_engine_and_metric"]
    minimum_blocks_per_seed = manifest["b3_scr_statistics"]["minimum_complete_blocks_per_seed"]
    dt_fs = manifest["b3_scr_statistics"]["half_dt"]["dt_fs"] if half else manifest[
        "b3_scr_statistics"]["configuration"]["dt_fs"]
    report: dict[str, Any] = {
        "schema": "dmgmd.npt-scr-statistics-report.v1",
        "look_steps": look_steps,
        "condition": condition,
        "dt_fs": dt_fs,
        "seeds": seeds,
        "burn_in_fraction": params["burn_fraction"],
        "block_length_steps": block_length,
        "block_length_fs": block_length * dt_fs,
        "confidence_z": z,
        "alpha_two_sided": math.erfc(z / math.sqrt(2.0)),
        "variance_ratio_bootstrap": {"replicates": BOOTSTRAP_REPLICATES,
                                     "analysis_seed": BOOTSTRAP_SEED},
        "confidence_scope": "per registered metric/look; not a joint 95% guarantee over the full matrix",
        "same_condition_reference_gate": {},
        "theory_diagnostics": {},
        "metrics": {},
        "limitations": [
            "Compatibility is assessed against same-condition GPUMD output; a common target-theory deviation is reported separately and does not block compatibility.",
            "This finite set of trajectories does not establish universal strict sampling or ergodicity.",
            "No iid KS p-value is computed; CDF intervals are block/seed resampling diagnostics.",
        ],
    }
    all_pass = True
    any_fail = False
    for metric in METRICS:
        by_engine = {
            engine: {seed: series[seed][engine][metric] for seed in seeds}
            for engine in ENGINE_NAMES
        }
        chains = {engine: [by_engine[engine][seed] for seed in seeds] for engine in ENGINE_NAMES}
        engine_summary = {engine: _mean_ci(chains[engine], block_length, z)
                          for engine in ENGINE_NAMES}
        ref_values = [x for seed in seeds for x in by_engine["reference"][seed]]
        cand_values = [x for seed in seeds for x in by_engine["candidate"][seed]]
        reference_mean = engine_summary["reference"]["estimate"]
        mean_bound, variance_bounds = _metric_bounds(metric, reference_mean, params)
        mean_difference = _paired_difference_ci(by_engine["candidate"], by_engine["reference"],
                                                 block_length, z)
        enough_ess = all(
            engine_summary[engine]["effective_samples_mean"] >= minimum_ess and
            engine_summary[engine]["effective_samples_squared_deviation"] >= minimum_ess and
            min(len(by_engine[engine][seed]) // block_length for seed in seeds) >= minimum_blocks_per_seed
            for engine in ENGINE_NAMES)
        mean_difference["allowed_interval"] = [-mean_bound, mean_bound]
        mean_difference["decision"] = _decision(
            mean_difference["confidence_interval"], -mean_bound, mean_bound, enough_ess)
        variance_ratio = _variance_ratio_ci(
            by_engine["candidate"], by_engine["reference"], block_length,
            block_length * dt_fs, z)
        variance_ratio["allowed_interval"] = variance_bounds
        variance_ratio["decision"] = _decision(
            variance_ratio["confidence_interval"], variance_bounds[0], variance_bounds[1], enough_ess)
        cdf_distance = _ecdf_distance(cand_values, ref_values)
        cdf_interval = _cdf_uncertainty(by_engine["candidate"], by_engine["reference"], block_length, z)
        cdf_limit = params["volume_cdf"] if metric == "volume_A3" else None
        cdf_decision = ("PASS" if cdf_distance <= cdf_limit and enough_ess
                        else "FAIL" if cdf_limit is not None and cdf_distance > cdf_limit and enough_ess
                        else "INCONCLUSIVE") if cdf_limit is not None else "REPORT_ONLY"
        metric_pass = mean_difference["decision"] == "PASS" and variance_ratio["decision"] == "PASS" and (
            cdf_decision in ("PASS", "REPORT_ONLY"))
        if any(value == "FAIL" for value in (mean_difference["decision"], variance_ratio["decision"], cdf_decision)):
            any_fail = True
        all_pass &= metric_pass
        reference_sd = math.sqrt(engine_summary["reference"]["sample_variance"])
        quantiles = {}
        for probability in (0.05, 0.5, 0.95):
            candidate_quantile = _quantile(cand_values, probability)
            reference_quantile = _quantile(ref_values, probability)
            uncertainty = _quantile_difference_ci(
                by_engine["candidate"], by_engine["reference"], probability, block_length, z)
            uncertainty["normalized_confidence_interval"] = [
                value / reference_sd for value in uncertainty["confidence_interval"]]
            quantiles[str(probability)] = {
                "candidate": candidate_quantile,
                "reference": reference_quantile,
                "normalized_candidate_minus_reference":
                    (candidate_quantile - reference_quantile) / reference_sd,
                "uncertainty": uncertainty,
            }
        report["metrics"][metric] = {
            "candidate": engine_summary["candidate"],
            "reference": engine_summary["reference"],
            "candidate_minus_reference_mean": mean_difference,
            "variance_ratio_candidate_over_reference": variance_ratio,
            "empirical_cdf": {
                "sup_distance": cdf_distance,
                "block_seed_uncertainty": cdf_interval,
                "limit": cdf_limit,
                "decision": cdf_decision,
                "quantiles": quantiles,
            },
            "target_diagnostics": {
                "candidate_offset": None,
                "reference_offset": None,
            },
            "precision_sufficient": enough_ess,
        }
        if metric == "temperature_K":
            target = params["temperature_target"]
        elif metric == "pressure_GPa":
            target = 0.0
        else:
            # The finite-temperature target volume is an independent EOS
            # diagnostic; do not equate the initial box to an NPT target.
            target = None
        report["metrics"][metric]["target_diagnostics"] = {
            "target": target,
            "candidate_offset": (engine_summary["candidate"]["estimate"] - target
                                 if target is not None else None),
            "reference_offset": (engine_summary["reference"]["estimate"] - target
                                 if target is not None else None),
            "status": "THEORY_TARGET_INCONCLUSIVE" if metric == "volume_A3" or (
                target is not None and (abs(engine_summary["candidate"]["estimate"] - target) > mean_bound or
                                        abs(engine_summary["reference"]["estimate"] - target) > mean_bound))
                else "DIAGNOSTIC_ONLY",
        }
    atoms = manifest["fixtures"]["statistics"]["atoms"]
    kb = manifest["source_contract"]["units"]["boltzmann_eV_per_K"]
    expected_k_mean = 1.5 * atoms * kb * 300.0
    expected_k_variance = 1.5 * atoms * (kb * 300.0) ** 2
    kinetic = {}
    for engine in ENGINE_NAMES:
        chains_k = [series[seed][engine]["kinetic_energy_eV"] for seed in seeds]
        flattened_k = [value for chain in chains_k for value in chain]
        mean_k = _mean_ci(chains_k, block_length, z)
        variance_k = _sample_variance(flattened_k)
        kinetic[engine] = {
            "mean_eV": mean_k,
            "mean_target_eV": expected_k_mean,
            "mean_offset_eV": mean_k["estimate"] - expected_k_mean,
            "variance_eV2": variance_k,
            "variance_target_eV2": expected_k_variance,
            "variance_ratio_to_canonical": variance_k / expected_k_variance,
            "variance_offset_eV2": variance_k - expected_k_variance,
        }
    report["kinetic_energy_theory_diagnostic"] = {
        "status": "THEORY_TARGET_INCONCLUSIVE",
        "dof": 3 * atoms,
        "target_mean_eV": expected_k_mean,
        "target_variance_eV2": expected_k_variance,
        "engines": kinetic,
        "gate_effect": "diagnostic only; does not override same-condition candidate/GPUMD compatibility",
    }
    report["same_condition_reference_gate"] = {
        "status": "PASS" if all_pass else "FAIL" if any_fail else "INCONCLUSIVE",
        "meaning": "Candidate and GPUMD same-condition equivalence only; target theory is a separate diagnostic.",
    }
    report["theory_diagnostics"] = {
        "status": "THEORY_TARGET_INCONCLUSIVE",
        "note": "A shared deviation from target is retained as a diagnostic and never overrides same-condition PASS.",
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True,
                        help="records.json emitted by the SCR runner; thermo_path may be relative to it")
    parser.add_argument("--look-steps", type=int, required=True,
                        help="one registered look: 20000/40000/80000 or half-dt 40000/80000/160000")
    parser.add_argument("--ranks", type=int, choices=(1, 2, 4), required=True)
    parser.add_argument("--backend", choices=("HostStaged", "CudaAware"), required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.records, args.look_steps, args.ranks, args.backend, args.manifest)
    report["records_sha256"] = _sha256(args.records)
    report["manifest_sha256"] = _sha256(args.manifest)
    report["analyzed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(f"{report['same_condition_reference_gate']['status']}: {args.output}")
    return 0 if report["same_condition_reference_gate"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
