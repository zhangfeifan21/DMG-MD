#!/usr/bin/env python3
"""Create a descriptive, correctly mapped long-chain MPI/P1 comparison."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

COLUMNS = ["T_K", "KE_eV", "PE_eV", "sxx_GPa", "syy_GPa", "szz_GPa",
           "syz_GPa", "sxz_GPa", "sxy_GPa", "ax_x_A", "ax_y_A", "ax_z_A",
           "bx_x_A", "bx_y_A", "bx_z_A", "cx_x_A", "cx_y_A", "cx_z_A"]
DERIVED = ["temperature_K", "kinetic_energy_eV", "potential_energy_eV",
           "pressure_GPa", "volume_A3"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result_file(job: dict) -> Path:
    root = Path(job["result_dir"])
    for candidate in (root.parent / "results.json", root.parent.parent / "results.json",
                      root / "results.json"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(job["result_dir"])


def rows(path: Path) -> list[list[float]]:
    result = []
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) != 18:
            continue
        try:
            row = [float(value) for value in fields]
        except ValueError:
            continue
        if not all(math.isfinite(value) for value in row):
            raise ValueError(f"nonfinite thermo data: {path}")
        result.append(row)
    return result


def determinant(x: list[float]) -> float:
    a, b, c, d, e, f, g, h, i = x
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def derived(row: list[float]) -> dict[str, float]:
    return {"temperature_K": row[0], "kinetic_energy_eV": row[1],
            "potential_energy_eV": row[2],
            "pressure_GPa": (row[3] + row[4] + row[5]) / 3.0,
            "volume_A3": determinant(row[9:18])}


def make_report(ledger_path: Path) -> dict:
    ledger = json.loads(ledger_path.read_text())
    candidates: dict[tuple[int, str, int], tuple[dict, Path, dict]] = {}
    for job in ledger["jobs"]:
        if job.get("profile") != "base" or job.get("engine") != "candidate" or \
                job.get("actual_steps") != 80000 or job.get("status") != "completed":
            continue
        result_path = result_file(job)
        payload = json.loads(result_path.read_text())
        records = [item for item in payload["records"]
                   if item.get("engine") == "candidate" and item.get("seed") == job["seed"]]
        if len(records) != 1:
            raise ValueError(f"expected one candidate record for {job['key']}")
        candidates[(job["ranks"], job["backend"], job["seed"])] = (job, result_path, records[0])

    report = {"schema": "dmgmd.npt-mpi-vs-p1-mapped-diagnostic.v2",
              "source_ledger": str(ledger_path.resolve()),
              "source_ledger_sha256": sha(ledger_path),
              "column_map": COLUMNS,
              "mapping_note": "P_scalar=(column3+column4+column5)/3; V=det(box matrix columns9..17). Raw thermo rows are retained by hash only, not copied. Descriptive numeric comparison, no acceptance threshold.",
              "conditions": []}
    for ranks, backend in ((2, "HostStaged"), (2, "CudaAware"),
                           (4, "HostStaged"), (4, "CudaAware")):
        pairs = []
        maximum = {name: 0.0 for name in DERIVED}
        column_max = [0.0] * 18
        first = None
        for seed in range(101, 109):
            mj, mrp, mrec = candidates[(ranks, backend, seed)]
            pj, prp, prec = candidates[(1, "HostStaged", seed)]
            mrows, prows = rows(Path(mrec["thermo_path"])), rows(Path(prec["thermo_path"]))
            if len(mrows) != 80000 or len(prows) != 80000:
                raise ValueError(f"incomplete P{ranks}/{backend} seed{seed}: {len(mrows)}, {len(prows)}")
            if mrec["input_sha256"] != prec["input_sha256"]:
                raise ValueError(f"same-seed MPI/P1 input hash mismatch: P{ranks}/{backend} seed{seed}")
            for row_index, (m, p) in enumerate(zip(mrows, prows)):
                for col, (x, y) in enumerate(zip(m, p)):
                    delta = abs(x - y)
                    column_max[col] = max(column_max[col], delta)
                    if first is None and x != y:
                        first = {"row_zero_based": row_index, "step_one_based": row_index + 1,
                                 "column_zero_based": col, "column_name": COLUMNS[col],
                                 "mpi": x, "p1": y, "abs_diff": delta}
                md, pd = derived(m), derived(p)
                for name in DERIVED:
                    maximum[name] = max(maximum[name], abs(md[name] - pd[name]))
            pairs.append({"seed": seed, "input_sha256": mrec["input_sha256"],
                          "inputs_equal": True,
                          "mpi_thermo_path": mrec["thermo_path"],
                          "mpi_thermo_sha256": mrec["thermo_sha256"],
                          "p1_thermo_path": prec["thermo_path"],
                          "p1_thermo_sha256": prec["thermo_sha256"],
                          "mpi_results_json": str(mrp), "mpi_results_json_sha256": sha(mrp),
                          "p1_results_json": str(prp), "p1_results_json_sha256": sha(prp),
                          "rows_each": 80000})
        report["conditions"].append({"ranks": ranks, "backend": backend,
                                     "pairs": pairs, "max_abs_raw_column_diff": column_max,
                                     "max_abs_derived_diff": maximum,
                                     "first_numeric_difference": first})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = make_report(args.ledger.resolve())
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite diagnostic: {output}")
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "sha256": sha(output),
                      "condition_count": len(report["conditions"]),
                      "pair_count": sum(len(c["pairs"]) for c in report["conditions"])}, indent=2))
    for condition in report["conditions"]:
        print(condition["ranks"], condition["backend"],
              json.dumps(condition["max_abs_derived_diff"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
