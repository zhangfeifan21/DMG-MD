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

#include "gpumd_compat/thermostat_nhc.hpp"

#include <cmath>

namespace gpumd_compat {
namespace {
constexpr double kBoltzmann = 8.617343e-5;
}

NhcState make_nhc_state(int atom_count, double temperature, double damping, double dt)
{
  NhcState state{};
  state.position[0] = state.position[1] = state.position[2] = state.position[3] = 0.0;
  state.momentum[0] = state.momentum[2] = 1.0;
  state.momentum[1] = state.momentum[3] = -1.0;

  double tau = dt * damping;
  double kT = kBoltzmann * temperature;
  double dN = 3.0 * atom_count;
  for (int i = 0; i < kNhcChainLength; i++) {
    state.mass[i] = kT * tau * tau;
  }
  state.mass[0] *= dN;
  return state;
}

double nhc_half_step(
  NhcState& state, double Ek2, double kT, double dN, double dt_half)
{
  // These constants are taken from Tuckerman's book in GPUMD.
  const int M = kNhcChainLength;
  const int n_sy = 7;
  const int n_respa = 4;
  const double w[7] = {
    0.784513610477560,
    0.235573213359357,
    -1.17767998417887,
    1.31518632068391,
    -1.17767998417887,
    0.235573213359357,
    0.784513610477560};

  double factor = 1.0; // to be accumulated

  for (int n1 = 0; n1 < n_sy; n1++) {
    double dt2 = dt_half * w[n1] / n_respa;
    double dt4 = dt2 * 0.5;
    double dt8 = dt4 * 0.5;
    for (int n2 = 0; n2 < n_respa; n2++) {

      // update velocity of the last (M - 1) thermostat:
      double G = state.momentum[M - 2] * state.momentum[M - 2] / state.mass[M - 2] - kT;
      state.momentum[M - 1] += dt4 * G;

      // update thermostat velocities from M - 2 to 0:
      for (int m = M - 2; m >= 0; m--) {
        double tmp = exp(-dt8 * state.momentum[m + 1] / state.mass[m + 1]);
        if (m == 0) {
          // Memory-safety adaptation: upstream first reads m-1 here, then
          // overwrites G; branch before that discarded negative-index read.
          G = Ek2 - dN * kT;
        } else {
          G = state.momentum[m - 1] * state.momentum[m - 1] / state.mass[m - 1] - kT;
        }
        state.momentum[m] = tmp * (tmp * state.momentum[m] + dt4 * G);
      }

      // update thermostat positions from M - 1 to 0:
      for (int m = M - 1; m >= 0; m--) {
        state.position[m] += dt2 * state.momentum[m] / state.mass[m];
      }

      // compute the scale factor
      double factor_local = exp(-dt2 * state.momentum[0] / state.mass[0]);
      Ek2 *= factor_local * factor_local;
      factor *= factor_local;

      // update thermostat velocities from 0 to M - 2:
      for (int m = 0; m < M - 1; m++) {
        double tmp = exp(-dt8 * state.momentum[m + 1] / state.mass[m + 1]);
        if (m == 0) {
          // Memory-safety adaptation: upstream first reads m-1 here, then
          // overwrites G; branch before that discarded negative-index read.
          G = Ek2 - dN * kT;
        } else {
          G = state.momentum[m - 1] * state.momentum[m - 1] / state.mass[m - 1] - kT;
        }
        state.momentum[m] = tmp * (tmp * state.momentum[m] + dt4 * G);
      }

      // update velocity of the last (M - 1) thermostat:
      G = state.momentum[M - 2] * state.momentum[M - 2] / state.mass[M - 2] - kT;
      state.momentum[M - 1] += dt4 * G;
    }
  }
  return factor;
}

} // namespace gpumd_compat
