#!/usr/bin/env python3
"""Check registered ordinary GPUMD public-input preparation stages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
from statistics import mean, stdev
import sys
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "thermostats"))
from sample_statistics import _integrated_autocorrelation_time_python


THERMO_COLUMNS = "T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz"
MANIFEST = json.loads(Path(__file__).with_name("manifest.json").read_text(encoding="utf-8"))
STAGES = MANIFEST["stages"]
CONDITIONS = MANIFEST["conditions"]


def thermo_schedule(stage: str) -> tuple[tuple[int, int], ...]:
    return tuple(
        (segment["steps"], segment["thermo_interval_steps"])
        for segment in STAGES[stage]["segments"]
    )


SCHEDULE = {stage: thermo_schedule(stage) for stage in ("berendsen", "scr", "scr-extension")}
OBSERVABLES = (
    ("temperature_K", "Temperature (K)", CONDITIONS["temperature_K"]),
    ("pressure_GPa", "Pressure (GPa)", CONDITIONS["pressure_GPa"]),
    ("volume_A3", "Volume (Å³)", None),
)


class Sample(NamedTuple):
    step: int
    temperature_K: float
    pressure_GPa: float
    volume_A3: float


def read_thermo(
    path: Path, schedule: tuple[tuple[int, int], ...], dt_fs: float
) -> tuple[int, list[Sample]]:
    """Recover global end-of-step indices from GPUMD's headers and segment lengths."""
    if not math.isfinite(dt_fs) or dt_fs <= 0:
        raise ValueError("time step must be finite and positive")

    samples = []
    atoms = 0
    start = 0
    with path.open() as stream:
        for segment, (steps, stride) in enumerate(schedule, 1):
            header = [stream.readline().split() for _ in range(5)]
            if (
                header[0] != ["#", "dump_thermo", str(stride)]
                or header[1] != ["#", "format_version", "1"]
                or header[4] != ["#", "columns", *THERMO_COLUMNS.split()]
            ):
                raise ValueError(f"unexpected thermo header in segment {segment}")
            if header[2][:2] != ["#", "num_atoms"] or len(header[2]) != 3:
                raise ValueError(f"missing atom count in segment {segment}")
            segment_atoms = int(header[2][2])
            if segment_atoms <= 0 or (atoms and atoms != segment_atoms):
                raise ValueError(f"inconsistent atom count in segment {segment}")
            atoms = segment_atoms
            if (
                header[3][:2] != ["#", "dt_output"]
                or len(header[3]) != 4
                or header[3][3] != "fs"
                or not math.isclose(float(header[3][2]), stride * dt_fs, rel_tol=1e-10)
            ):
                raise ValueError(f"unexpected thermo output interval in segment {segment}")

            for step in range(start + stride, start + steps + 1, stride):
                fields = stream.readline().split()
                if len(fields) != 18:
                    raise ValueError(f"expected 18 thermo columns at step {step}")
                row = tuple(float(field) for field in fields)
                if not all(math.isfinite(value) for value in row):
                    raise ValueError(f"nonfinite thermo value at step {step}")
                lattice = row[9:]
                if (
                    min(lattice[0], lattice[4], lattice[8]) <= 0
                    or any(lattice[index] != 0 for index in (1, 2, 3, 5, 6, 7))
                ):
                    raise ValueError(f"expected a positive diagonal lattice at step {step}")
                sample = Sample(step, row[0], mean(row[3:6]), lattice[0] * lattice[4] * lattice[8])
                if sample.temperature_K <= 0 or not 0 < sample.volume_A3 < math.inf:
                    raise ValueError(f"invalid temperature or volume at step {step}")
                samples.append(sample)
            start += steps
        if stream.read().strip():
            raise ValueError("unexpected data after the final thermo sample")
    return atoms, samples


def volume_drift(samples: list[Sample]) -> dict:
    steps = [sample.step for sample in samples]
    volumes = [sample.volume_A3 for sample in samples]
    center_step = mean(steps)
    center_volume = mean(volumes)
    slope = math.fsum(
        (step - center_step) * (volume - center_volume)
        for step, volume in zip(steps, volumes)
    ) / math.fsum((step - center_step) ** 2 for step in steps)
    return {
        "samples": len(samples),
        "first_step": steps[0],
        "last_step": steps[-1],
        "mean_volume_A3": center_volume,
        "volume_slope_A3_per_step": slope,
        "relative_end_to_end_drift": abs(slope * (steps[-1] - steps[0])) / center_volume,
    }


def effective_samples(values: list[float], max_lag: int) -> dict:
    # A constant trace cannot establish a sampled fluctuation.
    if min(values) == max(values):
        return {"tau_samples": None, "effective_samples": 0.0}
    tau = _integrated_autocorrelation_time_python(values, max_lag)
    return {"tau_samples": tau, "effective_samples": len(values) / tau}


def check_stage(stage: str, samples: list[Sample]) -> dict:
    criteria_stage = STAGES[stage].get("criteria_from_stage", stage)
    criteria = STAGES[criteria_stage]["criteria"]
    volume_sd_limit = criteria["relative_volume_sd_upper_bound_exclusive"]
    selected = samples
    if stage == "berendsen":
        _, uniform_interval = SCHEDULE[stage][0]
        selected = [sample for sample in samples if sample.step % uniform_interval == 0]

    volumes = [sample.volume_A3 for sample in selected]
    volume_mean = mean(volumes)
    relative_volume_sd = stdev(volumes) / volume_mean
    result = {
        "statistics_samples": len(selected),
        "mean_temperature_K": mean(sample.temperature_K for sample in selected),
        "mean_pressure_GPa": mean(sample.pressure_GPa for sample in selected),
        "mean_volume_A3": volume_mean,
        "sample_relative_sd_volume": relative_volume_sd,
    }
    if stage == "berendsen":
        terminal_steps = criteria["terminal_drift_steps"]
        total_steps = sum(steps for steps, _ in SCHEDULE[stage])
        drift = volume_drift([sample for sample in samples if sample.step > total_steps - terminal_steps])
        drift_limit = criteria["relative_terminal_drift_upper_bound_inclusive"]
        result[f"final_{terminal_steps}_steps"] = drift
        result["checks"] = {
            f"relative_volume_sd_lt_{volume_sd_limit:g}": relative_volume_sd < volume_sd_limit,
            f"relative_terminal_drift_le_{drift_limit:g}": drift["relative_end_to_end_drift"] <= drift_limit,
        }
    else:
        max_lag = len(samples) // criteria["acf_max_lag_sample_count_divisor"]
        centered_squared_volumes = [(volume - volume_mean) ** 2 for volume in volumes]
        result["effective_samples"] = {
            "method": "repository initial-positive adjacent-pair ACF, tau at least 1",
            "max_lag_samples": max_lag,
            **{
                name: effective_samples([getattr(sample, name) for sample in samples], max_lag)
                for name, _, _ in OBSERVABLES
            },
            "squared_volume_deviation_A6": effective_samples(centered_squared_volumes, max_lag),
        }
        target_temperature = CONDITIONS["temperature_K"]
        target_pressure = CONDITIONS["pressure_GPa"]
        temperature_limit = criteria["relative_temperature_offset_upper_bound_exclusive"]
        pressure_limit = criteria["absolute_pressure_offset_GPa_upper_bound_exclusive"]
        result["checks"] = {
            f"relative_temperature_offset_lt_{temperature_limit:g}": (
                abs(result["mean_temperature_K"] - target_temperature) < temperature_limit * target_temperature
            ),
            f"absolute_pressure_lt_{pressure_limit:g}_GPa": (
                abs(result["mean_pressure_GPa"] - target_pressure) < pressure_limit
            ),
            f"relative_volume_sd_lt_{volume_sd_limit:g}": relative_volume_sd < volume_sd_limit,
        }

    result["status"] = "PASS" if all(result["checks"].values()) else "FAIL"
    return result


def render_curves(samples: list[Sample], dt_fs: float) -> str:
    """Render a vector plot with the true physical spacing between samples."""
    total_ps = samples[-1].step * dt_fs / 1000
    width, left, right = 900, 110, 865
    plot = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 675" role="img">',
        '<title>GPUMD public input preparation thermo</title>',
        '<rect width="900" height="675" fill="white"/>',
        '<g font-family="sans-serif" font-size="13" fill="#172b44">',
    ]
    for index, (name, label, target) in enumerate(OBSERVABLES):
        top = 40 + 205 * index
        bottom = top + 155
        values = [getattr(sample, name) for sample in samples]
        bounds = values if target is None else [*values, target]
        low, high = min(bounds), max(bounds)
        padding = (high - low) * 0.06 or max(1e-10, abs(high) * 0.01)
        low, high = low - padding, high + padding

        def y(value: float) -> float:
            return bottom - (value - low) / (high - low) * (bottom - top)

        plot.append(f'<text x="{left}" y="{top - 12}" font-size="16">{label}</text>')
        for tick in range(5):
            value = low + (high - low) * tick / 4
            ordinate = y(value)
            plot.append(f'<path d="M{left} {ordinate:.3f}H{right}" stroke="#e6ebf2"/>')
            plot.append(
                f'<text x="{left - 8}" y="{ordinate + 4:.3f}" text-anchor="end">{value:.8g}</text>'
            )
        for tick in range(6):
            x = left + (right - left) * tick / 5
            plot.append(f'<path d="M{x:.3f} {top}V{bottom}" stroke="#e6ebf2"/>')
            if index == 2:
                plot.append(
                    f'<text x="{x:.3f}" y="{bottom + 19}" text-anchor="middle">'
                    f'{total_ps * tick / 5:.6g}</text>'
                )
        if target is not None:
            plot.append(
                f'<path d="M{left} {y(target):.3f}H{right}" '
                'stroke="#78909c" stroke-dasharray="5 3"/>'
            )
        points = " ".join(
            f"{left + (right - left) * sample.step / samples[-1].step:.3f},{y(value):.3f}"
            for sample, value in zip(samples, values)
        )
        plot.append(f'<polyline points="{points}" fill="none" stroke="#14569d" stroke-width="1.6"/>')
    plot.append(f'<text x="{width / 2}" y="655" text-anchor="middle">Stage time (ps)</text>')
    plot.append("</g></svg>")
    return "\n".join(plot) + "\n"


def render_csv(samples: list[Sample], dt_fs: float) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(("step", "time_ps", *Sample._fields[1:]))
    for sample in samples:
        writer.writerow((sample.step, sample.step * dt_fs / 1000, *sample[1:]))
    return stream.getvalue()


def publish_analysis(output_dir: Path, report: str, thermo_csv: str, curves: str) -> None:
    summary = output_dir / "summary.json"
    pending = output_dir / "summary.json.tmp"
    evidence = {
        summary: report.encode("utf-8"),
        output_dir / "thermo.csv": thermo_csv.encode("utf-8"),
        output_dir / "thermo.svg": curves.encode("utf-8"),
    }
    absolute_dir = output_dir.absolute()
    new_dir_example = absolute_dir.parent / f"{absolute_dir.name or 'analysis'}-recheck"
    conflict = (
        f"existing or incomplete analysis in {output_dir}; "
        f"use --output-dir to select a new evidence directory such as {new_dir_example}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    pending_exists = pending.exists() or pending.is_symlink()
    if pending_exists or any(path.exists() or path.is_symlink() for path in evidence):
        if not pending_exists and all(
            not path.is_symlink() and path.is_file() and path.read_bytes() == content
            for path, content in evidence.items()
        ):
            return
        raise ValueError(conflict)

    try:
        for path, content in evidence.items():
            if path != summary:
                with path.open("xb") as stream:
                    stream.write(content)
        # Publish the summary atomically after both evidence files have been written.
        stream = pending.open("xb")
        try:
            with stream:
                stream.write(evidence[summary])
            summary.hardlink_to(pending)
        finally:
            pending.unlink()
    except FileExistsError as error:
        raise ValueError(conflict) from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=SCHEDULE)
    parser.add_argument("--thermo", type=Path, required=True)
    parser.add_argument("--time-step-fs", type=float, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        schedule = SCHEDULE[args.stage]
        atoms, samples = read_thermo(args.thermo, schedule, args.time_step_fs)
        result = check_stage(args.stage, samples)
        result.update({
            "stage": args.stage,
            "thermo": str(args.thermo.resolve()),
            "thermo_sha256": hashlib.sha256(args.thermo.read_bytes()).hexdigest(),
            "atoms": atoms,
            "time_step_fs": args.time_step_fs,
            "schedule_steps_stride": schedule,
            "observed_samples": len(samples),
        })
        report = json.dumps(result, indent=2, allow_nan=False) + "\n"
        publish_analysis(
            args.output_dir, report, render_csv(samples, args.time_step_fs),
            render_curves(samples, args.time_step_fs),
        )
        print(report, end="")
    except (OSError, ValueError, OverflowError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")

    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
