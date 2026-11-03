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

// Origin: GPUMD commit 9d23496e41319b9e2af5221a7df6285387401d1e,
// src/integrate/svr_utilities.cuh: gasdev, gamdev, resamplekin_sumnoises,
// resamplekin. Host-only NVT BDP numerical subset; no CUDA/MPI. The only
// algorithm adaptation is moving gasdev's function-static iset/gset cache into
// SvrState. The invalid gamdev ia<1 placeholder now throws invalid_argument.

#include "gpumd_compat/thermostat_svr.hpp"

#include <cmath>
#include <stdexcept>

namespace gpumd_compat {

SvrState::SvrState(std::uint32_t seed) : rng(seed) {}

double gasdev(SvrState& state)
{
  std::uniform_real_distribution<double> rand1(0, 1);

  double fac, rsq, v1, v2;

  if (state.iset == 0) {
    do {
      v1 = 2.0 * rand1(state.rng) - 1.0;
      v2 = 2.0 * rand1(state.rng) - 1.0;
      rsq = v1 * v1 + v2 * v2;
    } while (rsq >= 1.0 || rsq == 0.0);
    fac = sqrt(-2.0 * log(rsq) / rsq);
    state.gset = v1 * fac;
    state.iset = 1;
    return v2 * fac;
  } else {
    state.iset = 0;
    return state.gset;
  }
}

double gamdev(const int ia, SvrState& state)
{
  std::uniform_real_distribution<double> rand1(0, 1);

  int j;
  double am, e, s, v1, v2, x, y;

  if (ia < 1) {
    throw std::invalid_argument("gamdev requires ia >= 1");
  }
  if (ia < 6) {
    x = 1.0;
    for (j = 1; j <= ia; j++)
      x *= rand1(state.rng);
    x = -log(x);
  } else {
    do {
      do {
        do {
          v1 = rand1(state.rng);
          v2 = 2.0 * rand1(state.rng) - 1.0;
        } while (v1 * v1 + v2 * v2 > 1.0);
        y = v2 / v1;
        am = ia - 1;
        s = sqrt(2.0 * am + 1.0);
        x = s * y + am;
      } while (x <= 0.0);
      e = (1.0 + y * y) * exp(am * log(x / am) - s * y);
    } while (rand1(state.rng) > e);
  }
  return x;
}

double resamplekin_sumnoises(int nn, SvrState& state)
{
  /*
    returns the sum of n independent gaussian noises squared
     (i.e. equivalent to summing the square of the return values of nn calls to gasdev)
  */
  double rr;
  if (nn == 0) {
    return 0.0;
  } else if (nn == 1) {
    rr = gasdev(state);
    return rr * rr;
  } else if (nn % 2 == 0) {
    return 2.0 * gamdev(nn / 2, state);
  } else {
    rr = gasdev(state);
    return 2.0 * gamdev((nn - 1) / 2, state) + rr * rr;
  }
}

double resamplekin(double kk, double sigma, int ndeg, double taut, SvrState& state)
{
  /*
    kk:    present value of the kinetic energy of the atoms to be thermalized (in arbitrary units)
    sigma: target average value of the kinetic energy (ndeg k_b T/2)  (in the same units as kk)
    ndeg:  number of degrees of freedom of the atoms to be thermalized
    taut:  relaxation time of the thermostat, in units of 'how often this routine is called'
  */
  double factor, rr;
  if (taut > 0.1) {
    factor = exp(-1.0 / taut);
  } else {
    factor = 0.0;
  }
  rr = gasdev(state);
  return kk +
         (1.0 - factor) * (sigma * (resamplekin_sumnoises(ndeg - 1, state) + rr * rr) / ndeg - kk) +
         2.0 * rr * sqrt(kk * sigma / ndeg * (1.0 - factor) * factor);
}

}  // namespace gpumd_compat
