#pragma once

#include "newmd/types.hpp"

#include <cstddef>
#include <cstdint>

namespace newmd {

using NeighborIndex = std::uint32_t;
using NeighborOffset = std::uint64_t;

enum class NeighborListLayout : unsigned char {
  csr,
  sliced_ell32,
};

// NeighborListBase deliberately contains only layout-independent metadata.
//
// The base class exists so host-side owners can report a common logical
// neighbor-list identity and common storage statistics as NewMD gains another
// layout. It does not provide a virtual neighbor(atom, ordinal) function:
// calling a virtual function in the innermost NEP neighbor loop would prevent
// the compiler from inlining layout-specific address calculations and would
// not map cleanly to a CUDA kernel argument.
//
// Each concrete layout therefore supplies a small, non-owning view with the
// common neighbor_count(atom)/neighbor(atom, ordinal) protocol. A future
// SlicedELL32 owner will derive from this class on the host, while its
// trivially-copyable view will be passed by value to CUDA kernels.
//
// Future SlicedELL32 work intentionally left outside this base class:
//   * neighbor_counts[N] stores each row's logical length;
//   * slice_offsets[ceil(N / 32) + 1] locates each 32-row slice;
//   * neighbors[] uses ordinal * 32 + lane addressing and contains padding;
//   * set_build_result(real_edges, slice_offsets.back()) records padding;
//   * SlicedEll32NeighborView implements the same two-function view protocol.
// Keeping these fields out of the base prevents CSR assumptions and GPU
// allocation details from leaking into every neighbor-list implementation.
class NeighborListBase {
public:
  virtual ~NeighborListBase() = default;

  NeighborListBase(const NeighborListBase&) = delete;
  NeighborListBase& operator=(const NeighborListBase&) = delete;
  NeighborListBase(NeighborListBase&&) noexcept = default;
  NeighborListBase& operator=(NeighborListBase&&) noexcept = default;

  [[nodiscard]] virtual NeighborListLayout layout() const noexcept = 0;

  [[nodiscard]] std::size_t atom_count() const noexcept {
    return atom_count_;
  }

  // Number of real directed edges. Padding is never included here.
  [[nodiscard]] NeighborOffset edge_count() const noexcept {
    return edge_count_;
  }

  // Number of addressable neighbor slots in the concrete layout. This equals
  // edge_count() for CSR. For SlicedELL32 it will include per-slice padding.
  [[nodiscard]] NeighborOffset storage_slots() const noexcept {
    return storage_slots_;
  }

  [[nodiscard]] NeighborOffset padding_slots() const noexcept {
    return storage_slots_ - edge_count_;
  }

  // cutoff is the physical maximum cutoff. skin is extra search distance used
  // by a reusable candidate list. The initial CPU reference uses skin == 0 by
  // default, but keeping both values here avoids changing the owner interface
  // when displacement-based rebuilding is added later.
  [[nodiscard]] Real cutoff() const noexcept {
    return cutoff_;
  }

  [[nodiscard]] Real skin() const noexcept {
    return skin_;
  }

  [[nodiscard]] Real build_radius() const noexcept {
    return cutoff_ + skin_;
  }

protected:
  NeighborListBase(
      std::size_t atom_count,
      Real cutoff,
      Real skin);

  void set_build_result(
      NeighborOffset edge_count,
      NeighborOffset storage_slots);

private:
  std::size_t atom_count_ = 0;
  NeighborOffset edge_count_ = 0;
  NeighborOffset storage_slots_ = 0;
  Real cutoff_ = 0;
  Real skin_ = 0;
};

}  // namespace newmd
