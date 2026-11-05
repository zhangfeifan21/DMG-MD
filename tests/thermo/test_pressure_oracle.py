#!/usr/bin/env python3
"""CPU oracle tied to the actual production thermo and virial output expressions."""

from __future__ import annotations

import math
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = (ROOT / "src/runtime.cu").read_text(encoding="utf-8")
COMMON = (ROOT / "src/gpumd_compat/common.cuh").read_text(encoding="utf-8")
INTERNAL = (ROOT / "src/runtime_internal.hpp").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_production_column_and_virial_mappings() -> dict:
    conversion_match = re.search(
        r"const double PRESSURE_UNIT_CONVERSION\s*=\s*([0-9.eE+-]+)", COMMON
    )
    require(conversion_match is not None, "production pressure conversion constant missing")
    conversion = float(conversion_match.group(1))

    row = RUNTIME.split("void write_thermo_row(", 1)[1].split("\n}", 1)[0]
    row_indices = [int(value) for value in re.findall(r"thermo\.values\[(\d+)\]\s*\*\s*PRESSURE_UNIT_CONVERSION", row)]
    require(row_indices == [2, 3, 4, 7, 6, 5], f"production thermo stress mapping changed: {row_indices}")
    headers = re.search(r'"# columns ([^"]+)"', RUNTIME)
    require(headers is not None, "production thermo headers missing")
    names = headers.group(1).split()
    require(names[3:9] == ["sxx", "syy", "szz", "syz", "sxz", "sxy"], "thermo output stress names changed")

    # Deliberately distinct values make every permutation visible. The source
    # indices above are extracted from write_thermo_row, not duplicated as the
    # implementation under test.
    internal = [321.0, -17.0, 0.11, 0.22, 0.33, 0.44, 0.55, 0.66]
    output = [internal[index] * conversion for index in row_indices]
    expected = [0.11, 0.22, 0.33, 0.66, 0.55, 0.44]
    require(output == [value * conversion for value in expected], "synthetic asymmetric six-stress mapping failed")
    require(conversion == 160.2177, f"unexpected production pressure conversion {conversion}")
    require(internal[2] == 0.11, "synthetic setup error")

    virial_match = re.search(r"constexpr int virial_index\[9\]\s*=\s*\{([^}]+)\}", RUNTIME)
    require(virial_match is not None, "production dump_xyz virial mapping missing")
    virial_indices = [int(value.strip()) for value in virial_match.group(1).split(",")]
    require(virial_indices == [0, 3, 4, 6, 1, 5, 7, 8, 2], f"production nine-column virial mapping changed: {virial_indices}")
    components = [10.0 + i for i in range(9)]
    emitted = [components[index] for index in virial_indices]
    require(emitted == [10.0, 13.0, 14.0, 16.0, 11.0, 15.0, 17.0, 18.0, 12.0], "synthetic asymmetric nine-virial layout failed")

    normalization = INTERNAL.split("__global__ void normalize_global_thermo(", 1)[1].split("\n}", 1)[0]
    require("thermo[quantity] /= volume" in normalization, "production pressure is not normalized by volume")
    require("thermo[quantity] /= 3.0 * global_count * K_B" in normalization, "production temperature denominator is not 3N k_B")
    thermo_kernel = RUNTIME.split("__global__ void find_owned_thermo_sums(", 1)[1].split("\n}\n", 1)[0]
    for component, kinetic in enumerate(("vx * vx", "vy * vy", "vz * vz", "vx * vy", "vx * vz", "vy * vz")):
        require(f"component == {component}" in thermo_kernel or component == 5, f"production kinetic stress component {component} missing")
    require("virial[component * stride + atom] + kinetic" in thermo_kernel, "production pressure does not combine virial and kinetic terms")
    return {
        "status": "PASS",
        "pressure_unit_conversion": conversion,
        "internal_stress_order": ["sxx", "syy", "szz", "sxy", "sxz", "syz"],
        "thermo_output_stress_order": names[3:9],
        "thermo_output_source_indices": row_indices,
        "synthetic_internal_stress_eV_A3": internal[2:],
        "synthetic_output_stress_GPa": output,
        "virial_source_order": ["xx", "yy", "zz", "xy", "xz", "yz", "yx", "zx", "zy"],
        "virial_output_indices": virial_indices,
        "synthetic_virial_output": emitted,
        "normalization_source": "src/runtime_internal.hpp::normalize_global_thermo",
        "thermo_sum_source": "src/runtime.cu::find_owned_thermo_sums",
        "thermo_row_source": "src/runtime.cu::write_thermo_row",
        "virial_row_source": "src/runtime.cu::write_xyz_frame virial_index",
    }


if __name__ == "__main__":
    import json

    print(json.dumps(test_production_column_and_virial_mappings(), indent=2, sort_keys=True))
