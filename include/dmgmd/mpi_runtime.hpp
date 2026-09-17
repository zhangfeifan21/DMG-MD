#pragma once

#include "dmgmd/spatial_ownership.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace dmgmd {

enum class CommunicationBackend {
  host_staged,
  cuda_aware,
};

// Keep this public boundary synchronized with docs/standards/replicated-mpi.md: callers
// supply replicated input buffers plus an M1 indexed ownership plan, while
// MpiRuntime owns backend selection, staging, collective calls, and byte
// accounting.
// Collective-buffer volume, not an estimate of physical network traffic.
// Open MPI/UCX is free to select a tree/ring/other algorithm, so physical link
// bytes are deliberately not claimed by this counter.
struct CommunicationVolume {
  std::uint64_t collective_calls = 0;
  std::uint64_t mpi_input_bytes_global = 0;
  std::uint64_t mpi_output_bytes_global = 0;
  std::uint64_t device_to_host_bytes_global = 0;
  std::uint64_t host_to_device_bytes_global = 0;
  std::uint64_t output_download_bytes = 0;
};

// Communication plan for the M1 indexed collectives. M1 spatial ownership is
// a non-contiguous subset of the replicated slots, so an Allgatherv/Gatherv
// can no longer be described by a contiguous OwnedRange (M0). The plan is
// rebuilt by the runtime whenever the ownership epoch changes and reused
// verbatim while it does not; MpiRuntime itself is stateless with respect to
// the plan.
//
// Layout contract: rank r contributes its owned atoms in the plan order
// (owned indices sorted by global_id); the concatenation over ranks in rank
// order is therefore identical on every rank, and scatter_slots maps each
// gathered atom position back to its replicated slot.
struct IndexedOwnershipPlan {
  // Device mirrors (owned by the caller, must outlive every call that uses
  // this plan):
  const int* device_owned_indices = nullptr;  // this rank's owned slots
  const int* device_scatter_slots = nullptr;  // gathered atom index -> slot
  // Host-side plan (identical on every rank):
  std::vector<int> atom_counts;        // owned atoms per source rank
  std::vector<int> atom_displacements;  // prefix sums, in atoms
  std::vector<int> host_scatter_slots;  // gathered atom index -> slot
  std::size_t global_count = 0;
  std::size_t owned_count = 0;
  std::uint64_t epoch = 0;
};

// Pure host-side validation for the count/displacement/scatter portion of an
// indexed ownership plan. Keeping this independent of MPI/CUDA makes the
// collective layout contract directly CPU-testable. Device pointer presence
// is checked by MpiRuntime at the call boundary.
inline void validate_indexed_ownership_plan_host(
    const IndexedOwnershipPlan& plan,
    int world_rank,
    int world_size,
    std::size_t stride)
{
  if (world_size <= 0 || world_rank < 0 || world_rank >= world_size) {
    throw std::invalid_argument("invalid indexed ownership rank or world size");
  }
  if (stride == 0 || plan.global_count == 0 ||
      plan.global_count > stride || plan.owned_count > plan.global_count) {
    throw std::invalid_argument("indexed ownership exceeds the replicated shape");
  }
  if (plan.atom_counts.size() != static_cast<std::size_t>(world_size) ||
      plan.atom_displacements.size() != static_cast<std::size_t>(world_size) ||
      plan.host_scatter_slots.size() != plan.global_count) {
    throw std::invalid_argument("indexed ownership plan is not world-sized");
  }

  std::size_t expected_displacement = 0;
  for (int source = 0; source < world_size; ++source) {
    const int count = plan.atom_counts[static_cast<std::size_t>(source)];
    const int displacement =
        plan.atom_displacements[static_cast<std::size_t>(source)];
    if (count < 0 || displacement < 0 ||
        static_cast<std::size_t>(displacement) != expected_displacement) {
      throw std::invalid_argument(
          "indexed ownership counts/displacements are not a canonical prefix sum");
    }
    expected_displacement += static_cast<std::size_t>(count);
    if (expected_displacement > plan.global_count) {
      throw std::invalid_argument("indexed ownership counts exceed the global count");
    }
  }
  if (expected_displacement != plan.global_count) {
    throw std::invalid_argument("indexed ownership counts do not cover the global count");
  }
  if (plan.owned_count != static_cast<std::size_t>(
                              plan.atom_counts[static_cast<std::size_t>(world_rank)])) {
    throw std::invalid_argument("indexed ownership send count disagrees with this rank");
  }

  std::vector<char> seen(plan.global_count, 0);
  for (int slot : plan.host_scatter_slots) {
    if (slot < 0 || static_cast<std::size_t>(slot) >= plan.global_count) {
      throw std::invalid_argument("indexed scatter slot is outside the global slot range");
    }
    if (seen[static_cast<std::size_t>(slot)] != 0) {
      throw std::invalid_argument("indexed scatter slots are not a unique permutation");
    }
    seen[static_cast<std::size_t>(slot)] = 1;
  }
}

class MpiRuntime {
 public:
  MpiRuntime(int& argc, char**& argv);
  ~MpiRuntime();

  MpiRuntime(const MpiRuntime&) = delete;
  MpiRuntime& operator=(const MpiRuntime&) = delete;

  [[nodiscard]] int world_rank() const noexcept;
  [[nodiscard]] int world_size() const noexcept;
  [[nodiscard]] int local_rank() const noexcept;
  [[nodiscard]] int local_size() const noexcept;
  [[nodiscard]] int cuda_device() const noexcept;
  [[nodiscard]] bool is_root() const noexcept;
  [[nodiscard]] CommunicationBackend backend() const noexcept;
  [[nodiscard]] const char* backend_name() const noexcept;
  [[nodiscard]] const std::string& hostname() const noexcept;

  // Kept separate from MPI initialization so run/model syntax errors retain
  // the single-rank fail-fast behavior and do not require a working GPU.
  void initialize_device();
  void barrier() const;
  std::string broadcast_string(std::string value, int root = 0) const;
  void broadcast_doubles(double* values, std::size_t count, int root = 0) const;
  void assert_same_fingerprint(std::uint64_t fingerprint, const char* name) const;

  // One-time startup proof: first require every rank's complete owner map to
  // have the same hash, then prove that the per-rank masks form a complete,
  // non-overlapping partition through an explicit Allreduce. The hash gate is
  // fixed-size and runs before any indexed collective can consume variable
  // counts. partition_axis is -1 for the P=1 degenerate map.
  void verify_and_log_center_partition(
      const SpatialOwnership& ownership,
      int partition_axis) const;

  // Per-step cross-rank check that every rank derived the same ownership map
  // before any count-dependent indexed collective runs. locally_valid=false
  // routes a local map-construction failure through the same fixed-size
  // handshake so all ranks fail together. A no-op at world_size 1.
  void assert_same_ownership_map(
      std::uint64_t map_hash,
      bool locally_valid,
      CommunicationVolume& volume) const;

  // Startup/control-plane form of the same fixed-size handshake. Its bytes
  // deliberately do not enter any per-step CommunicationVolume record.
  void assert_same_ownership_map(
      std::uint64_t map_hash,
      bool locally_valid) const;

  // Control-plane collectives for the rank I/O isolation handshake
  // (docs/standards/replicated-mpi.md). They are one-time setup/teardown
  // coordination, so they are deliberately outside CommunicationVolume.
  //
  // allreduce_all_passed returns true only when every rank passed true; the
  // isolation protocol requires this check before any rank may act on a local
  // filesystem failure, so all ranks take the same branch and no rank is left
  // waiting inside a later collective.
  [[nodiscard]] bool allreduce_all_passed(bool local_passed, const char* operation) const;

  // Gathers one diagnostic string per rank to rank 0 (empty means "nothing to
  // report"); non-root ranks always get an empty vector. Valid only inside the
  // cooperative isolation handshakes where every rank is known to participate;
  // exception paths must use report_error instead (see its comment for why).
  [[nodiscard]] std::vector<std::string> gather_strings(const std::string& value) const;

  // M1 indexed Allgatherv: each rank packs the slots in
  // plan.device_owned_indices from a replicated SoA into AoS order, exchanges
  // per-rank variable counts (including zero), and scatters the gathered
  // stream back onto the replicated SoA through device_scatter_slots.
  // HostStaged and CudaAware share the pack/unpack layout and ownership
  // semantics; only the transport differs.
  void allgather_indexed_device_soa(
      double* device_values,
      int components,
      std::size_t stride,
      const IndexedOwnershipPlan& plan,
      CommunicationVolume& volume) const;

  // M1 indexed Gatherv to rank 0: same packing, root-only receive, and a
  // host-side scatter through host_scatter_slots so the returned N-sized
  // global SoA is in replicated slot order, never in rank concatenation
  // order.
  std::vector<double> gather_indexed_device_soa_to_root(
      const double* device_values,
      int components,
      std::size_t stride,
      const IndexedOwnershipPlan& plan,
      CommunicationVolume& volume) const;

  void allreduce_sum_device(
      double* device_values,
      int count,
      CommunicationVolume& volume);

  void broadcast_device(
      double* device_values,
      std::size_t count,
      CommunicationVolume& volume,
      int root = 0);

  double allreduce_max_host(double value, CommunicationVolume& volume) const;
  void log_step_communication(std::uint64_t step, const CommunicationVolume& volume) const;
  void log_timing(
      const char* phase,
      std::uint64_t sequence,
      std::uint64_t steps,
      std::size_t atoms,
      double elapsed_seconds) const;
  void report_error(const char* category, const std::string& message) const;

  [[noreturn]] void abort(int error_code) const;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace dmgmd
