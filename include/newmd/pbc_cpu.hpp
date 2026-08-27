#pragma once

#include "newmd/simulation_box.cuh"

#include <cstddef>

namespace newmd::pbc::cpu {

// Reference implementation for host SoA positions. The result in every
// periodic direction is in the canonical half-open interval [0, L).
void wrap_positions(
    const SimulationBox& box,
    Real* position_soa,
    std::size_t atom_count);

// Applies the minimum-image convention independently in all three directions.
// Exact +/-L/2 ties retain their original sign, matching GPUMD's strict bounds.
void apply_minimum_image(
    const SimulationBox& box,
    Real& dx,
    Real& dy,
    Real& dz);

}  // namespace newmd::pbc::cpu
