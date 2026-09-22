// M2a local-domain runtime (docs/plans/domain-decomposition.md).
//
// Owns the rank-local owned/ghost data plane on top of the pure-CPU layout
// module (include/dmgmd/domain_layout.hpp) and the p2p MpiRuntime extension:
//   * local slot layout [0, owned) | dependency ghosts | coordinate-only
//     ghosts, local_count as the only SoA stride;
//   * conservative two-hop position halo (d_dep / d_coord) with per-step
//     position refresh through cached face plans and membership rebuilds tied
//     to the global neighbor-rebuild OR;
//   * direct migration to the final owner (Alltoall count handshake +
//     Alltoallv), one step may cross any number of slabs including the
//     periodic ends;
//   * the mandated per-step order of docs/plans/domain-decomposition.md
//     section 6 (correct_velocity -> adaptive dt -> VV1 -> wrap -> migration
//     -> halo refresh/rebuild -> global rebuild OR -> domain NEP -> VV2 ->
//     thermo -> thermostat -> output);
//   * local-owned-prefix gathers carrying global IDs, root-side global-order
//     reconstruction, and the shared GPUMD-compatible formatters.
#include "runtime_internal.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <iomanip>
#include <limits>
#include <memory>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace dmgmd {
namespace detail {
namespace {

using gpumd_compat::NEP;
using gpumd_compat::TIME_UNIT_CONVERSION;

// ---------------------------------------------------------------------------
// Device kernels over the contiguous owned prefix [0, owned_count) with the
// local stride. Same arithmetic as the M1 list-driven kernels; empty ranges
// are short-circuited before launch.
// ---------------------------------------------------------------------------

__global__ void velocity_verlet_range(
    bool first_half,
    int owned_count,
    int stride,
    double time_step,
    const double* mass,
    double* position,
    double* velocity,
    const double* force,
    double* epoch_displacement)
{
  const int atom = blockIdx.x * blockDim.x + threadIdx.x;
  if (atom >= owned_count) {
    return;
  }
  const double half = time_step * 0.5;
  const double inverse_mass = 1.0 / mass[atom];
  double vx = velocity[atom] + force[atom] * inverse_mass * half;
  double vy = velocity[stride + atom] + force[stride + atom] * inverse_mass * half;
  double vz = velocity[2 * stride + atom] + force[2 * stride + atom] * inverse_mass * half;
  velocity[atom] = vx;
  velocity[stride + atom] = vy;
  velocity[2 * stride + atom] = vz;
  if (first_half) {
    const double dx = vx * time_step;
    const double dy = vy * time_step;
    const double dz = vz * time_step;
    position[atom] += dx;
    position[stride + atom] += dy;
    position[2 * stride + atom] += dz;
    epoch_displacement[atom] += dx;
    epoch_displacement[stride + atom] += dy;
    epoch_displacement[2 * stride + atom] += dz;
  }
}

__global__ void update_unwrapped_range(
    int owned_count,
    int stride,
    const double* position,
    const double* previous,
    double* unwrapped)
{
  const int atom = blockIdx.x * blockDim.x + threadIdx.x;
  if (atom >= owned_count) {
    return;
  }
  for (int axis = 0; axis < 3; ++axis) {
    const int index = axis * stride + atom;
    unwrapped[index] += position[index] - previous[index];
  }
}

__global__ void find_owned_thermo_sums_range(
    int owned_count,
    int stride,
    const double* mass,
    const double* potential,
    const double* velocity,
    const double* virial,
    double* thermo)
{
  const int tid = threadIdx.x;
  const int quantity = blockIdx.x;
  const int patches = owned_count == 0 ? 0 : (owned_count - 1) / kThermoThreads + 1;
  __shared__ double values[kThermoThreads];
  double sum = 0.0;
  for (int patch = 0; patch < patches; ++patch) {
    const int atom = tid + patch * kThermoThreads;
    if (atom >= owned_count) {
      continue;
    }
    const double vx = velocity[atom];
    const double vy = velocity[stride + atom];
    const double vz = velocity[2 * stride + atom];
    if (quantity == 0) {
      sum += mass[atom] * (vx * vx + vy * vy + vz * vz);
    } else if (quantity == 1) {
      sum += potential[atom];
    } else {
      const int component = quantity - 2;
      double kinetic = 0.0;
      if (component == 0) kinetic = mass[atom] * vx * vx;
      else if (component == 1) kinetic = mass[atom] * vy * vy;
      else if (component == 2) kinetic = mass[atom] * vz * vz;
      else if (component == 3) kinetic = mass[atom] * vx * vy;
      else if (component == 4) kinetic = mass[atom] * vx * vz;
      else kinetic = mass[atom] * vy * vz;
      sum += virial[component * stride + atom] + kinetic;
    }
  }
  values[tid] = sum;
  __syncthreads();
  for (int offset = blockDim.x >> 1; offset > 0; offset >>= 1) {
    if (tid < offset) {
      values[tid] += values[tid + offset];
    }
    __syncthreads();
  }
  if (tid == 0) {
    thermo[quantity] = values[0];
  }
}

__global__ void scale_owned_velocity_range(
    int owned_count,
    int stride,
    double factor,
    double* velocity)
{
  const int atom = blockIdx.x * blockDim.x + threadIdx.x;
  if (atom >= owned_count) {
    return;
  }
  velocity[atom] *= factor;
  velocity[stride + atom] *= factor;
  velocity[2 * stride + atom] *= factor;
}

enum DomainDecisionReason : std::uint32_t {
  kOwnerRoutingNeeded = 1u << 0,
  kContinuityInvalid = 1u << 1,
  kDisplacementLimit = 1u << 2,
};

enum class DomainDecisionState : std::uint8_t {
  undetermined = 0,
  confirmed_reuse = 1,
  must_rebuild = 2,
};

// One pass over the unique manager copies. The continuous displacement was
// accumulated before wrap by velocity_verlet_range, so periodic crossings
// and boundary round trips cannot cancel through MIC. Non-negative finite
// doubles have monotonically ordered bit representations, allowing atomicMax.
__global__ void inspect_domain_cache(
    int owned_count,
    int stride,
    Box box,
    int axis,
    int rank,
    int world_size,
    double time_step,
    const double* position,
    const double* velocity,
    const double* epoch_displacement,
    unsigned long long* maximum_squared_bits,
    unsigned int* reasons)
{
  const int atom = blockIdx.x * blockDim.x + threadIdx.x;
  if (atom >= owned_count) {
    return;
  }
  const double x = position[atom];
  const double y = position[stride + atom];
  const double z = position[2 * stride + atom];
  const int row = 3 * axis;
  double s = box.cpu_h[9 + row] * x + box.cpu_h[10 + row] * y + box.cpu_h[11 + row] * z;
  // position has already passed through the pinned single-wrap kernel. A
  // still-outside fractional value therefore exceeds the supported routing
  // contract and must not be silently normalized a second time.
  const double dx = epoch_displacement[atom];
  const double dy = epoch_displacement[stride + atom];
  const double dz = epoch_displacement[2 * stride + atom];
  const double displacement_squared = dx * dx + dy * dy + dz * dz;
  bool routeable = isfinite(displacement_squared) && (s >= 0.0 && s <= 1.0);
  const double step_dx = velocity[atom] * time_step;
  const double step_dy = velocity[stride + atom] * time_step;
  const double step_dz = velocity[2 * stride + atom] * time_step;
  for (int dimension = 0; dimension < 3; ++dimension) {
    const int inverse_row = 9 + 3 * dimension;
    const double fractional_step =
        box.cpu_h[inverse_row] * step_dx +
        box.cpu_h[inverse_row + 1] * step_dy +
        box.cpu_h[inverse_row + 2] * step_dz;
    routeable = routeable && isfinite(fractional_step) &&
                fabs(fractional_step) <= 1.0;
  }
  if (!routeable) {
    atomicOr(reasons, static_cast<unsigned int>(kContinuityInvalid));
    return;
  }
  atomicMax(maximum_squared_bits, __double_as_longlong(displacement_squared));
  int slab = static_cast<int>(s * world_size);
  if (slab >= world_size) slab = world_size - 1;
  if (slab < 0) slab = 0;
  if (slab != rank) {
    atomicOr(reasons, static_cast<unsigned int>(kOwnerRoutingNeeded));
  }
}

// ---------------------------------------------------------------------------
// Host-side state.
// ---------------------------------------------------------------------------

// Migration wire record (plan section 8): every persistent per-atom field a
// migrated atom needs; force/PE/virial are recomputed and never travel.
// Group labels (group_count int32 values) are appended directly after the
// fixed part. When unwrapped tracking is off, the trailing unwrapped[3] is
// not sent: the wire size is computed from the actual offset.
#pragma pack(push, 8)
struct MigrationWireRecord {
  std::uint64_t global_id;
  std::int32_t type;
  std::int32_t group_count;
  double mass;
  double charge;
  double position[3];
  double velocity[3];
  double unwrapped[3];
};
#pragma pack(pop)
static_assert(sizeof(MigrationWireRecord) == 104, "migration record layout");
constexpr std::size_t kMigrationFixedBytes = 80;  // offsetof(unwrapped)

struct DomainDeviceAtoms {
  GPU_Vector<unsigned long long> global_id;
  GPU_Vector<int> type;
  GPU_Vector<double> mass;
  GPU_Vector<float> charge;
  GPU_Vector<double> position;
  GPU_Vector<double> velocity;
  GPU_Vector<double> force;
  GPU_Vector<double> potential;
  GPU_Vector<double> virial;
  GPU_Vector<double> unwrapped;
  GPU_Vector<double> previous_position;
  GPU_Vector<double> epoch_displacement;
  GPU_Vector<double> thermo;
  GPU_Vector<int> send_slots[2];  // per face: owned slots of the send list
  GPU_Vector<int> recv_slots[2];  // per face: ghost slot per stream index
  GPU_Vector<unsigned long long> decision_max_squared;
  GPU_Vector<unsigned int> decision_reasons;
};

struct DomainState {
  int rank = 0;
  int world_size = 1;
  int axis = 0;
  double axis_length = 0.0;
  double d_dep = 0.0;
  double d_coord = 0.0;
  std::size_t group_method_count = 0;
  bool track_unwrapped = false;
  LocalLayout layout;
  ExchangePlan plan;
  std::uint64_t epoch = 0;
  std::uint64_t mapping_epoch = 0;
  std::uint64_t refreshed_epoch = 0;
  DomainDecisionState decision_state = DomainDecisionState::undetermined;
  bool neighbor_cache_valid = false;
  bool detailed_logging = false;
  bool timing_enabled = false;
  DomainDeviceAtoms device;
  // Workspace bookkeeping: the NEP workspaces are re-sized only when
  // local_count changes; every layout change still forces a Verlet rebuild.
  int workspace_count = -1;
  std::uint64_t migration_steps = 0;
  std::uint64_t rebuild_steps = 0;
  std::uint64_t layout_uploads = 0;
  std::uint64_t workspace_updates = 0;
  std::uint64_t capacity_growth_events = 0;
  std::uint64_t last_layout_gpu_allocations = 0;
  std::uint64_t allocation_origin = 0;
  // Steps executed so far. An unwrapped-tracking enable before the first run
  // keeps the raw input coordinates as the seed (the M1 path seeds unwrapped
  // from its still-unwrapped device copy at that point); a later enable
  // seeds from the current wrapped positions, exactly like M1.
  std::uint64_t executed_steps = 0;
  // Communication volume of the current step, or null outside the step loop
  // (startup-time records then land in the uncounted volume).
  CommunicationVolume* current_volume = nullptr;
  CommunicationVolume uncounted_volume{};

  [[nodiscard]] CommunicationVolume& active_volume() noexcept
  {
    return current_volume ? *current_volume : uncounted_volume;
  }
  [[nodiscard]] std::size_t owned_count() const noexcept { return layout.owned_count(); }
  [[nodiscard]] std::size_t local_count() const noexcept { return layout.local_count(); }
  [[nodiscard]] std::size_t migration_record_bytes() const
  {
    return kMigrationFixedBytes + (track_unwrapped ? 3 * sizeof(double) : 0) +
           group_method_count * sizeof(std::int32_t);
  }
};

enum class TimedStepClass : int { ordinary = 0, rebuild = 1 };

enum TimingField : int {
  kCount, kStepWall, kDecision, kMigration, kMembershipLayout,
  kAllocationUpload, kHaloPackDevice, kHaloTransferWait, kHaloUnpackDevice,
  kCellNeighborDevice, kNepDevice, kIntegrationDevice, kThermoDevice,
  kIntegrationHost, kThermoHost,
  kScientificOutputHost, kMpiWait, kRemainingHost, kTimingFieldCount
};

struct SegmentTiming {
  static constexpr int kHistogramBuckets = 12;
  std::array<std::array<double, kTimingFieldCount>, 2> values{};
  std::array<double, 2> step_min{{std::numeric_limits<double>::infinity(),
                                  std::numeric_limits<double>::infinity()}};
  std::array<double, 2> step_max{{0.0, 0.0}};
  std::array<std::array<double, kHistogramBuckets>, 2> step_histogram{};
  std::uint64_t displacement_rebuilds = 0;
  std::uint64_t routing_rebuilds = 0;
  double step_loop_wall = 0.0;
  double setup_host = 0.0;
  double setup_cell_neighbor_device = 0.0;
  double setup_nep_device = 0.0;
};

[[nodiscard]] int timing_histogram_bucket(double seconds)
{
  // Powers of four from 1 us through roughly four seconds; the final bucket
  // also contains larger values. This is a bounded rank-step distribution,
  // not a claim of an exact percentile estimator.
  double upper = 1.0e-6;
  for (int bucket = 0; bucket < SegmentTiming::kHistogramBuckets - 1; ++bucket) {
    if (seconds < upper) return bucket;
    upper *= 4.0;
  }
  return SegmentTiming::kHistogramBuckets - 1;
}

[[nodiscard]] bool domain_timing_enabled()
{
  const char* value = std::getenv("DMGMD_DOMAIN_TIMING");
  if (value == nullptr || value[0] == '\0' || std::strcmp(value, "0") == 0) {
    return false;
  }
  if (std::strcmp(value, "1") == 0) return true;
  throw std::runtime_error("DMGMD_DOMAIN_TIMING must be 0 or 1");
}

void add_seconds(
    SegmentTiming& timing, TimedStepClass step_class, TimingField field, double seconds)
{
  timing.values[static_cast<int>(step_class)][field] += seconds;
}

[[nodiscard]] double elapsed_since(
    const std::chrono::steady_clock::time_point& start)
{
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

[[nodiscard]] std::chrono::steady_clock::time_point begin_detailed_timing(
    bool enabled)
{
  return enabled ? std::chrono::steady_clock::now()
                 : std::chrono::steady_clock::time_point{};
}

[[nodiscard]] double event_seconds(cudaEvent_t begin, cudaEvent_t end)
{
  float milliseconds = 0.0f;
  check_cuda(cudaEventElapsedTime(&milliseconds, begin, end),
             "read domain timing event");
  return static_cast<double>(milliseconds) * 1.0e-3;
}

void create_opaque_event(void*& handle, const char* operation)
{
  cudaEvent_t event = nullptr;
  check_cuda(cudaEventCreate(&event), operation);
  handle = reinterpret_cast<void*>(event);
}

void destroy_opaque_event(void* handle, const char* operation)
{
  check_cuda(cudaEventDestroy(reinterpret_cast<cudaEvent_t>(handle)), operation);
}

void log_segment_timing(
    const SegmentTiming& timing, std::uint64_t sequence, MpiRuntime& mpi)
{
  constexpr int extra_count = 10 + 2 * SegmentTiming::kHistogramBuckets;
  constexpr int count = 2 * kTimingFieldCount + extra_count;
  std::array<double, count> local{};
  std::array<double, count> sums{};
  std::array<double, count> minima{};
  std::array<double, count> maxima{};
  int cursor = 0;
  for (int step_class = 0; step_class < 2; ++step_class) {
    for (int field = 0; field < kTimingFieldCount; ++field) {
      local[cursor++] = timing.values[step_class][field];
    }
  }
  for (int step_class = 0; step_class < 2; ++step_class) {
    const double minimum = timing.step_min[step_class];
    local[cursor++] = std::isfinite(minimum) ? -minimum :
                      -std::numeric_limits<double>::infinity();
    local[cursor++] = timing.step_max[step_class];
  }
  local[cursor++] = static_cast<double>(timing.displacement_rebuilds);
  local[cursor++] = static_cast<double>(timing.routing_rebuilds);
  local[cursor++] = timing.step_loop_wall;
  local[cursor++] = timing.setup_host;
  local[cursor++] = timing.setup_cell_neighbor_device;
  local[cursor++] = timing.setup_nep_device;
  for (int step_class = 0; step_class < 2; ++step_class) {
    for (double bucket_count : timing.step_histogram[step_class]) {
      local[cursor++] = bucket_count;
    }
  }
  mpi.reduce_sum_min_max_doubles(
      local.data(), sums.data(), minima.data(), maxima.data(), count);
  if (!mpi.is_root()) return;
  const char* names[2] = {"ordinary", "rebuild"};
  const int ranks = mpi.world_size();
  for (int step_class = 0; step_class < 2; ++step_class) {
    const int base = step_class * kTimingFieldCount;
    const double step_count = maxima[base + kCount];
    const auto mean = [&](TimingField field) {
      return sums[base + field] / static_cast<double>(ranks);
    };
    const auto average_per_step = [&](TimingField field) {
      return step_count > 0.0 ? mean(field) / step_count : 0.0;
    };
    const auto stage = [&](TimingField field, const char* name) {
      std::cout << ' ' << name << "_seconds_per_step_rank_mean="
                << average_per_step(field)
                << ' ' << name << "_seconds_rank_min_total="
                << minima[base + field]
                << ' ' << name << "_seconds_rank_max_total="
                << maxima[base + field];
    };
    const int extrema_base = 2 * kTimingFieldCount + step_class * 2;
    std::cout << std::fixed << std::setprecision(9)
              << "DMGMD_DOMAIN_TIMING sequence=" << sequence
              << " class=" << names[step_class]
              << " count=" << static_cast<std::uint64_t>(step_count)
              << " step_seconds_rank_mean=" << average_per_step(kStepWall)
              << " step_seconds_rank_min_total=" << minima[base + kStepWall]
              << " step_seconds_rank_max_total=" << maxima[base + kStepWall]
              << " step_seconds_observed_min="
              << (step_count > 0.0 ? -maxima[extrema_base] : 0.0)
              << " step_seconds_observed_max="
              << (step_count > 0.0 ? maxima[extrema_base + 1] : 0.0);
    stage(kDecision, "decision");
    stage(kMigration, "migration");
    stage(kMembershipLayout, "membership_layout");
    stage(kAllocationUpload, "allocation_upload");
    stage(kHaloPackDevice, "halo_pack_device");
    stage(kHaloTransferWait, "halo_transfer_wait");
    stage(kHaloUnpackDevice, "halo_unpack_device");
    stage(kCellNeighborDevice, "cell_neighbor_device");
    stage(kNepDevice, "nep_device");
    stage(kIntegrationDevice, "integration_device");
    stage(kThermoDevice, "thermo_device");
    stage(kIntegrationHost, "integration_host");
    stage(kThermoHost, "thermo_host");
    stage(kScientificOutputHost, "scientific_output_host");
    stage(kMpiWait, "mpi_wait");
    stage(kRemainingHost, "remaining_host");
    std::cout << '\n';
  }
  const int reason_base = 2 * kTimingFieldCount + 4;
  std::cout << "DMGMD_DOMAIN_REBUILD_REASONS sequence=" << sequence
            << " displacement=" << static_cast<std::uint64_t>(maxima[reason_base])
            << " routing=" << static_cast<std::uint64_t>(maxima[reason_base + 1])
            << '\n';
  std::cout << "DMGMD_DOMAIN_TIMING_SEGMENT sequence=" << sequence
            << " step_loop_seconds_rank_mean="
            << sums[reason_base + 2] / static_cast<double>(ranks)
            << " step_loop_seconds_rank_min=" << minima[reason_base + 2]
            << " step_loop_seconds_rank_max=" << maxima[reason_base + 2]
            << '\n';
  const int setup_base = reason_base + 3;
  std::cout << "DMGMD_DOMAIN_TIMING_SETUP sequence=" << sequence
            << " count=1"
            << " host_seconds_rank_mean="
            << sums[setup_base] / static_cast<double>(ranks)
            << " host_seconds_rank_min=" << minima[setup_base]
            << " host_seconds_rank_max=" << maxima[setup_base]
            << " cell_neighbor_device_seconds_rank_mean="
            << sums[setup_base + 1] / static_cast<double>(ranks)
            << " cell_neighbor_device_seconds_rank_min=" << minima[setup_base + 1]
            << " cell_neighbor_device_seconds_rank_max=" << maxima[setup_base + 1]
            << " nep_device_seconds_rank_mean="
            << sums[setup_base + 2] / static_cast<double>(ranks)
            << " nep_device_seconds_rank_min=" << minima[setup_base + 2]
            << " nep_device_seconds_rank_max=" << maxima[setup_base + 2]
            << '\n';
  const int histogram_base = setup_base + 3;
  for (int step_class = 0; step_class < 2; ++step_class) {
    std::cout << "DMGMD_DOMAIN_TIMING_HISTOGRAM sequence=" << sequence
              << " class=" << names[step_class]
              << " unit=seconds bounds=";
    double upper = 1.0e-6;
    for (int bucket = 0; bucket < SegmentTiming::kHistogramBuckets; ++bucket) {
      if (bucket != 0) std::cout << ',';
      if (bucket == SegmentTiming::kHistogramBuckets - 1) {
        std::cout << "inf";
      } else {
        std::cout << upper;
        upper *= 4.0;
      }
    }
    std::cout << " counts=";
    const int offset = histogram_base +
                       step_class * SegmentTiming::kHistogramBuckets;
    for (int bucket = 0; bucket < SegmentTiming::kHistogramBuckets; ++bucket) {
      if (bucket != 0) std::cout << ',';
      std::cout << static_cast<std::uint64_t>(sums[offset + bucket]);
    }
    std::cout << '\n';
  }
  std::cout.flush();
}

[[nodiscard]] bool domain_diagnostics_enabled()
{
  const char* value = std::getenv("DMGMD_DOMAIN_DIAGNOSTICS");
  if (value == nullptr || value[0] == '\0' || std::strcmp(value, "0") == 0) {
    return false;
  }
  if (std::strcmp(value, "1") == 0) return true;
  throw std::runtime_error("DMGMD_DOMAIN_DIAGNOSTICS must be 0 or 1");
}

// Slot of a global ID inside the identity permutation (identity.global_id
// is a permutation of [0, N); the map below is built once per use site).
[[nodiscard]] std::vector<std::size_t> build_slot_of_global_id(
    const HostAtoms& identity)
{
  std::vector<std::size_t> slot_of_gid(identity.global_id.size());
  for (std::size_t slot = 0; slot < identity.global_id.size(); ++slot) {
    if (identity.global_id[slot] >= identity.global_id.size()) {
      throw std::runtime_error("global ID is outside the identity permutation");
    }
    slot_of_gid[identity.global_id[slot]] = slot;
  }
  return slot_of_gid;
}

void upload_domain_layout(
    const LocalLayout& layout,
    const ExchangePlan& plan,
    bool track_unwrapped,
    DomainDeviceAtoms& device,
    bool reset_epoch_displacement)
{
  const std::size_t local = layout.local_count();
  const std::size_t owned = layout.owned_count();
  std::vector<unsigned long long> gid(local);
  std::vector<int> type(local);
  std::vector<double> mass(local, 0.0);
  std::vector<float> charge(local, 0.0f);
  std::vector<double> position(3 * local, 0.0);
  std::vector<double> velocity(3 * local, 0.0);
  std::vector<double> unwrapped(3 * local, 0.0);
  for (std::size_t index = 0; index < owned; ++index) {
    const DomainAtomRecord& record = layout.owned[index];
    gid[index] = record.global_id;
    type[index] = record.type;
    mass[index] = record.mass;
    charge[index] = record.charge;
    for (int axis = 0; axis < 3; ++axis) {
      position[axis * local + index] = record.position[static_cast<std::size_t>(axis)];
      velocity[axis * local + index] = record.velocity[static_cast<std::size_t>(axis)];
      if (track_unwrapped) {
        unwrapped[axis * local + index] = record.unwrapped[static_cast<std::size_t>(axis)];
      }
    }
  }
  auto place_ghost = [&](const GhostSlotInfo& ghost, std::size_t slot) {
    gid[slot] = ghost.global_id;
    type[slot] = ghost.type;
    for (int axis = 0; axis < 3; ++axis) {
      position[axis * local + slot] = ghost.position[static_cast<std::size_t>(axis)];
    }
  };
  for (std::size_t index = 0; index < layout.dependency_ghosts.size(); ++index) {
    place_ghost(layout.dependency_ghosts[index], owned + index);
  }
  for (std::size_t index = 0; index < layout.coordinate_ghosts.size(); ++index) {
    place_ghost(layout.coordinate_ghosts[index], layout.dependency_end() + index);
  }
  device.global_id.resize_reuse(std::max<std::size_t>(local, 1));
  device.type.resize_reuse(std::max<std::size_t>(local, 1));
  device.mass.resize_reuse(std::max<std::size_t>(local, 1));
  device.charge.resize_reuse(std::max<std::size_t>(local, 1));
  device.position.resize_reuse(std::max<std::size_t>(3 * local, 1));
  device.velocity.resize_reuse(std::max<std::size_t>(3 * local, 1));
  device.force.resize_reuse(std::max<std::size_t>(3 * local, 1), 0.0);
  device.potential.resize_reuse(std::max<std::size_t>(local, 1), 0.0);
  device.virial.resize_reuse(std::max<std::size_t>(9 * local, 1), 0.0);
  if (reset_epoch_displacement) {
    device.epoch_displacement.resize_reuse(
        std::max<std::size_t>(3 * local, 1), 0.0);
  } else if (device.epoch_displacement.size() != std::max<std::size_t>(3 * local, 1)) {
    throw std::logic_error("epoch displacement shape changed without a rebuild");
  }
  if (local != 0) {
    device.global_id.copy_from_host(gid.data(), local);
    device.type.copy_from_host(type.data(), local);
    device.mass.copy_from_host(mass.data(), local);
    device.charge.copy_from_host(charge.data(), local);
    device.position.copy_from_host(position.data(), 3 * local);
    device.velocity.copy_from_host(velocity.data(), 3 * local);
  }
  if (track_unwrapped) {
    device.unwrapped.resize_reuse(std::max<std::size_t>(3 * local, 1));
    device.previous_position.resize_reuse(std::max<std::size_t>(3 * local, 1));
    if (local != 0) {
      device.unwrapped.copy_from_host(unwrapped.data(), 3 * local);
      device.previous_position.copy_from_host(position.data(), 3 * local);
    }
  } else {
    device.unwrapped.resize_reuse(0);
    device.previous_position.resize_reuse(0);
  }
  for (int face = 0; face < 2; ++face) {
    const std::size_t send_count = plan.face[face].send_slots.size();
    const std::size_t recv_count = plan.face[face].recv_slots.size();
    device.send_slots[face].resize_reuse(std::max<std::size_t>(send_count, 1));
    device.recv_slots[face].resize_reuse(std::max<std::size_t>(recv_count, 1));
    if (send_count != 0) {
      device.send_slots[face].copy_from_host(plan.face[face].send_slots.data(), send_count);
    }
    if (recv_count != 0) {
      device.recv_slots[face].copy_from_host(plan.face[face].recv_slots.data(), recv_count);
    }
  }
}

// Refreshes the host owned records' dynamic fields from the device (needed
// before building send lists or migration records).
void download_owned_dynamics(DomainState& state)
{
  const std::size_t owned = state.owned_count();
  const std::size_t local = state.local_count();
  if (owned == 0) return;
  for (int axis = 0; axis < 3; ++axis) {
    std::vector<double> values(owned);
    check_cuda(cudaMemcpy(values.data(),
                          state.device.position.data() + axis * local,
                          checked_int(owned, "owned slice") * sizeof(double),
                          cudaMemcpyDeviceToHost),
               "download owned positions");
    for (std::size_t index = 0; index < owned; ++index) {
      state.layout.owned[index].position[static_cast<std::size_t>(axis)] = values[index];
    }
    check_cuda(cudaMemcpy(values.data(),
                          state.device.velocity.data() + axis * local,
                          checked_int(owned, "owned slice") * sizeof(double),
                          cudaMemcpyDeviceToHost),
               "download owned velocities");
    for (std::size_t index = 0; index < owned; ++index) {
      state.layout.owned[index].velocity[static_cast<std::size_t>(axis)] = values[index];
    }
    if (state.track_unwrapped) {
      check_cuda(cudaMemcpy(values.data(),
                            state.device.unwrapped.data() + axis * local,
                            checked_int(owned, "owned slice") * sizeof(double),
                            cudaMemcpyDeviceToHost),
                 "download owned unwrapped positions");
      for (std::size_t index = 0; index < owned; ++index) {
        state.layout.owned[index].unwrapped[static_cast<std::size_t>(axis)] = values[index];
      }
    }
  }
}

void ensure_workspace(NEP& nep, DomainState& state)
{
  const int local = checked_int(state.layout.local_count(), "local_count");
  if (state.workspace_count != local) {
    // GPU_Vector capacity is kept non-zero for the empty-domain case, while
    // compute_domain receives the separate logical local_count and never
    // addresses this padding.
    nep.allocate_workspace(std::max(local, 1));
    state.workspace_count = local;
    ++state.workspace_updates;
  }
}

void log_domain_layout(const DomainState& state, std::uint64_t step)
{
  if (!state.detailed_logging) return;
  std::size_t dep_left = 0, dep_right = 0, coord_left = 0, coord_right = 0;
  for (const GhostSlotInfo& ghost : state.layout.dependency_ghosts) {
    if (ghost.face == kFaceLeft) ++dep_left; else ++dep_right;
  }
  for (const GhostSlotInfo& ghost : state.layout.coordinate_ghosts) {
    if (ghost.face == kFaceLeft) ++coord_left; else ++coord_right;
  }
  std::cout << "DMGMD_DOMAIN_LAYOUT rank=" << state.rank << " step=" << step
            << " epoch=" << state.epoch << " owned=" << state.layout.owned_count()
            << " dep_left=" << dep_left << " dep_right=" << dep_right
            << " coord_left=" << coord_left << " coord_right=" << coord_right
            << " local_count=" << state.layout.local_count()
            << " send_left=" << state.plan.face[kFaceLeft].send_slots.size()
            << " send_right=" << state.plan.face[kFaceRight].send_slots.size()
            << " recv_left=" << state.plan.face[kFaceLeft].recv_slots.size()
            << " recv_right=" << state.plan.face[kFaceRight].recv_slots.size()
            << " gpu_allocations=" << state.last_layout_gpu_allocations << '\n';
}

// Rebuilds the ghost membership through the two face exchanges (count
// handshake, control class, then 40-byte membership records, halo class),
// re-derives the local layout and exchange plan, re-uploads the device
// arrays and re-sizes the NEP workspace. `owned` carries the current owned
// records (dynamics already refreshed by the caller). Every layout change
// goes through here: bootstrap, migration and the skin-triggered rebuild.
void exchange_halo_membership(
    DomainState& state,
    std::vector<DomainAtomRecord> owned,
    NEP& nep,
    const Box& box,
    MpiRuntime& mpi,
    CommunicationVolume& communication,
    std::uint64_t step,
    SegmentTiming* timing = nullptr)
{
  const auto membership_started = begin_detailed_timing(timing != nullptr);
  static_cast<void>(box);
  // Tentative layout with empty ghosts: only used to derive the send lists
  // from the current owned positions.
  LocalLayout tentative = build_local_layout(
      owned, {}, {}, state.rank, state.world_size, state.axis, state.axis_length,
      state.d_dep, state.d_coord);
  ExchangePlan send_plan = build_exchange_plan(tentative);

  const int left_peer = send_plan.face[kFaceLeft].peer;
  const int right_peer = send_plan.face[kFaceRight].peer;
  const std::size_t send_left = send_plan.face[kFaceLeft].send_slots.size();
  const std::size_t send_right = send_plan.face[kFaceRight].send_slots.size();

  // Face count handshake (control class, 4 bytes per face and direction).
  std::array<int, 2> counts_out{static_cast<int>(send_left), static_cast<int>(send_right)};
  std::array<int, 2> counts_in{0, 0};
  mpi.exchange_p2p_host_bytes(
      &counts_out[0], sizeof(int), &counts_out[1], sizeof(int),
      &counts_in[0], sizeof(int), &counts_in[1], sizeof(int),
      left_peer, right_peer, ByteClass::control, communication);
  const std::size_t recv_left = static_cast<std::size_t>(counts_in[0]);
  const std::size_t recv_right = static_cast<std::size_t>(counts_in[1]);

  // Membership records (halo class, 40 bytes per atom).
  std::vector<GhostMembershipRecord> records_left(send_left);
  std::vector<GhostMembershipRecord> records_right(send_right);
  const auto fill_records = [&](std::vector<GhostMembershipRecord>& records,
                                const std::vector<int>& slots) {
    for (std::size_t index = 0; index < slots.size(); ++index) {
      const DomainAtomRecord& record = owned[static_cast<std::size_t>(slots[index])];
      records[index].global_id = record.global_id;
      records[index].type = record.type;
      records[index].reserved = 0;
      records[index].position[0] = record.position[0];
      records[index].position[1] = record.position[1];
      records[index].position[2] = record.position[2];
    }
  };
  fill_records(records_left, send_plan.face[kFaceLeft].send_slots);
  fill_records(records_right, send_plan.face[kFaceRight].send_slots);
  std::vector<GhostMembershipRecord> incoming_left(recv_left);
  std::vector<GhostMembershipRecord> incoming_right(recv_right);
  mpi.exchange_p2p_host_bytes(
      records_left.data(), send_left * sizeof(GhostMembershipRecord),
      records_right.data(), send_right * sizeof(GhostMembershipRecord),
      incoming_left.data(), recv_left * sizeof(GhostMembershipRecord),
      incoming_right.data(), recv_right * sizeof(GhostMembershipRecord),
      left_peer, right_peer, ByteClass::halo, communication);

  std::vector<GhostCandidate> candidates_left(recv_left);
  std::vector<GhostCandidate> candidates_right(recv_right);
  for (std::size_t index = 0; index < recv_left; ++index) {
    candidates_left[index] = GhostCandidate{incoming_left[index].global_id,
                                            incoming_left[index].type,
                                            {incoming_left[index].position[0],
                                             incoming_left[index].position[1],
                                             incoming_left[index].position[2]},
                                            kFaceLeft, left_peer,
                                            static_cast<int>(index)};
  }
  for (std::size_t index = 0; index < recv_right; ++index) {
    candidates_right[index] = GhostCandidate{incoming_right[index].global_id,
                                             incoming_right[index].type,
                                             {incoming_right[index].position[0],
                                              incoming_right[index].position[1],
                                              incoming_right[index].position[2]},
                                             kFaceRight, right_peer,
                                             static_cast<int>(index)};
  }

  state.layout = build_local_layout(
      std::move(owned), candidates_left, candidates_right, state.rank,
      state.world_size, state.axis, state.axis_length, state.d_dep, state.d_coord);
  state.plan = build_exchange_plan(state.layout);
  validate_exchange_plan(
      state.plan, state.layout.owned_count(), state.layout.local_count(),
      state.rank, state.world_size);
  if (timing != nullptr) {
    add_seconds(*timing, TimedStepClass::rebuild, kMembershipLayout,
                elapsed_since(membership_started));
  }
  const auto upload_started = begin_detailed_timing(timing != nullptr);
  const std::uint64_t allocations_before =
      gpumd_compat::gpu_vector_allocation_count();
  upload_domain_layout(
      state.layout, state.plan, state.track_unwrapped, state.device, true);
  ensure_workspace(nep, state);
  state.neighbor_cache_valid = false;
  ++state.layout_uploads;
  state.last_layout_gpu_allocations =
      gpumd_compat::gpu_vector_allocation_count() - allocations_before;
  if (state.last_layout_gpu_allocations != 0) {
    ++state.capacity_growth_events;
  }
  if (timing != nullptr) {
    add_seconds(*timing, TimedStepClass::rebuild, kAllocationUpload,
                elapsed_since(upload_started));
  }
  ++state.epoch;
  state.mapping_epoch = state.epoch;
  state.refreshed_epoch = state.epoch;
  log_domain_layout(state, step);
}

// Per-step ghost position refresh (halo class, 24 bytes per atom) through the
// cached face plan; membership itself is only re-evaluated at rebuilds.
void refresh_ghost_positions(
    DomainState& state, MpiRuntime& mpi, CommunicationVolume& communication,
    P2pTimingEvents* timing = nullptr)
{
  if (state.decision_state != DomainDecisionState::confirmed_reuse) {
    throw std::logic_error("ghost refresh requires a confirmed reuse decision");
  }
  if (state.mapping_epoch != state.epoch) {
    throw std::logic_error("ghost refresh uses a stale communication mapping");
  }
  const int left_peer = state.plan.face[kFaceLeft].peer;
  const int right_peer = state.plan.face[kFaceRight].peer;
  const int send_left = checked_int(state.plan.face[kFaceLeft].send_slots.size(), "send left");
  const int send_right = checked_int(state.plan.face[kFaceRight].send_slots.size(), "send right");
  const int recv_left = checked_int(state.plan.face[kFaceLeft].recv_slots.size(), "recv left");
  const int recv_right = checked_int(state.plan.face[kFaceRight].recv_slots.size(), "recv right");
  mpi.exchange_p2p_indexed_device_soa(
      state.device.position.data(), 3, state.local_count(),
      state.device.send_slots[kFaceLeft].data(), send_left,
      state.device.send_slots[kFaceRight].data(), send_right,
      state.device.recv_slots[kFaceLeft].data(), recv_left,
      state.device.recv_slots[kFaceRight].data(), recv_right,
      left_peer, right_peer, communication, timing);
  state.refreshed_epoch = state.mapping_epoch;
}

void log_domain_migration(
    const DomainState& state,
    std::uint64_t step,
    const std::vector<std::pair<std::uint64_t, int>>& incoming)
{
  if (!state.detailed_logging) return;
  std::string transitions;
  constexpr std::size_t kMaxLoggedTransitions = 64;
  bool truncated = false;
  for (std::size_t index = 0; index < incoming.size(); ++index) {
    if (index < kMaxLoggedTransitions) {
      if (!transitions.empty()) transitions += ',';
      transitions += std::to_string(incoming[index].first) + ':' +
                     std::to_string(incoming[index].second) + "->" +
                     std::to_string(state.rank);
    } else {
      truncated = true;
    }
  }
  std::cout << "DMGMD_DOMAIN_MIGRATION rank=" << state.rank << " step=" << step
            << " epoch=" << state.epoch << " migrated_in=" << incoming.size()
            << " transitions=\"" << transitions << "\""
            << (truncated ? " truncated=true" : " truncated=false") << '\n';
}

// Direct migration (plan section 8): refreshes the owned dynamics, computes
// the final owner of every owned atom from the wrapped positions, ships the
// records with Alltoall(count) + Alltoallv(bytes), and rebuilds the local
// layout / halo / workspace. Force, PE and virial never travel: the halo
// that follows re-derives them.
void do_migration(
    DomainState& state,
    NEP& nep,
    const Box& box,
    MpiRuntime& mpi,
    CommunicationVolume& communication,
    std::uint64_t step,
    SegmentTiming* timing = nullptr)
{
  const auto migration_started = begin_detailed_timing(timing != nullptr);
  static_cast<void>(box);
  download_owned_dynamics(state);
  const std::size_t owned = state.owned_count();

  std::vector<double> fractional(owned);
  for (std::size_t index = 0; index < owned; ++index) {
    const std::array<double, 3>& position = state.layout.owned[index].position;
    fractional[index] = fractional_along_axis(
        {box.cpu_h[9], box.cpu_h[10], box.cpu_h[11],
         box.cpu_h[12], box.cpu_h[13], box.cpu_h[14],
         box.cpu_h[15], box.cpu_h[16], box.cpu_h[17]},
        state.axis, position[0], position[1], position[2]);
  }
  MigrationPlan migration = plan_migration(fractional, state.rank, state.world_size);
  validate_migration_plan(migration, owned, state.rank, state.world_size);
  const std::vector<int> recv_counts =
      mpi.alltoall_ints(migration.send_counts, ByteClass::control, communication);

  const std::size_t record_bytes = state.migration_record_bytes();
  const std::size_t group_count = state.group_method_count;
  std::vector<int> send_byte_counts(static_cast<std::size_t>(state.world_size), 0);
  std::vector<int> send_byte_displacements(static_cast<std::size_t>(state.world_size), 0);
  std::size_t total_send_atoms = 0;
  for (int destination = 0; destination < state.world_size; ++destination) {
    const std::size_t atoms =
        migration.outgoing_slots[static_cast<std::size_t>(destination)].size();
    send_byte_counts[static_cast<std::size_t>(destination)] =
        checked_int(atoms * record_bytes, "migration send bytes");
    send_byte_displacements[static_cast<std::size_t>(destination)] =
        checked_int(total_send_atoms * record_bytes, "migration displacement");
    total_send_atoms += atoms;
  }
  std::vector<unsigned char> send_buffer(total_send_atoms * record_bytes, 0);
  const auto write_record = [&](std::size_t stream_index, const DomainAtomRecord& record) {
    unsigned char* out = send_buffer.data() + stream_index * record_bytes;
    MigrationWireRecord wire{};
    wire.global_id = record.global_id;
    wire.type = record.type;
    wire.group_count = static_cast<std::int32_t>(group_count);
    wire.mass = record.mass;
    wire.charge = record.charge;
    wire.position[0] = record.position[0];
    wire.position[1] = record.position[1];
    wire.position[2] = record.position[2];
    wire.velocity[0] = record.velocity[0];
    wire.velocity[1] = record.velocity[1];
    wire.velocity[2] = record.velocity[2];
    wire.unwrapped[0] = record.unwrapped[0];
    wire.unwrapped[1] = record.unwrapped[1];
    wire.unwrapped[2] = record.unwrapped[2];
    std::memcpy(out, &wire, kMigrationFixedBytes +
                                (state.track_unwrapped ? 3 * sizeof(double) : 0));
    std::memcpy(out + kMigrationFixedBytes +
                    (state.track_unwrapped ? 3 * sizeof(double) : 0),
                record.group_labels.data(), group_count * sizeof(std::int32_t));
  };
  {
    std::size_t stream_index = 0;
    for (int destination = 0; destination < state.world_size; ++destination) {
      for (int slot : migration.outgoing_slots[static_cast<std::size_t>(destination)]) {
        write_record(stream_index, state.layout.owned[static_cast<std::size_t>(slot)]);
        ++stream_index;
      }
    }
  }

  std::vector<int> recv_byte_counts(static_cast<std::size_t>(state.world_size), 0);
  std::vector<int> recv_byte_displacements(static_cast<std::size_t>(state.world_size), 0);
  std::size_t total_recv_atoms = 0;
  for (int source = 0; source < state.world_size; ++source) {
    const std::size_t atoms = static_cast<std::size_t>(
        recv_counts[static_cast<std::size_t>(source)]);
    recv_byte_counts[static_cast<std::size_t>(source)] =
        checked_int(atoms * record_bytes, "migration recv bytes");
    recv_byte_displacements[static_cast<std::size_t>(source)] =
        checked_int(total_recv_atoms * record_bytes, "migration displacement");
    total_recv_atoms += atoms;
  }
  std::vector<unsigned char> recv_buffer(total_recv_atoms * record_bytes, 0);
  mpi.alltoallv_host_bytes(
      send_buffer.data(), send_byte_counts, send_byte_displacements,
      recv_buffer.data(), recv_byte_counts, recv_byte_displacements,
      ByteClass::migration, communication);

  // Staying atoms keep their records; incoming records are parsed per source
  // rank (the source rank IS the previous owner, so the transition log is
  // exact).
  std::vector<char> routed(owned, 0);
  for (int destination = 0; destination < state.world_size; ++destination) {
    for (int slot : migration.outgoing_slots[static_cast<std::size_t>(destination)]) {
      routed[static_cast<std::size_t>(slot)] = 1;
    }
  }
  std::vector<DomainAtomRecord> next_owned;
  next_owned.reserve(migration.staying_count + total_recv_atoms);
  for (std::size_t index = 0; index < owned; ++index) {
    if (!routed[index]) next_owned.push_back(state.layout.owned[index]);
  }
  std::vector<std::pair<std::uint64_t, int>> incoming;
  // Parse all incoming records (per source rank, in stream order); the source
  // rank IS the previous owner, so the transition log is exact.
  for (int source = 0; source < state.world_size; ++source) {
    const std::size_t atoms = static_cast<std::size_t>(
        recv_counts[static_cast<std::size_t>(source)]);
    for (std::size_t index = 0; index < atoms; ++index) {
      const unsigned char* in = recv_buffer.data() +
          static_cast<std::size_t>(recv_byte_displacements[static_cast<std::size_t>(source)]) +
          index * record_bytes;
      MigrationWireRecord wire{};
      std::memcpy(&wire, in, kMigrationFixedBytes +
                                 (state.track_unwrapped ? 3 * sizeof(double) : 0));
      DomainAtomRecord record;
      record.global_id = wire.global_id;
      record.type = wire.type;
      record.mass = wire.mass;
      record.charge = static_cast<float>(wire.charge);
      record.position = {wire.position[0], wire.position[1], wire.position[2]};
      record.velocity = {wire.velocity[0], wire.velocity[1], wire.velocity[2]};
      record.unwrapped = {wire.unwrapped[0], wire.unwrapped[1], wire.unwrapped[2]};
      record.group_labels.assign(group_count, 0);
      if (group_count != 0) {
        std::memcpy(record.group_labels.data(),
                    in + kMigrationFixedBytes +
                        (state.track_unwrapped ? 3 * sizeof(double) : 0),
                    group_count * sizeof(std::int32_t));
      }
      next_owned.push_back(record);
      incoming.emplace_back(wire.global_id, source);
    }
  }
  std::sort(next_owned.begin(), next_owned.end(),
            [](const DomainAtomRecord& a, const DomainAtomRecord& b) {
              return a.global_id < b.global_id;
            });
  for (std::size_t index = 1; index < next_owned.size(); ++index) {
    if (next_owned[index - 1].global_id == next_owned[index].global_id) {
      throw std::runtime_error("migration produced a duplicate owned global ID");
    }
  }
  // Initial ownership is an exact global-ID partition. Each transaction
  // partitions every old slot into either staying or exactly one destination,
  // and MPI receives exactly the advertised records. The global count plus
  // local duplicate checks therefore preserves the exact unique set without
  // an O(N) bitmap collective at every rebuild.
  // MPI_Alltoall advertises every outgoing slot exactly once and the receive
  // parser consumes exactly those counts. Summing new_count = old_count -
  // sent + received over ranks cancels the routed terms, preserving the
  // already-proven global count without another collective.
  log_domain_migration(state, step, incoming);
  ++state.migration_steps;
  if (timing != nullptr) {
    add_seconds(*timing, TimedStepClass::rebuild, kMigration,
                elapsed_since(migration_started));
  }
  exchange_halo_membership(
      state, std::move(next_owned), nep, box, mpi, communication, step, timing);
}

// ---------------------------------------------------------------------------
// Per-step helpers.
// ---------------------------------------------------------------------------

void launch_domain_force(
    DomainState& state, Box& box, NEP& nep,
    gpumd_compat::DomainNeighborAction neighbor_action)
{
  if (state.mapping_epoch != state.epoch || state.refreshed_epoch != state.epoch) {
    throw std::logic_error("domain force uses a stale layout or halo mapping");
  }
  if (neighbor_action == gpumd_compat::DomainNeighborAction::confirmed_reuse &&
      !state.neighbor_cache_valid) {
    throw std::logic_error("domain force reuse was not confirmed by a valid cache");
  }
  if (state.executed_steps != 0 || state.decision_state != DomainDecisionState::undetermined) {
    const DomainDecisionState expected =
        neighbor_action == gpumd_compat::DomainNeighborAction::confirmed_reuse
            ? DomainDecisionState::confirmed_reuse
            : DomainDecisionState::must_rebuild;
    if (state.decision_state != expected) {
      throw std::logic_error("domain force action disagrees with the resolved cache decision");
    }
  }
  const int local = checked_int(state.local_count(), "local_count");
  const int owned = checked_int(state.owned_count(), "owned_count");
  const int dep = checked_int(state.layout.dep_ghost_count(), "dependency ghosts");
  box.set_is_orthogonal();
  if (local > 0) {
    clear_owned_properties<<<(local + kThreads - 1) / kThreads, kThreads>>>(
        0, local, local, state.device.force.data(), state.device.potential.data(),
        state.device.virial.data());
    check_cuda(cudaGetLastError(), "clear local NEP scratch");
  }
  nep.N1 = 0;
  nep.N2 = owned;
  nep.ND1 = 0;
  // An empty owned prefix consumes nobody's dependency products; skipping the
  // descriptor phase on such ranks is safe and avoids wasted ghost work.
  nep.ND2 = owned > 0 ? owned + dep : 0;
  nep.compute_domain(
      box, local, state.device.type, state.device.position, state.device.potential,
      state.device.force, state.device.virial, state.device.global_id, neighbor_action,
      state.epoch);
  state.neighbor_cache_valid = true;
}

void launch_domain_verlet(DomainState& state, bool first_half, double time_step)
{
  const int owned = checked_int(state.owned_count(), "owned_count");
  if (owned == 0) return;  // empty slab integrates nothing
  const int stride = checked_int(state.local_count(), "local_count");
  velocity_verlet_range<<<(owned + kThreads - 1) / kThreads, kThreads>>>(
      first_half, owned, stride, time_step, state.device.mass.data(),
      state.device.position.data(), state.device.velocity.data(),
      state.device.force.data(), state.device.epoch_displacement.data());
  if (first_half && state.track_unwrapped) {
    update_unwrapped_range<<<(owned + kThreads - 1) / kThreads, kThreads>>>(
        owned, stride, state.device.position.data(),
        state.device.previous_position.data(), state.device.unwrapped.data());
  }
  check_cuda(cudaGetLastError(), first_half ? "velocity-Verlet first half"
                                           : "velocity-Verlet second half");
}

ThermoState compute_domain_thermo(
    DomainState& state,
    const Box& box,
    const HostAtoms& identity,
    GPU_Vector<double>& device_thermo,
    MpiRuntime& mpi,
    CommunicationVolume& communication,
    const std::array<cudaEvent_t, 4>* timing_events = nullptr)
{
  const int owned = checked_int(state.owned_count(), "owned_count");
  const int stride = checked_int(state.local_count(), "local_count");
  find_owned_thermo_sums_range<<<8, kThermoThreads>>>(
      owned, stride, state.device.mass.data(), state.device.potential.data(),
      state.device.velocity.data(), state.device.virial.data(),
      device_thermo.data());
  check_cuda(cudaGetLastError(), "launch owned thermo reduction");
  if (timing_events != nullptr) {
    check_cuda(cudaEventRecord((*timing_events)[1]),
               "record local thermo reduction end");
  }
  mpi.allreduce_sum_device(device_thermo.data(), 8, communication);
  if (timing_events != nullptr) {
    check_cuda(cudaEventRecord((*timing_events)[2]),
               "record thermo normalization start");
  }
  normalize_global_thermo<<<1, 8>>>(
      checked_int(identity.counts.global_count, "global_count"),
      box.get_volume(), device_thermo.data());
  check_cuda(cudaGetLastError(), "normalize global thermo");
  if (timing_events != nullptr) {
    check_cuda(cudaEventRecord((*timing_events)[3]),
               "record thermo normalization end");
  }
  ThermoState result;
  device_thermo.copy_to_host(result.values.data());
  return result;
}

double adaptive_domain_time_step(
    DomainState& state,
    double initial_time_step,
    const std::optional<double>& maximum_distance,
    MpiRuntime& mpi,
    CommunicationVolume& communication)
{
  if (!maximum_distance) return initial_time_step;
  const std::size_t owned = state.owned_count();
  const std::size_t local = state.local_count();
  double maximum_squared = 0.0;
  if (owned != 0) {
    std::vector<double> velocity(3 * owned);
    for (int axis = 0; axis < 3; ++axis) {
      check_cuda(cudaMemcpy(velocity.data() + axis * owned,
                            state.device.velocity.data() + axis * local,
                            checked_int(owned, "owned slice") * sizeof(double),
                            cudaMemcpyDeviceToHost),
                 "download owned velocities for the adaptive timestep");
    }
    for (std::size_t atom = 0; atom < owned; ++atom) {
      const double vx = velocity[atom];
      const double vy = velocity[owned + atom];
      const double vz = velocity[2 * owned + atom];
      maximum_squared = std::max(maximum_squared, vx * vx + vy * vy + vz * vz);
    }
  }
  maximum_squared = mpi.allreduce_max_host(maximum_squared, communication);
  const double limited = maximum_squared == 0.0
                             ? initial_time_step
                             : *maximum_distance / std::sqrt(maximum_squared);
  return limited < initial_time_step ? limited : initial_time_step;
}

// correct_velocity on the local layout (plan section 6 step 1): gather
// (global_id, owned position, owned velocity) to the root, restore the
// global input-slot order, reuse the shared CPU correction, then scatter the
// corrected velocities back to the current owners. The global 3N data never
// enters any local_count-strided device array.
void correct_domain_velocity(
    DomainState& state,
    const HostAtoms& identity,
    const CorrectVelocityCommand& command,
    MpiRuntime& mpi,
    CommunicationVolume& communication)
{
  const int owned = checked_int(state.owned_count(), "owned_count");
  std::vector<int> counts = mpi.allgather_int(owned, ByteClass::control, communication);
  std::size_t total = 0;
  std::vector<int> displacements(static_cast<std::size_t>(mpi.world_size()), 0);
  for (int source = 0; source < mpi.world_size(); ++source) {
    displacements[static_cast<std::size_t>(source)] = static_cast<int>(total);
    total += static_cast<std::size_t>(counts[static_cast<std::size_t>(source)]);
  }
  if (total != identity.counts.global_count) {
    throw std::runtime_error("owned counts do not cover the global system");
  }
  std::vector<unsigned long long> host_gids(state.owned_count());
  for (std::size_t index = 0; index < host_gids.size(); ++index) {
    host_gids[index] = state.layout.owned[index].global_id;
  }
  const std::vector<unsigned long long> gathered_gids = mpi.gather_u64_to_root(
      host_gids.empty() ? nullptr : host_gids.data(), owned, counts, communication);
  const std::vector<double> gathered_position = mpi.gather_prefix_device_soa_to_root(
      state.device.position.data(), 3, owned, state.local_count(), counts, communication);
  const std::vector<double> gathered_velocity = mpi.gather_prefix_device_soa_to_root(
      state.device.velocity.data(), 3, owned, state.local_count(), counts, communication);

  std::vector<double> payload;  // root only: per-rank AoS streams
  if (mpi.is_root()) {
    const std::size_t global_count = identity.counts.global_count;
    std::vector<std::size_t> slot_of_gid(global_count);
    for (std::size_t slot = 0; slot < global_count; ++slot) {
      slot_of_gid[identity.global_id[slot]] = slot;
    }
    std::vector<double> full_position(3 * global_count);
    std::vector<double> full_velocity(3 * global_count);
    for (int source = 0; source < mpi.world_size(); ++source) {
      for (int index = 0; index < counts[static_cast<std::size_t>(source)]; ++index) {
        const std::size_t slot = slot_of_gid[gathered_gids[static_cast<std::size_t>(
            displacements[static_cast<std::size_t>(source)] + index)]];
        for (int axis = 0; axis < 3; ++axis) {
          full_position[axis * global_count + slot] =
              gathered_position[static_cast<std::size_t>(
                  (displacements[static_cast<std::size_t>(source)] + index) * 3 + axis)];
          full_velocity[axis * global_count + slot] =
              gathered_velocity[static_cast<std::size_t>(
                  (displacements[static_cast<std::size_t>(source)] + index) * 3 + axis)];
        }
      }
    }
    std::vector<std::size_t> indices(global_count);
    for (std::size_t index = 0; index < global_count; ++index) indices[index] = index;
    if (!command.grouping_method) {
      correct_velocity_subset(identity.mass, full_position, full_velocity, indices);
    } else {
      const std::size_t method = static_cast<std::size_t>(*command.grouping_method);
      if (method >= identity.group_labels.size()) {
        throw std::runtime_error("correct_velocity grouping method is out of range");
      }
      int maximum_group = -1;
      for (std::size_t atom = 0; atom < identity.counts.owned_count; ++atom) {
        maximum_group = std::max(maximum_group, identity.group_labels[method][atom]);
      }
      for (int group = 0; group <= maximum_group; ++group) {
        std::vector<std::size_t> subset;
        for (std::size_t atom = 0; atom < identity.counts.owned_count; ++atom) {
          if (identity.group_labels[method][atom] == group) subset.push_back(atom);
        }
        correct_velocity_subset(identity.mass, full_position, full_velocity, subset);
      }
    }
    payload.resize(3 * total);
    for (int source = 0; source < mpi.world_size(); ++source) {
      for (int index = 0; index < counts[static_cast<std::size_t>(source)]; ++index) {
        const std::size_t slot = slot_of_gid[gathered_gids[static_cast<std::size_t>(
            displacements[static_cast<std::size_t>(source)] + index)]];
        for (int axis = 0; axis < 3; ++axis) {
          payload[static_cast<std::size_t>(
              (displacements[static_cast<std::size_t>(source)] + index) * 3 + axis)] =
              full_velocity[axis * global_count + slot];
        }
      }
    }
  }
  mpi.scatterv_prefix_device_soa_from_root(
      payload, 3, counts, state.device.velocity.data(), state.local_count(),
      communication);
}

// Local-owned-prefix output gather (plan section 6 step 12): every field
// gather carries the owned prefix plus the global IDs; the root restores
// exactly N records in global input-slot order and hands them to the shared
// GPUMD-compatible formatters.
HostSnapshot gather_domain_snapshot(
    DomainState& state,
    const HostAtoms& identity,
    MpiRuntime& mpi,
    CommunicationVolume& communication)
{
  const int owned = checked_int(state.owned_count(), "owned_count");
  std::vector<int> counts = mpi.allgather_int(owned, ByteClass::control, communication);
  std::size_t total = 0;
  for (int count : counts) total += static_cast<std::size_t>(count);
  if (total != identity.counts.global_count) {
    throw std::runtime_error("owned counts do not cover the global system");
  }
  std::vector<unsigned long long> host_gids(state.owned_count());
  for (std::size_t index = 0; index < host_gids.size(); ++index) {
    host_gids[index] = state.layout.owned[index].global_id;
  }
  const std::vector<unsigned long long> gathered_gids = mpi.gather_u64_to_root(
      host_gids.empty() ? nullptr : host_gids.data(), owned, counts, communication);

  HostSnapshot snapshot;
  if (mpi.is_root()) {
    snapshot.global_id.assign(identity.global_id.begin(), identity.global_id.end());
  }
  const auto gather_field = [&](const GPU_Vector<double>& values, int components) {
    const std::vector<double> packed = mpi.gather_prefix_device_soa_to_root(
        values.data(), components, owned, state.local_count(), counts, communication);
    std::vector<double> soa(mpi.is_root()
                                ? identity.counts.global_count * static_cast<std::size_t>(components)
                                : 0);
    if (!mpi.is_root()) return soa;
    const std::size_t global_count = identity.counts.global_count;
    std::vector<std::size_t> slot_of_gid(global_count);
    for (std::size_t slot = 0; slot < global_count; ++slot) {
      slot_of_gid[identity.global_id[slot]] = slot;
    }
    std::size_t base = 0;
    for (int source = 0; source < mpi.world_size(); ++source) {
      for (int index = 0; index < counts[static_cast<std::size_t>(source)]; ++index) {
        const std::size_t slot =
            slot_of_gid[gathered_gids[base + static_cast<std::size_t>(index)]];
        for (int component = 0; component < components; ++component) {
          soa[static_cast<std::size_t>(component) * global_count + slot] =
              packed[(base + static_cast<std::size_t>(index)) * static_cast<std::size_t>(components) +
                     static_cast<std::size_t>(component)];
        }
      }
      base += static_cast<std::size_t>(counts[static_cast<std::size_t>(source)]);
    }
    return soa;
  };
  snapshot.position = gather_field(state.device.position, 3);
  snapshot.velocity = gather_field(state.device.velocity, 3);
  snapshot.force = gather_field(state.device.force, 3);
  snapshot.potential = gather_field(state.device.potential, 1);
  snapshot.virial = gather_field(state.device.virial, 9);
  if (state.track_unwrapped) {
    snapshot.unwrapped = gather_field(state.device.unwrapped, 3);
  }
  return snapshot;
}

void run_domain_segment(
    std::uint64_t sequence,
    int steps,
    double base_time_step,
    const std::optional<double>& maximum_distance,
    const EnsembleCommand& ensemble,
    const std::optional<CorrectVelocityCommand>& velocity_correction,
    const std::vector<Measurement>& measurements,
    double& global_time,
    std::uint64_t& global_step,
    Box& box,
    const HostAtoms& identity,
    NEP& nep,
    DomainState& state,
    MpiRuntime& mpi)
{
  const std::uint64_t migration_start = state.migration_steps;
  const std::uint64_t rebuild_start = state.rebuild_steps;
  const std::uint64_t layout_start = state.layout_uploads;
  const std::uint64_t workspace_start = state.workspace_updates;
  const std::uint64_t growth_start = state.capacity_growth_events;
  const std::uint64_t allocation_start =
      gpumd_compat::gpu_vector_allocation_count();
  int thermo_count = 0;
  int restart_count = 0;
  for (const Measurement& measurement : measurements) {
    if (std::holds_alternative<DumpThermoCommand>(measurement)) ++thermo_count;
    if (std::holds_alternative<DumpRestartCommand>(measurement)) ++restart_count;
  }
  if (thermo_count > 1 || restart_count > 1) {
    throw std::runtime_error("multiple dump_thermo or dump_restart commands within one run");
  }
  for (const Measurement& measurement : measurements) {
    if (const auto* dump = std::get_if<DumpThermoCommand>(&measurement)) {
      if (mpi.is_root()) {
        FILE* file = std::fopen("thermo.out", "a");
        if (!file) throw std::runtime_error("cannot open thermo.out");
        write_thermo_header(file, dump->interval, identity, base_time_step);
        std::fclose(file);
      }
    }
    if (const auto* dump = std::get_if<DumpXyzCommand>(&measurement)) {
      if (dump->quantities.group_labels && identity.group_labels.empty()) {
        throw std::runtime_error("cannot output group labels without model groups");
      }
    }
  }

  SegmentTiming segment_timing;
  std::array<cudaEvent_t, 4> setup_nep_events{nullptr, nullptr, nullptr, nullptr};
  std::array<cudaEvent_t, 4> nep_events{nullptr, nullptr, nullptr, nullptr};
  std::array<cudaEvent_t, 6> integration_events{
      nullptr, nullptr, nullptr, nullptr, nullptr, nullptr};
  std::array<cudaEvent_t, 4> thermo_events{nullptr, nullptr, nullptr, nullptr};
  P2pTimingEvents halo_events;
  std::optional<TimedStepClass> pending_thermostat_timing;
  if (state.timing_enabled) {
    for (cudaEvent_t& event : setup_nep_events) {
      check_cuda(cudaEventCreate(&event), "create setup NEP timing event");
    }
    for (cudaEvent_t& event : nep_events) {
      check_cuda(cudaEventCreate(&event), "create NEP timing event");
    }
    for (cudaEvent_t& event : integration_events) {
      check_cuda(cudaEventCreate(&event), "create integration timing event");
    }
    for (cudaEvent_t& event : thermo_events) {
      check_cuda(cudaEventCreate(&event), "create thermo timing event");
    }
    create_opaque_event(halo_events.pack_begin, "create halo timing event");
    create_opaque_event(halo_events.pack_end, "create halo timing event");
    create_opaque_event(halo_events.unpack_begin, "create halo timing event");
    create_opaque_event(halo_events.unpack_end, "create halo timing event");
    nep.domain_timing_marker = [&setup_nep_events](int marker) {
      check_cuda(cudaEventRecord(setup_nep_events.at(static_cast<std::size_t>(marker))),
                 "record setup NEP timing marker");
    };
  }
  // Preserve the GPUMD-compatible segment-initial force call, but only the
  // bootstrap call builds rows. Consecutive run segments reuse the already
  // proven neighbor cache instead of forcing a redundant rebuild.
  const auto setup_started = begin_detailed_timing(state.timing_enabled);
  state.decision_state = state.neighbor_cache_valid
                             ? DomainDecisionState::confirmed_reuse
                             : DomainDecisionState::must_rebuild;
  launch_domain_force(
      state, box, nep, state.neighbor_cache_valid
                           ? gpumd_compat::DomainNeighborAction::confirmed_reuse
                           : gpumd_compat::DomainNeighborAction::must_rebuild);
  if (state.timing_enabled) {
    segment_timing.setup_host = elapsed_since(setup_started);
    nep.domain_timing_marker = [&nep_events](int marker) {
      check_cuda(cudaEventRecord(nep_events.at(static_cast<std::size_t>(marker))),
                 "record NEP timing marker");
    };
  }
  const auto step_loop_started = begin_detailed_timing(state.timing_enabled);
  for (int step = 0; step < steps; ++step) {
    const auto step_started = begin_detailed_timing(state.timing_enabled);
    double accounted_host = 0.0;
    CommunicationVolume communication;
    communication.measure_mpi_wait = state.timing_enabled;
    state.current_volume = &communication;
    const auto integration_first_started = begin_detailed_timing(state.timing_enabled);
    // 1. correct_velocity trigger: gather to the root, CPU correction over
    // the global order, scatter back to the current owners.
    if (velocity_correction && step % velocity_correction->interval == 0) {
      correct_domain_velocity(state, identity, *velocity_correction, mpi, communication);
    }
    // 2. Adaptive timestep: owned max |v|, global max reduction.
    const double time_step = adaptive_domain_time_step(
        state, base_time_step, maximum_distance, mpi, communication);
    global_time += time_step;
    if (state.track_unwrapped) {
      const std::size_t owned = state.owned_count();
      const std::size_t local = state.local_count();
      for (int axis = 0; axis < 3; ++axis) {
        check_cuda(cudaMemcpy(
                       state.device.previous_position.data() + axis * local,
                       state.device.position.data() + axis * local,
                       checked_int(owned, "owned slice") * sizeof(double),
                       cudaMemcpyDeviceToDevice),
                   "snapshot owned positions for unwrapped tracking");
      }
    }
    // 3. VV first half over the owned prefix.
    if (state.timing_enabled) {
      check_cuda(cudaEventRecord(integration_events[0]),
                 "record first integration start");
    }
    launch_domain_verlet(state, true, time_step);
    // 4. Wrap the owned prefix into the global box.
    {
      const int owned = checked_int(state.owned_count(), "owned_count");
      const int local = checked_int(state.local_count(), "local_count");
      if (owned > 0) {
        wrap_positions<<<(owned + kThreads - 1) / kThreads, kThreads>>>(
            owned, local, box, state.device.position.data());
        check_cuda(cudaGetLastError(), "wrap owned positions");
      }
    }
    if (state.timing_enabled) {
      check_cuda(cudaEventRecord(integration_events[1]),
                 "record first integration end");
    }
    const double integration_first_seconds =
        state.timing_enabled ? elapsed_since(integration_first_started) : 0.0;
    // 5. One globally consistent cache decision. A geometric slab crossing
    // alone only marks the eventual route; manager ownership remains fixed
    // until the displacement cache itself must be rebuilt.
    const auto decision_started = begin_detailed_timing(state.timing_enabled);
    state.decision_state = DomainDecisionState::undetermined;
    double local_max_squared = 0.0;
    std::uint32_t local_reasons = 0;
    {
      const int owned = checked_int(state.owned_count(), "owned_count");
      const int local = checked_int(state.local_count(), "local_count");
      state.device.decision_max_squared.resize_reuse(1, 0ULL);
      state.device.decision_reasons.resize_reuse(1, 0U);
      if (owned > 0) {
        inspect_domain_cache<<<(owned + kThreads - 1) / kThreads, kThreads>>>(
            owned, local, box, state.axis, state.rank, state.world_size,
            time_step, state.device.position.data(), state.device.velocity.data(),
            state.device.epoch_displacement.data(),
            state.device.decision_max_squared.data(),
            state.device.decision_reasons.data());
        check_cuda(cudaGetLastError(), "inspect domain cache validity");
        unsigned long long max_bits = 0;
        unsigned int reasons = 0;
        state.device.decision_max_squared.copy_to_host(&max_bits, 1);
        state.device.decision_reasons.copy_to_host(&reasons, 1);
        std::memcpy(&local_max_squared, &max_bits, sizeof(local_max_squared));
        local_reasons = reasons;
      }
    }
    const double global_max_squared =
        mpi.allreduce_max_host(local_max_squared, communication);
    constexpr double displacement_limit_squared =
        0.25 * kNeighborSkin * kNeighborSkin;
    if (global_max_squared > displacement_limit_squared) {
      local_reasons |= kDisplacementLimit;
    }
    const std::uint32_t global_reasons =
        mpi.allreduce_or_u32(local_reasons, communication);
    if ((global_reasons & kContinuityInvalid) != 0) {
      throw std::runtime_error(
          "an owned atom has a non-finite or unsupported multi-box displacement");
    }
    const bool rebuild = (global_reasons & kDisplacementLimit) != 0;
    state.decision_state = rebuild ? DomainDecisionState::must_rebuild
                                   : DomainDecisionState::confirmed_reuse;
    const TimedStepClass step_class =
        rebuild ? TimedStepClass::rebuild : TimedStepClass::ordinary;
    if (state.timing_enabled) {
      const double seconds = elapsed_since(decision_started);
      add_seconds(segment_timing, step_class, kIntegrationHost,
                  integration_first_seconds);
      accounted_host += integration_first_seconds;
      add_seconds(segment_timing, step_class, kDecision, seconds);
      accounted_host += seconds;
    }
    gpumd_compat::DomainNeighborAction neighbor_action =
        gpumd_compat::DomainNeighborAction::confirmed_reuse;
    if (rebuild) {
      const auto rebuild_started = begin_detailed_timing(state.timing_enabled);
      if (state.timing_enabled) ++segment_timing.displacement_rebuilds;
      if ((global_reasons & kOwnerRoutingNeeded) != 0) {
        if (state.timing_enabled) ++segment_timing.routing_rebuilds;
        do_migration(state, nep, box, mpi, communication, global_step + 1,
                     state.timing_enabled ? &segment_timing : nullptr);
      } else {
        download_owned_dynamics(state);
        std::vector<DomainAtomRecord> owned = state.layout.owned;
        exchange_halo_membership(
            state, std::move(owned), nep, box, mpi, communication, global_step + 1,
            state.timing_enabled ? &segment_timing : nullptr);
      }
      ++state.rebuild_steps;
      neighbor_action = gpumd_compat::DomainNeighborAction::must_rebuild;
      if (state.timing_enabled) accounted_host += elapsed_since(rebuild_started);
    } else {
      // 6. Ordinary step: manager/layout/mapping stay fixed and only ghost
      // coordinates are refreshed through the cached face plan.
      halo_events.transfer_wait_seconds = 0.0;
      refresh_ghost_positions(
          state, mpi, communication, state.timing_enabled ? &halo_events : nullptr);
      if (state.timing_enabled) {
        add_seconds(segment_timing, step_class, kHaloTransferWait,
                    halo_events.transfer_wait_seconds);
        accounted_host += halo_events.transfer_wait_seconds;
      }
    }
    // 8. Domain NEP: dependency-center neighbor/descriptor/partial, owned
    // radial force / many-body / ZBL, all with the local stride.
    launch_domain_force(state, box, nep, neighbor_action);
    // 9. VV second half over the owned prefix.
    const auto integration_second_started = begin_detailed_timing(state.timing_enabled);
    if (state.timing_enabled) {
      check_cuda(cudaEventRecord(integration_events[2]),
                 "record second integration start");
    }
    launch_domain_verlet(state, false, time_step);
    if (state.timing_enabled) {
      check_cuda(cudaEventRecord(integration_events[3]),
                 "record second integration end");
      check_cuda(cudaEventRecord(thermo_events[0]), "record thermo start");
    }
    // 10. Thermo: owned local sums + global Allreduce.
    const double integration_second_seconds =
        state.timing_enabled ? elapsed_since(integration_second_started) : 0.0;
    if (state.timing_enabled) {
      add_seconds(segment_timing, step_class, kIntegrationHost,
                  integration_second_seconds);
      accounted_host += integration_second_seconds;
    }
    const auto thermo_started = begin_detailed_timing(state.timing_enabled);
    const ThermoState thermo =
        compute_domain_thermo(state, box, identity, state.device.thermo, mpi, communication,
                              state.timing_enabled ? &thermo_events : nullptr);
    if (state.timing_enabled) {
      const double seconds = elapsed_since(thermo_started);
      add_seconds(segment_timing, step_class, kThermoHost, seconds);
      accounted_host += seconds;
    }
    // 11. Thermostat over the owned prefix.
    const auto thermostat_started = begin_detailed_timing(state.timing_enabled);
    if (state.timing_enabled) {
      // The current thermo D2H is a natural readiness point for the preceding
      // step's thermostat. Resolve it before reusing the event pair; no
      // diagnostic-only per-step synchronization is introduced.
      if (pending_thermostat_timing) {
        add_seconds(segment_timing, *pending_thermostat_timing, kIntegrationDevice,
                    event_seconds(integration_events[4], integration_events[5]));
      }
      check_cuda(cudaEventRecord(integration_events[4]),
                 "record thermostat start");
    }
    if (ensemble.kind == EnsembleKind::nvt_ber) {
      const double fraction = static_cast<double>(step) / static_cast<double>(steps);
      const double target = ensemble.initial_temperature +
                            (ensemble.final_temperature - ensemble.initial_temperature) * fraction;
      const double coupling = 1.0 / ensemble.temperature_coupling;
      if (coupling > 1.0e-5) {
        const double factor = std::sqrt(1.0 + coupling * (target / thermo.values[0] - 1.0));
        const int owned = checked_int(state.owned_count(), "owned_count");
        if (owned != 0) {
          const int stride = checked_int(state.local_count(), "local_count");
          scale_owned_velocity_range<<<(owned + kThreads - 1) / kThreads, kThreads>>>(
              owned, stride, factor, state.device.velocity.data());
        }
        check_cuda(cudaGetLastError(), "Berendsen velocity scaling");
      }
    }
    if (state.timing_enabled) {
      check_cuda(cudaEventRecord(integration_events[5]),
                 "record thermostat end");
      pending_thermostat_timing = step_class;
    }
    if (state.timing_enabled) {
      const double seconds = elapsed_since(thermostat_started);
      add_seconds(segment_timing, step_class, kIntegrationHost, seconds);
      accounted_host += seconds;
      add_seconds(segment_timing, step_class, kCellNeighborDevice,
                  event_seconds(nep_events[0], nep_events[1]));
      add_seconds(segment_timing, step_class, kNepDevice,
                  event_seconds(nep_events[2], nep_events[3]));
      add_seconds(segment_timing, step_class, kIntegrationDevice,
                  event_seconds(integration_events[0], integration_events[1]) +
                      event_seconds(integration_events[2], integration_events[3]));
      add_seconds(segment_timing, step_class, kThermoDevice,
                  event_seconds(thermo_events[0], thermo_events[1]) +
                      event_seconds(thermo_events[2], thermo_events[3]));
      if (!rebuild) {
        add_seconds(segment_timing, step_class, kHaloPackDevice,
                    event_seconds(reinterpret_cast<cudaEvent_t>(halo_events.pack_begin),
                                  reinterpret_cast<cudaEvent_t>(halo_events.pack_end)));
        add_seconds(segment_timing, step_class, kHaloUnpackDevice,
                    event_seconds(reinterpret_cast<cudaEvent_t>(halo_events.unpack_begin),
                                  reinterpret_cast<cudaEvent_t>(halo_events.unpack_end)));
      }
    }
    // 12. Outputs.
    const auto output_started = begin_detailed_timing(state.timing_enabled);
    bool need_snapshot = false;
    for (const Measurement& measurement : measurements) {
      if (const auto* dump = std::get_if<DumpXyzCommand>(&measurement)) {
        need_snapshot = need_snapshot || ((step + 1) % dump->interval == 0);
      } else if (const auto* restart = std::get_if<DumpRestartCommand>(&measurement)) {
        need_snapshot = need_snapshot || ((step + 1) % restart->interval == 0);
      }
    }
    std::optional<HostSnapshot> snapshot;
    if (need_snapshot) {
      snapshot = gather_domain_snapshot(state, identity, mpi, communication);
    }
    for (const Measurement& measurement : measurements) {
      if (const auto* dump = std::get_if<DumpThermoCommand>(&measurement)) {
        if ((step + 1) % dump->interval == 0) {
          if (mpi.is_root()) {
            FILE* file = std::fopen("thermo.out", "a");
            if (!file) throw std::runtime_error("cannot open thermo.out");
            write_thermo_row(file, thermo, identity, box);
            std::fclose(file);
          }
        }
      } else if (const auto* dump = std::get_if<DumpXyzCommand>(&measurement)) {
        if (mpi.is_root() && (step + 1) % dump->interval == 0) {
          write_xyz(*dump, step, global_time, box, identity, *snapshot, thermo);
        }
      } else if (const auto* restart = std::get_if<DumpRestartCommand>(&measurement)) {
        if (mpi.is_root() && (step + 1) % restart->interval == 0) {
          write_restart(box, identity, *snapshot);
        }
      }
    }
    if (state.timing_enabled) {
      const double seconds = elapsed_since(output_started);
      add_seconds(segment_timing, step_class, kScientificOutputHost, seconds);
      accounted_host += seconds;
    }
    state.current_volume = nullptr;
    ++global_step;
    ++state.executed_steps;
    mpi.log_step_communication(global_step, communication);
    mpi.log_step_domain_communication(global_step, communication);
    if (state.timing_enabled) {
      const double step_seconds = elapsed_since(step_started);
      const int class_index = static_cast<int>(step_class);
      segment_timing.values[static_cast<int>(step_class)][kCount] += 1.0;
      segment_timing.step_min[class_index] =
          std::min(segment_timing.step_min[class_index], step_seconds);
      segment_timing.step_max[class_index] =
          std::max(segment_timing.step_max[class_index], step_seconds);
      ++segment_timing.step_histogram[class_index]
                                      [timing_histogram_bucket(step_seconds)];
      add_seconds(segment_timing, step_class, kStepWall, step_seconds);
      add_seconds(segment_timing, step_class, kMpiWait,
                  communication.mpi_wait_seconds_local);
      const double remaining = step_seconds - accounted_host;
      if (remaining < -1.0e-9) {
        throw std::logic_error("domain timing host intervals overlap");
      }
      add_seconds(segment_timing, step_class, kRemainingHost,
                  std::max(0.0, remaining));
    }
  }
  if (state.timing_enabled) {
    // The production caller already synchronizes at this segment boundary.
    // Resolve the final deferred event here so the timing reduction sees a
    // complete ledger, without synchronizing after individual kernels.
    check_cuda(cudaDeviceSynchronize(), "resolve domain timing events at segment end");
    if (pending_thermostat_timing) {
      add_seconds(segment_timing, *pending_thermostat_timing, kIntegrationDevice,
                  event_seconds(integration_events[4], integration_events[5]));
    }
    segment_timing.setup_cell_neighbor_device =
        event_seconds(setup_nep_events[0], setup_nep_events[1]);
    segment_timing.setup_nep_device =
        event_seconds(setup_nep_events[2], setup_nep_events[3]);
    segment_timing.step_loop_wall = elapsed_since(step_loop_started);
    nep.domain_timing_marker = {};
    log_segment_timing(segment_timing, sequence, mpi);
    for (cudaEvent_t event : nep_events) {
      check_cuda(cudaEventDestroy(event), "destroy NEP timing event");
    }
    for (cudaEvent_t event : setup_nep_events) {
      check_cuda(cudaEventDestroy(event), "destroy setup NEP timing event");
    }
    for (cudaEvent_t event : integration_events) {
      check_cuda(cudaEventDestroy(event), "destroy integration timing event");
    }
    for (cudaEvent_t event : thermo_events) {
      check_cuda(cudaEventDestroy(event), "destroy thermo timing event");
    }
    destroy_opaque_event(halo_events.pack_begin, "destroy halo timing event");
    destroy_opaque_event(halo_events.pack_end, "destroy halo timing event");
    destroy_opaque_event(halo_events.unpack_begin, "destroy halo timing event");
    destroy_opaque_event(halo_events.unpack_end, "destroy halo timing event");
  }
  std::ostringstream summary;
  summary << "DMGMD_DOMAIN_SUMMARY rank=" << state.rank
          << " sequence=" << sequence << " steps=" << steps
          << " migration_steps=" << (state.migration_steps - migration_start)
          << " rebuild_steps=" << (state.rebuild_steps - rebuild_start)
          << " layout_uploads=" << (state.layout_uploads - layout_start)
          << " workspace_updates=" << (state.workspace_updates - workspace_start)
          << " capacity_growth_events="
          << (state.capacity_growth_events - growth_start)
          << " gpu_allocations="
          << (gpumd_compat::gpu_vector_allocation_count() - allocation_start)
          << " gpu_allocations_cumulative="
          << (gpumd_compat::gpu_vector_allocation_count() - state.allocation_origin)
          << '\n';
  std::cout << summary.str();
  std::cout.flush();
}

}  // namespace
}  // namespace detail

void run_local_domain(
    const RunProgram& program,
    Model model,
    gpumd_compat::Box box,
    std::unique_ptr<gpumd_compat::NEP> potential,
    const DomainEligibility& eligibility,
    MpiRuntime& mpi)
{
  using gpumd_compat::NEP;
  const auto total_started = std::chrono::steady_clock::now();
  HostAtoms& identity = model.atoms;  // full replicated host model (static
                                      // metadata plus the root's output identity)
  NEP& nep = *potential;
  if (eligibility.axis < 0 || eligibility.axis > 2 || !eligibility.eligible) {
    throw std::logic_error("the domain runtime requires an eligible decomposition");
  }

  detail::DomainState state;
  state.rank = mpi.world_rank();
  state.world_size = mpi.world_size();
  state.axis = eligibility.axis;
  state.axis_length = std::abs(box.cpu_h[4 * eligibility.axis]);
  state.d_dep = eligibility.d_dep;
  state.d_coord = eligibility.d_coord;
  state.group_method_count = identity.group_labels.size();
  state.detailed_logging = detail::domain_diagnostics_enabled();
  state.timing_enabled = detail::domain_timing_enabled();
  mpi.assert_same_fingerprint(
      state.detailed_logging ? 1 : 0, "domain diagnostics configuration");
  mpi.assert_same_fingerprint(
      state.timing_enabled ? 1 : 0, "domain timing configuration");
  state.allocation_origin = gpumd_compat::gpu_vector_allocation_count();
  state.device.thermo.resize_reuse(8);
  if (mpi.is_root()) {
    std::cout << "DMGMD_DOMAIN_DIAGNOSTICS detailed="
              << (state.detailed_logging ? "on" : "off")
              << " summary=run-segment timing="
              << (state.timing_enabled ? "on" : "off") << '\n';
  }

  // Ownership is computed from the raw input coordinates (slab ownership
  // applies the same single wrap adjustment as the wrap kernel, exactly like
  // the M1 RuntimeOwnership constructor). The owned positions are then
  // wrapped ON DEVICE with the shared wrap_positions kernel so the very
  // first force sees bit-identical coordinates on every path (a host-side
  // wrap can differ by one ULP because nvcc contracts the fractional
  // round-trip with FMA while the host compiler does not).

  // Initial ownership from the replicated wrapped input (no communication:
  // every rank derives the same pure function of the same data).
  std::vector<DomainAtomRecord> owned;
  {
    const std::array<double, 9> inverse{
        box.cpu_h[9], box.cpu_h[10], box.cpu_h[11],
        box.cpu_h[12], box.cpu_h[13], box.cpu_h[14],
        box.cpu_h[15], box.cpu_h[16], box.cpu_h[17]};
    for (std::size_t slot = 0; slot < identity.counts.global_count; ++slot) {
      const double s = fractional_along_axis(
          inverse, state.axis, identity.position[slot],
          identity.position[identity.counts.global_count + slot],
          identity.position[2 * identity.counts.global_count + slot]);
      if (slab_owner_of_fractional(s, state.world_size) == state.rank) {
        DomainAtomRecord record;
        record.global_id = identity.global_id[slot];
        record.type = identity.type[slot];
        record.mass = identity.mass[slot];
        record.charge = identity.charge[slot];
        record.position = {identity.position[slot],
                           identity.position[identity.counts.global_count + slot],
                           identity.position[2 * identity.counts.global_count + slot]};
        record.velocity = {identity.velocity[slot],
                           identity.velocity[identity.counts.global_count + slot],
                           identity.velocity[2 * identity.counts.global_count + slot]};
        record.unwrapped = record.position;
        record.group_labels.resize(state.group_method_count);
        for (std::size_t method = 0; method < state.group_method_count; ++method) {
          record.group_labels[method] = identity.group_labels[method][slot];
        }
        owned.push_back(record);
      }
    }
    std::sort(owned.begin(), owned.end(),
              [](const DomainAtomRecord& a, const DomainAtomRecord& b) {
                return a.global_id < b.global_id;
              });
    // Device-side wrap of the owned positions (bit-identical to the wrap the
    // M1 path performs inside its first force). The unwrapped seed keeps the
    // raw coordinate, matching M1's enable_unwrapped timing.
    const std::size_t owned_count = owned.size();
    if (owned_count != 0) {
      std::vector<double> host(3 * owned_count);
      for (std::size_t index = 0; index < owned_count; ++index) {
        for (int axis = 0; axis < 3; ++axis) {
          host[static_cast<std::size_t>(axis) * owned_count + index] =
              owned[index].position[static_cast<std::size_t>(axis)];
        }
      }
      gpumd_compat::GPU_Vector<double> device_position(3 * owned_count);
      device_position.copy_from_host(host.data(), 3 * owned_count);
      detail::wrap_positions<<<(detail::checked_int(owned_count, "owned_count") + detail::kThreads - 1) /
                           detail::kThreads,
                       detail::kThreads>>>(
          detail::checked_int(owned_count, "owned_count"),
          detail::checked_int(owned_count, "owned_count"),
          box, device_position.data());
      detail::check_cuda(cudaGetLastError(), "wrap initial owned positions");
      device_position.copy_to_host(host.data(), 3 * owned_count);
      for (std::size_t index = 0; index < owned_count; ++index) {
        for (int axis = 0; axis < 3; ++axis) {
          owned[index].position[static_cast<std::size_t>(axis)] =
              host[static_cast<std::size_t>(axis) * owned_count + index];
        }
      }
    }
  }

  // Random velocity initialization is identical to the M1/P1 path: the root
  // fills the full model with the same rand() sequence, the host broadcast
  // replicates it, and every rank extracts its owned slice.
  if (!identity.has_input_velocity) {
    if (mpi.is_root()) {
      std::srand(static_cast<unsigned int>(
          std::chrono::system_clock::now().time_since_epoch().count()));
      detail::initialize_random_velocity(identity, 300.0, std::nullopt);
    }
    mpi.broadcast_doubles(identity.velocity.data(), identity.velocity.size());
    const std::vector<std::size_t> slot_of_gid = detail::build_slot_of_global_id(identity);
    const std::size_t global_count = identity.counts.global_count;
    for (DomainAtomRecord& record : owned) {
      const std::size_t slot = slot_of_gid[record.global_id];
      record.velocity = {identity.velocity[slot],
                         identity.velocity[global_count + slot],
                         identity.velocity[2 * global_count + slot]};
    }
  }

  // The neighbor-occupancy record is aggregated across ranks (MPI_MAX over
  // the per-rank dependency-center maxima) and written once by rank 0 in the
  // legacy format; rank 0 is the only writer of the job directory.
  nep.neighbor_record_sink = [&mpi, &nep, &state](int call_index, int radial_actual,
                                                  int angular_actual) {
    double radial = static_cast<double>(radial_actual);
    double angular = static_cast<double>(angular_actual);
    radial = mpi.allreduce_max_host(radial, state.active_volume());
    angular = mpi.allreduce_max_host(angular, state.active_volume());
    if (mpi.is_root()) {
      std::ofstream output_file("neighbor.out", std::ios_base::app);
      output_file << "Neighbor info at step " << call_index << ": "
                  << "radial(max=" << nep.params().MN_radial
                  << ",actual=" << static_cast<int>(radial) << "), angular(max="
                  << nep.params().MN_angular << ",actual=" << static_cast<int>(angular)
                  << ")." << std::endl;
      output_file.close();
    }
  };

  detail::RankIoIsolation rank_io(mpi);

  // Initial coverage proof: the per-rank owned counts must cover the global
  // system exactly once (control-plane, outside the step records).
  {
    const std::vector<int> counts =
        mpi.allgather_int(static_cast<int>(owned.size()), ByteClass::control,
                          state.uncounted_volume);
    std::size_t total = 0;
    for (int count : counts) total += static_cast<std::size_t>(count);
    if (total != identity.counts.global_count) {
      throw std::runtime_error("initial domain ownership does not cover the global system");
    }
    if (mpi.is_root()) {
      std::cout << "DMGMD_CENTER_PARTITION global_count=" << identity.counts.global_count
                << " ranks=" << mpi.world_size() << " partition=spatial-slab axis="
                << (state.axis == 0 ? "x" : state.axis == 1 ? "y" : "z")
                << " slab_rule=equal-width-fractional"
                << " missing=0 overlapping=0 owned_output_coverage=complete"
                << " nep_kernel_centers=local-domain-force-centers"
                << " nep_N1_N2_shard_complete=true"
                << " reason=two-hop-coordinate-halo\n";
      for (int source = 0; source < mpi.world_size(); ++source) {
        std::cout << "DMGMD_CENTER_OWNERSHIP rank=" << source
                  << " owned_count=" << counts[static_cast<std::size_t>(source)] << '\n';
      }
      std::cout.flush();
    }
  }

  // Initial layout + halo + workspace (step=0 in the layout records).
  {
    CommunicationVolume startup;
    detail::exchange_halo_membership(
        state, std::move(owned), nep, box, mpi, startup, 0);
  }

  bool potential_seen = false;
  double time_step = 1.0 / gpumd_compat::TIME_UNIT_CONVERSION;
  std::optional<double> maximum_distance;
  std::optional<EnsembleCommand> ensemble;
  std::optional<CorrectVelocityCommand> velocity_correction;
  std::vector<detail::Measurement> measurements;
  double global_time = 0.0;
  std::uint64_t global_step = 0;
  std::uint64_t run_sequence = 0;
  std::string potential_filename;
  for (const Command& command : program.commands) {
    if (const auto* potential_command = std::get_if<PotentialCommand>(&command.data)) {
      potential_filename = potential_command->filename;
      break;
    }
  }

  for (const Command& command : program.commands) {
    try {
      if (const auto* potential_command = std::get_if<PotentialCommand>(&command.data)) {
        if (potential_seen || potential_command->filename != potential_filename) {
          throw std::runtime_error(
              "multiple potentials are not supported by the domain runtime");
        }
        potential_seen = true;
      } else if (const auto* velocity = std::get_if<VelocityCommand>(&command.data)) {
        if (!identity.has_input_velocity) {
          if (mpi.is_root()) {
            detail::initialize_random_velocity(identity, velocity->temperature, velocity->seed);
          }
          mpi.broadcast_doubles(identity.velocity.data(), identity.velocity.size());
          // Extract the owned slice and upload it into the owned prefix.
          const std::vector<std::size_t> slot_of_gid =
              detail::build_slot_of_global_id(identity);
          const std::size_t global_count = identity.counts.global_count;
          for (DomainAtomRecord& record : state.layout.owned) {
            const std::size_t slot = slot_of_gid[record.global_id];
            record.velocity = {identity.velocity[slot],
                               identity.velocity[global_count + slot],
                               identity.velocity[2 * global_count + slot]};
          }
          const std::size_t owned_count = state.owned_count();
          const std::size_t local = state.local_count();
          for (int axis = 0; axis < 3; ++axis) {
            std::vector<double> slice(owned_count);
            for (std::size_t index = 0; index < owned_count; ++index) {
              slice[index] = state.layout.owned[index].velocity[static_cast<std::size_t>(axis)];
            }
            if (owned_count != 0) {
              state.device.velocity.copy_from_host(
                  slice.data(), owned_count, static_cast<int>(axis * local));
            }
          }
        }
      } else if (const auto* step = std::get_if<TimeStepCommand>(&command.data)) {
        time_step = step->femtoseconds / gpumd_compat::TIME_UNIT_CONVERSION;
        maximum_distance = step->maximum_distance_angstrom;
      } else if (const auto* selected = std::get_if<EnsembleCommand>(&command.data)) {
        ensemble = *selected;
      } else if (const auto* correction =
                     std::get_if<CorrectVelocityCommand>(&command.data)) {
        velocity_correction = *correction;
      } else if (const auto* dump = std::get_if<DumpThermoCommand>(&command.data)) {
        measurements.emplace_back(*dump);
      } else if (const auto* dump = std::get_if<DumpXyzCommand>(&command.data)) {
        if (dump->quantities.unwrapped_position && !state.track_unwrapped) {
          // Same semantics as M1: unwrapped tracking starts from the current
          // wrapped positions of the owned atoms. The device arrays must be
          // sized before the dynamics download reads them.
          state.track_unwrapped = true;
          if (state.local_count() != 0) {
            state.device.unwrapped.resize_reuse(3 * state.local_count());
            state.device.previous_position.resize_reuse(3 * state.local_count());
          }
          // The raw input coordinates are the pre-run seed (the M1 path
          // enables unwrapped tracking before its first wrap); capture them
          // before the dynamics download, whose freshly-resized device
          // unwrapped buffer holds no data yet. After steps have run, the
          // seed is the current wrapped position, exactly like M1.
          std::vector<std::array<double, 3>> seed;
          seed.reserve(state.layout.owned.size());
          for (const DomainAtomRecord& record : state.layout.owned) {
            seed.push_back(record.unwrapped);
          }
          detail::download_owned_dynamics(state);
          for (std::size_t index = 0; index < state.layout.owned.size(); ++index) {
            state.layout.owned[index].unwrapped =
                state.executed_steps != 0 ? state.layout.owned[index].position
                                          : seed[index];
          }
          const std::uint64_t allocations_before =
              gpumd_compat::gpu_vector_allocation_count();
          detail::upload_domain_layout(
              state.layout, state.plan, state.track_unwrapped, state.device, false);
          ++state.layout_uploads;
          if (gpumd_compat::gpu_vector_allocation_count() != allocations_before) {
            ++state.capacity_growth_events;
          }
        }
        measurements.emplace_back(*dump);
      } else if (const auto* dump = std::get_if<DumpRestartCommand>(&command.data)) {
        measurements.emplace_back(*dump);
      } else if (const auto* run = std::get_if<RunCommand>(&command.data)) {
        if (!potential_seen) throw std::runtime_error("run requires a preceding potential command");
        if (!ensemble) throw std::runtime_error("run requires a preceding ensemble command");
        detail::check_cuda(cudaDeviceSynchronize(), "synchronize before timed run segment");
        mpi.barrier();
        const auto segment_started = std::chrono::steady_clock::now();
        detail::run_domain_segment(
            run_sequence, run->steps, time_step, maximum_distance, *ensemble, velocity_correction,
            measurements, global_time, global_step, box, identity, nep, state, mpi);
        detail::check_cuda(cudaDeviceSynchronize(), "synchronize after timed run segment");
        const double segment_seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - segment_started).count();
        mpi.log_timing(
            "run", run_sequence, static_cast<std::uint64_t>(run->steps),
            identity.counts.global_count, segment_seconds);
        ++run_sequence;
        measurements.clear();
        velocity_correction.reset();
        maximum_distance.reset();
      }
    } catch (const InputError&) {
      throw;
    } catch (const std::exception& error) {
      throw InputError(command.source, error.what());
    }
  }
  if (!measurements.empty()) {
    throw InputError(SourceLocation{"run.in", 0, {}},
                     "dump command is not followed by run");
  }
  detail::check_cuda(cudaDeviceSynchronize(), "finish local-domain MPI run");
  rank_io.finish();
  const double total_seconds = std::chrono::duration<double>(
      std::chrono::steady_clock::now() - total_started).count();
  mpi.log_timing(
      "total", 0, global_step, identity.counts.global_count, total_seconds);
}

}  // namespace dmgmd
