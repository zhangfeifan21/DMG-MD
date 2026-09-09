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
Origin file: src/force/nep.cuh
Purpose: NEP class: parameter blocks (ParaMB/ANN/ZBL), expanded-box and
small-box workspaces, and the public compute interface.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

#include "neighbor.cuh"
#include "potential.cuh"
#include "common.cuh"
#include "gpu_vector.cuh"

namespace gpumd_compat {

#pragma once

struct NEP_Data {
  GPU_Vector<float> f12x; // 3-body or manybody partial forces
  GPU_Vector<float> f12y; // 3-body or manybody partial forces
  GPU_Vector<float> f12z; // 3-body or manybody partial forces
  GPU_Vector<float> Fp;
  GPU_Vector<float> sum_fxyz;
  GPU_Vector<float> descriptor_parameters_type_pair;
  GPU_Vector<int> NN_radial;    // radial neighbor list
  GPU_Vector<int> NL_radial;    // radial neighbor list
  GPU_Vector<int> NN_angular;   // angular neighbor list
  GPU_Vector<int> NL_angular;   // angular neighbor list
  GPU_Vector<float> parameters; // parameters to be optimized
  std::vector<int> cpu_NN_radial;
  std::vector<int> cpu_NN_angular;
};

class NEP : public Potential
{
public:
  NEP_Data nep_data;
  struct ParaMB {
    bool use_typewise_cutoff_zbl = false;
    float typewise_cutoff_zbl_factor = 0.0f;
    int version = 4; // NEP version, 3 for NEP3 and 4 for NEP4
    int model_type =
      0; // 0=potential, 1=dipole, 2=polarizability, 3=temperature-dependent free energy
    float rc_radial_max = 0.0f;
    float rc_radial_max_inv = 0.0f; 
    float rc_radial[NUM_ELEMENTS];     // radial cutoff
    float rc_angular[NUM_ELEMENTS];    // angular cutoff
    int MN_radial = 200;
    int MN_angular = 100;
    int n_max_radial = 0;  // n_radial = 0, 1, 2, ..., n_max_radial
    int n_max_angular = 0; // n_angular = 0, 1, 2, ..., n_max_angular
    int L_max = 0;         // l = 0, 1, 2, ..., L_max
    int dim_angular;
    int has_q_222 = 0;
    int has_q_1111 = 0;
    int has_q_112 = 0;
    int has_q_123 = 0;
    int has_q_233 = 0;
    int has_q_134 = 0;
    int num_L;
    int basis_size_radial = 8;  // for nep3
    int basis_size_angular = 8; // for nep3
    int num_types_sq = 0;       // for nep3
    int num_c_radial = 0;       // for nep3
    int num_types = 0;
  };

  struct ANN {
    int dim = 0;                   // dimension of the descriptor
    int num_neurons1 = 0;          // number of neurons in the 1st hidden layer
    int num_para = 0;              // number of parameters
    int num_para_ann = 0;          // number of parameters for the ANN part
    const float* w0[NUM_ELEMENTS]; // weight from the input layer to the hidden layer
    const float* b0[NUM_ELEMENTS]; // bias for the hidden layer
    const float* w1[NUM_ELEMENTS]; // weight from the hidden layer to the output layer
    const float* b1;               // bias for the output layer
    const float* c;
    const float* c_type_pair;
    // for the scalar part of polarizability
    const float* w0_pol[10];
    const float* b0_pol[10];
    const float* w1_pol[10];
    const float* b1_pol;
    const float* q_scaler;
  };

  struct ZBL {
    bool enabled = false;
    bool flexibled = false;
    float rc_inner = 1.0f;
    float rc_outer = 2.0f;
    float para[550];
    int atomic_numbers[NUM_ELEMENTS];
    int num_types;
  };

  struct ExpandedBox {
    int num_cells[3];
    float h[18];
  };

  struct Small_Box_Data {
        GPU_Vector<int> NN_radial;
        GPU_Vector<int> NL_radial;
        GPU_Vector<int> NN_angular;
        GPU_Vector<int> NL_angular;
        GPU_Vector<float> r12;
    } small_box_data;

  NEP(const char* file_potential, const int num_atoms);
  virtual ~NEP(void);
  virtual void compute(
    Box& box,
    const GPU_Vector<int>& type,
    const GPU_Vector<double>& position,
    GPU_Vector<double>& potential,
    GPU_Vector<double>& force,
    GPU_Vector<double>& virial);

  const GPU_Vector<int>& get_NN_radial_ptr();

  const GPU_Vector<int>& get_NL_radial_ptr();

private:
  ParaMB paramb;
  ANN annmb;
  ZBL zbl;
  ExpandedBox ebox;
  Neighbor neighbor;

  void update_potential(float* parameters, ANN& ann);

  void compute_small_box(
    Box& box,
    const GPU_Vector<int>& type,
    const GPU_Vector<double>& position,
    GPU_Vector<double>& potential,
    GPU_Vector<double>& force,
    GPU_Vector<double>& virial);

  void compute_large_box(
    Box& box,
    const GPU_Vector<int>& type,
    const GPU_Vector<double>& position,
    GPU_Vector<double>& potential,
    GPU_Vector<double>& force,
    GPU_Vector<double>& virial);

};

}  // namespace gpumd_compat
