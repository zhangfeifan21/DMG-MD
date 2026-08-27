#pragma once

#include "newmd/neighbor_list.hpp"

#include <cstddef>
#include <type_traits>
#include <vector>

#if defined(__CUDACC__)
#define NEWMD_NEIGHBOR_INLINE __host__ __device__ __forceinline__
#else
#define NEWMD_NEIGHBOR_INLINE inline
#endif

namespace newmd {

// Non-owning logical access to a CSR neighbor list. Keeping the hot-loop
// protocol in a small value type rather than NeighborListBase is important:
// a future SlicedEll32NeighborView can implement the same two functions with
// its own ordinal-major, 32-lane address calculation, and both layouts remain
// fully inlineable in CPU code or CUDA kernels.
struct CsrNeighborView {
  const NeighborOffset* row_offsets = nullptr;
  const NeighborIndex* neighbors = nullptr;
  std::size_t atom_count = 0;

  NEWMD_NEIGHBOR_INLINE NeighborOffset neighbor_count(
      std::size_t atom) const noexcept {
    return row_offsets[atom + 1] - row_offsets[atom];
  }

  NEWMD_NEIGHBOR_INLINE NeighborIndex neighbor(
      std::size_t atom,
      NeighborOffset ordinal) const noexcept {
    return neighbors[row_offsets[atom] + ordinal];
  }
};

static_assert(std::is_trivially_copyable_v<CsrNeighborView>);

class CsrNeighborList final : public NeighborListBase {
public:
  CsrNeighborList(const CsrNeighborList&) = delete;
  CsrNeighborList& operator=(const CsrNeighborList&) = delete;
  CsrNeighborList(CsrNeighborList&&) noexcept = default;
  CsrNeighborList& operator=(CsrNeighborList&&) noexcept = default;
  ~CsrNeighborList() override = default;

  [[nodiscard]] NeighborListLayout layout() const noexcept override {
    return NeighborListLayout::csr;
  }

  [[nodiscard]] CsrNeighborView view() const noexcept {
    return CsrNeighborView{
        row_offsets_.data(),
        column_indices_.data(),
        atom_count(),
    };
  }

  [[nodiscard]] const std::vector<NeighborOffset>& row_offsets()
      const noexcept {
    return row_offsets_;
  }

  [[nodiscard]] const std::vector<NeighborIndex>& column_indices()
      const noexcept {
    return column_indices_;
  }

private:
  friend CsrNeighborList build_neighbor_list_n2_cpu(
      const class SimulationBox& box,
      const Real* position_soa,
      std::size_t atom_count,
      Real cutoff,
      Real skin);

  CsrNeighborList(
      std::size_t atom_count,
      Real cutoff,
      Real skin)
      : NeighborListBase(atom_count, cutoff, skin) {}

  void finish_build() {
    const NeighborOffset slots =
        static_cast<NeighborOffset>(column_indices_.size());
    // CSR has no padding. SlicedELL32 will instead pass the final slice offset
    // as storage_slots while retaining the unpadded directed-edge count.
    set_build_result(slots, slots);
  }

  std::vector<NeighborOffset> row_offsets_;
  std::vector<NeighborIndex> column_indices_;
};

}  // namespace newmd

#undef NEWMD_NEIGHBOR_INLINE
