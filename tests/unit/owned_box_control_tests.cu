#include "dmgmd/box_control.hpp"
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

void upload(gpumd_compat::GPU_Vector<double>& vector, const std::vector<double>& values)
{
  vector.copy_from_host(values.data(), values.size());
}

std::vector<double> download(gpumd_compat::GPU_Vector<double>& vector)
{
  std::vector<double> values(vector.size());
  vector.copy_to_host(values.data(), values.size());
  return values;
}

}  // namespace

int main()
{
  int device_count = 0;
  const cudaError_t device_status = cudaGetDeviceCount(&device_count);
  if (device_status != cudaSuccess || device_count == 0) return 77;
  try {
    using dmgmd::detail::BoxScaleStatus;
    using dmgmd::detail::BoxScaleWorkspace;
    using dmgmd::detail::commit_owned_scale;
    using dmgmd::detail::preflight_owned_scale;
    using dmgmd::detail::prepare_owned_box_scale;

    // Owned slots scale; poison-filled ghosts remain untouched and are not
    // considered by the position preflight. Stride is local_count, not N.
    {
      constexpr std::size_t stride = 4;
      std::vector<double> initial(3 * stride, -701.0);
      initial[0] = 1.0; initial[1] = 2.0;
      initial[stride] = 3.0; initial[stride + 1] = 4.0;
      initial[2 * stride] = 5.0; initial[2 * stride + 1] = 6.0;
      for (std::size_t axis = 0; axis < 3; ++axis) {
        initial[axis * stride + 2] = std::numeric_limits<double>::quiet_NaN();
        initial[axis * stride + 3] = std::numeric_limits<double>::infinity();
      }
      gpumd_compat::GPU_Vector<double> position(initial.size());
      upload(position, initial);
      auto box = make_box();
      const auto candidate = prepare_owned_box_scale(box, 2.0);
      BoxScaleWorkspace workspace;
      require(preflight_owned_scale(position, workspace, candidate, 2, stride) ==
                  BoxScaleStatus::ok,
              "poisoned ghosts affected owned preflight");
      commit_owned_scale(box, position, workspace, candidate, 2, stride);
      const auto result = download(position);
      for (std::size_t axis = 0; axis < 3; ++axis) {
        require(result[axis * stride] == 2.0 * initial[axis * stride] &&
                    result[axis * stride + 1] == 2.0 * initial[axis * stride + 1],
                "owned position was not scaled");
        require(std::isnan(result[axis * stride + 2]) &&
                    std::isinf(result[axis * stride + 3]),
                "ghost position was modified");
      }
      require(box.cpu_h[0] == 20.0 && box.cpu_h[4] == 24.0 && box.cpu_h[8] == 28.0,
              "candidate box was not committed");
    }

    // Factor 1 is a valid transaction and leaves every position unchanged.
    {
      constexpr std::size_t stride = 3;
      const std::vector<double> initial{1, 2, 3, 4, 5, 6, 7, 8, 9};
      gpumd_compat::GPU_Vector<double> position(initial.size());
      upload(position, initial);
      auto box = make_box();
      const auto candidate = prepare_owned_box_scale(box, 1.0);
      BoxScaleWorkspace workspace;
      require(preflight_owned_scale(position, workspace, candidate, 2, stride) ==
                  BoxScaleStatus::ok,
              "factor-one preflight failed");
      commit_owned_scale(box, position, workspace, candidate, 2, stride);
      require(download(position) == initial, "factor-one changed positions");
      require(box.cpu_h[0] == 10.0 && box.cpu_h[4] == 12.0 && box.cpu_h[8] == 14.0,
              "factor-one changed the box");
    }

    // Empty-owned rank commits the common box without touching its ghost-only
    // storage; this also covers an entirely empty local buffer.
    {
      gpumd_compat::GPU_Vector<double> position(3);
      const std::vector<double> ghosts{31.0, 32.0, 33.0};
      upload(position, ghosts);
      auto box = make_box();
      const auto candidate = prepare_owned_box_scale(box, 1.25);
      BoxScaleWorkspace workspace;
      require(preflight_owned_scale(position, workspace, candidate, 0, 1) ==
                  BoxScaleStatus::ok,
              "empty-owned preflight failed");
      commit_owned_scale(box, position, workspace, candidate, 0, 1);
      require(download(position) == ghosts, "empty-owned rank changed ghost storage");
      require(box.cpu_h[0] == 12.5, "empty-owned rank did not commit box");
      gpumd_compat::GPU_Vector<double> empty;
      require(preflight_owned_scale(empty, workspace, candidate, 0, 0) == BoxScaleStatus::ok,
              "zero-stride empty rank was rejected");
    }

    // Overflow is discovered before commit, with no partial position or box
    // mutation. The test models the local vote; the MPI caller ORs this status.
    {
      constexpr std::size_t stride = 2;
      std::vector<double> initial(3 * stride, 1.0);
      initial[stride - 1] = std::numeric_limits<double>::max();
      gpumd_compat::GPU_Vector<double> position(initial.size());
      upload(position, initial);
      auto box = make_box();
      const auto before_box = box;
      const auto candidate = prepare_owned_box_scale(box, 2.0);
      BoxScaleWorkspace workspace;
      require(preflight_owned_scale(position, workspace, candidate, 2, stride) ==
                  BoxScaleStatus::non_finite_position,
              "overflowing owned coordinate passed preflight");
      require(download(position) == initial, "preflight modified positions");
      require(box.cpu_h[0] == before_box.cpu_h[0] && box.cpu_h[4] == before_box.cpu_h[4] &&
                  box.cpu_h[8] == before_box.cpu_h[8],
              "failed preflight modified the box");
    }

    // Invalid shape is reported in preflight, leaving commit unreachable.
    {
      gpumd_compat::GPU_Vector<double> position(3);
      BoxScaleWorkspace workspace;
      const auto candidate = prepare_owned_box_scale(make_box(), 1.1);
      require(preflight_owned_scale(position, workspace, candidate, 2, 1) ==
                  BoxScaleStatus::invalid_layout,
              "owned count larger than stride was accepted");
    }
    std::cout << "PASS owned box-scale transaction tests\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL owned box-scale transaction tests: " << error.what() << '\n';
    return 1;
  }
}
