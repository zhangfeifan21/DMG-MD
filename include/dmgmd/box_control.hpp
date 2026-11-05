#pragma once

#include <cmath>
#include <cuda_runtime.h>
#include "gpumd_compat/box.cuh"

#include <cstddef>
#include <memory>

namespace gpumd_compat {
template <typename T> class GPU_Vector;
}

namespace dmgmd::detail {

struct BoxScaleWorkspace {
  BoxScaleWorkspace();
  ~BoxScaleWorkspace();
  BoxScaleWorkspace(const BoxScaleWorkspace&) = delete;
  BoxScaleWorkspace& operator=(const BoxScaleWorkspace&) = delete;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
  friend void apply_isotropic_scale(
      gpumd_compat::Box&, gpumd_compat::GPU_Vector<double>&, BoxScaleWorkspace&,
      std::size_t, std::size_t, double);
};

// Build and validate the scaled box without changing the live box.
gpumd_compat::Box prepare_scaled_box(
    const gpumd_compat::Box& box, double factor);

// Apply an isotropic origin-centered position scaling to a replicated replicated-full
// position buffer. The numerical preflight completes before either positions
// or the live box are changed.
void apply_isotropic_scale(
    gpumd_compat::Box& box,
    gpumd_compat::GPU_Vector<double>& position,
    BoxScaleWorkspace& workspace,
    std::size_t global_count,
    std::size_t local_count,
    double factor);

}  // namespace dmgmd::detail
