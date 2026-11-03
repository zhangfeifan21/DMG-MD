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
// resamplekin. This is the host-only NVT BDP numerical subset; CUDA/MPI and
// unrelated heating/cooling integrations are excluded. The upstream function
// static gasdev cache is instance state here so independent controllers do not
// share cached Gaussian values. gamdev rejects ia < 1 explicitly (upstream has
// an empty FATAL ERROR placeholder).

#pragma once

#include <cstdint>
#include <random>

namespace gpumd_compat {

struct SvrState {
  std::mt19937 rng;
  int iset = 0;
  double gset = 0.0;

  explicit SvrState(std::uint32_t seed);
};

double gasdev(SvrState& state);
double gamdev(int ia, SvrState& state);
double resamplekin_sumnoises(int nn, SvrState& state);
double resamplekin(double kk, double sigma, int ndeg, double taut, SvrState& state);

}  // namespace gpumd_compat
