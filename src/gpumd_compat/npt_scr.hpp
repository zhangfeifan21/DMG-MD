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
// This host-only scalar preserves the isotropic pressure equation and its
// literal constants/operation order; state validation and RNG ownership stay
// in EnsembleController.

#pragma once

namespace gpumd_compat {

double npt_scr_isotropic_scale(
    double target_pressure, double pressure_coupling,
    double pressure_instant, double target_temperature,
    double volume, double gaussian);

}  // namespace gpumd_compat
