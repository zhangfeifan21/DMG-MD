#include "newmd/simulation_box.cuh"

#include <array>
#include <cmath>
#include <stdexcept>

namespace newmd {

SimulationBox SimulationBox::orthorhombic(
    Real lx,
    Real ly,
    Real lz) {
  const std::array<Real, 3> lengths{lx, ly, lz};

  for (const Real length : lengths) {
    if (!std::isfinite(length) || length <= 0) {
      throw std::invalid_argument(
          "Orthorhombic box lengths must be finite and positive.");
    }
  }

  const std::array<Real, 3> inverse_lengths{
      1.0 / lx,
      1.0 / ly,
      1.0 / lz,
  };
  for (const Real inverse_length : inverse_lengths) {
    if (!std::isfinite(inverse_length)) {
      throw std::invalid_argument(
          "Orthorhombic inverse box lengths must be finite.");
    }
  }

  const Real volume = lx * ly * lz;
  if (!std::isfinite(volume) || volume <= 0) {
    throw std::invalid_argument(
        "Orthorhombic box volume must be finite and positive.");
  }

  return SimulationBox(lengths, inverse_lengths, volume);
}

}  // namespace newmd
