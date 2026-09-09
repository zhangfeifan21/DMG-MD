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
Origin file: src/model/box.cu
Purpose: Host-side Box math: face areas, volume, inverse matrix, cell-list bin
counts, and the orthogonality test; replicated verbatim.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

#include "box.cuh"
#include "error.cuh"
#include "gpu_macro.cuh"
#include <cmath>
#include <cstring>

namespace gpumd_compat {

/*----------------------------------------------------------------------------80
The class defining the simulation box.
------------------------------------------------------------------------------*/


// |a x b| for two box edge vectors (reference box.cu:26).

static double get_area_one_direction(const double* a, const double* b)
{
  double s1 = a[1] * b[2] - a[2] * b[1];
  double s2 = a[2] * b[0] - a[0] * b[2];
  double s3 = a[0] * b[1] - a[1] * b[0];
  return sqrt(s1 * s1 + s2 * s2 + s3 * s3);
}

// Area of the face perpendicular to direction d (reference box.cu:34).

double Box::get_area(const int d) const
{
  double area;
  double a[3] = {cpu_h[0], cpu_h[3], cpu_h[6]};
  double b[3] = {cpu_h[1], cpu_h[4], cpu_h[7]};
  double c[3] = {cpu_h[2], cpu_h[5], cpu_h[8]};
  if (d == 0) {
    area = get_area_one_direction(b, c);
  } else if (d == 1) {
    area = get_area_one_direction(c, a);
  } else {
    area = get_area_one_direction(a, b);
  }
  return area;
}

// |det(h)| (reference box.cu:50).

double Box::get_volume(void) const
{
  double volume;
  volume = abs(
    cpu_h[0] * (cpu_h[4] * cpu_h[8] - cpu_h[5] * cpu_h[7]) +
    cpu_h[1] * (cpu_h[5] * cpu_h[6] - cpu_h[3] * cpu_h[8]) +
    cpu_h[2] * (cpu_h[3] * cpu_h[7] - cpu_h[4] * cpu_h[6]));
  return volume;
}

// Stores the inverse box matrix in cpu_h[9..17]; also the fractional-space
// rows used by apply_mic and find_cell_id (reference box.cu:60).

void Box::get_inverse(void)
{
  double det;
  cpu_h[9] = cpu_h[4] * cpu_h[8] - cpu_h[5] * cpu_h[7];
  cpu_h[10] = cpu_h[2] * cpu_h[7] - cpu_h[1] * cpu_h[8];
  cpu_h[11] = cpu_h[1] * cpu_h[5] - cpu_h[2] * cpu_h[4];
  cpu_h[12] = cpu_h[5] * cpu_h[6] - cpu_h[3] * cpu_h[8];
  cpu_h[13] = cpu_h[0] * cpu_h[8] - cpu_h[2] * cpu_h[6];
  cpu_h[14] = cpu_h[2] * cpu_h[3] - cpu_h[0] * cpu_h[5];
  cpu_h[15] = cpu_h[3] * cpu_h[7] - cpu_h[4] * cpu_h[6];
  cpu_h[16] = cpu_h[1] * cpu_h[6] - cpu_h[0] * cpu_h[7];
  cpu_h[17] = cpu_h[0] * cpu_h[4] - cpu_h[1] * cpu_h[3];
  det = cpu_h[0] * (cpu_h[4] * cpu_h[8] - cpu_h[5] * cpu_h[7]) +
        cpu_h[1] * (cpu_h[5] * cpu_h[6] - cpu_h[3] * cpu_h[8]) +
        cpu_h[2] * (cpu_h[3] * cpu_h[7] - cpu_h[4] * cpu_h[6]);
  for (int n = 9; n < 18; n++) {
    cpu_h[n] /= det;
  }
}

// Bins per periodic direction with floor(length/rc); fewer than 3 bins
// forces the O(N^2) fallback flag (reference box.cu:80).

void static get_num_bins_one_direction(
  const int pbc, const double rc, const double box_length, int& num_bins, bool& use_ON2)
{
  if (pbc) {
    num_bins = floor(box_length / rc);
    if (num_bins < 3) {
      use_ON2 = true;
    }
  } else {
    num_bins = 1;
  }
}

// Computes thickness and bin counts for the cell list and returns whether
// the O(N^2) path should be used (reference box.cu:93).

bool Box::get_num_bins(const double rc, int num_bins[])
{
  bool use_ON2 = false;

  double volume = get_volume();
  thickness_x = volume / get_area(0);
  thickness_y = volume / get_area(1);
  thickness_z = volume / get_area(2);
  get_num_bins_one_direction(pbc_x, rc, thickness_x, num_bins[0], use_ON2);
  get_num_bins_one_direction(pbc_y, rc, thickness_y, num_bins[1], use_ON2);
  get_num_bins_one_direction(pbc_z, rc, thickness_z, num_bins[2], use_ON2);

  if (num_bins[0] * num_bins[1] * num_bins[2] < 50) {
    use_ON2 = true;
  }
  return use_ON2;
}

// Tests strict orthogonality and refreshes the float copy of the box matrix
// used by the float apply_mic overload (reference box.cu:111).

void Box::set_is_orthogonal()
{
  is_orthogonal = cpu_h[1] == 0 && cpu_h[2] == 0 && cpu_h[3] == 0 && cpu_h[5] == 0 && cpu_h[6] == 0 && cpu_h[7] == 0;
  for (int d = 0; d < 18; ++d) {
    float_h[d] = cpu_h[d];
  }
}

}  // namespace gpumd_compat
