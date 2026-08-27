#pragma once

#include "newmd/atom.cuh"
#include "newmd/simulation_box.cuh"

#include <cuda_runtime.h>

namespace newmd::pbc::gpu {

// Enqueues position wrapping on stream and returns without synchronizing.
// All resulting coordinates use the canonical half-open interval [0, L).
void wrap_positions_async(
    AtomView atoms,
    OrthorhombicBoxView box,
    cudaStream_t stream = nullptr);

}  // namespace newmd::pbc::gpu
