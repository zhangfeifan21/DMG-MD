#include "newmd/neighbor_list.hpp"

#include <cmath>
#include <stdexcept>

namespace newmd {

NeighborListBase::NeighborListBase(
    std::size_t atom_count,
    Real cutoff,
    Real skin)
    : atom_count_(atom_count), cutoff_(cutoff), skin_(skin) {
  if (!std::isfinite(cutoff) || cutoff <= 0) {
    throw std::invalid_argument(
        "Neighbor-list cutoff must be finite and positive.");
  }
  if (!std::isfinite(skin) || skin < 0) {
    throw std::invalid_argument(
        "Neighbor-list skin must be finite and nonnegative.");
  }
  if (!std::isfinite(cutoff + skin)) {
    throw std::invalid_argument(
        "Neighbor-list build radius must be finite.");
  }
}

void NeighborListBase::set_build_result(
    NeighborOffset edge_count,
    NeighborOffset storage_slots) {
  if (storage_slots < edge_count) {
    throw std::logic_error(
        "Neighbor-list storage cannot contain fewer slots than edges.");
  }
  edge_count_ = edge_count;
  storage_slots_ = storage_slots;
}

}  // namespace newmd
