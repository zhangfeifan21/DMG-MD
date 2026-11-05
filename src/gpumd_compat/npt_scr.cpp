/*
    Copyright 2017 Zheyong Fan and GPUMD development team
    This file is part of GPUMD.
    GPUMD is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
*/

// Origin: GPUMD commit 9d23496e41319b9e2af5221a7df6285387401d1e,
// src/integrate/ensemble_npt_scr.cu::cpu_pressure_isotropic (lines 137-160).
// This is the host-only isotropic scalar subset; no CUDA, MPI, or RNG code.

#include "gpumd_compat/npt_scr.hpp"
#include "gpumd_compat/common.cuh"

#include <cmath>

namespace gpumd_compat {

double npt_scr_isotropic_scale(
    const double target_pressure, const double pressure_coupling,
    const double pressure_instant, const double target_temperature,
    const double volume, const double gaussian)
{
  const double scale_factor_Berendsen =
      1.0 - pressure_coupling * (target_pressure - pressure_instant);
  // Keep the upstream literal 2/3 approximation and expression order.
  const double scale_factor_stochastic =
      std::sqrt(0.666666666666667 * pressure_coupling * K_B * target_temperature / volume) *
      gaussian;
  return scale_factor_Berendsen + scale_factor_stochastic;
}

}  // namespace gpumd_compat
