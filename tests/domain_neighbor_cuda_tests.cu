// CUDA regression for the M2a Neighbor center-range API.  In particular,
// center_begin is deliberately non-zero so ELL row selection must use
// slot * stride + center rather than stride * center_begin.
#include "gpumd_compat/box.cuh"
#include "gpumd_compat/gpu_vector.cuh"
#include "gpumd_compat/neighbor.cuh"

#include <cuda_runtime.h>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {

using gpumd_compat::Box;
using gpumd_compat::GPU_Vector;
using gpumd_compat::Neighbor;

void require(bool condition, const char* message)
{
  if (!condition) throw std::runtime_error(message);
}

Box make_box()
{
  Box box{};
  box.cpu_h[0] = 64.0;
  box.cpu_h[4] = 24.0;
  box.cpu_h[8] = 24.0;
  box.get_inverse();
  box.set_is_orthogonal();
  return box;
}

void check_nonzero_center_begin()
{
  constexpr int count = 4;
  const std::vector<int> host_type(count, 0);
  const std::vector<unsigned long long> host_gid{40, 30, 20, 10};
  const std::vector<double> host_position{
      1.0, 2.0, 3.0, 4.0,
      12.0, 12.0, 12.0, 12.0,
      12.0, 12.0, 12.0, 12.0};

  GPU_Vector<int> type(count);
  GPU_Vector<unsigned long long> gid(count);
  GPU_Vector<double> position(3 * count);
  type.copy_from_host(host_type.data());
  gid.copy_from_host(host_gid.data());
  position.copy_from_host(host_position.data());

  Neighbor neighbor;
  neighbor.initialize(7.0, count, 16);
  Box box = make_box();
  neighbor.find_neighbor_domain(
      7.0, box, type, position, gid, 1, 3, count,
      gpumd_compat::DomainNeighborAction::must_rebuild, 7);

  std::vector<int> nn(count, 0);
  std::vector<int> nl(neighbor.NL.size(), -1);
  neighbor.NN.copy_to_host(nn.data());
  neighbor.NL.copy_to_host(nl.data());
  require(nn[1] == 3 && nn[2] == 3,
          "non-zero center range must build both selected rows");

  const auto row = [&](int center) {
    std::vector<int> values;
    for (int slot = 0; slot < nn[center]; ++slot) {
      values.push_back(nl[static_cast<std::size_t>(slot) * count + center]);
    }
    return values;
  };
  require(row(1) == std::vector<int>({3, 2, 0}),
          "center 1 row must be sorted by candidate global ID");
  require(row(2) == std::vector<int>({3, 1, 0}),
          "center 2 row must be sorted by candidate global ID");

  // The runtime owns the displacement decision. Moving a candidate far away
  // and explicitly confirming reuse must leave the cached rows untouched.
  std::vector<double> moved = host_position;
  moved[3] = 30.0;
  position.copy_from_host(moved.data());
  neighbor.find_neighbor_domain(
      7.0, box, type, position, gid, 1, 3, count,
      gpumd_compat::DomainNeighborAction::confirmed_reuse, 7);
  std::vector<int> reused(count, 0);
  neighbor.NN.copy_to_host(reused.data());
  require(reused == nn, "confirmed reuse must not rebuild cached rows");

  bool stale_epoch_rejected = false;
  try {
    neighbor.find_neighbor_domain(
        7.0, box, type, position, gid, 1, 3, count,
        gpumd_compat::DomainNeighborAction::confirmed_reuse, 8);
  } catch (const std::logic_error&) {
    stale_epoch_rejected = true;
  }
  require(stale_epoch_rejected, "confirmed reuse with a stale layout epoch must fail");

  Neighbor missing_cache;
  missing_cache.initialize(7.0, count, 16);
  bool rejected = false;
  try {
    missing_cache.find_neighbor_domain(
        7.0, box, type, position, gid, 1, 3, count,
        gpumd_compat::DomainNeighborAction::confirmed_reuse, 7);
  } catch (const std::logic_error&) {
    rejected = true;
  }
  require(rejected, "confirmed reuse without a reference cache must fail");
}

void check_gpu_vector_capacity_reuse()
{
  GPU_Vector<int> values;
  const std::uint64_t allocations_before =
      gpumd_compat::gpu_vector_allocation_count();
  values.resize_reuse(8, 3);
  require(values.size() == 8 && values.capacity() >= 8,
          "capacity-aware resize must retain an exact logical size");
  require(gpumd_compat::gpu_vector_allocation_count() == allocations_before + 1,
          "first capacity-aware resize must allocate once");
  int* const storage = values.data();
  const std::size_t capacity = values.capacity();

  values.resize_reuse(4, 7);
  require(values.data() == storage && values.size() == 4 && values.capacity() == capacity,
          "shrinking logical size must reuse physical storage");
  require(gpumd_compat::gpu_vector_allocation_count() == allocations_before + 1,
          "reused storage must not increment the allocation counter");
  std::vector<int> host(4, 0);
  values.copy_to_host(host.data());
  require(host == std::vector<int>(4, 7),
          "initialized resize must fill the complete reused logical range");

  values.resize_reuse(capacity + 1);
  require(values.size() == capacity + 1 && values.capacity() >= values.size(),
          "growth must preserve exact logical size and add sufficient capacity");
  require(gpumd_compat::gpu_vector_allocation_count() == allocations_before + 2,
          "capacity growth must increment the allocation counter once");
  int* const grown_storage = values.data();
  values.resize_reuse(0);
  require(values.size() == 0 && values.data() == grown_storage,
          "logical clear must retain reusable physical storage");
}

}  // namespace

int main()
{
  int device_count = 0;
  const cudaError_t status = cudaGetDeviceCount(&device_count);
  if (status != cudaSuccess || device_count == 0) {
    std::cout << "domain_neighbor_cuda_tests: skipped (no CUDA device)\n";
    return 77;
  }
  try {
    check_gpu_vector_capacity_reuse();
    check_nonzero_center_begin();
    std::cout << "domain_neighbor_cuda_tests: all checks passed\n";
  } catch (const std::exception& error) {
    std::cerr << "domain_neighbor_cuda_tests FAILED: " << error.what() << '\n';
    return 1;
  }
  return 0;
}
