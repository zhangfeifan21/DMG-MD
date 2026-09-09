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
Origin file: src/model/box.cuh
Purpose: Simulation-box data structure with the minimum-image-convention (MIC)
device helpers for orthogonal and triclinic boxes; verbatim.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

namespace gpumd_compat {

#pragma once

// Simulation box: h holds the 3x3 matrix (columns a,b,c in cpu_h[0..8])
// followed by its inverse in cpu_h[9..17]; float_h is the float copy used
// by float kernels (reference box.cuh:18).

class Box
{
public:
  int pbc_x = 1;                      // pbc_x = 1 means periodic in the x-direction
  int pbc_y = 1;                      // pbc_y = 1 means periodic in the y-direction
  int pbc_z = 1;                      // pbc_z = 1 means periodic in the z-direction
  double cpu_h[18];                   // the box data
  float float_h[18];
  double thickness_x = 0.0;           // thickness perpendicular to (b x c)
  double thickness_y = 0.0;           // thickness perpendicular to (c x a)
  double thickness_z = 0.0;           // thickness perpendicular to (a x b)
  double get_area(const int d) const; // get the area of one face
  double get_volume(void) const;      // get the volume of the box
  void get_inverse(void);             // get the inverse box matrix
  bool get_num_bins(const double rc, int num_bins[]); // get the number of bins in each direction
  bool is_orthogonal = false;
  void set_is_orthogonal();
};

// Minimum image convention for double displacements: orthogonal boxes use
// the half-box shortcut, triclinic boxes map to fractional space, apply
// nearbyint per periodic direction and transform back (reference
// box.cuh:37).

inline __host__ __device__ void apply_mic(const Box& box, double& x12, double& y12, double& z12)
{
  if (box.is_orthogonal) {
    double Lx = box.cpu_h[0];
    double Ly = box.cpu_h[4];
    double Lz = box.cpu_h[8];

    if (box.pbc_x == 1) {
      if (x12 < -Lx*0.5) {
        x12 += Lx;
      } else if (x12 > +Lx*0.5) {
        x12 -= Lx;
      }
    }

    if (box.pbc_y == 1) {
      if (y12 < -Ly*0.5) {
        y12 += Ly;
      } else if (y12 > +Ly*0.5) {
        y12 -= Ly;
      }
    }

    if (box.pbc_z == 1) {
      if (z12 < -Lz*0.5) {
        z12 += Lz;
      } else if (z12 > +Lz*0.5) {
        z12 -= Lz;
      }
    }
  }
  else {
    double sx12 = box.cpu_h[9] * x12 + box.cpu_h[10] * y12 + box.cpu_h[11] * z12;
    double sy12 = box.cpu_h[12] * x12 + box.cpu_h[13] * y12 + box.cpu_h[14] * z12;
    double sz12 = box.cpu_h[15] * x12 + box.cpu_h[16] * y12 + box.cpu_h[17] * z12;
    if (box.pbc_x == 1)
      sx12 -= nearbyint(sx12);
    if (box.pbc_y == 1)
      sy12 -= nearbyint(sy12);
    if (box.pbc_z == 1)
      sz12 -= nearbyint(sz12);
    x12 = box.cpu_h[0] * sx12 + box.cpu_h[1] * sy12 + box.cpu_h[2] * sz12;
    y12 = box.cpu_h[3] * sx12 + box.cpu_h[4] * sy12 + box.cpu_h[5] * sz12;
    z12 = box.cpu_h[6] * sx12 + box.cpu_h[7] * sy12 + box.cpu_h[8] * sz12;
  }
}

// Float overload used inside the NEP kernels; identical logic on float_h
// (reference box.cuh:84).

inline __host__ __device__ void apply_mic(const Box& box, float& x12, float& y12, float& z12)
{
  if (box.is_orthogonal) {
    float Lx2 = box.float_h[0]*0.5f;
    float Ly2 = box.float_h[4]*0.5f;
    float Lz2 = box.float_h[8]*0.5f;

    if (box.pbc_x == 1) {
      if (x12 < -Lx2) {
        x12 += box.float_h[0];
      } else if (x12 > +Lx2) {
        x12 -= box.float_h[0];
      }
    }

    if (box.pbc_y == 1) {
      if (y12 < -Ly2) {
        y12 += box.float_h[4];
      } else if (y12 > +Ly2) {
        y12 -= box.float_h[4];
      }
    }

    if (box.pbc_z == 1) {
      if (z12 < -Lz2) {
        z12 += box.float_h[8];
      } else if (z12 > +Lz2) {
        z12 -= box.float_h[8];
      }
    }
  }
  else {
    float sx12 = box.float_h[9] * x12 + box.float_h[10] * y12 + box.float_h[11] * z12;
    float sy12 = box.float_h[12] * x12 + box.float_h[13] * y12 + box.float_h[14] * z12;
    float sz12 = box.float_h[15] * x12 + box.float_h[16] * y12 + box.float_h[17] * z12;
    if (box.pbc_x == 1)
      sx12 -= nearbyint(sx12);
    if (box.pbc_y == 1)
      sy12 -= nearbyint(sy12);
    if (box.pbc_z == 1)
      sz12 -= nearbyint(sz12);
    x12 = box.float_h[0] * sx12 + box.float_h[1] * sy12 + box.float_h[2] * sz12;
    y12 = box.float_h[3] * sx12 + box.float_h[4] * sy12 + box.float_h[5] * sz12;
    z12 = box.float_h[6] * sx12 + box.float_h[7] * sy12 + box.float_h[8] * sz12;
  }
}

}  // namespace gpumd_compat
