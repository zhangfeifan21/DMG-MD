#include "dmgmd/box_control.hpp"
#include "dmgmd/mpi_runtime.hpp"
#include "gpumd_compat/gpu_vector.cuh"

#include <cuda_runtime.h>

#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void require(bool condition, const std::string& message)
{
  if (!condition) throw std::runtime_error(message);
}

gpumd_compat::Box make_box()
{
  gpumd_compat::Box box{};
  box.cpu_h[0] = 10.0;
  box.cpu_h[4] = 12.0;
  box.cpu_h[8] = 14.0;
  box.get_inverse();
  box.set_is_orthogonal();
  return box;
}

}  // namespace

int main(int argc, char** argv)
{
  try {
    dmgmd::MpiRuntime mpi(argc, argv);
    const int rank = mpi.world_rank();
    const int ranks = mpi.world_size();
    require(ranks == 2 || ranks == 4, "expected P=2 or P=4");
    mpi.initialize_device();

    const bool empty_rank = rank == ranks - 1;
    const std::size_t owned_count = empty_rank ? 0 : 1;
    const std::size_t stride = empty_rank ? 0 : 2;
    std::vector<double> original(3 * stride, 1.0);
    if (rank == 0) original[0] = std::numeric_limits<double>::max();
    gpumd_compat::GPU_Vector<double> position(original.size());
    if (!original.empty()) position.copy_from_host(original.data(), original.size());

    auto box = make_box();
    const auto original_box = box;
    const auto candidate = dmgmd::detail::prepare_owned_box_scale(box, 2.0);
    dmgmd::detail::BoxScaleWorkspace workspace;
    const auto local_status = dmgmd::detail::preflight_owned_scale(
        position, workspace, candidate, owned_count, stride);
    dmgmd::CommunicationVolume communication;
    const auto reason = mpi.allreduce_or_u32(
        local_status == dmgmd::detail::BoxScaleStatus::ok ? 0u : 1u, communication);

    // Rank 0 overflows, while rank P-1 has no owned atoms. Every rank must
    // observe the failed vote and skip commit, including otherwise valid ranks.
    require(reason == 1u, "collective did not propagate the overflow reason");
    if (rank == 0) {
      require(local_status == dmgmd::detail::BoxScaleStatus::non_finite_position,
              "overflowing rank returned the wrong status");
    } else {
      require(local_status == dmgmd::detail::BoxScaleStatus::ok,
              "empty or valid rank returned a local failure");
    }

    if (!original.empty()) {
      std::vector<double> after(original.size());
      position.copy_to_host(after.data(), after.size());
      require(after == original, "collectively rejected preflight modified positions");
    }
    require(box.cpu_h[0] == original_box.cpu_h[0] &&
                box.cpu_h[4] == original_box.cpu_h[4] &&
                box.cpu_h[8] == original_box.cpu_h[8],
            "collectively rejected preflight modified the box");
    if (rank == 0) {
      std::cout << "OWNED_BOX_PREFLIGHT_PASS ranks=" << ranks
                << " overflow_rank=0 empty_rank=" << ranks - 1 << '\n';
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL owned box preflight MPI test: " << error.what() << '\n';
    return 1;
  }
}
