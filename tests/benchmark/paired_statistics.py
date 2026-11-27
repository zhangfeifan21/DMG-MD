#!/usr/bin/env python3
"""Recompute paired timing and resource budgets from recorded jobs."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from fractions import Fraction
from pathlib import Path
from statistics import NormalDist, fmean, stdev


_STATS_PATH = Path(__file__).resolve().parents[1] / "thermostats/sample_statistics.py"
_SPEC = importlib.util.spec_from_file_location("dmgmd_shared_sample_statistics", _STATS_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load shared statistics primitives: {_STATS_PATH}")
_STATS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_STATS)

CONFIDENCE_Z = NormalDist().inv_cdf(0.975)
TAIL_FRACTION = 0.01
TAIL_TRIGGERS = {"certain_exceeds", "midpoint_exceeds", "upper_bound_only_exceeds", "scalar_exceeds"}
RECORD_PHASES = ("pilot", "warmup", "sample", "smoke")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _positive_int(value):
    return type(value) is int and value > 0


def _record_key(row):
    if not isinstance(row, dict):
        raise ValueError("record must be an object")
    cell, block, phase = row.get("cell"), row.get("block"), row.get("phase")
    if not isinstance(cell, str) or not cell.strip():
        raise ValueError("record cell must be a nonempty string")
    if not _positive_int(block) or phase not in RECORD_PHASES:
        raise ValueError(f"{cell}: invalid block number or phase")
    return cell, block, phase


def _index_records(records):
    index = {}
    for row in records:
        key = _record_key(row)
        if key in index:
            raise ValueError(f"duplicate raw record: {key}")
        index[key] = row
        if type(row.get("valid")) is not bool:
            raise ValueError(f"{key}: valid must be a boolean")
        if not row["valid"]:
            continue
        wall, tail, steady = (row.get(field) for field in ("wall_s", "tail_s", "steady_ms_step"))
        if not _positive_int(row.get("gpus")) or not _finite(wall) or wall <= 0:
            raise ValueError(f"{key}: valid record requires positive gpus and wall_s")
        if (key[2] == "sample" or tail is not None) and not (_finite(tail) and 0 <= tail < wall):
            raise ValueError(f"{key}: valid record requires 0 <= tail_s < wall_s")
        bounds = row.get("tail_bounds_s")
        if bounds is not None and (
            not isinstance(bounds, (list, tuple)) or len(bounds) != 2
            or not all(_finite(bound) for bound in bounds)
            or not 0 <= bounds[0] <= bounds[1] <= wall
            or sum(bounds) / 2 >= wall
        ):
            raise ValueError(f"{key}: valid record requires ordered tail_bounds_s within wall_s")
        if (key[2] == "sample" or steady is not None) and not (_finite(steady) and steady > 0):
            raise ValueError(f"{key}: valid record requires positive steady_ms_step")
    return index


def _validate_plan(index, expected_records):
    expected = set()
    for row in expected_records:
        key = _record_key(row)
        if key in expected:
            raise ValueError(f"duplicate expected record: {key}")
        expected.add(key)
    missing, unexpected = expected.difference(index), index.keys() - expected
    if missing or unexpected:
        raise ValueError(f"raw records differ from plan: missing={sorted(missing)}, "
                         f"unexpected={sorted(unexpected)}")


def _absolute(values):
    if not values:
        return {"n": 0, "mean": None, "min": None, "max": None}
    return {"n": len(values), "mean": fmean(values), "min": min(values), "max": max(values)}


def _gpu_hours(row):
    wall, gpus = row.get("wall_s"), row.get("gpus")
    if _finite(wall) and wall >= 0 and _positive_int(gpus):
        return wall * gpus / 3600
    return None


def _cell_summary(rows):
    if {row["block"] for row in rows} != set(range(1, len(rows) + 1)):
        raise ValueError(f"{rows[0]['cell']}: missing raw block in a cell")
    valid = [row for row in rows if row["valid"]]
    gpu_counts = {row["gpus"] for row in valid}
    if len(gpu_counts) > 1:
        raise ValueError(f"{rows[0]['cell']}: valid records disagree on GPU count")
    valid_gpu_hours = [_gpu_hours(row) for row in valid]
    measured_costs = [cost for row in rows if (cost := _gpu_hours(row)) is not None]
    return {
        "attempted_runs": len(rows),
        "valid_runs": len(valid),
        "invalid_blocks": sorted(row["block"] for row in rows if not row["valid"]),
        "gpus": next(iter(gpu_counts), None),
        "wall_s": _absolute([row["wall_s"] for row in valid]),
        "steady_ms_step": _absolute([row["steady_ms_step"] for row in valid
                                     if row.get("steady_ms_step") is not None]),
        "gpu_hours_per_valid_run": _absolute(valid_gpu_hours),
        "gpu_hours_total_valid": math.fsum(valid_gpu_hours),
        "gpu_hours_total_measured": math.fsum(measured_costs),
        "gpu_hours_measured_runs": len(measured_costs),
    }


def _ratio_interval(log_ratios):
    n = len(log_ratios)
    result = {"n": n, "estimate": None, "ci95": None,
              "degrees_of_freedom": max(n - 1, 0), "t_critical": None,
              "status": "insufficient_pairs"}
    if not n:
        return result
    mean_log = fmean(log_ratios)
    result["estimate"] = math.exp(mean_log)
    if n < 2:
        return result
    critical = _STATS._student_t_critical(CONFIDENCE_Z, n - 1)
    half_width = critical * stdev(log_ratios) / math.sqrt(n)
    low, high = math.exp(mean_log - half_width), math.exp(mean_log + half_width)
    status = "unresolved"
    if low > 1:
        status = "above_one"
    elif high < 1:
        status = "below_one"
    return result | {"ci95": [low, high], "t_critical": critical, "status": status}


def _comparison_blocks(left, right, comparison, has_plan):
    name = comparison["id"]
    common = left.keys() & right.keys()
    if not common:
        raise ValueError(f"{name}: no raw blocks in common")
    if left.keys() != right.keys() and (not has_plan or "blocks" not in comparison):
        raise ValueError(f"{name}: missing raw block or unequal counts without a frozen pairing")
    if "blocks" in comparison:
        blocks = comparison["blocks"]
        if (not isinstance(blocks, (list, tuple)) or not all(_positive_int(b) for b in blocks)
                or len(blocks) != len(common) or set(blocks) != common):
            raise ValueError(f"{name}: comparison blocks must match every common raw block")
    return sorted(common)


def _tail_observation(row):
    limit = TAIL_FRACTION * row["wall_s"]
    bounds = row.get("tail_bounds_s")
    if bounds is None:
        recorded = row.get("tail_s")
        if recorded is None:
            return None, "missing"
        return recorded, "scalar_exceeds" if recorded > limit else "within_threshold"

    lower, upper = bounds
    midpoint = sum(bounds) / 2
    if lower > limit:
        return midpoint, "certain_exceeds"
    if midpoint > limit:
        return midpoint, "midpoint_exceeds"
    if upper > limit:
        return midpoint, "upper_bound_only_exceeds"
    return midpoint, "within_threshold"


def _compare(comparison, rows_by_cell, cells, has_plan):
    if not isinstance(comparison, dict):
        raise ValueError("comparison must be an object")
    name, numerator, denominator = (comparison.get(field) for field in
                                    ("id", "numerator", "denominator"))
    if not isinstance(name, str) or not name.strip():
        raise ValueError("comparison id must be a nonempty string")
    if (not isinstance(numerator, str) or not isinstance(denominator, str)
            or numerator == denominator or numerator not in cells or denominator not in cells):
        raise ValueError(f"{name}: comparison requires two distinct cells in the selected phase")
    divisor = comparison.get("divisor", 1)
    if not _finite(divisor) or divisor <= 0:
        raise ValueError(f"{name}: comparison divisor must be positive")
    left, right = rows_by_cell[numerator], rows_by_cell[denominator]
    blocks = _comparison_blocks(left, right, comparison, has_plan)
    pairs, excluded, log_ratios, log_adjusted = [], [], [], []
    tail_trigger_blocks, tail_missing_blocks = [], []
    for block in blocks:
        a, b = left[block], right[block]
        valid = a["valid"] and b["valid"]
        pair = {"block": block, "valid": valid, "numerator": a, "denominator": b}
        pairs.append(pair)
        if not valid:
            excluded.append(block)
            continue
        log_ratios.append(math.log(a["wall_s"] / b["wall_s"] / divisor))
        tail_a, status_a = _tail_observation(a)
        tail_b, status_b = _tail_observation(b)
        pair["tail_threshold"] = {"numerator": status_a, "denominator": status_b}
        if status_a in TAIL_TRIGGERS or status_b in TAIL_TRIGGERS:
            tail_trigger_blocks.append(block)
        if tail_a is None or tail_b is None:
            tail_missing_blocks.append(block)
        else:
            adjusted_a, adjusted_b = a["wall_s"] - tail_a, b["wall_s"] - tail_b
            log_adjusted.append(math.log(adjusted_a / adjusted_b / divisor))
    tail_ratio = None
    if tail_trigger_blocks and not tail_missing_blocks:
        tail_ratio = _ratio_interval(log_adjusted)
    return {
        "id": name, "numerator": numerator, "denominator": denominator, "divisor": divisor,
        "numerator_summary": cells[numerator], "denominator_summary": cells[denominator],
        "blocks": blocks, "excluded_blocks": excluded, "pairs": pairs,
        "wall_ratio": _ratio_interval(log_ratios), "tail_adjusted_ratio": tail_ratio,
        "tail_adjustment_method": "midpoint_of_bounds_else_recorded_tail" if tail_ratio else None,
        "tail_trigger_blocks": tail_trigger_blocks, "tail_missing_blocks": tail_missing_blocks,
    }


def summarize(records, comparisons, *, expected_records=None, phase="sample"):
    """Analyze one phase, preserving unsuccessful records and the logs they identify.

    Expected records are frozen cell/block/phase triples. Provide this plan to
    detect jobs missing from both cells, and to compare cells with unequal
    planned counts. A comparison's explicit blocks must cover the full planned
    intersection. Absolute cell summaries use every valid job in the selected
    phase. Ratio intervals use only jointly valid pairs. A tail bound above
    1% of its job wall time triggers a midpoint-adjusted sensitivity ratio.
    For scalar-only records, the recorded tail supplies both decisions.
    """
    if phase not in ("pilot", "sample", "smoke"):
        raise ValueError("paired analysis phase must be pilot, sample or smoke")
    records = list(records)
    index = _index_records(records)
    if expected_records is not None:
        _validate_plan(index, expected_records)
    rows_by_cell = {}
    for (cell, block, record_phase), row in index.items():
        if record_phase == phase:
            rows_by_cell.setdefault(cell, {})[block] = row
    cells = {cell: _cell_summary(list(rows.values())) for cell, rows in sorted(rows_by_cell.items())}
    reports = []
    seen = set()
    for comparison in comparisons:
        report = _compare(comparison, rows_by_cell, cells, expected_records is not None)
        if report["id"] in seen:
            raise ValueError(f"duplicate comparison id: {report['id']}")
        seen.add(report["id"])
        reports.append(report)
    return {"phase": phase, "records": records, "cells": cells, "comparisons": reports}


BUDGET_CATEGORIES = ("pilot", "warmup", "sample", "debug", "smoke")
BUDGET_FIELDS = (
    "retained_node_h", "discarded_node_h", "fixed_overhead_node_h", "exclusive_node_h",
    "retained_gpu_hours", "discarded_gpu_hours", "fixed_overhead_gpu_hours", "gpu_hours",
)


def _budget_steps(item):
    step_fields = ("retained_steps", "discarded_steps")
    duration_fields = ("retained_duration_ps", "discarded_duration_ps")
    if any(field in item for field in step_fields):
        if any(field in item for field in (*duration_fields, "time_step_fs")):
            raise ValueError(f"{item['id']}: supply steps or durations with time_step_fs")
        steps = tuple(item.get(field) for field in step_fields)
        if not all(type(value) is int and value >= 0 for value in steps):
            raise ValueError(f"{item['id']}: step counts must be nonnegative integers")
        return steps

    durations = tuple(item.get(field) for field in duration_fields)
    if not all(_finite(value) and value >= 0 for value in durations):
        raise ValueError(f"{item['id']}: retained and discarded durations must be nonnegative")
    step_fs = item.get("time_step_fs")
    if not _finite(step_fs) or step_fs <= 0:
        raise ValueError(f"{item['id']}: time_step_fs must be positive")
    # Round each segment up to a whole integration step. Use the supplied
    # decimal values so an exact whole-step duration stays on its boundary.
    return tuple(math.ceil(Fraction(str(value)) * 1000 / Fraction(str(step_fs)))
                 for value in durations)


def _budget_item(item):
    if not isinstance(item, dict):
        raise ValueError("budget item must be an object")
    for key in ("id", "ticket"):
        label = item.get(key)
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"budget item {key} must be a nonempty string")
    name = item["id"]
    if item.get("category") not in BUDGET_CATEGORIES:
        raise ValueError(f"{name}: category must be one of {', '.join(BUDGET_CATEGORIES)}")
    runs, gpus = item.get("runs"), item.get("gpus")
    if not _positive_int(runs) or not _positive_int(gpus):
        raise ValueError(f"{name}: runs and gpus must be positive integers")
    ms_per_step, fixed = item.get("ms_step"), item.get("fixed_overhead_s")
    if not _finite(ms_per_step) or ms_per_step <= 0 or not _finite(fixed) or fixed < 0:
        raise ValueError(f"{name}: ms_step must be positive and fixed_overhead_s nonnegative")
    retained_steps, discarded_steps = _budget_steps(item)
    retained_h = retained_steps * ms_per_step * runs / 3_600_000
    discarded_h = discarded_steps * ms_per_step * runs / 3_600_000
    fixed_h = fixed * runs / 3600
    total_h = math.fsum((retained_h, discarded_h, fixed_h))
    return item | {
        "retained_steps": retained_steps,
        "discarded_steps": discarded_steps,
        "retained_node_h": retained_h,
        "discarded_node_h": discarded_h,
        "fixed_overhead_node_h": fixed_h,
        "exclusive_node_h": total_h,
        "retained_gpu_hours": retained_h * gpus,
        "discarded_gpu_hours": discarded_h * gpus,
        "fixed_overhead_gpu_hours": fixed_h * gpus,
        "gpu_hours": total_h * gpus,
    }


def _budget_total(rows):
    return {field: math.fsum(row[field] for row in rows) for field in BUDGET_FIELDS}


def calculate_budget(items, *, exclusive_window_h=None):
    """Budget serial jobs on an exclusive node.

    Each item names id, ticket, category, runs, gpus, ms_step and
    fixed_overhead_s per run. Specify retained_steps and discarded_steps, or
    retained_duration_ps and discarded_duration_ps with time_step_fs.
    Items retain any evidence metadata, such as timing_source.
    """
    if exclusive_window_h is not None and (
        not _finite(exclusive_window_h) or exclusive_window_h < 0
    ):
        raise ValueError("exclusive_window_h must be nonnegative")
    rows, seen, by_ticket, by_category = [], set(), {}, {}
    for item in items:
        row = _budget_item(item)
        if row["id"] in seen:
            raise ValueError(f"duplicate budget item id: {row['id']}")
        seen.add(row["id"])
        rows.append(row)
        by_ticket.setdefault(row["ticket"], []).append(row)
        by_category.setdefault(row["category"], []).append(row)
    totals = _budget_total(rows)
    remaining = (None if exclusive_window_h is None
                 else exclusive_window_h - totals["exclusive_node_h"])
    return {
        "items": rows,
        "totals": totals,
        "by_ticket": {label: _budget_total(group) for label, group in sorted(by_ticket.items())},
        "by_category": {label: _budget_total(group) for label, group in sorted(by_category.items())},
        "exclusive_window_h": exclusive_window_h,
        "remaining_exclusive_window_h": remaining,
        "fits_exclusive_window": None if remaining is None else remaining >= 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("summary", "budget"))
    parser.add_argument("input", type=Path, help="JSON object with raw records or budget items")
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("input must be a JSON object")
        if args.command == "summary":
            result = summarize(data["records"], data["comparisons"],
                               expected_records=data.get("expected_records"),
                               phase=data.get("phase", "sample"))
        else:
            result = calculate_budget(data["items"],
                                      exclusive_window_h=data.get("exclusive_window_h"))
        output = json.dumps(result, indent=2, allow_nan=False)
    except (OSError, KeyError, TypeError, ValueError, OverflowError) as error:
        parser.error(str(error))
    print(output)


if __name__ == "__main__":
    main()
