// CPU unit tests for the M1 spatial slab ownership logic
// (include/dmgmd/spatial_ownership.hpp). Everything here is pure host code:
// no MPI, no CUDA. The cases mirror the checklist in the M1 plan: slab
// boundary rules, periodic end-to-head migration, multi-slab crossings, empty
// slabs, axis tie-breaking, and malformed global_id rejection.
#include "dmgmd/mpi_runtime.hpp"

#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void require(bool condition, const char* message)
{
  if (!condition) throw std::runtime_error(message);
}

std::vector<std::uint64_t> identity_ids(std::size_t count)
{
  std::vector<std::uint64_t> ids(count);
  for (std::size_t index = 0; index < count; ++index) ids[index] = index;
  return ids;
}

// Shuffled but valid permutation, so tests never depend on gid == subscript.
std::vector<std::uint64_t> rotated_ids(std::size_t count, std::size_t offset)
{
  std::vector<std::uint64_t> ids(count);
  for (std::size_t index = 0; index < count; ++index) {
    ids[index] = (index + offset) % count;
  }
  return ids;
}

void check_slab_basics()
{
  require(dmgmd::slab_owner_of_fractional(0.0, 4) == 0, "s=0 must belong to slab 0");
  require(dmgmd::slab_owner_of_fractional(1.0, 4) == 3, "exact s=1 must belong to the last slab");
  // Interior boundaries belong to the right slab.
  require(dmgmd::slab_owner_of_fractional(0.25, 4) == 1, "boundary 1/4 belongs to slab 1");
  require(dmgmd::slab_owner_of_fractional(0.5, 4) == 2, "boundary 1/2 belongs to slab 2");
  require(dmgmd::slab_owner_of_fractional(0.75, 4) == 3, "boundary 3/4 belongs to slab 3");
  require(dmgmd::slab_owner_of_fractional(0.1, 4) == 0, "s below the first boundary is slab 0");
  require(dmgmd::slab_owner_of_fractional(0.249999, 4) == 0, "just left of a boundary stays left");
  require(dmgmd::slab_owner_of_fractional(0.250001, 4) == 1, "just right of a boundary is right");
  require(dmgmd::slab_owner_of_fractional(0.999999, 4) == 3, "just below s=1 is the last slab");
  // P = 1 degenerates to rank 0 for any valid coordinate.
  require(dmgmd::slab_owner_of_fractional(0.0, 1) == 0, "P=1 owns everything");
  require(dmgmd::slab_owner_of_fractional(0.7, 1) == 0, "P=1 owns everything");
  require(dmgmd::slab_owner_of_fractional(1.0, 1) == 0, "P=1 owns everything");
}

void check_slab_periodic_wrap()
{
  // Out-of-range coordinates follow wrap_positions' single <0 / >1
  // adjustment, so a coordinate wrapped past the end lands in slab 0 and one
  // wrapped below the origin lands in the last slab.
  require(dmgmd::slab_owner_of_fractional(1.05, 4) == 0, "periodic wrap past the end goes to slab 0");
  require(dmgmd::slab_owner_of_fractional(-0.05, 4) == 3, "periodic wrap below 0 goes to the last slab");
  require(dmgmd::slab_owner_of_fractional(1.5, 4) == 2, "one full adjustment of 1.5 gives 0.5");
  require(dmgmd::slab_owner_of_fractional(-0.75, 2) == 0, "-0.75 wraps to 0.25");
  // More than one box length away cannot be normalized and must be rejected.
  bool rejected = false;
  try {
    static_cast<void>(dmgmd::slab_owner_of_fractional(2.5, 4));
  } catch (const std::runtime_error&) {
    rejected = true;
  }
  require(rejected, "beyond-one-box displacement must be rejected");
  rejected = false;
  try {
    static_cast<void>(dmgmd::slab_owner_of_fractional(-1.5, 4));
  } catch (const std::runtime_error&) {
    rejected = true;
  }
  require(rejected, "beyond-one-box negative displacement must be rejected");
  rejected = false;
  try {
    static_cast<void>(dmgmd::slab_owner_of_fractional(
        std::numeric_limits<double>::quiet_NaN(), 4));
  } catch (const std::runtime_error&) {
    rejected = true;
  }
  require(rejected, "non-finite fractional coordinate must be rejected");
}

dmgmd::SpatialOwnership ownership_from_positions(
    const std::vector<double>& x_fractional,
    const std::vector<std::uint64_t>& global_id,
    int rank,
    int world_size)
{
  std::vector<int> owners(x_fractional.size());
  for (std::size_t slot = 0; slot < x_fractional.size(); ++slot) {
    owners[slot] = dmgmd::slab_owner_of_fractional(x_fractional[slot], world_size);
  }
  return dmgmd::SpatialOwnership(owners, global_id, rank, world_size);
}

void check_ownership_partition()
{
  const std::size_t count = 9;
  const auto ids = rotated_ids(count, 4);  // non-identity permutation
  // Deliberately non-monotonic slots: slot 0 in slab 3, slot 8 in slab 0...
  const std::vector<double> fractional{
      0.9, 0.1, 0.35, 0.6, 0.85, 0.05, 0.3, 0.55, 0.8};
  for (int world_size : {1, 2, 3, 4}) {
    for (int rank = 0; rank < world_size; ++rank) {
      const auto ownership = ownership_from_positions(fractional, ids, rank, world_size);
      // Every slot has exactly one owner; masks are derived from the map.
      const auto& owners = ownership.owner_by_slot();
      for (std::size_t slot = 0; slot < count; ++slot) {
        require(owners[slot] >= 0 && owners[slot] < world_size, "owner is a valid rank");
        require(ownership.owned_mask()[slot] == (owners[slot] == rank ? 1 : 0),
                "owned mask must be derived from the owner map");
      }
      // owned_indices: global_id ascending, in-range, unique.
      const auto& indices = ownership.owned_indices();
      require(indices.size() == ownership.owned_counts_by_rank()[static_cast<std::size_t>(rank)],
              "owned count matches the per-rank table");
      std::uint64_t previous_id = 0;
      bool first = true;
      std::vector<char> seen(count, 0);
      for (std::size_t slot : indices) {
        require(slot < count, "owned slot is in range");
        require(seen[slot] == 0, "owned indices must be unique");
        seen[slot] = 1;
        const std::uint64_t id = ids[slot];
        if (!first) require(previous_id < id, "owned indices must be sorted by global_id");
        previous_id = id;
        first = false;
      }
      for (std::size_t slot = 0; slot < count; ++slot) {
        require(seen[slot] == ownership.owned_mask()[slot], "mask and index list agree");
      }
      // slot_of_global_id is the inverse of global_id.
      for (std::size_t slot = 0; slot < count; ++slot) {
        require(ownership.slot_of_global_id()[static_cast<std::size_t>(ids[slot])] == slot,
                "slot_of_global_id must invert global_id");
      }
      // owned_counts_by_rank() is the GLOBAL table: it must be a complete
      // partition and identical from every rank's construction.
      std::size_t sum = 0;
      for (std::size_t owned : ownership.owned_counts_by_rank()) sum += owned;
      require(sum == count, "sum of owned counts equals the global count");
    }
    // The per-rank table from any single rank is the global table.
    const auto probe = ownership_from_positions(fractional, ids, 0, world_size);
    std::size_t sum = 0;
    for (std::size_t owned : probe.owned_counts_by_rank()) sum += owned;
    require(sum == count, "per-rank counts cover the global count exactly once");
  }
}

void check_migration_transitions()
{
  // P=4 slabs: [0,0.25) [0.25,0.5) [0.5,0.75) [0.75,1].
  const std::size_t count = 4;
  const auto ids = identity_ids(count);

  // One atom crosses exactly one interior boundary (slab 1 -> slab 2).
  {
    auto before = ownership_from_positions({0.1, 0.4, 0.6, 0.9}, ids, 0, 4);
    auto after = ownership_from_positions({0.1, 0.51, 0.6, 0.9}, ids, 0, 4);
    require(before.owner_of_slot(1) == 1 && after.owner_of_slot(1) == 2,
            "interior crossing moves slab 1 -> 2");
    require(!before.same_partition(after), "the partition must change");
    require(before.map_hash() != after.map_hash(), "the map hash must change");
  }
  // One atom wraps past the periodic end back to the first slab (3 -> 0).
  {
    auto before = ownership_from_positions({0.1, 0.4, 0.6, 0.99}, ids, 0, 4);
    auto after = ownership_from_positions({0.1, 0.4, 0.6, 0.02}, ids, 0, 4);
    require(before.owner_of_slot(3) == 3 && after.owner_of_slot(3) == 0,
            "periodic end-to-head crossing moves slab 3 -> 0");
  }
  // One atom crosses multiple slabs in a single step (0 -> 3).
  {
    auto before = ownership_from_positions({0.01, 0.3, 0.55, 0.8}, ids, 0, 4);
    auto after = ownership_from_positions({0.9, 0.3, 0.55, 0.8}, ids, 0, 4);
    require(before.owner_of_slot(0) == 0 && after.owner_of_slot(0) == 3,
            "multi-slab crossing targets the slab of the final position");
  }
  // Wrapping semantics: raw 1.02 maps to the same owner as wrapped 0.02.
  {
    auto raw = ownership_from_positions({1.02, 0.3, 0.55, 0.8}, ids, 0, 4);
    auto wrapped = ownership_from_positions({0.02, 0.3, 0.55, 0.8}, ids, 0, 4);
    require(raw.owner_of_slot(0) == 0 && raw.same_partition(wrapped),
            "wrap-compatible owner for out-of-box coordinates");
  }
}

void check_empty_slabs()
{
  // N >= P does not imply every slab has an atom: with all four atoms in
  // [0, 0.5), slabs 2 and 3 own nothing at P=4.
  const auto ids = identity_ids(4);
  const std::vector<double> fractional{0.05, 0.15, 0.35, 0.45};
  for (int rank = 0; rank < 4; ++rank) {
    const auto ownership = ownership_from_positions(fractional, ids, rank, 4);
    const std::size_t expected = rank < 2 ? 2 : 0;
    require(ownership.owned_count() == expected, "empty slabs are legal ownership");
    if (expected == 0) {
      require(ownership.owned_indices().empty(), "empty owned list is empty");
    }
  }
  // A temporary empty list must still be a valid ownership set.
  const auto empty = ownership_from_positions(fractional, ids, 3, 4);
  require(empty.map_hash() != 0, "empty-rank ownership still hashes its map");

  // N < P is valid in M1: indexed collectives carry zero counts for ranks
  // whose slabs contain no atoms instead of requiring balanced non-empty
  // ranges as M0 did.
  const auto sparse_ids = identity_ids(2);
  const std::vector<double> sparse_fractional{0.05, 0.55};
  std::size_t sparse_total = 0;
  std::size_t empty_ranks = 0;
  for (int rank = 0; rank < 4; ++rank) {
    const auto ownership =
        ownership_from_positions(sparse_fractional, sparse_ids, rank, 4);
    sparse_total += ownership.owned_count();
    if (ownership.owned_count() == 0) ++empty_ranks;
  }
  require(sparse_total == sparse_ids.size(), "N<P ownership still covers every atom once");
  require(empty_ranks == 2, "N<P ownership produces the expected empty ranks");
}

void check_axis_selection()
{
  std::array<double, 9> h{};
  // Orthogonal h layout: a_x, b_y, c_z on the diagonal.
  h[0] = 30.0; h[4] = 20.0; h[8] = 20.0;
  require(dmgmd::longest_box_axis(h) == 0, "x wins when strictly longest");
  h[0] = 20.0; h[4] = 30.0; h[8] = 20.0;
  require(dmgmd::longest_box_axis(h) == 1, "y wins when strictly longest");
  h[0] = 20.0; h[4] = 20.0; h[8] = 30.0;
  require(dmgmd::longest_box_axis(h) == 2, "z wins when strictly longest");
  // Tie rules copied from the reference cascade: y wins x/y and y/z ties,
  // x wins x/z ties, and a cube picks y.
  h[0] = 30.0; h[4] = 30.0; h[8] = 20.0;
  require(dmgmd::longest_box_axis(h) == 1, "y wins the x==y tie");
  h[0] = 20.0; h[4] = 30.0; h[8] = 30.0;
  require(dmgmd::longest_box_axis(h) == 1, "y wins the y==z tie");
  h[0] = 30.0; h[4] = 20.0; h[8] = 30.0;
  require(dmgmd::longest_box_axis(h) == 0, "x wins the x==z tie");
  h[0] = 24.0; h[4] = 24.0; h[8] = 24.0;
  require(dmgmd::longest_box_axis(h) == 1, "a cube deterministically picks y");
  // Lattice-vector orientation does not change geometric edge length.
  h[0] = -30.0; h[4] = 20.0; h[8] = -20.0;
  require(dmgmd::longest_box_axis(h) == 0, "negative x orientation keeps x longest");
  h[0] = -20.0; h[4] = -30.0; h[8] = 30.0;
  require(dmgmd::longest_box_axis(h) == 1, "absolute lengths preserve the y/z tie rule");
  // Fractional helper uses the inverse-matrix rows like wrap_positions.
  std::array<double, 9> inverse{};
  inverse[0] = 1.0 / 24.0; inverse[4] = 1.0 / 24.0; inverse[8] = 1.0 / 24.0;
  require(std::fabs(dmgmd::fractional_along_axis(inverse, 0, 12.0, 5.0, 5.0) - 0.5) < 1e-15,
          "fractional_along_axis uses the inverse row");
  require(std::fabs(dmgmd::fractional_along_axis(inverse, 2, 12.0, 5.0, 6.0) - 0.25) < 1e-15,
          "fractional_along_axis uses the inverse row");
}

void check_indexed_plan_validation()
{
  dmgmd::IndexedOwnershipPlan plan;
  plan.atom_counts = {2, 0, 2, 0};
  plan.atom_displacements = {0, 2, 2, 4};
  plan.host_scatter_slots = {0, 2, 1, 3};
  plan.global_count = 4;
  plan.owned_count = 2;
  dmgmd::validate_indexed_ownership_plan_host(plan, 0, 4, 4);

  const auto rejected = [](const dmgmd::IndexedOwnershipPlan& candidate,
                           int rank = 0) {
    try {
      dmgmd::validate_indexed_ownership_plan_host(candidate, rank, 4, 4);
      return false;
    } catch (const std::invalid_argument&) {
      return true;
    }
  };

  auto malformed = plan;
  malformed.owned_count = 1;
  require(rejected(malformed), "local send count mismatch must be rejected");

  malformed = plan;
  malformed.atom_displacements[2] = 3;
  require(rejected(malformed), "non-prefix displacement must be rejected");

  malformed = plan;
  malformed.host_scatter_slots = {0, 2, 2, 3};
  require(rejected(malformed), "duplicate scatter slots must be rejected");

  malformed = plan;
  malformed.host_scatter_slots = {0, 2, 1, 4};
  require(rejected(malformed), "out-of-range scatter slots must be rejected");

  malformed = plan;
  malformed.atom_counts = {3, 0, 2, 0};
  require(rejected(malformed), "counts exceeding global_count must be rejected");

  malformed = plan;
  require(rejected(malformed, 4), "rank outside world must be rejected");
}

void check_trivial_and_rejection()
{
  const auto ids = rotated_ids(6, 2);
  const auto ownership = dmgmd::SpatialOwnership::trivial(6, ids, 1);
  require(ownership.owned_count() == 6, "trivial map owns everything");
  for (int owner : ownership.owner_by_slot()) {
    require(owner == 0, "trivial map assigns rank 0");
  }

  bool rejected = false;
  try {
    // Duplicate global_id (slot 0 and slot 2 both claim id 1).
    dmgmd::SpatialOwnership bad({0, 0, 0}, {1, 0, 1}, 0, 1);
    static_cast<void>(bad);
  } catch (const std::runtime_error&) {
    rejected = true;
  }
  require(rejected, "duplicate global_id must be rejected");

  rejected = false;
  try {
    // Out-of-range global_id (id == 3 with only 3 slots).
    dmgmd::SpatialOwnership bad({0, 0, 0}, {0, 1, 3}, 0, 1);
    static_cast<void>(bad);
  } catch (const std::runtime_error&) {
    rejected = true;
  }
  require(rejected, "out-of-range global_id must be rejected");

  rejected = false;
  try {
    // Incomplete global_id coverage (id 2 missing, id 0 duplicated).
    dmgmd::SpatialOwnership bad({0, 0, 0}, {0, 1, 0}, 0, 1);
    static_cast<void>(bad);
  } catch (const std::runtime_error&) {
    rejected = true;
  }
  require(rejected, "non-permutation global_id must be rejected");

  rejected = false;
  try {
    dmgmd::SpatialOwnership bad({0, 0, 5}, {0, 1, 2}, 0, 2);
    static_cast<void>(bad);
  } catch (const std::logic_error&) {
    rejected = true;
  }
  require(rejected, "owner outside the world must be rejected");

  rejected = false;
  try {
    // global_id list size mismatch.
    dmgmd::SpatialOwnership bad({0, 0, 0}, {0, 1}, 0, 1);
    static_cast<void>(bad);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "global_id size mismatch must be rejected");

  rejected = false;
  try {
    dmgmd::SpatialOwnership bad({0, 0}, identity_ids(2), 3, 2);
    static_cast<void>(bad);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "invalid rank must be rejected");

  rejected = false;
  try {
    static_cast<void>(dmgmd::slab_owner_of_fractional(0.5, 0));
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "zero world size must be rejected");
}

}  // namespace

int main()
{
  try {
    check_slab_basics();
    check_slab_periodic_wrap();
    check_ownership_partition();
    check_migration_transitions();
    check_empty_slabs();
    check_axis_selection();
    check_indexed_plan_validation();
    check_trivial_and_rejection();
    return EXIT_SUCCESS;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
