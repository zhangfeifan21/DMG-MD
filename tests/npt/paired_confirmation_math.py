#!/usr/bin/env python3
"""Pure statistical primitives for the independent paired SCR confirmation.

This module intentionally has no manifest, filesystem, runner, or production
data interface. All protocol parameters are explicit function arguments so the
separately reviewed confirmation protocol can adapt it without changing the
mathematics here.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import random
from typing import Mapping, Sequence


_STATS_PATH = Path(__file__).resolve().parents[1] / "thermostats/sample_statistics.py"
_SPEC = importlib.util.spec_from_file_location("dmgmd_confirmation_sample_stats", _STATS_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load shared statistics primitives: {_STATS_PATH}")
_STATS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_STATS)


def _validate_pairs(
    candidate: Mapping[int, Sequence[float]], reference: Mapping[int, Sequence[float]],
    block_length: int,
) -> tuple[list[int], int]:
    if not candidate or set(candidate) != set(reference):
        raise ValueError("paired chains must have the same nonempty seed set")
    if not isinstance(block_length, int) or block_length <= 0:
        raise ValueError("block_length must be a positive integer")
    seeds = sorted(candidate)
    chain_lengths = set()
    for seed in seeds:
        left, right = candidate[seed], reference[seed]
        if len(left) != len(right) or len(left) < block_length:
            raise ValueError(f"seed {seed}: paired chains need equal lengths and one complete block")
        if len(left) % block_length:
            raise ValueError(f"seed {seed}: paired chain length must contain only complete blocks")
        if not all(math.isfinite(float(value)) for value in (*left, *right)):
            raise ValueError(f"seed {seed}: paired chains contain non-finite values")
        chain_lengths.add(len(left))
    if len(chain_lengths) != 1:
        raise ValueError("all paired chains must have a common sample count")
    return seeds, chain_lengths.pop() // block_length


def _block_means(chains: Mapping[int, Sequence[float]], seeds: Sequence[int],
                 block_length: int, block_count: int) -> dict[int, list[float]]:
    return {
        seed: [math.fsum(chains[seed][i * block_length:(i + 1) * block_length]) / block_length
               for i in range(block_count)]
        for seed in seeds
    }


def paired_mean_difference_ci(
    candidate: Mapping[int, Sequence[float]], reference: Mapping[int, Sequence[float]],
    *, block_length: int, confidence_z: float,
) -> dict:
    """Paired mean-difference interval using same-seed, same-time-index blocks."""
    seeds, block_count = _validate_pairs(candidate, reference, block_length)
    if not math.isfinite(confidence_z) or confidence_z <= 0:
        raise ValueError("confidence_z must be finite and positive")
    cand_blocks = _block_means(candidate, seeds, block_length, block_count)
    ref_blocks = _block_means(reference, seeds, block_length, block_count)
    paired_blocks = [cand_blocks[s][i] - ref_blocks[s][i]
                     for s in seeds for i in range(block_count)]
    seed_differences = [math.fsum(paired_blocks[j * block_count:(j + 1) * block_count]) /
                         block_count for j, _ in enumerate(seeds)]
    estimate = math.fsum(paired_blocks) / len(paired_blocks)
    block_se = math.sqrt(_STATS._sample_variance(paired_blocks) / len(paired_blocks))
    seed_se = math.sqrt(_STATS._sample_variance(seed_differences) / len(seed_differences))
    block_critical = _STATS._student_t_critical(confidence_z, len(paired_blocks) - 1)
    seed_critical = _STATS._student_t_critical(confidence_z, len(seeds) - 1)
    half_width = max(block_se * block_critical, seed_se * seed_critical)
    return {
        "estimate": estimate,
        "confidence_interval": [estimate - half_width, estimate + half_width],
        "block_standard_error": block_se,
        "seed_standard_error": seed_se,
        "block_t_critical": block_critical,
        "seed_t_critical": seed_critical,
        "complete_paired_blocks": len(paired_blocks),
        "pairing": "registered same initial-model and ensemble seed, matched time-block index; numeric samples need not be byte-identical",
    }


def _merge_moments(left: tuple[int, float, float], right: tuple[int, float, float]
                   ) -> tuple[int, float, float]:
    """Merge (count, mean, centered sum-of-squares) summaries."""
    n_left, mean_left, m2_left = left
    n_right, mean_right, m2_right = right
    if n_left == 0:
        return right
    if n_right == 0:
        return left
    total = n_left + n_right
    delta = mean_right - mean_left
    return (total,
            mean_left + delta * n_right / total,
            m2_left + m2_right + delta * delta * n_left * n_right / total)


def paired_global_variance_ratio_ci(
    candidate: Mapping[int, Sequence[float]], reference: Mapping[int, Sequence[float]],
    *, block_length: int, confidence_z: float, bootstrap_replicates: int,
    bootstrap_seed: int,
) -> dict:
    """CI for Var(candidate)/Var(reference), resampling matched seed/block pairs.

    Each engine's point estimate is its global unbiased sample variance over
    all values. Bootstrap replicates resample seed clusters and then block
    indices within a selected seed, using the same sampled index for both
    engines. Block summaries are merged into one global centered M2 per engine;
    blockwise variances are never averaged.
    """
    seeds, block_count = _validate_pairs(candidate, reference, block_length)
    if not math.isfinite(confidence_z) or confidence_z <= 0:
        raise ValueError("confidence_z must be finite and positive")
    if not isinstance(bootstrap_replicates, int) or bootstrap_replicates <= 0:
        raise ValueError("bootstrap_replicates must be a positive integer")
    cand_flat = [float(x) for seed in seeds for x in candidate[seed]]
    ref_flat = [float(x) for seed in seeds for x in reference[seed]]
    cand_mean = math.fsum(cand_flat) / len(cand_flat)
    ref_mean = math.fsum(ref_flat) / len(ref_flat)
    cand_var = _STATS._sample_variance(cand_flat, cand_mean)
    ref_var = _STATS._sample_variance(ref_flat, ref_mean)
    if cand_var <= 0.0 or ref_var <= 0.0:
        raise ValueError("variance ratio requires positive global sample variances")

    summaries: dict[int, tuple[list[tuple[int, float, float]], list[tuple[int, float, float]]]] = {}
    for seed in seeds:
        per_engine = []
        for chains in (candidate, reference):
            blocks = []
            for i in range(block_count):
                values = [float(x) for x in chains[seed][i * block_length:(i + 1) * block_length]]
                mean = math.fsum(values) / len(values)
                m2 = math.fsum((x - mean) ** 2 for x in values)
                blocks.append((len(values), mean, m2))
            per_engine.append(blocks)
        summaries[seed] = (per_engine[0], per_engine[1])

    rng = random.Random(bootstrap_seed)
    ratios: list[float] = []
    for _ in range(bootstrap_replicates):
        merged = [(0, 0.0, 0.0), (0, 0.0, 0.0)]
        for _ in seeds:
            seed = rng.choice(seeds)
            cand_blocks, ref_blocks = summaries[seed]
            for _ in range(block_count):
                index = rng.randrange(block_count)
                merged[0] = _merge_moments(merged[0], cand_blocks[index])
                merged[1] = _merge_moments(merged[1], ref_blocks[index])
        cand_boot_var = merged[0][2] / (merged[0][0] - 1)
        ref_boot_var = merged[1][2] / (merged[1][0] - 1)
        if cand_boot_var > 0.0 and ref_boot_var > 0.0:
            ratios.append(cand_boot_var / ref_boot_var)
    if len(ratios) < math.ceil(0.99 * bootstrap_replicates):
        raise ValueError("too many degenerate paired variance bootstrap replicates")
    alpha = math.erfc(confidence_z / math.sqrt(2.0))
    return {
        "estimate": cand_var / ref_var,
        "candidate_global_unbiased_variance": cand_var,
        "reference_global_unbiased_variance": ref_var,
        "confidence_interval": [_quantile(ratios, alpha / 2.0),
                                _quantile(ratios, 1.0 - alpha / 2.0)],
        "bootstrap_replicates": bootstrap_replicates,
        "analysis_seed": bootstrap_seed,
        "alpha_two_sided": alpha,
        "estimator": "ratio of global unbiased sample variances",
        "resampling": "seed clusters then paired within-seed block indices",
    }


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values or not 0.0 <= probability <= 1.0:
        raise ValueError("quantile needs values and probability in [0,1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def combine_mean_intervals(primary: dict, bridge: dict) -> dict:
    """Combine disjoint-error-budget mean components by endpoint addition."""
    p_est, p_ci = _validated_ci(primary, positive=False)
    b_est, b_ci = _validated_ci(bridge, positive=False)
    return {"estimate": p_est + b_est,
            "confidence_interval": [p_ci[0] + b_ci[0], p_ci[1] + b_ci[1]],
            "combination": "primary plus paired bridge; component intervals combined by endpoint addition"}


def combine_variance_ratio_intervals(primary: dict, bridge: dict) -> dict:
    """Combine positive ratio intervals by endpoint multiplication."""
    p_est, p_ci = _validated_ci(primary, positive=True)
    b_est, b_ci = _validated_ci(bridge, positive=True)
    return {"estimate": p_est * b_est,
            "confidence_interval": [p_ci[0] * b_ci[0], p_ci[1] * b_ci[1]],
            "combination": "primary ratio times paired bridge ratio; component intervals combined by endpoint multiplication"}


def _validated_ci(result: dict, *, positive: bool) -> tuple[float, list[float]]:
    estimate = float(result["estimate"])
    ci = [float(x) for x in result["confidence_interval"]]
    if len(ci) != 2 or not all(math.isfinite(x) for x in (estimate, *ci)) or ci[0] > ci[1]:
        raise ValueError("invalid confidence interval")
    if positive and (estimate <= 0 or ci[0] <= 0):
        raise ValueError("variance ratio intervals must be strictly positive")
    return estimate, ci


def _ecdf_distance(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        raise ValueError("empirical CDF distance requires nonempty samples")
    if not all(math.isfinite(float(x)) for x in (*left, *right)):
        raise ValueError("empirical CDF samples must be finite")
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


def cdf_triangle_bound(
    mpi_paired: Sequence[float], p1_paired: Sequence[float],
    p1_all: Sequence[float], gpumd_all: Sequence[float],
) -> dict:
    """Compute D(MPI8,P1-8)+D(P1-8,P1-32)+D(P1-32,GPUMD-32)."""
    components = {
        "mpi8_vs_p1_same8": _ecdf_distance(mpi_paired, p1_paired),
        "p1_same8_vs_p1_all32": _ecdf_distance(p1_paired, p1_all),
        "p1_all32_vs_gpumd_all32": _ecdf_distance(p1_all, gpumd_all),
    }
    return {"components": components, "triangle_sum": math.fsum(components.values()),
            "identity": "D(MPI8,P1-8)+D(P1-8,P1-32)+D(P1-32,GPUMD-32)"}


def cdf_triangle_decision(triangle_sum: float, threshold: float, *, enough: bool) -> str:
    """A passing upper bound proves the gate; a loose bound cannot prove failure."""
    if not math.isfinite(triangle_sum) or triangle_sum < 0 or \
            not math.isfinite(threshold) or threshold < 0:
        raise ValueError("CDF triangle sum and threshold must be finite and nonnegative")
    if not enough or triangle_sum > threshold:
        return "INCONCLUSIVE"
    return "PASS"
