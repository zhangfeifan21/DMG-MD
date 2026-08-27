#include "newmd/pbc_gpu.cuh"

#include <cuda_runtime.h>

#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <string>

namespace newmd::pbc::gpu {
namespace {

constexpr unsigned int kThreadsPerBlock = 256;
constexpr unsigned int kMaximumBlockCount = 65535;

__global__ void wrap_positions_kernel(
    VectorSoAView<Real> position,
    OrthorhombicBoxView box) {
  std::size_t atom = blockIdx.x * blockDim.x + threadIdx.x;
  const std::size_t stride = gridDim.x * blockDim.x;

  for (; atom < position.count; atom += stride) {
    position.x(atom) = box.wrap_x(position.x(atom));
    position.y(atom) = box.wrap_y(position.y(atom));
    position.z(atom) = box.wrap_z(position.z(atom));
  }
}

void check_launch(cudaError_t status, const char* operation) {
  if (status != cudaSuccess) {
    throw std::runtime_error(std::string(operation) + ": " +
                             cudaGetErrorString(status));
  }
}

}  // namespace

void wrap_positions_async(
    AtomView atoms,
    OrthorhombicBoxView box,
    cudaStream_t stream) {
  if (atoms.empty()) {
    return;
  }

  const std::size_t required_blocks =
      (atoms.size() + kThreadsPerBlock - 1) / kThreadsPerBlock;
  const unsigned int block_count = static_cast<unsigned int>(
      std::min<std::size_t>(required_blocks, kMaximumBlockCount));

  wrap_positions_kernel<<<block_count, kThreadsPerBlock, 0, stream>>>(
      atoms.position, box);
  check_launch(cudaGetLastError(), "launch wrap_positions_kernel");
}

}  // namespace newmd::pbc::gpu
