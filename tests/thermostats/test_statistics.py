import math
import importlib.util
import os
import pathlib
import random
import unittest
from unittest import mock

_MODULE_SPEC = importlib.util.spec_from_file_location(
    "thermostat_statistics", pathlib.Path(__file__).with_name("sample_statistics.py")
)
_MODULE = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(_MODULE)
analyze_series = _MODULE.analyze_series


def config(**overrides):
    values = {
        "mean_relative_bound": 0.10,
        "variance_relative_bound": 0.50,
        "confidence_z": 1.96,
        "block_length": 40,
        "minimum_effective_samples": 500,
        "minimum_blocks": 32,
    }
    values.update(overrides)
    return values


class AnalyzeSeriesTests(unittest.TestCase):
    def test_independent_seeds_combine_and_recover_iid_moments(self):
        chains = []
        for seed in (1729, 2718, 31415):
            rng = random.Random(seed)
            chains.append([0.5 + rng.gauss(0.0, 1.0) for _ in range(4000)])

        result = analyze_series(chains, 0.5, 1.0, config())
        self.assertEqual(result["chain_count"], 3)
        self.assertEqual(result["sample_count"], 12000)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["mean"]["decision"], "PASS")
        self.assertEqual(result["variance"]["decision"], "PASS")
        self.assertTrue(all(chain["effective_samples_mean"] > 3000 for chain in result["chains"]))

    def test_ar1_correlation_reduces_effective_sample_count(self):
        rng = random.Random(8675309)
        phi = 0.8
        value = rng.gauss(0.0, 1.0)
        chain = []
        innovation_scale = math.sqrt(1.0 - phi * phi)
        for _ in range(5000):
            value = phi * value + innovation_scale * rng.gauss(0.0, 1.0)
            chain.append(value)

        result = analyze_series([chain], 0.0, 1.0, config(minimum_blocks=2))
        diagnostics = result["chains"][0]
        self.assertGreater(diagnostics["tau_mean"], 3.0)
        self.assertLess(diagnostics["effective_samples_mean"], len(chain) / 3.0)

    def test_too_few_complete_blocks_is_inconclusive(self):
        chains = [[0.5 + ((index % 7) - 3) * 0.01 for index in range(120)]]
        result = analyze_series(
            chains,
            0.5,
            0.0005,
            config(block_length=100, minimum_blocks=32, minimum_effective_samples=1),
        )
        self.assertEqual(result["blocks"]["complete_count"], 1)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertNotEqual(result["status"], "PASS")

    def test_precise_conflicting_mean_fails_registered_band(self):
        chain = [0.0 if index % 2 == 0 else 1.0 for index in range(4000)]
        result = analyze_series(
            [chain, chain.copy()],
            3.0,
            0.25,
            config(mean_relative_bound=0.02, variance_relative_bound=0.15),
        )
        self.assertEqual(result["mean"]["decision"], "FAIL")
        self.assertEqual(result["status"], "FAIL")

    def test_constant_data_cannot_pass_variance_gate(self):
        result = analyze_series(
            [[1.0] * 400, [1.0] * 400],
            1.0,
            1.0,
            config(block_length=10, minimum_blocks=32, minimum_effective_samples=100),
        )
        self.assertTrue(result["variance"]["degenerate"])
        self.assertEqual(result["variance"]["decision"], "INCONCLUSIVE")
        self.assertEqual(result["status"], "INCONCLUSIVE")

    def test_invalid_lengths_and_nonfinite_samples_are_errors(self):
        with self.assertRaisesRegex(ValueError, "at least 10 samples"):
            analyze_series([[1.0] * 9], 1.0, 1.0, config())
        with self.assertRaisesRegex(ValueError, "non-finite"):
            analyze_series([[1.0] * 9 + [float("nan")]], 1.0, 1.0, config())


class AutocorrelationHelperTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("DMGMD_ACF_HELPER"), "native helper is not configured")
    def test_native_helper_matches_python_on_representative_series(self):
        rng = random.Random(5112026)
        iid = [rng.gauss(0.0, 1.0) for _ in range(600)]
        ar1 = []
        value = 0.0
        for _ in range(600):
            value = 0.75 * value + math.sqrt(1.0 - 0.75**2) * rng.gauss(0.0, 1.0)
            ar1.append(value)
        oscillatory = [math.sin(2.0 * math.pi * index / 10.0) + 0.03 * math.cos(index) for index in range(600)]
        constant = [2.5] * 600

        helper = os.environ["DMGMD_ACF_HELPER"]
        with mock.patch.dict(os.environ, {"DMGMD_ACF_HELPER": helper}):
            for values in (iid, ar1, oscillatory, constant):
                max_lag = min(len(values) // 10, 2048)
                expected = _MODULE._integrated_autocorrelation_time_python(values, max_lag)
                actual = _MODULE._integrated_autocorrelation_time(values, max_lag)
                self.assertAlmostEqual(actual, expected, delta=1.0e-10)

    def test_configured_but_missing_helper_fails_without_fallback(self):
        with mock.patch.dict(os.environ, {"DMGMD_ACF_HELPER": "/path/that/does/not/exist"}):
            with self.assertRaisesRegex(RuntimeError, "cannot execute DMGMD_ACF_HELPER"):
                _MODULE._integrated_autocorrelation_time([float(index) for index in range(100)], 10)


class ConservativeConfidenceIntervalTests(unittest.TestCase):
    def test_student_t_critical_values_at_registered_95_percent_level(self):
        z_95 = 1.959963984540054
        self.assertAlmostEqual(_MODULE._student_t_critical(z_95, 2), 4.3026527297, places=8)
        self.assertAlmostEqual(_MODULE._student_t_critical(z_95, 7), 2.3646242510, places=8)

    def test_long_term_chain_offsets_cannot_be_hidden_by_many_blocks(self):
        chains = []
        for offset in (-2.0, -1.0, 1.0, 2.0):
            chains.append([offset + 0.001 * ((index % 4) - 1.5) for index in range(1000)])

        result = analyze_series(
            chains,
            0.0,
            2.5,
            config(
                mean_relative_bound=0.02,
                variance_relative_bound=0.15,
                block_length=10,
                minimum_blocks=32,
                minimum_effective_samples=100,
            ),
        )
        mean = result["mean"]
        self.assertIsNotNone(mean["chain_standard_error"])
        self.assertGreater(mean["chain_standard_error"], mean["block_standard_error"])
        self.assertGreater(mean["confidence_half_width"], 2.0)
        self.assertAlmostEqual(
            mean["confidence_half_width"],
            mean["chain_standard_error"] * mean["chain_t_critical"],
            places=12,
        )
        variance = result["variance"]
        self.assertGreater(
            variance["confidence_half_width"],
            variance["block_standard_error"] * variance["block_t_critical"],
        )


if __name__ == "__main__":
    unittest.main()
