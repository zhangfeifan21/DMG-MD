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
Origin file: src/force/neighbor.cuh
Purpose: Cell-list / full-neighbor-list declarations and the ELL row-sorting
kernel required by the NEP many-body reverse-edge binary search.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

#include "box.cuh"
#include "gpu_vector.cuh"

namespace gpumd_compat {

#pragma once

// Builds the bin-quantized cell list (count -> exclusive prefix sum ->
// contents) for the current positions; cell edge is rc/2 of the Verlet
// build radius (reference neighbor.cu:164).

void find_cell_list(
  const double rc,
  const int* num_bins,
  Box& box,
  const GPU_Vector<double>& position_per_atom,
  GPU_Vector<int>& cell_count,
  GPU_Vector<int>& cell_count_sum,
  GPU_Vector<int>& cell_contents);

// Builds the full (Verlet) neighbor list NN/NL over centers [N1,N2) from the
// cell list, then sorts each ELL row by atom index via
// gpu_sort_neighbor_list (reference neighbor.cu:297).

void find_neighbor(
  const int N1,
  const int N2,
  double rc,
  Box& box,
  const GPU_Vector<int>& type,
  const GPU_Vector<double>& position_per_atom,
  GPU_Vector<int>& cell_count,
  GPU_Vector<int>& cell_count_sum,
  GPU_Vector<int>& cell_contents,
  GPU_Vector<int>& NN,
  GPU_Vector<int>& NL);

// Maps a position to a cell id in fractional coordinates with wrap-around
// for periodic directions (reference neighbor.cuh:76).

static __device__ void find_cell_id(
  const Box& box,
  const double x,
  const double y,
  const double z,
  const double rc_inv,
  const int nx,
  const int ny,
  const int nz,
  int& cell_id_x,
  int& cell_id_y,
  int& cell_id_z,
  int& cell_id)
{
  const double sx = box.cpu_h[9] * x + box.cpu_h[10] * y + box.cpu_h[11] * z;
  const double sy = box.cpu_h[12] * x + box.cpu_h[13] * y + box.cpu_h[14] * z;
  const double sz = box.cpu_h[15] * x + box.cpu_h[16] * y + box.cpu_h[17] * z;
  cell_id_x = floor(sx * box.thickness_x * rc_inv);
  cell_id_y = floor(sy * box.thickness_y * rc_inv);
  cell_id_z = floor(sz * box.thickness_z * rc_inv);

  while (cell_id_x < 0)
    cell_id_x += nx;
  while (cell_id_x >= nx)
    cell_id_x -= nx;
  while (cell_id_y < 0)
    cell_id_y += ny;
  while (cell_id_y >= ny)
    cell_id_y -= ny;
  while (cell_id_z < 0)
    cell_id_z += nz;
  while (cell_id_z >= nz)
    cell_id_z -= nz;
  cell_id = cell_id_x + nx * cell_id_y + nx * ny * cell_id_z;
}

// One block per atom row; ranks each neighbor index by counting smaller
// entries, producing the ascending order required by the binary search in
// gpu_find_force_many_body (potential.cu) (reference neighbor.cuh:112).

static __global__ void gpu_sort_neighbor_list(const int N, const int* NN, int* NL)
{
  int bid = blockIdx.x;
  int tid = threadIdx.x;
  int neighbor_number = NN[bid];
  int atom_index;
  extern __shared__ int atom_index_copy[];

  if (tid < neighbor_number) {
    atom_index = NL[static_cast<size_t>(N) * tid + bid];
    atom_index_copy[tid] = atom_index;
  }
  int count = 0;
  __syncthreads();

  for (int j = 0; j < neighbor_number; ++j) {
    if (atom_index > atom_index_copy[j]) {
      count++;
    }
  }

  if (tid < neighbor_number) {
    NL[static_cast<size_t>(N) * count + bid] = atom_index;
  }
}


// Verlet-list manager with a fixed skin of 1 Angstrom: rebuilds the global
// full neighbor list only when any atom moved more than skin/2 relative to
// the reference positions x0/y0/z0 (reference neighbor.cuh:194).

class Neighbor
{
public:
  GPU_Vector<int> NN, NL; // global neighbor list
  void initialize(const double rc, const int num_atoms, const int num_neighbors);
  void find_neighbor_global(
    const double rc,
    Box& box, 
    const GPU_Vector<int>& type, 
    const GPU_Vector<double>& position_per_atom);

private:
  double skin = 1.0;              // skin distance
  GPU_Vector<int> cell_count;     // for cell list
  GPU_Vector<int> cell_count_sum; // for cell list
  GPU_Vector<int> cell_contents;  // for cell list
  GPU_Vector<double> x0, y0, z0;  // for checking atom distance
  int check_atom_distance(Box& box, const double* x, const double* y, const double* z);
};

}  // namespace gpumd_compat
