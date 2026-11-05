#!/usr/bin/env python3
"""Synthetic-only tests for paired SCR confirmation math primitives."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "dmgmd_paired_confirmation_math", HERE / "paired_confirmation_math.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load paired confirmation math module")
maths = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maths)


class PairedConfirmationMathTests(unittest.TestCase):
    @staticmethod
    def chains() -> dict[int, list[float]]:
        # Seed-specific offsets and within-seed temporal patterns ensure the
        # pooled variance differs from a within-seed-only variance estimate.
        return {
            seed: [float(10 * seed + ((i * (seed + 1)) % 7) - 3) for i in range(24)]
            for seed in range(201, 205)
        }

    def test_identical_paired_chains_have_zero_difference_and_unit_ratio(self) -> None:
        reference = self.chains()
        mean = maths.paired_mean_difference_ci(
            reference, reference, block_length=4, confidence_z=2.64)
        self.assertEqual(mean["estimate"], 0.0)
        self.assertEqual(mean["confidence_interval"], [0.0, 0.0])
        ratio = maths.paired_global_variance_ratio_ci(
            reference, reference, block_length=4, confidence_z=2.64,
            bootstrap_replicates=200, bootstrap_seed=2026110301)
        self.assertEqual(ratio["estimate"], 1.0)
        self.assertEqual(ratio["confidence_interval"], [1.0, 1.0])

    def test_known_offset_and_scale_propagate_through_paired_statistics(self) -> None:
        reference = self.chains()
        shifted = {seed: [x + 2.5 for x in values]
                   for seed, values in reference.items()}
        mean = maths.paired_mean_difference_ci(
            shifted, reference, block_length=4, confidence_z=2.64)
        self.assertAlmostEqual(mean["estimate"], 2.5, places=14)
        self.assertEqual(mean["confidence_interval"], [2.5, 2.5])
        scaled = {seed: [2.0 * x for x in values]
                  for seed, values in reference.items()}
        ratio = maths.paired_global_variance_ratio_ci(
            scaled, reference, block_length=4, confidence_z=2.64,
            bootstrap_replicates=200, bootstrap_seed=2026110301)
        self.assertAlmostEqual(ratio["estimate"], 4.0, places=13)
        self.assertEqual(ratio["confidence_interval"], [4.0, 4.0])

    def test_global_unbiased_variance_retains_between_seed_mean_differences(self) -> None:
        chains = self.chains()
        flat = [x for seed in sorted(chains) for x in chains[seed]]
        pooled_mean = sum(flat) / len(flat)
        expected = sum((x - pooled_mean) ** 2 for x in flat) / (len(flat) - 1)
        within_seed = sum(
            sum((x - sum(values) / len(values)) ** 2 for x in values)
            for values in chains.values()) / (len(flat) - len(chains))
        result = maths.paired_global_variance_ratio_ci(
            chains, {seed: list(values) for seed, values in chains.items()},
            block_length=4, confidence_z=2.64,
            bootstrap_replicates=100, bootstrap_seed=11)
        self.assertAlmostEqual(result["candidate_global_unbiased_variance"], expected)
        self.assertGreater(expected, within_seed * 10)

    def test_time_block_index_pairing_cancels_shared_temporal_structure(self) -> None:
        reference = self.chains()
        candidate = {}
        for seed, values in reference.items():
            candidate[seed] = [
                value + 1.0 + 0.25 * (i // 4) + 0.5 * (seed - 201)
                for i, value in enumerate(values)
            ]
        result = maths.paired_mean_difference_ci(
            candidate, reference, block_length=4, confidence_z=2.64)
        expected_offsets = [1.0 + 0.25 * block + 0.5 * (seed - 201)
                            for seed in sorted(reference) for block in range(6)]
        self.assertAlmostEqual(result["estimate"], sum(expected_offsets) / len(expected_offsets))
        self.assertEqual(result["complete_paired_blocks"], 24)
        # The paired block SE is computed from the known block-level offsets;
        # independently pairing different block indices would retain the large
        # shared temporal pattern and produce a much wider error estimate.
        mean = sum(expected_offsets) / len(expected_offsets)
        expected_se = (sum((x - mean) ** 2 for x in expected_offsets) /
                       (len(expected_offsets) - 1) / len(expected_offsets)) ** 0.5
        self.assertAlmostEqual(result["block_standard_error"], expected_se)

    def test_composite_interval_endpoints_add_and_multiply(self) -> None:
        mean = maths.combine_mean_intervals(
            {"estimate": 1.0, "confidence_interval": [0.7, 1.3]},
            {"estimate": -0.2, "confidence_interval": [-0.4, 0.1]})
        self.assertEqual(mean["estimate"], 0.8)
        self.assertAlmostEqual(mean["confidence_interval"][0], 0.3)
        self.assertAlmostEqual(mean["confidence_interval"][1], 1.4)
        variance = maths.combine_variance_ratio_intervals(
            {"estimate": 1.1, "confidence_interval": [0.9, 1.2]},
            {"estimate": 0.95, "confidence_interval": [0.8, 1.05]})
        self.assertAlmostEqual(variance["estimate"], 1.045)
        self.assertAlmostEqual(variance["confidence_interval"][0], 0.72)
        self.assertAlmostEqual(variance["confidence_interval"][1], 1.26)
        with self.assertRaises(ValueError):
            maths.combine_variance_ratio_intervals(
                {"estimate": 1.0, "confidence_interval": [0.0, 1.2]},
                {"estimate": 1.0, "confidence_interval": [0.9, 1.1]})

    def test_cdf_triangle_includes_all_three_empirical_links(self) -> None:
        mpi = [0.0, 0.0, 1.0, 1.0]
        p1_eight = [0.0, 1.0, 1.0, 1.0]
        p1_all = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        gpumd_all = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
        result = maths.cdf_triangle_bound(mpi, p1_eight, p1_all, gpumd_all)
        components = result["components"]
        self.assertEqual(set(components), {
            "mpi8_vs_p1_same8", "p1_same8_vs_p1_all32", "p1_all32_vs_gpumd_all32"})
        self.assertAlmostEqual(result["triangle_sum"], sum(components.values()))
        direct = maths._ecdf_distance(mpi, gpumd_all)
        self.assertGreaterEqual(result["triangle_sum"], direct)
        self.assertGreater(components["p1_same8_vs_p1_all32"], 0.0)

    def test_loose_cdf_triangle_bound_is_inconclusive_not_false_failure(self) -> None:
        mpi = [0.0] * 4
        p1_same8 = [0.0] * 4
        p1_full32 = [0.0] * 7 + [1.0]
        gpumd_full32 = [0.0] * 4
        result = maths.cdf_triangle_bound(mpi, p1_same8, p1_full32, gpumd_full32)
        self.assertGreater(result["triangle_sum"], 0.15)
        self.assertLessEqual(maths._ecdf_distance(mpi, gpumd_full32), 0.15)
        self.assertEqual(maths.cdf_triangle_decision(
            result["triangle_sum"], 0.15, enough=True), "INCONCLUSIVE")

    def test_invalid_pairing_and_nonfinite_values_are_rejected(self) -> None:
        values = {1: [1.0, 2.0, 3.0, 4.0], 2: [2.0, 3.0, 4.0, 5.0]}
        with self.assertRaisesRegex(ValueError, "same nonempty seed set"):
            maths.paired_mean_difference_ci(values, {1: values[1]},
                                            block_length=2, confidence_z=2.64)
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            maths.paired_mean_difference_ci(values, {1: values[1], 2: [1.0]},
                                            block_length=2, confidence_z=2.64)
        with self.assertRaisesRegex(ValueError, "only complete blocks"):
            maths.paired_mean_difference_ci(
                {1: [1.0, 2.0, 3.0], 2: [2.0, 3.0, 4.0]},
                {1: [1.0, 2.0, 3.0], 2: [2.0, 3.0, 4.0]},
                block_length=2, confidence_z=2.64)
        nonfinite = {1: [1.0, float("nan"), 3.0, 4.0], 2: values[2]}
        with self.assertRaisesRegex(ValueError, "non-finite"):
            maths.paired_global_variance_ratio_ci(
                nonfinite, values, block_length=2, confidence_z=2.64,
                bootstrap_replicates=20, bootstrap_seed=1)
        # Percentile intervals need not contain the sample point estimate.
        outlying = maths.combine_mean_intervals(
            {"estimate": 2.0, "confidence_interval": [0.0, 1.0]},
            {"estimate": 0.0, "confidence_interval": [-0.5, 0.5]})
        self.assertEqual(outlying["estimate"], 2.0)
        with self.assertRaisesRegex(ValueError, "strictly positive"):
            maths.combine_variance_ratio_intervals(
                {"estimate": -0.1, "confidence_interval": [0.1, 0.2]},
                {"estimate": 1.0, "confidence_interval": [0.9, 1.1]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
