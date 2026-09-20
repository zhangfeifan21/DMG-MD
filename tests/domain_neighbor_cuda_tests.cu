// CUDA regression for the M2a Neighbor center-range API.  In particular,
// center_begin is deliberately non-zero so ELL row selection must use
// slot * stride + center rather than stride * center_begin.
#include "gpumd_compat/box.cuh"
#include "gpumd_compat/gpu_vector.cuh"
#include "gpumd_compat/neighbor.cuh"

#include <cuda_runtime.h>

#include <algorithm>
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
      7.0, box, type, position, gid, 1, 3, count, true);

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
    check_nonzero_center_begin();
    std::cout << "domain_neighbor_cuda_tests: all checks passed\n";
  } catch (const std::exception& error) {
    std::cerr << "domain_neighbor_cuda_tests FAILED: " << error.what() << '\n';
    return 1;
  }
  return 0;
}
