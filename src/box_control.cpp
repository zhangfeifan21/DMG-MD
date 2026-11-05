#include <cuda_runtime.h>
#include "dmgmd/box_control.hpp"

#include <cmath>
#include <stdexcept>

namespace dmgmd::detail {

gpumd_compat::Box prepare_scaled_box(const gpumd_compat::Box& box, double factor)
{
  if (!std::isfinite(factor) || factor <= 0.0) {
    throw std::invalid_argument("isotropic box scale factor must be finite and positive");
  }
  if (!box.is_orthogonal) {
    throw std::invalid_argument("isotropic box scaling requires an orthogonal box");
  }
  if (box.cpu_h[1] != 0.0 || box.cpu_h[2] != 0.0 || box.cpu_h[3] != 0.0 ||
      box.cpu_h[5] != 0.0 || box.cpu_h[6] != 0.0 || box.cpu_h[7] != 0.0) {
    throw std::invalid_argument(
        "isotropic box scaling requires zero off-diagonal box entries");
  }
  if (box.pbc_x != 1 || box.pbc_y != 1 || box.pbc_z != 1) {
    throw std::invalid_argument("isotropic box scaling requires periodicity in all directions");
  }

  gpumd_compat::Box candidate = box;
  candidate.cpu_h[0] *= factor;
  candidate.cpu_h[4] *= factor;
  candidate.cpu_h[8] *= factor;
  for (int index = 0; index < 9; ++index) {
    if (!std::isfinite(candidate.cpu_h[index])) {
      throw std::overflow_error("scaled box matrix contains a non-finite value");
    }
  }
  if (candidate.cpu_h[0] <= 0.0 || candidate.cpu_h[4] <= 0.0 ||
      candidate.cpu_h[8] <= 0.0) {
    throw std::overflow_error("scaled box lengths must remain positive");
  }
  candidate.get_inverse();
  candidate.set_is_orthogonal();
  const double volume = candidate.get_volume();
  if (!std::isfinite(volume) || volume <= 0.0) {
    throw std::overflow_error("scaled box volume must remain finite and positive");
  }
  for (int index = 9; index < 18; ++index) {
    if (!std::isfinite(candidate.cpu_h[index])) {
      throw std::overflow_error("scaled inverse box matrix contains a non-finite value");
    }
  }
  if (!(candidate.cpu_h[9] > 0.0) || !(candidate.cpu_h[13] > 0.0) ||
      !(candidate.cpu_h[17] > 0.0)) {
    throw std::overflow_error("scaled inverse box lengths must remain positive");
  }
  for (int index = 0; index < 18; ++index) {
    if (!std::isfinite(candidate.float_h[index])) {
      throw std::overflow_error("scaled float box matrix contains a non-finite value");
    }
  }
  if (!(candidate.float_h[9] > 0.0f) || !(candidate.float_h[13] > 0.0f) ||
      !(candidate.float_h[17] > 0.0f)) {
    throw std::overflow_error("scaled float inverse box lengths must remain positive");
  }
  if (candidate.float_h[0] <= 0.0f || candidate.float_h[4] <= 0.0f ||
      candidate.float_h[8] <= 0.0f) {
    throw std::overflow_error("scaled float box lengths must remain positive");
  }
  return candidate;
}

}  // namespace dmgmd::detail
