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
Origin file: src/force/potential.cuh
Purpose: Abstract Potential base class kept for the NEP inheritance and the
shared many-body force/virial gather.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

#include "box.cuh"
#include "gpu_vector.cuh"

namespace gpumd_compat {

#pragma once

class Potential
{
public:
  // size of the B vector (for each atom) in extrapolation grade calculation
  int B_projection_size = 0;
  // this points to GPU
  double* B_projection = nullptr;
  bool need_B_projection = false;

  int N1;
  int N2;
  double rc; // maximum cutoff distance
  int nep_model_type =
    -1; // -1 for non_nep, 0 for potential, 1 for dipole, 2 for polarizability, 3 for temperature
  int ilp_flag = 0; // 0 for non_ilp, 1 for ilp
  Potential(void);
  virtual ~Potential(void);

  virtual void compute(
    Box& box,
    const GPU_Vector<int>& type,
    const GPU_Vector<double>& position,
    GPU_Vector<double>& potential,
    GPU_Vector<double>& force,
    GPU_Vector<double>& virial) = 0;

  virtual void compute(
    const float /* temperature */,
    Box& /* box */,
    const GPU_Vector<int>& /* type */,
    const GPU_Vector<double>& /* position */,
    GPU_Vector<double>& /* potential */,
    GPU_Vector<double>& /* force */,
    GPU_Vector<double>& /* virial */){}

  virtual const GPU_Vector<int>& get_NN_radial_ptr()
  {
    static GPU_Vector<int> dummy_NN;
    return dummy_NN; // Return the const reference to NN_radial
  }

  virtual const GPU_Vector<int>& get_NL_radial_ptr()
  {
    static GPU_Vector<int> dummy_NL;
    return dummy_NL; // Return the const reference to NL_radial
  }

protected:
  // NOTE(dmg-md): the double-precision find_properties_many_body overload in
  // the reference (potential.cu:136) is only used by the EAM/FCP-style
  // potentials and by nothing in the NEP path; it is not replicated here.
  void find_properties_many_body(
    Box& box,
    const int* NN,
    const int* NL,
    const float* f12x,
    const float* f12y,
    const float* f12z,
    const bool is_dipole,
    const GPU_Vector<double>& position_per_atom,
    GPU_Vector<double>& force_per_atom,
    GPU_Vector<double>& virial_per_atom);
};

}  // namespace gpumd_compat
