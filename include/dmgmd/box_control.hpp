#pragma once

#include <cmath>
#include <cuda_runtime.h>
#include "gpumd_compat/box.cuh"

#include <cstddef>
#include <memory>
#include <utility>

namespace gpumd_compat {
template <typename T> class GPU_Vector;
}

namespace dmgmd::detail {

struct OwnedBoxScaleCandidate;
enum class BoxScaleStatus;

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
  friend BoxScaleStatus preflight_owned_scale(
      const gpumd_compat::GPU_Vector<double>&, BoxScaleWorkspace&,
      const OwnedBoxScaleCandidate&, std::size_t, std::size_t);
  friend void commit_owned_scale(
      gpumd_compat::Box&, gpumd_compat::GPU_Vector<double>&, BoxScaleWorkspace&,
      const OwnedBoxScaleCandidate&, std::size_t, std::size_t);
};

// Validated immutable input to the owned two-phase transaction.
class OwnedBoxScaleCandidate {
 public:
  const gpumd_compat::Box& box() const noexcept { return box_; }
  double factor() const noexcept { return factor_; }

 private:
  OwnedBoxScaleCandidate(gpumd_compat::Box box, double factor)
      : box_(std::move(box)), factor_(factor) {}
  gpumd_compat::Box box_;
  double factor_;
  friend OwnedBoxScaleCandidate prepare_owned_box_scale(
      const gpumd_compat::Box&, double);
};

enum class BoxScaleStatus {
  ok,
  invalid_layout,
  non_finite_position
};

// Build and validate the scaled box without changing the live box.
gpumd_compat::Box prepare_scaled_box(
    const gpumd_compat::Box& box, double factor);

// Owned counterpart of prepare_scaled_box. Callers may catch failures and
// combine them across ranks before entering the position preflight.
OwnedBoxScaleCandidate prepare_owned_box_scale(
    const gpumd_compat::Box& box, double factor);

// Read-only position preflight for a local [owned | ghosts] buffer. Only the
// first owned_count slots are examined; local_stride is the component stride,
// not a global atom count. A non-ok result must be combined across ranks
// before any rank calls commit_owned_scale.
BoxScaleStatus preflight_owned_scale(
    const gpumd_compat::GPU_Vector<double>& position,
    BoxScaleWorkspace& workspace,
    const OwnedBoxScaleCandidate& candidate,
    std::size_t owned_count,
    std::size_t local_stride);

// Commit after every rank has passed preflight. This scales only owned slots
// and then replaces the live box. Empty-owned ranks are valid.
void commit_owned_scale(
    gpumd_compat::Box& box,
    gpumd_compat::GPU_Vector<double>& position,
    BoxScaleWorkspace& workspace,
    const OwnedBoxScaleCandidate& candidate,
    std::size_t owned_count,
    std::size_t local_stride);

// Apply an isotropic origin-centered position scaling to a replicated-full
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
