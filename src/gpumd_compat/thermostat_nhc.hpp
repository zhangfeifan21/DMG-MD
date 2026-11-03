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

// Origin: GPUMD 9d23496e41319b9e2af5221a7df6285387401d1e,
// src/integrate/ensemble_nhc.cu, Ensemble_NHC constructor and nhc().
// Host-only NVT NHC numerical subset; CUDA integration and other ensembles
// are intentionally omitted.

#pragma once

#include <array>

namespace gpumd_compat {

inline constexpr int kNhcChainLength = 4;

struct NhcState {
  std::array<double, kNhcChainLength> position;
  std::array<double, kNhcChainLength> momentum;
  std::array<double, kNhcChainLength> mass;
};

NhcState make_nhc_state(int atom_count, double temperature, double damping, double dt);

double nhc_half_step(
  NhcState& state, double Ek2, double kT, double dN, double dt_half);

} // namespace gpumd_compat
