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
Origin file: src/force/potential.cu
Purpose: Center-atom many-body force/virial gather kernel (float partial
forces) used by the NEP angular force path.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

#include "potential.cuh"
#include "error.cuh"
#include "gpu_macro.cuh"
#include <thrust/execution_policy.h>
#include <thrust/scan.h>
#include <cstring>

namespace gpumd_compat {

/*----------------------------------------------------------------------------80
The abstract base class (ABC) for the potential classes.
------------------------------------------------------------------------------*/

#define BLOCK_SIZE_FORCE 64

Potential::Potential(void) { rc = 0.0; }

Potential::~Potential(void)
{
  // nothing
}

// Gathers per-atom forces and virials from the directed partial forces
// f12(i,j) produced by find_partial_force_angular (nep.cu).  One thread per
// center atom in [N1,N2); for each angular neighbor j it adds f12(i,j) and
// binary-searches the sorted neighbor row of j for the reverse partial
// f12(j,i) (the loop is sorted by gpu_sort_neighbor_list in neighbor.cu).
// Virial layout: xx yy zz xy xz yz yx zx zy at offsets 0..8.
// NOTE(dmg-md): the double-precision overload of gpu_find_force_many_body
// (reference potential.cu:35) serves EAM/FCP-style potentials and is not
// reachable from the NEP path replicated here.
static __global__ void gpu_find_force_many_body(
  const bool is_dipole,
  const int number_of_particles,
  const int N1,
  const int N2,
  const Box box,
  const int* g_neighbor_number,
  const int* g_neighbor_list,
  const float* __restrict__ g_f12x,
  const float* __restrict__ g_f12y,
  const float* __restrict__ g_f12z,
  const double* __restrict__ g_x,
  const double* __restrict__ g_y,
  const double* __restrict__ g_z,
  double* g_fx,
  double* g_fy,
  double* g_fz,
  double* g_virial)
{
  int n1 = blockIdx.x * blockDim.x + threadIdx.x + N1;
  float s_fx = 0.0f;  // force_x
  float s_fy = 0.0f;  // force_y
  float s_fz = 0.0f;  // force_z
  float s_sxx = 0.0f; // virial_stress_xx
  float s_sxy = 0.0f; // virial_stress_xy
  float s_sxz = 0.0f; // virial_stress_xz
  float s_syx = 0.0f; // virial_stress_yx
  float s_syy = 0.0f; // virial_stress_yy
  float s_syz = 0.0f; // virial_stress_yz
  float s_szx = 0.0f; // virial_stress_zx
  float s_szy = 0.0f; // virial_stress_zy
  float s_szz = 0.0f; // virial_stress_zz

  if (n1 >= N1 && n1 < N2) {
    int neighbor_number = g_neighbor_number[n1];
    double x1 = g_x[n1];
    double y1 = g_y[n1];
    double z1 = g_z[n1];

    for (int i1 = 0; i1 < neighbor_number; ++i1) {
      int index = i1 * number_of_particles + n1;
      int n2 = g_neighbor_list[index];

      double x12double = g_x[n2] - x1;
      double y12double = g_y[n2] - y1;
      double z12double = g_z[n2] - z1;
      apply_mic(box, x12double, y12double, z12double);
      float x12 = float(x12double);
      float y12 = float(y12double);
      float z12 = float(z12double);

      float f12x = g_f12x[index];
      float f12y = g_f12y[index];
      float f12z = g_f12z[index];
      // int offset = 0;

      int l = 0;
      int r = g_neighbor_number[n2];
      int m = 0;
      int tmp_value = 0;
      while (l < r) {
        m = (l + r) >> 1;
        tmp_value = g_neighbor_list[n2 + number_of_particles * m];
        if (tmp_value < n1) {
          l = m + 1;
        } else if (tmp_value > n1) {
          r = m - 1;
        } else {
          break;
        }
      }
      // for (int k = 0; k < neighbor_number_2; ++k) {
      //   if (n1 == g_neighbor_list[n2 + number_of_particles * k]) {
      //     offset = k;
      //     break;
      //   }
      // }
      index = ((l + r) >> 1) * number_of_particles + n2;
      float f21x = g_f12x[index];
      float f21y = g_f12y[index];
      float f21z = g_f12z[index];

      // per atom force
      s_fx += f12x - f21x;
      s_fy += f12y - f21y;
      s_fz += f12z - f21z;

      // per-atom virial
      if (is_dipole) {
        // Float version of the function
        // The dipole is proportional to minus the sum of the virials times r12
        float r12_square = x12 * x12 + y12 * y12 + z12 * z12;
        s_sxx -= r12_square * f21x;
        s_syy -= r12_square * f21y;
        s_szz -= r12_square * f21z;
      } else {
        s_sxx += x12 * f21x;
        s_syy += y12 * f21y;
        s_szz += z12 * f21z;
      }
      s_sxy += x12 * f21y;
      s_sxz += x12 * f21z;
      s_syx += y12 * f21x;
      s_syz += y12 * f21z;
      s_szx += z12 * f21x;
      s_szy += z12 * f21y;
    }

    // save force
    g_fx[n1] += s_fx;
    g_fy[n1] += s_fy;
    g_fz[n1] += s_fz;

    // save virial
    // xx xy xz    0 3 4
    // yx yy yz    6 1 5
    // zx zy zz    7 8 2
    g_virial[n1 + 0 * number_of_particles] += s_sxx;
    g_virial[n1 + 1 * number_of_particles] += s_syy;
    g_virial[n1 + 2 * number_of_particles] += s_szz;
    g_virial[n1 + 3 * number_of_particles] += s_sxy;
    g_virial[n1 + 4 * number_of_particles] += s_sxz;
    g_virial[n1 + 5 * number_of_particles] += s_syz;
    g_virial[n1 + 6 * number_of_particles] += s_syx;
    g_virial[n1 + 7 * number_of_particles] += s_szx;
    g_virial[n1 + 8 * number_of_particles] += s_szy;
  }
}

// Host wrapper for gpu_find_force_many_body: launches one block-size-64
// grid over the Potential::N1..N2 center range with the atom stride taken
// from the position vector (reference potential.cu:299).
void Potential::find_properties_many_body(
  Box& box,
  const int* NN,
  const int* NL,
  const float* f12x,
  const float* f12y,
  const float* f12z,
  const bool is_dipole,
  const GPU_Vector<double>& position_per_atom,
  GPU_Vector<double>& force_per_atom,
  GPU_Vector<double>& virial_per_atom)
{
  const int number_of_atoms = position_per_atom.size() / 3;
  int grid_size = (N2 - N1 - 1) / BLOCK_SIZE_FORCE + 1;

  gpu_find_force_many_body<<<grid_size, BLOCK_SIZE_FORCE>>>(
    is_dipole,
    number_of_atoms,
    N1,
    N2,
    box,
    NN,
    NL,
    f12x,
    f12y,
    f12z,
    position_per_atom.data(),
    position_per_atom.data() + number_of_atoms,
    position_per_atom.data() + number_of_atoms * 2,
    force_per_atom.data(),
    force_per_atom.data() + number_of_atoms,
    force_per_atom.data() + 2 * number_of_atoms,
    virial_per_atom.data());
  GPU_CHECK_KERNEL
}

}  // namespace gpumd_compat
