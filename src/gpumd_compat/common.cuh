/*
    Copyright 2017 Zheyong Fan and GPUMD development team
    This file is part of GPUMD.
    GPUMD is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
    GPUMD is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.
    You should have received a copy of the GNU General Public License
    along with GPUMD.  If not, see <http://www.gnu.org/licenses/>.
*/

/*----------------------------------------------------------------------------
DMG-MD replicated GPUMD core -- DO NOT include or link ../gpumd-reference.

This directory is the DMG-MD-owned replication of the minimal GPUMD subset
that the DMG-MD runtime needs (tokenizer, Box math, GPU_Vector, neighbor
builder, Potential base, and the NEP/NEP-ZBL CUDA kernels).  It was copied
from the read-only reference checkout

    ../gpumd-reference  (commit 9d23496e41319b9e2af5221a7df6285387401d1e)

and is adapted only as documented in each file header: symbols that are
unreachable from the DMG-MD runtime are removed, includes are flattened to
"gpumd_compat/*.cuh", and everything is placed in namespace gpumd_compat.
Floating-point expressions, memory layouts, kernel launch parameters and
accumulation order are unchanged so the GPUMD golden baselines keep holding.

House rules (see AGENTS.md):
- DMG-MD sources must include "gpumd_compat/*.cuh", never a path into
  ../gpumd-reference, and the build must not compile reference sources.
- Numerics in this directory may only change together with a golden-baseline
  re-validation (tests/baseline, tests/long_nve).
------------------------------------------------------------------------------
*/

/*----------------------------------------------------------------------------
Origin file: src/utilities/common.cuh
Purpose: Physical constants and unit conversions shared by the GPUMD parsers,
kernels and output formatters; replicated verbatim.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

namespace gpumd_compat {

#pragma once

// GPUMD natural-unit constants.  K_B, TIME_UNIT_CONVERSION and
// PRESSURE_UNIT_CONVERSION are load-bearing for the DMG-MD thermo and output
// formatters; they must stay bit-identical to the reference (common.cuh:18).
// NOTE(dmg-md): MAX_NUM_BEADS (PIMD-only) from the reference is unused by
// DMG-MD and therefore not replicated.
const int NUM_ELEMENTS = 94;
#define PI 3.14159265358979
#define HBAR 6.465412e-2                             // Planck's constant
#define K_B 8.617343e-5                              // Boltzmann's constant
#define K_C 14.399645                                // 1/(4*PI*epsilon_0)
#define K_C_SP 14.399645f                            // 1/(4*PI*epsilon_0)
const double PRESSURE_UNIT_CONVERSION = 1.602177e+2; // from natural to GPa
const double TIME_UNIT_CONVERSION = 1.018051e+1;     // from natural to fs
const double KAPPA_UNIT_CONVERSION = 1.573769e+5;    // from natural to W/mK

}  // namespace gpumd_compat
