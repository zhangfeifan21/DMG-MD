#pragma once

#include "newmd/types.hpp"

#include <array>
#include <cmath>
#include <cstddef>
#include <type_traits>

#if defined(__CUDACC__)
#define NEWMD_DEVICE_INLINE __device__ __forceinline__
#else
#define NEWMD_DEVICE_INLINE inline
#endif

namespace newmd {

enum class BoxGeometry : unsigned char {
  orthorhombic,
  // Future versions may add triclinic without changing the v0.1 fast path.
};

// Small non-owning value passed directly as a CUDA kernel argument.
struct OrthorhombicBoxView {
  Real lx = 0;
  Real ly = 0;
  Real lz = 0;

  Real inverse_lx = 0;
  Real inverse_ly = 0;
  Real inverse_lz = 0;

  // Position wrapping uses the canonical half-open interval [0, L).
  // In particular, a coordinate exactly equal to L is mapped to 0.
  NEWMD_DEVICE_INLINE Real wrap_x(Real x) const noexcept {
    return x - lx * floor(x * inverse_lx);
  }

  NEWMD_DEVICE_INLINE Real wrap_y(Real y) const noexcept {
    return y - ly * floor(y * inverse_ly);
  }

  NEWMD_DEVICE_INLINE Real wrap_z(Real z) const noexcept {
    return z - lz * floor(z * inverse_lz);
  }

  NEWMD_DEVICE_INLINE void apply_minimum_image(
      Real& dx,
      Real& dy,
      Real& dz) const noexcept {
    dx -= lx * nearbyint(dx * inverse_lx);
    dy -= ly * nearbyint(dy * inverse_ly);
    dz -= lz * nearbyint(dz * inverse_lz);
  }
};

static_assert(std::is_trivially_copyable_v<OrthorhombicBoxView>);

class SimulationBox {
public:
  static SimulationBox orthorhombic(
      Real lx,
      Real ly,
      Real lz);

  [[nodiscard]] BoxGeometry geometry() const noexcept {
    return BoxGeometry::orthorhombic;
  }

  [[nodiscard]] Real length(Axis axis) const noexcept {
    return lengths_[static_cast<std::size_t>(axis)];
  }

  [[nodiscard]] Real inverse_length(Axis axis) const noexcept {
    return inverse_lengths_[static_cast<std::size_t>(axis)];
  }

  [[nodiscard]] Real volume() const noexcept {
    return volume_;
  }

  // NewMD v0.1 is periodic in all three dimensions.
  [[nodiscard]] bool periodic(Axis) const noexcept {
    return true;
  }

  [[nodiscard]] OrthorhombicBoxView orthorhombic_view() const noexcept {
    return OrthorhombicBoxView{
        lengths_[0],
        lengths_[1],
        lengths_[2],
        inverse_lengths_[0],
        inverse_lengths_[1],
        inverse_lengths_[2],
    };
  }

private:
  SimulationBox(
      std::array<Real, 3> lengths,
      std::array<Real, 3> inverse_lengths,
      Real volume) noexcept
      : lengths_(lengths),
        inverse_lengths_(inverse_lengths),
        volume_(volume) {}

  std::array<Real, 3> lengths_{};
  std::array<Real, 3> inverse_lengths_{};
  Real volume_ = 0;
};

}  // namespace newmd

#undef NEWMD_DEVICE_INLINE
