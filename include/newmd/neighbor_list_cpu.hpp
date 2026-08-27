#pragma once

#include "newmd/neighbor_list_csr.hpp"
#include "newmd/simulation_box.cuh"

#include <cstddef>

namespace newmd {

// Builds a deterministic full directed neighbor list from host SoA positions
// in O(N^2) time. For every center atom i, candidate atom indices j are tested
// in ascending order, so every CSR row is sorted without a separate sort.
//
// The actual search radius is cutoff + skin. The correctness-first baseline
// defaults to skin == 0 and rebuilds whenever it is called. A future reusable
// list may keep reference positions and rebuild when an atom has moved by more
// than skin / 2; that state belongs to a builder/manager, not to the storage
// base class.
CsrNeighborList build_neighbor_list_n2_cpu(
    const SimulationBox& box,
    const Real* position_soa,
    std::size_t atom_count,
    Real cutoff,
    Real skin = 0);

}  // namespace newmd
