#pragma once

#include <algorithm>
#include <cstddef>
#include <stdexcept>

namespace dmgmd {

// Half-open global-index range owned by one rank. The ownership proof and
// replicated-input exception is specified in docs/standards/replicated-mpi.md.
// Replicated inputs continue to use global_count as their stride; this range
// only grants write/reduction ownership for outputs and integration state.
struct OwnedRange {
  std::size_t begin = 0;
  std::size_t end = 0;

  [[nodiscard]] std::size_t size() const noexcept { return end - begin; }
};

inline OwnedRange balanced_owned_range(
    std::size_t global_count,
    int rank,
    int world_size)
{
  if (world_size <= 0 || rank < 0 || rank >= world_size) {
    throw std::invalid_argument("invalid MPI rank or world size");
  }
  const std::size_t ranks = static_cast<std::size_t>(world_size);
  const std::size_t rank_index = static_cast<std::size_t>(rank);
  const std::size_t base = global_count / ranks;
  const std::size_t remainder = global_count % ranks;
  const std::size_t begin = rank_index * base + std::min(rank_index, remainder);
  const std::size_t count = base + (rank_index < remainder ? 1 : 0);
  return OwnedRange{begin, begin + count};
}

}  // namespace dmgmd
