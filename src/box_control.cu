#include <cuda_runtime.h>
#include "dmgmd/box_control.hpp"

#include "gpumd_compat/gpu_vector.cuh"

#include <cuda_runtime.h>

#include <algorithm>
#include <climits>
#include <limits>
#include <stdexcept>
#include <string>

namespace dmgmd::detail {
namespace {

constexpr int kThreads = 256;

__global__ void preflight_scaled_positions(
    std::size_t global_count,
    std::size_t stride,
    double factor,
    const double* position,
    int* invalid)
{
  const std::size_t slot = blockIdx.x * blockDim.x + threadIdx.x;
  if (slot >= global_count) return;
  for (int axis = 0; axis < 3; ++axis) {
    const double value = position[static_cast<std::size_t>(axis) * stride + slot];
    const double scaled = value * factor;
    if (!isfinite(value) || !isfinite(scaled)) atomicExch(invalid, 1);
  }
}

__global__ void scale_positions(
    std::size_t global_count,
    std::size_t stride,
    double factor,
    double* position)
{
  const std::size_t slot = blockIdx.x * blockDim.x + threadIdx.x;
  if (slot >= global_count) return;
  for (int axis = 0; axis < 3; ++axis) {
    position[static_cast<std::size_t>(axis) * stride + slot] *= factor;
  }
}

void check_cuda(cudaError_t status, const char* what)
{
  if (status != cudaSuccess) {
    throw std::runtime_error(std::string(what) + ": " + cudaGetErrorString(status));
  }
}

}  // namespace

struct BoxScaleWorkspace::Impl {
  gpumd_compat::GPU_Vector<int> invalid_flag;
};

BoxScaleWorkspace::BoxScaleWorkspace() : impl_(std::make_unique<Impl>()) {}
BoxScaleWorkspace::~BoxScaleWorkspace() = default;

void apply_isotropic_scale(
    gpumd_compat::Box& box,
    gpumd_compat::GPU_Vector<double>& position,
    BoxScaleWorkspace& workspace,
    std::size_t global_count,
    std::size_t local_count,
    double factor)
{
  if (global_count > static_cast<std::size_t>(INT_MAX) ||
      local_count > static_cast<std::size_t>(INT_MAX)) {
    throw std::length_error("box-scale atom count exceeds the supported int range");
  }
  if (local_count > std::numeric_limits<std::size_t>::max() / 3 ||
      global_count > local_count) {
    throw std::invalid_argument("M1 position stride is smaller than global_count");
  }
  if (position.size() < 3 * local_count) {
    throw std::invalid_argument("M1 position buffer is smaller than three local strides");
  }
  gpumd_compat::Box candidate = prepare_scaled_box(box, factor);
  if (workspace.impl_->invalid_flag.size() == 0) {
    workspace.impl_->invalid_flag.resize(1, 0);
  }
  int* device_invalid = workspace.impl_->invalid_flag.data();
  int invalid = 0;
  check_cuda(cudaMemset(device_invalid, 0, sizeof(int)), "clear box-scale preflight flag");
  const int blocks = static_cast<int>(global_count / kThreads +
      (global_count % kThreads != 0));
  if (global_count != 0) {
    preflight_scaled_positions<<<blocks, kThreads>>>(
        global_count, local_count, factor, position.data(), device_invalid);
    check_cuda(cudaGetLastError(), "launch box-scale position preflight");
  }
  check_cuda(cudaMemcpy(&invalid, device_invalid, sizeof(int), cudaMemcpyDeviceToHost),
             "read box-scale preflight status");
  if (invalid != 0) {
    throw std::overflow_error("position scaling would produce a non-finite coordinate");
  }
  if (global_count != 0) {
    scale_positions<<<blocks, kThreads>>>(global_count, local_count, factor, position.data());
    check_cuda(cudaGetLastError(), "launch isotropic position scaling");
    check_cuda(cudaDeviceSynchronize(), "complete isotropic position scaling");
  }
  box = candidate;
}

}  // namespace dmgmd::detail
