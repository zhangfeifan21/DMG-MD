#pragma once

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace dmgmd {

// M1 spatial slab ownership (docs/standards/replicated-mpi.md, "M1 空间所有权").
// One MPI rank owns one equal-width fractional half-open slab along one box
// axis. The box itself stays replicated: every rank still holds all N slots,
// and this type only records *who* is authoritative for integration, thermo
// and output of each replicated slot. It replaces the M0 contiguous OwnedRange
// representation; AtomCounts::owned_count deliberately stays N (the replicated
// SoA stride) and never becomes this rank's spatial owned count.

// Owner rank of a fractional coordinate for P equal-width slabs.
//
// Slab rules (unit-tested in tests/spatial_ownership_tests.cpp):
//   * slabs are half-open [k/P, (k+1)/P), so an interior boundary k/P
//     belongs to the right slab k, and s=0 belongs to slab 0;
//   * the exact upper edge s=1 stays with the last slab, matching the
//     wrap_positions kernel's `<0 / >1` convention (it never wraps s==1);
//   * out-of-range s first gets the same single `<0 -> +1 / >1 -> -1`
//     adjustment that wrap_positions applies, so this function accepts both
//     wrapped and wrap-compatible unwrapped fractional coordinates.
// Non-finite s, or s still outside [0, 1] after one adjustment (a per-step
// displacement of at least one full box length), is rejected.
inline int slab_owner_of_fractional(double fractional, int world_size)
{
  if (world_size <= 0) {
    throw std::invalid_argument("slab owner requires a positive world size");
  }
  double s = fractional;
  if (s < 0.0) {
    s += 1.0;
  } else if (s > 1.0) {
    s -= 1.0;
  }
  if (!(s >= 0.0 && s <= 1.0)) {
    throw std::runtime_error(
        "fractional coordinate " + std::to_string(fractional) +
        " is outside the periodic range that wrap_positions can normalize");
  }
  if (world_size == 1) return 0;
  int slab = static_cast<int>(s * world_size);
  if (slab >= world_size) slab = world_size - 1;  // exact s == 1
  if (slab < 0) slab = 0;                        // guarded above; keeps static analyzers calm
  return slab;
}

// Fractional coordinate along one axis of an orthogonal box, using the exact
// inverse-matrix row product that the wrap_positions kernel uses, so both
// compute identical fractional values (including any tiny off-diagonal
// residues that a merely "orthogonal" box may still carry). `inverse_box` is
// the 3x3 inverse matrix in row-major order (Box::cpu_h[9..17]).
inline double fractional_along_axis(
    const std::array<double, 9>& inverse_box,
    int axis,
    double x,
    double y,
    double z)
{
  if (axis < 0 || axis > 2) {
    throw std::invalid_argument("partition axis must be 0, 1 or 2");
  }
  const std::size_t row = 3 * static_cast<std::size_t>(axis);
  return inverse_box[row] * x + inverse_box[row + 1] * y + inverse_box[row + 2] * z;
}

// Partition axis: the longest box edge, with the deterministic tie rule of
// the pinned reference partitioner (gpumd-reference/src/force/
// nep_multigpu.cu:1438-1446): a cascade where direction y wins ties against
// x and z, and x wins ties against z (all-equal cubes pick y). The reference
// compares rc/2 bin counts; M1 has no cell-aligned bins yet (that guard is
// deliberately deferred to M2a), so the edge lengths are compared directly.
inline int longest_box_axis(const std::array<double, 9>& h)
{
  const std::array<double, 3> length{
      std::abs(h[0]), std::abs(h[4]), std::abs(h[8])};
  int axis = 2;
  if (length[0] >= length[1] && length[0] >= length[2]) axis = 0;
  if (length[1] >= length[0] && length[1] >= length[2]) axis = 1;
  return axis;
}

// Authoritative ownership of the N replicated slots. owner_by_slot is the
// single source of truth; owned_indices, owned_mask and the global_id/slot
// permutation are all derived in the constructor so the three views can
// never drift apart.
class SpatialOwnership {
 public:
  // world_size == 1 degenerate map: every slot owned by rank 0. This is also
  // the only map used at P=1 so that path stays byte-identical to M0.
  static SpatialOwnership trivial(
      std::size_t global_count,
      const std::vector<std::uint64_t>& global_id,
      int world_size)
  {
    std::vector<int> owners(global_count, 0);
    return SpatialOwnership(std::move(owners), global_id, 0, world_size);
  }

  // Builds the ownership set from a full owner map. Validates:
  //   * owner_of_slot[slot] in [0, world_size) for every slot;
  //   * global_id is a unique permutation of [0, global_count) (built into
  //     slot_of_global_id; the mapping is explicit rather than assumed, so a
  //     future non-permutation ID scheme fails here instead of silently
  //     equating IDs with array subscripts);
  //   * owned_indices is duplicate-free, in-slot-range and sorted by
  //     global_id ascending (rank-independent derivation, never the
  //     accidental order of a spatial scan).
  SpatialOwnership(
      std::vector<int> owner_by_slot,
      const std::vector<std::uint64_t>& global_id,
      int rank,
      int world_size)
      : rank_(rank),
        world_size_(world_size),
        global_count_(owner_by_slot.size()),
        global_id_(global_id),
        owner_by_slot_(std::move(owner_by_slot)),
        owned_mask_(global_count_, 0)
  {
    if (world_size <= 0 || rank < 0 || rank >= world_size) {
      throw std::invalid_argument("invalid MPI rank or world size");
    }
    if (global_id_.size() != global_count_) {
      throw std::invalid_argument("global_id does not cover the replicated slots");
    }
    slot_of_global_id_.assign(global_count_, 0);
    std::vector<std::size_t> per_rank_counts(static_cast<std::size_t>(world_size_), 0);
    for (std::size_t slot = 0; slot < global_count_; ++slot) {
      const int owner = owner_by_slot_[slot];
      if (owner < 0 || owner >= world_size_) {
        throw std::logic_error("spatial owner rank is outside the world");
      }
      ++per_rank_counts[static_cast<std::size_t>(owner)];
      if (owner == rank_) owned_mask_[slot] = 1;
    }
    std::size_t owned_total = 0;
    owned_counts_by_rank_.assign(per_rank_counts.begin(), per_rank_counts.end());
    for (std::size_t count : per_rank_counts) owned_total += count;
    if (owned_total != global_count_) {
      // Unreachable while owner_by_slot_ is a total function; kept as an
      // internal invariant for future constructor variants.
      throw std::logic_error("spatial owner map is not a partition of the slots");
    }

    // global_id permutation: slot_of_global_id[id] == its unique slot.
    std::vector<int> seen(global_count_, 0);
    for (std::size_t slot = 0; slot < global_count_; ++slot) {
      const std::uint64_t id = global_id_[slot];
      if (id >= global_count_) {
        throw std::runtime_error("global_id exceeds the replicated slot range");
      }
      const std::size_t index = static_cast<std::size_t>(id);
      if (seen[index] != 0) {
        throw std::runtime_error("global_id is not a unique permutation of slots");
      }
      seen[index] = 1;
      slot_of_global_id_[index] = slot;
    }
    for (int flag : seen) {
      if (flag == 0) {
        throw std::runtime_error("global_id does not cover every slot");
      }
    }

    // Owned slots of every rank, visited in global_id order so each list is
    // ascending in global_id by construction.
    owned_indices_.reserve(per_rank_counts[static_cast<std::size_t>(rank_)]);
    for (std::uint64_t id = 0; id < global_count_; ++id) {
      const std::size_t slot = slot_of_global_id_[static_cast<std::size_t>(id)];
      if (owner_by_slot_[slot] == rank_) {
        owned_indices_.push_back(slot);
      }
    }
  }

  [[nodiscard]] int rank() const noexcept { return rank_; }
  [[nodiscard]] int world_size() const noexcept { return world_size_; }
  [[nodiscard]] std::size_t global_count() const noexcept { return global_count_; }
  [[nodiscard]] std::size_t owned_count() const noexcept { return owned_indices_.size(); }
  [[nodiscard]] const std::vector<int>& owner_by_slot() const noexcept { return owner_by_slot_; }
  [[nodiscard]] const std::vector<std::size_t>& owned_indices() const noexcept
  {
    return owned_indices_;
  }
  [[nodiscard]] const std::vector<char>& owned_mask() const noexcept { return owned_mask_; }
  [[nodiscard]] const std::vector<std::uint64_t>& global_id() const noexcept
  {
    return global_id_;
  }
  // slot_of_global_id()[id] is the replicated slot of global atom `id`.
  [[nodiscard]] const std::vector<std::size_t>& slot_of_global_id() const noexcept
  {
    return slot_of_global_id_;
  }
  [[nodiscard]] const std::vector<std::size_t>& owned_counts_by_rank() const noexcept
  {
    return owned_counts_by_rank_;
  }

  [[nodiscard]] int owner_of_slot(std::size_t slot) const
  {
    if (slot >= global_count_) {
      throw std::out_of_range("slot is outside the replicated storage");
    }
    return owner_by_slot_[slot];
  }

  // Two ownership sets describe the same partition (rank bookkeeping aside).
  [[nodiscard]] bool same_partition(const SpatialOwnership& other) const
  {
    return global_count_ == other.global_count_ &&
           owner_by_slot_ == other.owner_by_slot_;
  }

  // 63-bit FNV-1a over the owner map and the global_id permutation. Used for
  // the per-step cross-rank consistency Allreduce; the top bit is cleared so
  // the sentinel used by that collective can never collide with a valid hash.
  [[nodiscard]] std::uint64_t map_hash() const noexcept
  {
    std::uint64_t hash = UINT64_C(1469598103934665603);
    const auto mix = [&hash](std::uint64_t value) {
      for (int byte = 0; byte < 8; ++byte) {
        hash ^= (value >> (8 * byte)) & UINT64_C(0xFF);
        hash *= UINT64_C(1099511628211);
      }
    };
    for (int owner : owner_by_slot_) mix(static_cast<std::uint64_t>(owner));
    for (std::uint64_t id : global_id_) mix(id);
    return hash & UINT64_C(0x7FFFFFFFFFFFFFFF);
  }

 private:
  int rank_ = 0;
  int world_size_ = 1;
  std::size_t global_count_ = 0;
  std::vector<std::uint64_t> global_id_;        // static identity, by slot
  std::vector<int> owner_by_slot_;              // single source of truth
  std::vector<std::size_t> owned_indices_;      // this rank's slots, global_id order
  std::vector<char> owned_mask_;                // derived: 1 where this rank owns
  std::vector<std::size_t> slot_of_global_id_;  // derived: id -> slot permutation
  std::vector<std::size_t> owned_counts_by_rank_;
};

}  // namespace dmgmd
