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
Origin file: src/force/neighbor.cu
Purpose: Cell-list construction and the O(N) full (Verlet) neighbor builder
with skin-based rebuild checks.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

#include "neighbor.cuh"
#include "error.cuh"
#include "gpu_macro.cuh"
#include <thrust/execution_policy.h>
#include <thrust/scan.h>
#include <cstring>

namespace gpumd_compat {

/*----------------------------------------------------------------------------80
neighbor list.
------------------------------------------------------------------------------*/


// Thin wrapper resolving only the linear cell id (reference neighbor.cu:27).

static __device__ void find_cell_id(
  const Box& box,
  const double x,
  const double y,
  const double z,
  const double rc_inv,
  const int nx,
  const int ny,
  const int nz,
  int& cell_id)
{
  int cell_id_x, cell_id_y, cell_id_z;
  find_cell_id(box, x, y, z, rc_inv, nx, ny, nz, cell_id_x, cell_id_y, cell_id_z, cell_id);
}

// Counts atoms per cell with atomicAdd (reference neighbor.cu:42).

static __global__ void find_cell_counts(
  const Box box,
  const int N,
  int* cell_count,
  const double* x,
  const double* y,
  const double* z,
  const int nx,
  const int ny,
  const int nz,
  const double rc_inv)
{
  const int n1 = blockIdx.x * blockDim.x + threadIdx.x;
  if (n1 < N) {
    int cell_id;
    find_cell_id(box, x[n1], y[n1], z[n1], rc_inv, nx, ny, nz, cell_id);
    atomicAdd(&cell_count[cell_id], 1);
  }
}

// Fills cell_contents in stable per-cell order using the prefix sums and an
// atomic per-cell cursor (reference neighbor.cu:62).

static __global__ void find_cell_contents(
  const Box box,
  const int N,
  int* cell_count,
  const int* cell_count_sum,
  int* cell_contents,
  const double* x,
  const double* y,
  const double* z,
  const int nx,
  const int ny,
  const int nz,
  const double rc_inv)
{
  const int n1 = blockIdx.x * blockDim.x + threadIdx.x;
  if (n1 < N) {
    int cell_id;
    find_cell_id(box, x[n1], y[n1], z[n1], rc_inv, nx, ny, nz, cell_id);
    const int ind = atomicAdd(&cell_count[cell_id], 1);
    cell_contents[cell_count_sum[cell_id] + ind] = n1;
  }
}

// One thread per center in [N1,N2): scans the 3x3x3 neighboring cells and
// records every candidate within the (rc+skin) build radius into the ELL
// row NL[i*N+n1]; rows are sorted afterwards by gpu_sort_neighbor_list
// (reference neighbor.cu:85).

static __global__ void gpu_find_neighbor_ON1(
  const Box box,
  const int N,
  const int N1,
  const int N2,
  const int* __restrict__ type,
  const int* __restrict__ cell_counts,
  const int* __restrict__ cell_count_sum,
  const int* __restrict__ cell_contents,
  int* NN,
  int* NL,
  const double* __restrict__ x,
  const double* __restrict__ y,
  const double* __restrict__ z,
  const int nx,
  const int ny,
  const int nz,
  const double rc_inv,
  const float cutoff_square)
{
  const int n1 = blockIdx.x * blockDim.x + threadIdx.x + N1;
  int count = 0;
  if (n1 < N2) {
    const double x1 = x[n1];
    const double y1 = y[n1];
    const double z1 = z[n1];
    int cell_id;
    int cell_id_x;
    int cell_id_y;
    int cell_id_z;
    find_cell_id(box, x1, y1, z1, rc_inv, nx, ny, nz, cell_id_x, cell_id_y, cell_id_z, cell_id);

    const int z_lim = box.pbc_z ? 2 : 0;
    const int y_lim = box.pbc_y ? 2 : 0;
    const int x_lim = box.pbc_x ? 2 : 0;

    // get radial descriptors
    for (int k = -z_lim; k <= z_lim; ++k) {
      for (int j = -y_lim; j <= y_lim; ++j) {
        for (int i = -x_lim; i <= x_lim; ++i) {
          int neighbor_cell = cell_id + k * nx * ny + j * nx + i;
          if (cell_id_x + i < 0)
            neighbor_cell += nx;
          else if (cell_id_x + i >= nx)
            neighbor_cell -= nx;
          if (cell_id_y + j < 0)
            neighbor_cell += ny * nx;
          else if (cell_id_y + j >= ny)
            neighbor_cell -= ny * nx;
          if (cell_id_z + k < 0)
            neighbor_cell += nz * ny * nx;
          else if (cell_id_z + k >= nz)
            neighbor_cell -= nz * ny * nx;

          const int num_atoms_neighbor_cell = cell_counts[neighbor_cell];
          const int num_atoms_previous_cells = cell_count_sum[neighbor_cell];

          for (int m = 0; m < num_atoms_neighbor_cell; ++m) {
            const int n2 = cell_contents[num_atoms_previous_cells + m];
            if (n2 >= N1 && n2 < N2 && n1 != n2) {

              float x12 = x[n2] - x1;
              float y12 = y[n2] - y1;
              float z12 = z[n2] - z1;
              apply_mic(box, x12, y12, z12);
              const float d2 = x12 * x12 + y12 * y12 + z12 * z12;

              if (d2 < cutoff_square) {
                NL[static_cast<size_t>(N) * count++ + n1] = n2;
              }
            }
          }
        }
      }
    }
    NN[n1] = count;
  }
}

// Host driver of the cell list: memset, count, thrust exclusive scan,
// contents (reference neighbor.cu:164).

void find_cell_list(
  const double rc,
  const int* num_bins,
  Box& box,
  const GPU_Vector<double>& position_per_atom,
  GPU_Vector<int>& cell_count,
  GPU_Vector<int>& cell_count_sum,
  GPU_Vector<int>& cell_contents)
{
  const int N = position_per_atom.size() / 3;
  const int block_size = 256;
  const int grid_size = (N - 1) / block_size + 1;
  const double rc_inv = 1.0 / rc;
  const double* x = position_per_atom.data();
  const double* y = position_per_atom.data() + N;
  const double* z = position_per_atom.data() + N * 2;
  const int N_cells = num_bins[0] * num_bins[1] * num_bins[2];

  // number of cells is allowed to be larger than the number of atoms
  if (N_cells > cell_count.size()) {
    cell_count.resize(N_cells);
    cell_count_sum.resize(N_cells);
  }

  CHECK(gpuMemset(cell_count.data(), 0, sizeof(int) * N_cells));
  CHECK(gpuMemset(cell_count_sum.data(), 0, sizeof(int) * N_cells));
  CHECK(gpuMemset(cell_contents.data(), 0, sizeof(int) * N));

  find_cell_counts<<<grid_size, block_size>>>(
    box, N, cell_count.data(), x, y, z, num_bins[0], num_bins[1], num_bins[2], rc_inv);
  GPU_CHECK_KERNEL

  thrust::exclusive_scan(
    thrust::device, cell_count.data(), cell_count.data() + N_cells, cell_count_sum.data());

  CHECK(gpuMemset(cell_count.data(), 0, sizeof(int) * N_cells));

  find_cell_contents<<<grid_size, block_size>>>(
    box,
    N,
    cell_count.data(),
    cell_count_sum.data(),
    cell_contents.data(),
    x,
    y,
    z,
    num_bins[0],
    num_bins[1],
    num_bins[2],
    rc_inv);
  GPU_CHECK_KERNEL
}

static void __global__ set_to_zero(int size, int* data)
{
  int n = threadIdx.x + blockIdx.x * blockDim.x;
  if (n < size) {
    data[n] = 0;
  }
}


// Host driver of the full neighbor list with cell size rc/2 and the
// row-sorting launch (reference neighbor.cu:297).

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
  GPU_Vector<int>& NL)
{
  const int N = NN.size();
  const int block_size = 256;
  const int grid_size = (N2 - N1 - 1) / block_size + 1;
  const double* x = position_per_atom.data();
  const double* y = position_per_atom.data() + N;
  const double* z = position_per_atom.data() + N * 2;
  const double rc_cell_list = 0.5 * rc;
  const double rc_inv_cell_list = 2.0 / rc;

  int num_bins[3];
  box.get_num_bins(rc_cell_list, num_bins);

  find_cell_list(
    rc_cell_list, num_bins, box, position_per_atom, cell_count, cell_count_sum, cell_contents);

  gpu_find_neighbor_ON1<<<grid_size, block_size>>>(
    box,
    N,
    N1,
    N2,
    type.data(),
    cell_count.data(),
    cell_count_sum.data(),
    cell_contents.data(),
    NN.data(),
    NL.data(),
    x,
    y,
    z,
    num_bins[0],
    num_bins[1],
    num_bins[2],
    rc_inv_cell_list,
    rc * rc);
  GPU_CHECK_KERNEL

  const int MN = NL.size() / NN.size();
  gpu_sort_neighbor_list<<<N, MN, MN * sizeof(int)>>>(N, NN.data(), NL.data());
  GPU_CHECK_KERNEL
}

// For ILP, the neighbor could not contain atoms in the same layer




// Rebuild-decision helpers below (reference neighbor.cu:644-737).

namespace {

// Block-reduces how many atoms moved more than d2 from the reference
// positions (reference neighbor.cu:646).

__global__ void gpu_check_atom_distance(
  const Box box,
  int N,
  double d2,
  const double* x_old,
  const double* y_old,
  const double* z_old,
  const double* x_new,
  const double* y_new,
  const double* z_new,
  int* g_sum)
{
  int tid = threadIdx.x;
  int bid = blockIdx.x;
  int n = bid * blockDim.x + tid;
  __shared__ int s_sum[128];
  s_sum[tid] = 0;
  if (n < N) {
    float dx = x_new[n] - x_old[n];
    float dy = y_new[n] - y_old[n];
    float dz = z_new[n] - z_old[n];
    apply_mic(box, dx, dy, dz);
    if ((dx * dx + dy * dy + dz * dz) > d2) {
      s_sum[tid] = 1;
    }
  }
  __syncthreads();

  for (int offset = blockDim.x >> 1; offset > 0; offset >>= 1) {
    if (tid < offset) {
      s_sum[tid] += s_sum[tid + offset];
    }
    __syncthreads();
  }

  if (tid == 0) {
    atomicAdd(g_sum, s_sum[0]);
  }
}

// Device-side counter read back by Neighbor::check_atom_distance via
// cudaGetSymbolAddress (reference neighbor.cu:686).

__device__ int static_s2[1];

// Stores the current positions as the rebuild reference (reference
// neighbor.cu:688).

__global__ void
gpu_update_xyz0(int N, const double* x, const double* y, const double* z, double* x0, double* y0, double* z0)
{
  int n = blockIdx.x * blockDim.x + threadIdx.x;
  if (n < N) {
    x0[n] = x[n];
    y0[n] = y[n];
    z0[n] = z[n];
  }
}


}

// Returns the number of atoms that moved more than skin/2 since the last
// rebuild; the H2D/D2H of the counter is a host synchronization point
// (reference neighbor.cu:741).

int Neighbor::check_atom_distance(Box& box, const double* x, const double* y, const double* z)
{
  const int N = NN.size();
  double d2 = skin * skin * 0.25;
  int* gpu_s2;
  CHECK(gpuGetSymbolAddress((void**)&gpu_s2, static_s2));
  int cpu_s2[1] = {0};
  CHECK(gpuMemcpy(gpu_s2, cpu_s2, sizeof(int), gpuMemcpyHostToDevice));
  gpu_check_atom_distance<<<(N - 1) / 128 + 1, 128>>>(
    box, N, d2, x0.data(), y0.data(), z0.data(), x, y, z, gpu_s2);
  GPU_CHECK_KERNEL
  CHECK(gpuMemcpy(cpu_s2, gpu_s2, sizeof(int), gpuMemcpyDeviceToHost));
  return cpu_s2[0];
}

// Entry point used by NEP::compute_large_box: rebuilds the Verlet list
// when atoms have drifted, otherwise reuses the cached NN/NL (reference
// neighbor.cu:756).

void Neighbor::find_neighbor_global(
  const double rc,
  Box& box, 
  const GPU_Vector<int>& type, 
  const GPU_Vector<double>& position_per_atom)
{
  const int N = type.size();
  const double* x = position_per_atom.data();
  const double* y = position_per_atom.data() + N;
  const double* z = position_per_atom.data() + N * 2;

  bool is_first_time = false;

  if (x0.size() == 0) {
    is_first_time = true;
    x0.resize(N);
    y0.resize(N);
    z0.resize(N);
  }

  if (is_first_time || check_atom_distance(box, x, y, z)) {
    find_neighbor(
      0,
      N,
      rc + skin,
      box, 
      type, 
      position_per_atom,
      cell_count,
      cell_count_sum,
      cell_contents,
      NN,
      NL);

    gpu_update_xyz0<<<(N - 1) / 128 + 1, 128>>>(
      N, 
      x, 
      y, 
      z, 
      x0.data(), 
      y0.data(), 
      z0.data());
    GPU_CHECK_KERNEL
  }
}


// Allocates NN/NL and cell scratch; NL capacity is scaled by
// (rc+skin)^3/rc^3 to absorb skin growth (reference neighbor.cu:824).

void Neighbor::initialize(const double rc, const int num_atoms, const int num_neighbors)
{
  const double rc_plus_skin = rc + skin;
  const int MN = num_neighbors * rc_plus_skin * rc_plus_skin * rc_plus_skin / (rc * rc * rc);
  NN.resize(num_atoms);
  NL.resize(static_cast<size_t>(num_atoms) * MN);
  cell_count.resize(num_atoms);
  cell_count_sum.resize(num_atoms);
  cell_contents.resize(num_atoms);
}

}  // namespace gpumd_compat
