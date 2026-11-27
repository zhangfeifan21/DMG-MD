#!/usr/bin/env python3
"""CPU-only checks of paired timing and resource accounting."""
import unittest

import paired_statistics as stats


def record(cell, block, wall_s, *, phase="sample", valid=True, tail_s=0, gpus=1):
    return dict(cell=cell, block=block, phase=phase, valid=valid, wall_s=wall_s,
                tail_s=tail_s, steady_ms_step=2.5, gpus=gpus)


def comparison(**fields):
    return dict(id="a-over-b", numerator="a", denominator="b") | fields


class PairedTimingTests(unittest.TestCase):
    def test_hand_calculated_three_block_interval(self):
        # Ratios 1, 2 and 4 have mean and sample SD both ln(2) on the log scale.
        # With df=2, the interval endpoints are 2**(1 +/- 4.3026527297/sqrt(3)).
        rows = [record("a", 3, 40), record("b", 1, 10), record("a", 1, 10),
                record("b", 3, 10), record("a", 2, 20), record("b", 2, 10)]
        ratio = stats.summarize(rows, [comparison()])["comparisons"][0]["wall_ratio"]
        self.assertEqual(ratio["n"], 3)
        self.assertEqual(ratio["degrees_of_freedom"], 2)
        self.assertAlmostEqual(ratio["estimate"], 2)
        self.assertAlmostEqual(ratio["t_critical"], 4.3026527297, places=8)
        self.assertAlmostEqual(ratio["ci95"][0], 0.3574621236, places=8)
        self.assertAlmostEqual(ratio["ci95"][1], 11.1899967464, places=8)
        self.assertEqual(ratio["status"], "unresolved")


    def test_unequal_frozen_counts_keep_both_full_absolute_summaries(self):
        rows = [record("a", block, wall) for block, wall in
                enumerate((100, 100, 100, 200, 200), start=1)]
        rows += [record("b", block, 25, gpus=4) for block in (1, 2, 3)]
        planned = [{field: row[field] for field in ("cell", "block", "phase")} for row in rows]
        result = stats.summarize(rows, [comparison(blocks=[1, 2, 3], divisor=4)],
                                 expected_records=planned)["comparisons"][0]
        self.assertEqual(result["blocks"], [1, 2, 3])
        self.assertEqual(result["numerator_summary"]["wall_s"]["mean"], 140)
        self.assertEqual(result["denominator_summary"]["wall_s"]["mean"], 25)
        self.assertEqual(result["wall_ratio"]["ci95"], [1, 1])
        self.assertEqual(result["wall_ratio"]["status"], "unresolved")
        self.assertAlmostEqual(result["denominator_summary"]["gpu_hours_total_valid"], 1 / 12)

        with self.assertRaisesRegex(ValueError, "every common"):
            stats.summarize(rows, [comparison(blocks=[1, 2])], expected_records=planned)
        with self.assertRaisesRegex(ValueError, "unequal counts"):
            stats.summarize(rows, [comparison(blocks=[1, 2, 3])])

    def test_invalid_records_leave_auditable_pairs_and_valid_side_cost(self):
        invalid = dict(cell="a", block=2, phase="sample", valid=False, wall_s=34, gpus=2,
                       error="nonzero exit", log_path="a/b2/stdout.log")
        valid_peer = record("b", 2, 10)
        rows = [record("a", 1, 20, gpus=2), record("b", 1, 10), invalid, valid_peer]
        result = stats.summarize(rows, [comparison()])
        report = result["comparisons"][0]
        self.assertEqual(report["excluded_blocks"], [2])
        self.assertEqual(report["pairs"][1]["numerator"], invalid)
        self.assertEqual(report["pairs"][1]["denominator"], valid_peer)
        self.assertEqual(report["wall_ratio"]["n"], 1)
        self.assertIsNone(report["wall_ratio"]["ci95"])
        self.assertEqual(report["wall_ratio"]["status"], "insufficient_pairs")
        self.assertEqual(report["denominator_summary"]["valid_runs"], 2)
        self.assertAlmostEqual(result["cells"]["a"]["gpu_hours_total_measured"], 0.03)

    def test_tail_trigger_and_phase_exclusion(self):
        rows = [record("a", block, 100, tail_s=1) for block in (1, 2)]
        rows += [record("b", block, 50, tail_s=0.5) for block in (1, 2)]
        rows += [record("a", 1, 1000, phase="warmup", tail_s=500),
                 record("b", 1, 200, phase="pilot", tail_s=100)]
        report = stats.summarize(rows, [comparison()])["comparisons"][0]
        self.assertIsNone(report["tail_adjusted_ratio"])
        rows[0]["tail_s"] = 20
        report = stats.summarize(rows, [comparison()])["comparisons"][0]
        self.assertEqual(report["wall_ratio"]["ci95"], [2, 2])
        self.assertEqual(report["wall_ratio"]["status"], "above_one")
        self.assertEqual(report["tail_trigger_blocks"], [1])
        # Adjusted ratios are 80/49.5 and 99/49.5, geometric mean sqrt(320/99).
        self.assertAlmostEqual(report["tail_adjusted_ratio"]["estimate"], 1.7978662999)
        self.assertEqual(report["tail_adjusted_ratio"]["n"], 2)

    def test_tail_bounds_report_each_kind_of_threshold_crossing(self):
        cases = (
            ([0.25, 1.25], 0.75, "upper_bound_only_exceeds", 1.985),
            ([0.75, 1.75], 1.25, "midpoint_exceeds", 1.975),
            ([1.25, 1.75], 1.50, "certain_exceeds", 1.970),
            ([0.50, 1.00], 0.75, "within_threshold", None),
        )
        for bounds, midpoint, expected_status, adjusted_ratio in cases:
            with self.subTest(bounds=bounds):
                rows = [record("a", 1, 100, tail_s=midpoint) | {"tail_bounds_s": bounds},
                        record("b", 1, 50)]
                result = stats.summarize(rows, [comparison()])["comparisons"][0]
                self.assertEqual(result["wall_ratio"]["estimate"], 2)
                self.assertEqual(result["tail_trigger_blocks"], [] if adjusted_ratio is None else [1])
                self.assertEqual(result["pairs"][0]["tail_threshold"]["numerator"], expected_status)
                self.assertEqual(result["pairs"][0]["numerator"]["tail_bounds_s"], bounds)
                if adjusted_ratio is None:
                    self.assertIsNone(result["tail_adjusted_ratio"])
                else:
                    self.assertAlmostEqual(result["tail_adjusted_ratio"]["estimate"], adjusted_ratio)
                    self.assertEqual(result["tail_adjustment_method"],
                                     "midpoint_of_bounds_else_recorded_tail")

    def test_frozen_plan_detects_absent_entire_block_and_duplicates(self):
        rows = [record(cell, block, 10) for cell in ("a", "b") for block in (1, 2, 3)]
        planned = [{field: row[field] for field in ("cell", "block", "phase")} for row in rows]
        absent_block = [row for row in rows if row["block"] < 3]
        with self.assertRaisesRegex(ValueError, "missing="):
            stats.summarize(absent_block, [comparison()], expected_records=planned)
        with self.assertRaisesRegex(ValueError, "duplicate raw record"):
            stats.summarize(rows + [rows[0]], [comparison()], expected_records=planned)
        with self.assertRaisesRegex(ValueError, "duplicate expected record"):
            stats.summarize(rows, [comparison()], expected_records=planned + [planned[0]])

    def test_rejects_missing_internal_absolute_block_without_plan(self):
        rows = [record("a", 1, 20), record("a", 3, 20)]
        with self.assertRaisesRegex(ValueError, "missing raw block"):
            stats.summarize(rows, [])


    def test_explicit_pilot_replay_preserves_its_phase(self):
        rows = [record("a", 1, 4, phase="pilot"), record("b", 1, 2, phase="pilot")]
        result = stats.summarize(rows, [comparison()], expected_records=rows, phase="pilot")
        self.assertEqual(result["phase"], "pilot")
        self.assertEqual(result["comparisons"][0]["wall_ratio"]["n"], 1)
        sample = stats.summarize(rows, [], expected_records=rows)
        self.assertEqual(sample["phase"], "sample")
        self.assertEqual(sample["cells"], {})


class TimingBudgetTests(unittest.TestCase):
    def test_steps_duration_and_exclusive_window(self):
        sample = dict(id="science", ticket="09", category="sample", retained_steps=1000,
                      discarded_steps=200, ms_step=10, fixed_overhead_s=3,
                      runs=4, gpus=2)
        warmup = dict(id="warmup", ticket="13", category="warmup", retained_duration_ps=0.05001,
                      discarded_duration_ps=0.0499999, time_step_fs=1, ms_step=100,
                      fixed_overhead_s=0.9, runs=2, gpus=4)
        debug = dict(id="debug", ticket="25", category="debug", retained_steps=0,
                     discarded_steps=0, ms_step=10, fixed_overhead_s=10, runs=1, gpus=1)
        result = stats.calculate_budget([sample, warmup, debug], exclusive_window_h=0.025)

        # Sample: 40 s retained + 8 s discarded + 12 s fixed, using two GPUs.
        # Warmup: 51 and 50 steps, or 22 s with overhead, using four GPUs.
        # Debug: 10 s overhead using one GPU. The 90 s window is short by 2 s.
        self.assertAlmostEqual(result["by_ticket"]["09"]["retained_node_h"] * 3600, 40)
        self.assertAlmostEqual(result["by_ticket"]["09"]["discarded_node_h"] * 3600, 8)
        self.assertAlmostEqual(result["by_ticket"]["09"]["fixed_overhead_node_h"] * 3600, 12)
        self.assertEqual(result["items"][1]["retained_steps"], 51)
        self.assertEqual(result["items"][1]["discarded_steps"], 50)
        self.assertAlmostEqual(result["by_category"]["warmup"]["gpu_hours"] * 3600, 88)
        self.assertAlmostEqual(result["by_category"]["debug"]["gpu_hours"] * 3600, 10)
        self.assertAlmostEqual(result["totals"]["exclusive_node_h"] * 3600, 92)
        self.assertAlmostEqual(result["totals"]["gpu_hours"] * 3600, 218)
        self.assertAlmostEqual(result["remaining_exclusive_window_h"] * 3600, -2)
        self.assertFalse(result["fits_exclusive_window"])


if __name__ == "__main__":
    unittest.main()
