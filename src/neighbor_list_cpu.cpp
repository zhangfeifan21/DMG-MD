#include "newmd/neighbor_list_cpu.hpp"

#include "newmd/pbc_cpu.hpp"

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace newmd {
namespace {

void validate_atom_count(std::size_t atom_count) {
  if (atom_count > 0 &&
      atom_count - 1 > std::numeric_limits<NeighborIndex>::max()) {
    throw std::length_error(
        "Atom count exceeds the CSR neighbor-index range.");
  }
  if (atom_count > std::numeric_limits<std::size_t>::max() / 3) {
    throw std::length_error(
        "Host SoA position size exceeds the addressable range.");
  }
}

void validate_large_box(
    const SimulationBox& box,
    Real build_radius) {
  const std::array<Real, 3> lengths{
      box.length(Axis::x),
      box.length(Axis::y),
      box.length(Axis::z),
  };

  for (const Real length : lengths) {
    if (build_radius > 0.5 * length) {
      // A minimum-image edge stores only an atom index. When the search radius
      // exceeds half a periodic box length, multiple images of the same atom
      // can be inside the sphere and an index alone cannot distinguish them.
      // A future small-box implementation must enumerate image translations
      // and store an image shift (or an explicit displacement) per edge. The
      // future SlicedELL32 layout must preserve that additional edge payload.
      throw std::invalid_argument(
          "Neighbor-list build radius exceeds the minimum-image large-box "
          "limit; periodic image metadata would be required.");
    }
  }
}

void validate_positions(
    const Real* position_soa,
    std::size_t atom_count) {
  if (atom_count == 0) {
    return;
  }
  if (position_soa == nullptr) {
    throw std::invalid_argument(
        "Position pointer must not be null when atom_count is nonzero.");
  }

  for (std::size_t component = 0; component < 3; ++component) {
    const Real* values = position_soa + component * atom_count;
    for (std::size_t atom = 0; atom < atom_count; ++atom) {
      if (!std::isfinite(values[atom])) {
        throw std::invalid_argument(
            "Neighbor-list positions must be finite.");
      }
    }
  }
}

}  // namespace

CsrNeighborList build_neighbor_list_n2_cpu(
    const SimulationBox& box,
    const Real* position_soa,
    std::size_t atom_count,
    Real cutoff,
    Real skin) {
  validate_atom_count(atom_count);

  // Constructing the owner validates cutoff and skin before either is used in
  // arithmetic or in the distance comparison.
  CsrNeighborList result(atom_count, cutoff, skin);
  validate_large_box(box, result.build_radius());
  validate_positions(position_soa, atom_count);

  result.row_offsets_.resize(atom_count + 1);
  result.row_offsets_[0] = 0;

  if (atom_count == 0) {
    result.finish_build();
    return result;
  }

  const Real* const x = position_soa;
  const Real* const y = position_soa + atom_count;
  const Real* const z = position_soa + 2 * atom_count;
  const Real build_radius_squared =
      result.build_radius() * result.build_radius();

  for (std::size_t center = 0; center < atom_count; ++center) {
    for (std::size_t candidate = 0; candidate < atom_count; ++candidate) {
      if (center == candidate) {
        continue;
      }

      Real dx = x[candidate] - x[center];
      Real dy = y[candidate] - y[center];
      Real dz = z[candidate] - z[center];
      if (!std::isfinite(dx) || !std::isfinite(dy) ||
          !std::isfinite(dz)) {
        throw std::invalid_argument(
            "Neighbor-list displacement overflowed its Real representation.");
      }
      pbc::cpu::apply_minimum_image(box, dx, dy, dz);

      const Real distance_squared = dx * dx + dy * dy + dz * dz;
      if (distance_squared < build_radius_squared) {
        if (result.column_indices_.size() ==
            std::numeric_limits<NeighborOffset>::max()) {
          throw std::length_error(
              "CSR directed-edge count exceeds the offset range.");
        }
        result.column_indices_.push_back(
            static_cast<NeighborIndex>(candidate));
      }
    }

    result.row_offsets_[center + 1] =
        static_cast<NeighborOffset>(result.column_indices_.size());
  }

  result.finish_build();
  return result;
}

}  // namespace newmd
