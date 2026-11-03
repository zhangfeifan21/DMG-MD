"""Small, dependency-free diagnostics for thermostat ensemble trajectories."""

from __future__ import annotations

import math
import os
import subprocess
from typing import Any


def _sample_variance(values: list[float], mean: float | None = None) -> float:
    if len(values) < 2:
        raise ValueError("each chain must contain at least two samples")
    if mean is None:
        mean = math.fsum(values) / len(values)
    return math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _integrated_autocorrelation_time_python(values: list[float], max_lag: int) -> float:
    """Geyer initial-positive adjacent-pair estimate, conservatively >= 1."""
    count = len(values)
    mean = math.fsum(values) / count
    centered = [value - mean for value in values]
    denominator = math.fsum(value * value for value in centered)
    if denominator == 0.0:
        return 1.0

    def rho_at(lag: int) -> float:
        covariance = math.fsum(
            centered[index] * centered[index + lag]
            for index in range(count - lag)
        )
        return covariance / denominator

    pair_sum = 0.0
    lag = 1
    while lag + 1 <= max_lag:
        pair = rho_at(lag) + rho_at(lag + 1)
        if not math.isfinite(pair) or pair <= 0.0:
            break
        pair_sum += pair
        lag += 2
    return max(1.0, 1.0 + 2.0 * pair_sum)


def _integrated_autocorrelation_time(values: list[float], max_lag: int) -> float:
    """Use the optional native helper, failing explicitly if it is misconfigured."""
    if "DMGMD_ACF_HELPER" not in os.environ:
        return _integrated_autocorrelation_time_python(values, max_lag)
    helper = os.environ["DMGMD_ACF_HELPER"]
    payload = f"{max_lag} {len(values)}\n" + "".join(
        f"{value:.17g}\n" for value in values
    )
    try:
        completed = subprocess.run(
            [helper], input=payload, text=True, capture_output=True, check=False
        )
    except OSError as error:
        raise RuntimeError(f"cannot execute DMGMD_ACF_HELPER {helper!r}: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        raise RuntimeError(
            f"DMGMD_ACF_HELPER exited with status {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    fields = completed.stdout.split()
    if len(fields) != 1:
        raise RuntimeError("DMGMD_ACF_HELPER must emit exactly one tau value")
    try:
        tau = float(fields[0])
    except ValueError as error:
        raise RuntimeError("DMGMD_ACF_HELPER emitted a non-numeric tau") from error
    if not math.isfinite(tau) or tau < 1.0:
        raise RuntimeError("DMGMD_ACF_HELPER emitted a non-finite or sub-unit tau")
    return tau


def _block_values(chains: list[list[float]], global_mean: float, block_length: int) -> tuple[list[float], list[float]]:
    block_means: list[float] = []
    block_second_moments: list[float] = []
    for chain in chains:
        complete = len(chain) // block_length
        for block_index in range(complete):
            start = block_index * block_length
            block = chain[start : start + block_length]
            block_means.append(math.fsum(block) / block_length)
            block_second_moments.append(
                math.fsum((value - global_mean) ** 2 for value in block) / block_length
            )
    return block_means, block_second_moments


def _interval(estimate: float, standard_error: float, z: float) -> list[float]:
    return [estimate - z * standard_error, estimate + z * standard_error]


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Numerical Recipes continued fraction for the regularized beta CDF."""
    max_iterations = 300
    epsilon = 3.0e-14
    tiny = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for iteration in range(1, max_iterations + 1):
        m2 = 2 * iteration
        coefficient = iteration * (b - iteration) * x / ((qam + m2) * (a + m2))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        coefficient = -(a + iteration) * (qab + iteration) * x / (
            (a + m2) * (qap + m2)
        )
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < epsilon:
            return result
    raise ArithmeticError("incomplete beta continued fraction did not converge")


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    factor = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return factor * _beta_continued_fraction(a, b, x) / a
    return 1.0 - factor * _beta_continued_fraction(b, a, 1.0 - x) / b


def _student_t_critical(confidence_z: float, degrees_of_freedom: int) -> float:
    """Return the two-sided t critical value at z's normal-equivalent level."""
    if degrees_of_freedom < 1:
        raise ValueError("Student-t degrees of freedom must be positive")
    if not math.isfinite(confidence_z) or confidence_z <= 0.0:
        raise ValueError("confidence_z must be finite and positive")
    if degrees_of_freedom >= 10000:
        return confidence_z
    tail_probability = 0.5 * math.erfc(confidence_z / math.sqrt(2.0))
    if tail_probability <= 0.0:
        raise ValueError("confidence_z is too large to represent its two-sided tail")

    a = 0.5 * degrees_of_freedom
    b = 0.5
    beta_probability = 2.0 * tail_probability
    low, high = 0.0, 1.0
    for _ in range(100):
        middle = 0.5 * (low + high)
        probability = _regularized_incomplete_beta(middle, a, b)
        if probability < beta_probability:
            low = middle
        else:
            high = middle
    x = 0.5 * (low + high)
    return math.sqrt(degrees_of_freedom * (1.0 - x) / x)


def _conservative_interval(
    estimate: float,
    block_standard_error: float | None,
    block_critical: float | None,
    chain_standard_error: float | None,
    chain_critical: float | None,
) -> tuple[list[float] | None, float | None]:
    half_widths = []
    if block_standard_error is not None and block_critical is not None:
        half_widths.append(block_standard_error * block_critical)
    if chain_standard_error is not None and chain_critical is not None:
        half_widths.append(chain_standard_error * chain_critical)
    if not half_widths:
        return None, None
    half_width = max(half_widths)
    return [estimate - half_width, estimate + half_width], half_width


def _metric_decision(
    interval: list[float], allowed: list[float], precision_ok: bool
) -> str:
    if not precision_ok:
        return "INCONCLUSIVE"
    if interval[0] >= allowed[0] and interval[1] <= allowed[1]:
        return "PASS"
    if interval[1] < allowed[0] or interval[0] > allowed[1]:
        return "FAIL"
    return "INCONCLUSIVE"


def analyze_series(
    chains: list[list[float]], expected_mean: float, expected_variance: float, config: dict
) -> dict[str, Any]:
    """Analyze independent trajectory chains against pre-registered moments.

    The caller is responsible for burn-in removal and chain selection. This
    function uses every supplied sample and never changes the acceptance bounds.
    """
    if not isinstance(chains, list) or not chains:
        raise ValueError("chains must be a non-empty list of chains")
    if not math.isfinite(expected_mean) or not math.isfinite(expected_variance):
        raise ValueError("expected moments must be finite")
    if expected_variance <= 0.0:
        raise ValueError("expected_variance must be positive")

    required = (
        "mean_relative_bound",
        "variance_relative_bound",
        "confidence_z",
        "block_length",
        "minimum_effective_samples",
        "minimum_blocks",
    )
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"missing config values: {', '.join(missing)}")
    mean_bound = float(config["mean_relative_bound"])
    variance_bound = float(config["variance_relative_bound"])
    z = float(config["confidence_z"])
    block_length = config["block_length"]
    minimum_effective_samples = float(config["minimum_effective_samples"])
    minimum_blocks = config["minimum_blocks"]
    if not all(math.isfinite(value) for value in (mean_bound, variance_bound, z, minimum_effective_samples)):
        raise ValueError("config numeric values must be finite")
    if mean_bound < 0 or variance_bound < 0 or z <= 0 or minimum_effective_samples < 0:
        raise ValueError("bounds and effective-sample threshold must be nonnegative; confidence_z must be positive")
    if isinstance(block_length, bool) or not isinstance(block_length, int) or block_length <= 0:
        raise ValueError("block_length must be a positive integer")
    if isinstance(minimum_blocks, bool) or not isinstance(minimum_blocks, int) or minimum_blocks < 2:
        raise ValueError("minimum_blocks must be an integer of at least two")

    clean_chains: list[list[float]] = []
    for chain_index, chain in enumerate(chains):
        if not isinstance(chain, list) or len(chain) < 10:
            raise ValueError(f"chain {chain_index} must contain at least 10 samples")
        values = [float(value) for value in chain]
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"chain {chain_index} contains a non-finite sample")
        clean_chains.append(values)

    all_values = [value for chain in clean_chains for value in chain]
    sample_count = len(all_values)
    global_mean = math.fsum(all_values) / sample_count
    global_variance = _sample_variance(all_values, global_mean)
    chain_diagnostics: list[dict[str, float | int]] = []
    chain_means = [math.fsum(chain) / len(chain) for chain in clean_chains]
    variance_correction = sample_count / (sample_count - 1)
    chain_second_moments = [
        math.fsum((value - global_mean) ** 2 for value in chain) / len(chain)
        * variance_correction
        for chain in clean_chains
    ]
    chain_mean_se = (
        math.sqrt(_sample_variance(chain_means) / len(chain_means))
        if len(chain_means) >= 2
        else None
    )
    chain_second_moment_se = (
        math.sqrt(_sample_variance(chain_second_moments) / len(chain_second_moments))
        if len(chain_second_moments) >= 2
        else None
    )
    mean_effective_samples = 0.0
    variance_effective_samples = 0.0
    for chain in clean_chains:
        max_lag = min(len(chain) // 10, 2048)
        tau_mean = _integrated_autocorrelation_time(chain, max_lag)
        squared_deviations = [(value - global_mean) ** 2 for value in chain]
        tau_variance = _integrated_autocorrelation_time(squared_deviations, max_lag)
        chain_diagnostics.append(
            {
                "sample_count": len(chain),
                "max_lag": max_lag,
                "tau_mean": tau_mean,
                "effective_samples_mean": len(chain) / tau_mean,
                "tau_squared_deviation": tau_variance,
                "effective_samples_variance": len(chain) / tau_variance,
            }
        )
        mean_effective_samples += len(chain) / tau_mean
        variance_effective_samples += len(chain) / tau_variance

    block_means, block_second_moments = _block_values(clean_chains, global_mean, block_length)
    block_count = len(block_means)
    mean_block_se: float | None = None
    variance_block_se: float | None = None
    if block_count >= 2:
        mean_block_se = math.sqrt(_sample_variance(block_means) / block_count)
        scaled_second_moments = [value * variance_correction for value in block_second_moments]
        variance_block_se = math.sqrt(_sample_variance(scaled_second_moments) / block_count)

    block_critical = _student_t_critical(z, block_count - 1) if block_count >= 2 else None
    chain_critical = _student_t_critical(z, len(clean_chains) - 1) if len(clean_chains) >= 2 else None
    mean_ci, mean_half_width = _conservative_interval(
        global_mean, mean_block_se, block_critical, chain_mean_se, chain_critical
    )
    variance_ci, variance_half_width = _conservative_interval(
        global_variance,
        variance_block_se,
        block_critical,
        chain_second_moment_se,
        chain_critical,
    )
    variance_ratio = global_variance / expected_variance
    variance_ratio_ci = (
        [bound / expected_variance for bound in variance_ci] if variance_ci is not None else None
    )
    mean_allowed = [expected_mean - abs(expected_mean) * mean_bound, expected_mean + abs(expected_mean) * mean_bound]
    variance_ratio_allowed = [1.0 - variance_bound, 1.0 + variance_bound]
    blocks_ok = block_count >= minimum_blocks
    mean_precision_ok = blocks_ok and mean_effective_samples >= minimum_effective_samples
    variance_precision_ok = (
        blocks_ok
        and variance_effective_samples >= minimum_effective_samples
        and global_variance > 0.0
    )
    mean_decision = (
        _metric_decision(mean_ci, mean_allowed, mean_precision_ok)
        if mean_ci is not None
        else "INCONCLUSIVE"
    )
    variance_decision = (
        _metric_decision(variance_ratio_ci, variance_ratio_allowed, variance_precision_ok)
        if variance_ratio_ci is not None
        else "INCONCLUSIVE"
    )
    if "FAIL" in (mean_decision, variance_decision):
        status = "FAIL"
    elif mean_decision == "PASS" and variance_decision == "PASS":
        status = "PASS"
    else:
        status = "INCONCLUSIVE"

    return {
        "status": status,
        "chain_count": len(clean_chains),
        "sample_count": sample_count,
        "mean": {
            "estimate": global_mean,
            "target": expected_mean,
            "sample_variance": global_variance,
            "relative_bound": mean_bound,
            "allowed_interval": mean_allowed,
            "block_standard_error": mean_block_se,
            "chain_standard_error": chain_mean_se,
            "block_t_critical": block_critical,
            "chain_t_critical": chain_critical,
            "confidence_half_width": mean_half_width,
            "confidence_interval": mean_ci,
            "effective_samples": mean_effective_samples,
            "effective_samples_threshold": minimum_effective_samples,
            "decision": mean_decision,
        },
        "variance": {
            "estimate": global_variance,
            "target": expected_variance,
            "ratio": variance_ratio,
            "relative_bound": variance_bound,
            "allowed_ratio_interval": variance_ratio_allowed,
            "block_standard_error": variance_block_se,
            "chain_standard_error": chain_second_moment_se,
            "block_t_critical": block_critical,
            "chain_t_critical": chain_critical,
            "confidence_half_width": variance_half_width,
            "confidence_interval": variance_ci,
            "ratio_confidence_interval": variance_ratio_ci,
            "effective_samples": variance_effective_samples,
            "effective_samples_threshold": minimum_effective_samples,
            "decision": variance_decision,
            "degenerate": global_variance == 0.0,
        },
        "blocks": {
            "length": block_length,
            "complete_count": block_count,
            "minimum_count": minimum_blocks,
            "complete_blocks_sufficient": blocks_ok,
        },
        "chains": chain_diagnostics,
        "confidence_z": z,
        "confidence_probability_two_sided": math.erf(z / math.sqrt(2.0)),
        "explanation": (
            "CI must lie entirely inside the registered error band to pass; "
            "a sufficiently precise CI disjoint from the band fails."
        ),
    }
