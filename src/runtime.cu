#include "dmgmd/runtime.hpp"

// The NEP force path is the DMG-MD-owned replication of the minimal GPUMD
// subset in src/gpumd_compat (copied from the pinned reference commit
// 9d23496e41319b9e2af5221a7df6285387401d1e, numerics unchanged).  DMG-MD
// must never include or link ../gpumd-reference directly.
//
// File layout since M2a: this translation unit keeps the M1/P1
// replicated-full runtime and the GPUMD-compatible output formatters shared
// with the local-domain runtime; the pieces both runtimes need live in
// src/runtime_internal.hpp, and the M2a local-domain runtime lives in
// src/domain_runtime.cu. run_replicated() is the mode dispatcher
// (docs/plans/domain-decomposition.md section 3.3).
#include "runtime_internal.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

namespace dmgmd {
namespace detail {

using gpumd_compat::NEP;
using gpumd_compat::Potential;
// K_B is a #define in gpumd_compat/common.cuh and needs no using-declaration.
using gpumd_compat::PRESSURE_UNIT_CONVERSION;
using gpumd_compat::TIME_UNIT_CONVERSION;

namespace {

class DeviceAtoms {
 public:
  explicit DeviceAtoms(const HostAtoms& host)
      : counts(host.counts),
        global_id(counts.local_count()),
        type(counts.local_count()),
        mass(counts.local_count()),
        charge(counts.local_count()),
        position(3 * counts.local_count()),
        velocity(3 * counts.local_count()),
        force(3 * counts.local_count(), 0.0),
        potential(counts.local_count(), 0.0),
        virial(9 * counts.local_count(), 0.0)
  {
    host.validate();
    static_assert(sizeof(std::uint64_t) == sizeof(unsigned long long));
    const std::vector<unsigned long long> ids(
        host.global_id.begin(), host.global_id.end());
    global_id.copy_from_host(ids.data());
    type.copy_from_host(host.type.data());
    mass.copy_from_host(host.mass.data());
    charge.copy_from_host(host.charge.data());
    position.copy_from_host(host.position.data());
    velocity.copy_from_host(host.velocity.data());
  }

  void upload_velocity(const std::vector<double>& values)
  {
    if (values.size() != velocity.size()) {
      throw std::logic_error("velocity upload does not match local stride");
    }
    velocity.copy_from_host(values.data());
  }

  void enable_unwrapped()
  {
    if (unwrapped.size() != 0) {
      return;
    }
    unwrapped.resize(position.size());
    previous_position.resize(position.size());
    unwrapped.copy_from_device(position.data());
  }

  [[nodiscard]] bool has_unwrapped() const noexcept { return unwrapped.size() != 0; }

  AtomCounts counts;
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
};

// Persistent M1 spatial ownership state plus its device mirrors and the
// indexed communication plan. The ownership epoch survives across run
// segments (never resets to a balanced range). The per-step migration
// protocol is steps 6-7 of docs/standards/replicated-mpi.md:
//   * recompute the owner map from the wrapped replicated positions;
//   * verify every rank derived the identical map (fixed-size Allreduce,
//     before ANY collective whose counts depend on the new epoch);
//   * if the map changed, migrate the authoritative dynamic state (velocity,
//     and unwrapped when enabled) with the OLD ownership, so the new owner
//     receives the latest half-step values instead of M0's stale replicas;
//   * only then adopt the new map (epoch++, plan rebuild, rank 0 log).
// Position is already replicated by the step-4 Allgatherv; type/mass/group/
// global_id are static replicated data that M1 never migrates.
class RuntimeOwnership {
 public:
  RuntimeOwnership(
      const HostAtoms& identity,
      const Box& box,
      MpiRuntime& mpi)
      : global_id_(identity.global_id),
        host_position_(identity.position),
        rank_(mpi.world_rank()),
        world_size_(mpi.world_size())
  {
    const std::size_t global_count = identity.counts.global_count;
    if (world_size_ == 1) {
      // P=1 fully degenerates to the M0 path: rank 0 owns every slot, the
      // map can never change, and no migration communication is added.
      partition_axis_ = -1;
      adopt(SpatialOwnership::trivial(global_count, global_id_, world_size_));
      return;
    }
    if (!box.is_orthogonal) {
      throw std::runtime_error(
          "M1 spatial slab ownership supports only orthogonal boxes; a "
          "triclinic lattice must be reported before any decomposition");
    }
    if (box.pbc_x != 1 || box.pbc_y != 1 || box.pbc_z != 1) {
      throw std::runtime_error(
          "M1 spatial slab ownership requires periodicity in all three "
          "directions; a non-periodic direction is unsupported");
    }
    partition_axis_ = longest_box_axis({box.cpu_h[0], box.cpu_h[1], box.cpu_h[2],
                                        box.cpu_h[3], box.cpu_h[4], box.cpu_h[5],
                                        box.cpu_h[6], box.cpu_h[7], box.cpu_h[8]});
    std::copy(box.cpu_h + 9, box.cpu_h + 18, inverse_box_.begin());
    // Initial map from the input positions. slab_owner_of_fractional applies
    // the wrap_positions <0/>1 adjustment, so unwrapped input coordinates
    // map to the same owner as their wrapped form.
    adopt(SpatialOwnership(compute_owner_map(), global_id_, rank_, world_size_));
  }

  [[nodiscard]] const SpatialOwnership& current() const noexcept { return *current_; }
  [[nodiscard]] const IndexedOwnershipPlan& plan() const noexcept { return plan_; }
  [[nodiscard]] int partition_axis() const noexcept { return partition_axis_; }

  // Steps 6-7 of the per-step protocol. `step` is the 1-based global step for
  // the ownership epoch log record.
  void recompute_and_commit(
      DeviceAtoms& atoms,
      MpiRuntime& mpi,
      CommunicationVolume& communication,
      std::uint64_t step)
  {
    if (partition_axis_ < 0) {
      // P=1: the map is constant, so there is nothing to recompute, verify
      // or migrate. This keeps the single-rank path byte-identical to M0,
      // including its communication records.
      return;
    }
    atoms.position.copy_to_host(host_position_.data());
    bool locally_valid = true;
    std::optional<SpatialOwnership> next;
    try {
      next.emplace(compute_owner_map(), global_id_, rank_, world_size_);
    } catch (const std::exception& error) {
      // The failure is derived from replicated state, so every rank fails
      // identically; the flag still routes it through the collective
      // handshake below in case a local bug ever makes it asymmetric. The
      // specific reason is preserved on stderr before that handshake.
      locally_valid = false;
      mpi.report_error(
          "ownership",
          std::string("spatial ownership recomputation failed: ") + error.what());
    }
    // Cross-rank consistency gate BEFORE any collective whose counts depend
    // on the new epoch. Throws identically on every rank.
    mpi.assert_same_ownership_map(
        next ? next->map_hash() : 0, locally_valid, communication);
    if (next->same_partition(*current_)) {
      return;  // Epoch unchanged: reuse the pack/unpack plan verbatim.
    }
    // The owner map changed: hand the latest half-step dynamic state of the
    // old owners to every rank while the OLD plan is still valid.
    const std::size_t stride = atoms.counts.local_count();
    mpi.allgather_indexed_device_soa(
        atoms.velocity.data(), 3, stride, plan_, communication);
    if (atoms.has_unwrapped()) {
      mpi.allgather_indexed_device_soa(
          atoms.unwrapped.data(), 3, stride, plan_, communication);
    }
    log_epoch_change(step, *next);
    adopt(std::move(*next));
  }

 private:
  [[nodiscard]] std::vector<int> compute_owner_map() const
  {
    // During construction the ownership set does not exist yet; the global
    // count then comes from the static identity.
    const std::size_t global_count = current_.has_value()
                                         ? current_->global_count()
                                         : global_id_.size();
    const std::size_t stride = host_position_.size() / 3;
    if (stride != global_count) {
      throw std::logic_error("replicated position stride does not match global_count");
    }
    std::vector<int> owners(global_count);
    for (std::size_t slot = 0; slot < global_count; ++slot) {
      const double s = fractional_along_axis(
          inverse_box_, partition_axis_, host_position_[slot],
          host_position_[stride + slot], host_position_[2 * stride + slot]);
      owners[slot] = slab_owner_of_fractional(s, world_size_);
    }
    return owners;
  }

  // Rebuilds all derived state (device mirrors + plan) from the new map and
  // advances the epoch. Callers have already synced the dynamic state under
  // the old ownership, so the switch is atomic from the step's perspective.
  void adopt(SpatialOwnership&& next)
  {
    current_ = std::move(next);
    ++epoch_;
    const std::size_t global_count = current_->global_count();

    // Gathered stream layout: rank r's owned slots in global_id order,
    // concatenated in rank order. Identical on every rank because the owner
    // map and global_id permutation are replicated.
    plan_.host_scatter_slots.clear();
    plan_.host_scatter_slots.reserve(global_count);
    const auto& slot_of_id = current_->slot_of_global_id();
    const auto& owners = current_->owner_by_slot();
    std::vector<std::vector<int>> per_rank(static_cast<std::size_t>(world_size_));
    for (std::uint64_t id = 0; id < global_count; ++id) {
      const std::size_t slot = slot_of_id[static_cast<std::size_t>(id)];
      per_rank[static_cast<std::size_t>(owners[slot])].push_back(
          static_cast<int>(slot));
    }
    plan_.atom_counts.clear();
    plan_.atom_displacements.clear();
    int displacement = 0;
    for (int source = 0; source < world_size_; ++source) {
      auto& section = per_rank[static_cast<std::size_t>(source)];
      plan_.atom_counts.push_back(static_cast<int>(section.size()));
      plan_.atom_displacements.push_back(displacement);
      displacement += static_cast<int>(section.size());
      plan_.host_scatter_slots.insert(
          plan_.host_scatter_slots.end(), section.begin(), section.end());
    }
    plan_.global_count = global_count;
    plan_.owned_count = current_->owned_count();
    plan_.epoch = epoch_;

    const std::vector<int> owned_list(
        current_->owned_indices().begin(), current_->owned_indices().end());
    device_owned_indices_.resize(std::max<std::size_t>(owned_list.size(), 1));
    if (!owned_list.empty()) {
      device_owned_indices_.copy_from_host(owned_list.data(), owned_list.size());
    }
    device_scatter_slots_.resize(std::max<std::size_t>(global_count, 1));
    device_scatter_slots_.copy_from_host(
        plan_.host_scatter_slots.data(), plan_.host_scatter_slots.size());
    plan_.device_owned_indices = device_owned_indices_.data();
    plan_.device_scatter_slots = device_scatter_slots_.data();
  }

  // Rank 0 log record for every ownership epoch change, listing up to 64
  // expected owner transitions (global_id:old->new) for migration fixtures.
  void log_epoch_change(std::uint64_t step, const SpatialOwnership& next) const
  {
    if (rank_ != 0) return;
    const auto& old_owners = current_->owner_by_slot();
    const auto& new_owners = next.owner_by_slot();
    const auto& global_id = current_->global_id();
    std::size_t changed = 0;
    std::string transitions;
    constexpr std::size_t kMaxLoggedTransitions = 64;
    bool truncated = false;
    for (std::size_t slot = 0; slot < old_owners.size(); ++slot) {
      if (old_owners[slot] == new_owners[slot]) continue;
      ++changed;
      if (changed <= kMaxLoggedTransitions) {
        if (!transitions.empty()) transitions += ',';
        transitions += std::to_string(global_id[slot]) + ':' +
                       std::to_string(old_owners[slot]) + "->" +
                       std::to_string(new_owners[slot]);
      } else {
        truncated = true;
      }
    }
    std::cout << "DMGMD_OWNERSHIP_EPOCH step=" << step << " epoch=" << epoch_ + 1
              << " changed_atoms=" << changed << " owned_sum=" << next.global_count()
              << " transitions=\"" << transitions << "\""
              << (truncated ? " truncated=true" : " truncated=false") << '\n';
    std::cout.flush();
  }

  std::vector<std::uint64_t> global_id_;      // static identity, by slot
  std::vector<double> host_position_;         // scratch for map recomputes
  std::array<double, 9> inverse_box_{};
  int partition_axis_ = -1;                   // -1: P=1 degenerate map
  int rank_ = 0;
  int world_size_ = 1;
  std::uint64_t epoch_ = 0;
  std::optional<SpatialOwnership> current_;
  GPU_Vector<int> device_owned_indices_;  // this rank's owned slots
  GPU_Vector<int> device_scatter_slots_;  // gathered atom index -> slot
  IndexedOwnershipPlan plan_;
};

HostSnapshot gather_owned_snapshot(
    DeviceAtoms& atoms,
    const IndexedOwnershipPlan& plan,
    MpiRuntime& mpi,
    CommunicationVolume& communication)
{
  // Replicated device arrays are inputs; the snapshot is reconstructed only
  // from uniquely owned slots, preserving global atom order on rank 0 through
  // the plan's scatter map.
  const std::size_t local = atoms.counts.local_count();
  HostSnapshot snapshot;
  if (mpi.is_root()) {
    snapshot.global_id.resize(local);
    atoms.global_id.copy_to_host(snapshot.global_id.data());
  }
  snapshot.position = mpi.gather_indexed_device_soa_to_root(
      atoms.position.data(), 3, local, plan, communication);
  snapshot.velocity = mpi.gather_indexed_device_soa_to_root(
      atoms.velocity.data(), 3, local, plan, communication);
  snapshot.force = mpi.gather_indexed_device_soa_to_root(
      atoms.force.data(), 3, local, plan, communication);
  snapshot.potential = mpi.gather_indexed_device_soa_to_root(
      atoms.potential.data(), 1, local, plan, communication);
  snapshot.virial = mpi.gather_indexed_device_soa_to_root(
      atoms.virial.data(), 9, local, plan, communication);
  if (atoms.has_unwrapped()) {
    snapshot.unwrapped = mpi.gather_indexed_device_soa_to_root(
        atoms.unwrapped.data(), 3, local, plan, communication);
  }
  return snapshot;
}

// M1 kernels iterate an owned slot index list instead of a contiguous
// [begin,end) range: spatial slab ownership is an arbitrary subset of the
// replicated slots. The per-atom arithmetic is unchanged, so a P=1 run whose
// index list is [0,N) reproduces the M0 results bit-for-bit.
__global__ void velocity_verlet(
    bool first_half,
    const int* owned_indices,
    int owned_count,
    int stride,
    double time_step,
    const double* mass,
    double* position,
    double* velocity,
    const double* force)
{
  const int item = blockIdx.x * blockDim.x + threadIdx.x;
  if (item >= owned_count) {
    return;
  }
  const int atom = owned_indices[item];
  const double half = time_step * 0.5;
  const double inverse_mass = 1.0 / mass[atom];
  double vx = velocity[atom] + force[atom] * inverse_mass * half;
  double vy = velocity[stride + atom] + force[stride + atom] * inverse_mass * half;
  double vz = velocity[2 * stride + atom] + force[2 * stride + atom] * inverse_mass * half;
  velocity[atom] = vx;
  velocity[stride + atom] = vy;
  velocity[2 * stride + atom] = vz;
  if (first_half) {
    position[atom] += vx * time_step;
    position[stride + atom] += vy * time_step;
    position[2 * stride + atom] += vz * time_step;
  }
}

__global__ void update_unwrapped(
    const int* owned_indices,
    int owned_count,
    int stride,
    const double* position,
    const double* previous,
    double* unwrapped)
{
  const int item = blockIdx.x * blockDim.x + threadIdx.x;
  if (item >= owned_count) {
    return;
  }
  const int atom = owned_indices[item];
  for (int axis = 0; axis < 3; ++axis) {
    const int index = axis * stride + atom;
    unwrapped[index] += position[index] - previous[index];
  }
}

__global__ void find_owned_thermo_sums(
    const int* owned_indices,
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
    const int item = tid + patch * kThermoThreads;
    if (item >= owned_count) {
      continue;
    }
    const int atom = owned_indices[item];
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

__global__ void scale_owned_velocity(
    const int* owned_indices,
    int owned_count,
    int stride,
    double factor,
    double* velocity)
{
  const int item = blockIdx.x * blockDim.x + threadIdx.x;
  if (item >= owned_count) {
    return;
  }
  const int atom = owned_indices[item];
  velocity[atom] *= factor;
  velocity[stride + atom] *= factor;
  velocity[2 * stride + atom] *= factor;
}

ThermoState compute_thermo(
    DeviceAtoms& atoms,
    const Box& box,
    const RuntimeOwnership& ownership,
    GPU_Vector<double>& device_thermo,
    MpiRuntime& mpi,
    CommunicationVolume& communication)
{
  const int owned_count = checked_int(ownership.current().owned_count(), "owned_count");
  const int stride = checked_int(atoms.counts.local_count(), "local_count");
  find_owned_thermo_sums<<<8, kThermoThreads>>>(
      ownership.plan().device_owned_indices, owned_count, stride, atoms.mass.data(),
      atoms.potential.data(), atoms.velocity.data(), atoms.virial.data(),
      device_thermo.data());
  check_cuda(cudaGetLastError(), "launch owned thermo reduction");
  mpi.allreduce_sum_device(device_thermo.data(), 8, communication);
  normalize_global_thermo<<<1, 8>>>(
      checked_int(atoms.counts.global_count, "global_count"),
      box.get_volume(), device_thermo.data());
  check_cuda(cudaGetLastError(), "normalize global thermo");
  ThermoState result;
  device_thermo.copy_to_host(result.values.data());
  return result;
}

class NepForce {
 public:
  // Takes over the exactly-once parsed NEP (M2a contract: the potential
  // parameters are never re-parsed) and sizes the replicated-full workspace
  // for the global atom count.
  NepForce(std::unique_ptr<NEP> potential, const AtomCounts& counts)
      : nep_(std::move(potential))
  {
    const int local = checked_int(counts.local_count(), "local_count");
    nep_->allocate_workspace(local);
    // See the NEP completeness proof in docs/standards/replicated-mpi.md. The
    // pinned ordinary NEP implementation reads Fp(n2) and reverse directed
    // partials belonging to neighboring centers. Merely assigning a rank-local
    // N1/N2 leaves those arrays incomplete. Until phase-level intermediate
    // exchange exists, every rank evaluates full NEP scratch and the runtime
    // grants authority only to its M1 spatial-ownership outputs.
    nep_->N1 = 0;
    nep_->N2 = local;
  }

  void compute(Box& box, DeviceAtoms& atoms)
  {
    const int local = checked_int(atoms.counts.local_count(), "local_count");
    box.set_is_orthogonal();
    wrap_positions<<<(local + kThreads - 1) / kThreads, kThreads>>>(
        local, local, box, atoms.position.data());
    clear_owned_properties<<<(local + kThreads - 1) / kThreads, kThreads>>>(
        0, local, local, atoms.force.data(), atoms.potential.data(), atoms.virial.data());
    check_cuda(cudaGetLastError(), "prepare NEP force buffers");
    nep_->compute(box, atoms.type, atoms.position, atoms.potential, atoms.force,
                  atoms.virial);
  }

 private:
  std::unique_ptr<NEP> nep_;
};

std::vector<std::size_t> all_owned_indices(const HostAtoms& atoms)
{
  std::vector<std::size_t> result(atoms.counts.owned_count);
  for (std::size_t index = 0; index < result.size(); ++index) result[index] = index;
  return result;
}

double adaptive_time_step(
    DeviceAtoms& atoms,
    const RuntimeOwnership& ownership,
    double initial_time_step,
    const std::optional<double>& maximum_distance,
    MpiRuntime& mpi,
    CommunicationVolume& communication)
{
  if (!maximum_distance) return initial_time_step;
  std::vector<double> velocity(atoms.velocity.size());
  atoms.velocity.copy_to_host(velocity.data());
  const std::size_t stride = atoms.counts.local_count();
  double maximum_squared = 0.0;
  for (std::size_t slot : ownership.current().owned_indices()) {
    const double vx = velocity[slot];
    const double vy = velocity[stride + slot];
    const double vz = velocity[2 * stride + slot];
    maximum_squared = std::max(maximum_squared, vx * vx + vy * vy + vz * vz);
  }
  maximum_squared = mpi.allreduce_max_host(maximum_squared, communication);
  const double limited = maximum_squared == 0.0
                             ? initial_time_step
                             : *maximum_distance / std::sqrt(maximum_squared);
  return limited < initial_time_step ? limited : initial_time_step;
}

void correct_device_velocity(
    DeviceAtoms& device,
    const HostAtoms& identity,
    const CorrectVelocityCommand& command,
    MpiRuntime& mpi,
    CommunicationVolume& communication)
{
  std::vector<double> position(device.position.size());
  std::vector<double> velocity(device.velocity.size());
  if (mpi.is_root()) {
    device.position.copy_to_host(position.data());
    device.velocity.copy_to_host(velocity.data());
  }
  if (mpi.is_root() && !command.grouping_method) {
    correct_velocity_subset(identity.mass, position, velocity, all_owned_indices(identity));
  } else if (mpi.is_root()) {
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
      correct_velocity_subset(identity.mass, position, velocity, subset);
    }
  }
  if (mpi.is_root()) device.upload_velocity(velocity);
  mpi.broadcast_device(
      device.velocity.data(), device.velocity.size(), communication);
}

void launch_velocity_verlet(
    DeviceAtoms& atoms,
    const RuntimeOwnership& ownership,
    bool first_half,
    double time_step)
{
  const int owned_count = checked_int(ownership.current().owned_count(), "owned_count");
  if (owned_count == 0) return;  // An empty spatial slab integrates nothing.
  const int stride = checked_int(atoms.counts.local_count(), "local_count");
  velocity_verlet<<<(owned_count + kThreads - 1) / kThreads, kThreads>>>(
      first_half, ownership.plan().device_owned_indices, owned_count, stride,
      time_step, atoms.mass.data(), atoms.position.data(), atoms.velocity.data(),
      atoms.force.data());
  if (first_half && atoms.has_unwrapped()) {
    update_unwrapped<<<(owned_count + kThreads - 1) / kThreads, kThreads>>>(
        ownership.plan().device_owned_indices, owned_count, stride,
        atoms.position.data(), atoms.previous_position.data(),
        atoms.unwrapped.data());
  }
  check_cuda(cudaGetLastError(), first_half ? "velocity-Verlet first half"
                                            : "velocity-Verlet second half");
}

void run_segment(
    int steps,
    double base_time_step,
    const std::optional<double>& maximum_distance,
    const EnsembleCommand& ensemble,
    const std::optional<CorrectVelocityCommand>& velocity_correction,
    const std::vector<Measurement>& measurements,
    double& global_time,
    std::uint64_t& global_step,
    Box& box,
    HostAtoms& identity,
    DeviceAtoms& atoms,
    NepForce& force,
    RuntimeOwnership& ownership,
    MpiRuntime& mpi)
{
  // Per-step protocol (docs/standards/replicated-mpi.md, M1): integrate the
  // currently owned slots, allgather replicated coordinates, evaluate full
  // NEP scratch, recompute spatial ownership and migrate the dynamic state
  // under the old ownership, then integrate/thermo/output under the new
  // ownership. Replicated velocity is not refreshed every step (M0): the only
  // remaining consumers of non-owned velocity are correct_velocity (restored
  // here on trigger steps) and ownership migration itself (which syncs the
  // authoritative half-step velocity before a new owner takes over).
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

  GPU_Vector<double> device_thermo(8);
  force.compute(box, atoms);
  for (int step = 0; step < steps; ++step) {
    CommunicationVolume communication;
    // 1. correct_velocity trigger: restore the replicated velocity under the
    // CURRENT ownership so the root's CPU correction reads fresh values.
    if (velocity_correction && step % velocity_correction->interval == 0) {
      mpi.allgather_indexed_device_soa(
          atoms.velocity.data(), 3, atoms.counts.local_count(),
          ownership.plan(), communication);
      correct_device_velocity(atoms, identity, *velocity_correction, mpi, communication);
    }
    // 2. Adaptive timestep scans only the currently owned slots.
    const double time_step = adaptive_time_step(
        atoms, ownership, base_time_step, maximum_distance, mpi, communication);
    global_time += time_step;
    if (atoms.has_unwrapped()) {
      atoms.previous_position.copy_from_device(atoms.position.data());
    }
    // 3. VV first half + position/unwrapped update: current owned slots.
    launch_velocity_verlet(atoms, ownership, true, time_step);
    // 4. Indexed position Allgatherv restores the replicated state.
    mpi.allgather_indexed_device_soa(
        atoms.position.data(), 3, atoms.counts.local_count(),
        ownership.plan(), communication);
    // 5. Full replicated NEP (wrap + clear + compute), unchanged from M0.
    force.compute(box, atoms);
    // 6-7. Recompute the spatial owner map from the wrapped positions,
    // verify it matches on every rank, migrate velocity/unwrapped under the
    // OLD ownership, and only then adopt the new ownership (epoch++).
    ownership.recompute_and_commit(atoms, mpi, communication, global_step + 1);
    // 8. VV second half, thermo, Berendsen and output gather run under the
    // NEW ownership: an atom that crossed a slab boundary finishes its step
    // with its new owner, exactly once.
    launch_velocity_verlet(atoms, ownership, false, time_step);
    const ThermoState thermo = compute_thermo(
        atoms, box, ownership, device_thermo, mpi, communication);

    if (ensemble.kind == EnsembleKind::nvt_ber) {
      const double fraction = static_cast<double>(step) / static_cast<double>(steps);
      const double target = ensemble.initial_temperature +
                            (ensemble.final_temperature - ensemble.initial_temperature) * fraction;
      const double coupling = 1.0 / ensemble.temperature_coupling;
      if (coupling > 1.0e-5) {
        const double factor = std::sqrt(1.0 + coupling * (target / thermo.values[0] - 1.0));
        const int owned_count = checked_int(ownership.current().owned_count(), "owned_count");
        if (owned_count != 0) {
          const int stride = checked_int(atoms.counts.local_count(), "local_count");
          scale_owned_velocity<<<(owned_count + kThreads - 1) / kThreads, kThreads>>>(
              ownership.plan().device_owned_indices, owned_count, stride, factor,
              atoms.velocity.data());
        }
        check_cuda(cudaGetLastError(), "Berendsen velocity scaling");
      }
    }
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
      snapshot = gather_owned_snapshot(atoms, ownership.plan(), mpi, communication);
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
    ++global_step;
    mpi.log_step_communication(global_step, communication);
  }
}

// The M1 replicated-full runtime: also the P=1 path and the M2a fallback for
// inputs that do not meet the local-domain eligibility criteria. Behavior is
// unchanged from the M1 milestone; only the NEP construction moved to the
// dispatcher (parsed exactly once, workspace sized here).
void run_m1_replicated(
    const RunProgram& program,
    Model model,
    gpumd_compat::Box box,
    std::unique_ptr<NEP> potential,
    const std::string& potential_filename,
    MpiRuntime& mpi)
{
  const auto total_started = std::chrono::steady_clock::now();
  // The full Model is replicated input. The M1 spatial ownership set below is
  // the only authority for integration, thermodynamics and output; data stays
  // fully replicated (no ghost slots, no local compaction) and NEP stays
  // replicated-full. Ownership migrates logically between epochs; atoms are
  // never inserted or removed from the replicated slots.
  // Keep this boundary aligned with docs/standards/replicated-mpi.md.
  if (model.atoms.counts.ghost_count != 0 ||
      model.atoms.counts.owned_count != model.atoms.counts.global_count) {
    throw std::logic_error(
        "replicated initialization requires a complete input model and no ghosts");
  }
  // P=1 degenerates to the constant rank-0 map (M0 path, byte-identical);
  // P>1 requires an orthogonal fully-periodic box and partitions along the
  // longest edge. The ownership epoch persists across every run segment.
  RuntimeOwnership ownership(model.atoms, box, mpi);
  mpi.verify_and_log_center_partition(ownership.current(), ownership.partition_axis());

  if (!model.atoms.has_input_velocity) {
    if (mpi.is_root()) {
      std::srand(static_cast<unsigned int>(
          std::chrono::system_clock::now().time_since_epoch().count()));
      initialize_random_velocity(model.atoms, 300.0, std::nullopt);
    }
    mpi.broadcast_doubles(model.atoms.velocity.data(), model.atoms.velocity.size());
  }

  DeviceAtoms atoms(model.atoms);
  RankIoIsolation rank_io(mpi);
  auto force = std::make_unique<NepForce>(std::move(potential), atoms.counts);
  bool potential_seen = false;
  double time_step = 1.0 / TIME_UNIT_CONVERSION;
  std::optional<double> maximum_distance;
  std::optional<EnsembleCommand> ensemble;
  std::optional<CorrectVelocityCommand> velocity_correction;
  std::vector<Measurement> measurements;
  double global_time = 0.0;
  std::uint64_t global_step = 0;
  std::uint64_t run_sequence = 0;

  for (const Command& command : program.commands) {
    try {
      if (const auto* potential_command = std::get_if<PotentialCommand>(&command.data)) {
        if (potential_seen || potential_command->filename != potential_filename) {
          throw std::runtime_error(
              "multiple potentials are not supported by the replicated runtime");
        }
        potential_seen = true;
      } else if (const auto* velocity = std::get_if<VelocityCommand>(&command.data)) {
        if (!model.atoms.has_input_velocity) {
          if (mpi.is_root()) {
            initialize_random_velocity(model.atoms, velocity->temperature, velocity->seed);
          }
          mpi.broadcast_doubles(model.atoms.velocity.data(), model.atoms.velocity.size());
          atoms.upload_velocity(model.atoms.velocity);
        }
      } else if (const auto* step = std::get_if<TimeStepCommand>(&command.data)) {
        time_step = step->femtoseconds / TIME_UNIT_CONVERSION;
        maximum_distance = step->maximum_distance_angstrom;
      } else if (const auto* selected = std::get_if<EnsembleCommand>(&command.data)) {
        ensemble = *selected;
      } else if (const auto* correction =
                     std::get_if<CorrectVelocityCommand>(&command.data)) {
        velocity_correction = *correction;
      } else if (const auto* dump = std::get_if<DumpThermoCommand>(&command.data)) {
        measurements.emplace_back(*dump);
      } else if (const auto* dump = std::get_if<DumpXyzCommand>(&command.data)) {
        if (dump->quantities.unwrapped_position) atoms.enable_unwrapped();
        measurements.emplace_back(*dump);
      } else if (const auto* dump = std::get_if<DumpRestartCommand>(&command.data)) {
        measurements.emplace_back(*dump);
      } else if (const auto* run = std::get_if<RunCommand>(&command.data)) {
        if (!potential_seen) throw std::runtime_error("run requires a preceding potential command");
        if (!ensemble) throw std::runtime_error("run requires a preceding ensemble command");
        check_cuda(cudaDeviceSynchronize(), "synchronize before timed run segment");
        mpi.barrier();
        const auto segment_started = std::chrono::steady_clock::now();
        run_segment(run->steps, time_step, maximum_distance, *ensemble, velocity_correction,
                    measurements, global_time, global_step, box, model.atoms, atoms, *force,
                    ownership, mpi);
        check_cuda(cudaDeviceSynchronize(), "synchronize after timed run segment");
        const double segment_seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - segment_started).count();
        mpi.log_timing(
            "run", run_sequence, static_cast<std::uint64_t>(run->steps),
            model.atoms.counts.global_count, segment_seconds);
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
  check_cuda(cudaDeviceSynchronize(), "finish replicated MPI run");
  force.reset();
  rank_io.finish();
  const double total_seconds = std::chrono::duration<double>(
      std::chrono::steady_clock::now() - total_started).count();
  mpi.log_timing(
      "total", 0, global_step, model.atoms.counts.global_count, total_seconds);
}

}  // namespace

// ---------------------------------------------------------------------------
// Shared host functions declared in runtime_internal.hpp.
// ---------------------------------------------------------------------------

void zero_linear_momentum(
    const std::vector<double>& mass,
    std::vector<double>& velocity,
    const std::vector<std::size_t>& atoms)
{
  const std::size_t stride = mass.size();
  std::array<double, 3> center{};
  double total_mass = 0.0;
  for (const std::size_t atom : atoms) {
    total_mass += mass[atom];
    for (std::size_t axis = 0; axis < 3; ++axis) {
      center[axis] += mass[atom] * velocity[axis * stride + atom];
    }
  }
  if (total_mass == 0.0) return;
  for (double& component : center) component /= total_mass;
  for (const std::size_t atom : atoms) {
    for (std::size_t axis = 0; axis < 3; ++axis) {
      velocity[axis * stride + atom] -= center[axis];
    }
  }
}

void correct_velocity_subset(
    const std::vector<double>& mass,
    const std::vector<double>& position,
    std::vector<double>& velocity,
    const std::vector<std::size_t>& atoms)
{
  if (atoms.empty()) return;
  const std::size_t stride = mass.size();
  zero_linear_momentum(mass, velocity, atoms);
  std::array<double, 3> center{};
  double total_mass = 0.0;
  for (const std::size_t atom : atoms) {
    total_mass += mass[atom];
    for (std::size_t axis = 0; axis < 3; ++axis) {
      center[axis] += position[axis * stride + atom] * mass[atom];
    }
  }
  for (double& component : center) component /= total_mass;

  std::array<double, 3> angular{};
  double inertia[3][3]{};
  for (const std::size_t atom : atoms) {
    const double dx = position[atom] - center[0];
    const double dy = position[stride + atom] - center[1];
    const double dz = position[2 * stride + atom] - center[2];
    const double vx = velocity[atom];
    const double vy = velocity[stride + atom];
    const double vz = velocity[2 * stride + atom];
    angular[0] += mass[atom] * (dy * vz - dz * vy);
    angular[1] += mass[atom] * (dz * vx - dx * vz);
    angular[2] += mass[atom] * (dx * vy - dy * vx);
    inertia[0][0] += mass[atom] * (dy * dy + dz * dz);
    inertia[1][1] += mass[atom] * (dx * dx + dz * dz);
    inertia[2][2] += mass[atom] * (dx * dx + dy * dy);
    inertia[0][1] -= mass[atom] * dx * dy;
    inertia[1][2] -= mass[atom] * dy * dz;
    inertia[0][2] -= mass[atom] * dx * dz;
  }
  inertia[1][0] = inertia[0][1];
  inertia[2][1] = inertia[1][2];
  inertia[2][0] = inertia[0][2];
  const double determinant =
      inertia[0][0] * inertia[1][1] * inertia[2][2] +
      inertia[0][1] * inertia[1][2] * inertia[2][0] +
      inertia[0][2] * inertia[1][0] * inertia[2][1] -
      inertia[0][0] * inertia[1][2] * inertia[2][1] -
      inertia[0][1] * inertia[1][0] * inertia[2][2] -
      inertia[2][0] * inertia[1][1] * inertia[0][2];
  if (determinant > -1.0e-10 && determinant < 1.0e-10) return;

  double inverse[3][3];
  inverse[0][0] = inertia[1][1] * inertia[2][2] - inertia[1][2] * inertia[2][1];
  inverse[0][1] = -(inertia[0][1] * inertia[2][2] - inertia[0][2] * inertia[2][1]);
  inverse[0][2] = inertia[0][1] * inertia[1][2] - inertia[0][2] * inertia[1][1];
  inverse[1][0] = -(inertia[1][0] * inertia[2][2] - inertia[1][2] * inertia[2][0]);
  inverse[1][1] = inertia[0][0] * inertia[2][2] - inertia[0][2] * inertia[2][0];
  inverse[1][2] = -(inertia[0][0] * inertia[1][2] - inertia[0][2] * inertia[1][0]);
  inverse[2][0] = inertia[1][0] * inertia[2][1] - inertia[1][1] * inertia[2][0];
  inverse[2][1] = -(inertia[0][0] * inertia[2][1] - inertia[0][1] * inertia[2][0]);
  inverse[2][2] = inertia[0][0] * inertia[1][1] - inertia[0][1] * inertia[1][0];
  std::array<double, 3> omega{};
  for (std::size_t row = 0; row < 3; ++row) {
    for (std::size_t column = 0; column < 3; ++column) {
      omega[row] += inverse[row][column] / determinant * angular[column];
    }
  }
  for (const std::size_t atom : atoms) {
    const double dx = position[atom] - center[0];
    const double dy = position[stride + atom] - center[1];
    const double dz = position[2 * stride + atom] - center[2];
    velocity[atom] -= omega[1] * dz - omega[2] * dy;
    velocity[stride + atom] -= omega[2] * dx - omega[0] * dz;
    velocity[2 * stride + atom] -= omega[0] * dy - omega[1] * dx;
  }
}

void initialize_random_velocity(
    HostAtoms& atoms,
    double temperature,
    std::optional<int> seed)
{
  const std::size_t stride = atoms.local_stride();
  for (std::size_t atom = 0; atom < atoms.counts.owned_count; ++atom) {
    for (std::size_t axis = 0; axis < 3; ++axis) {
      if (seed) {
        std::srand(static_cast<unsigned int>(*seed + atoms.global_id[atom] * 3 + axis));
      }
      atoms.velocity[axis * stride + atom] =
          -1.0 + (std::rand() * 2.0) / static_cast<double>(RAND_MAX);
    }
  }
  const auto indices = all_owned_indices(atoms);
  correct_velocity_subset(atoms.mass, atoms.position, atoms.velocity, indices);
  double actual_temperature = 0.0;
  for (const std::size_t atom : indices) {
    const double vx = atoms.velocity[atom];
    const double vy = atoms.velocity[stride + atom];
    const double vz = atoms.velocity[2 * stride + atom];
    actual_temperature += atoms.mass[atom] * (vx * vx + vy * vy + vz * vz);
  }
  actual_temperature /= (3.0 * K_B * atoms.counts.owned_count);
  const double factor = std::sqrt(temperature / actual_temperature);
  for (const std::size_t atom : indices) {
    for (std::size_t axis = 0; axis < 3; ++axis) {
      atoms.velocity[axis * stride + atom] *= factor;
    }
  }
}

std::vector<std::size_t> output_order(
    const HostAtoms& identity,
    const HostSnapshot& snapshot,
    const DumpXyzCommand* command)
{
  std::vector<std::size_t> order;
  const std::size_t owned = identity.counts.owned_count;
  for (std::size_t atom = 0; atom < owned; ++atom) {
    if (command && command->grouping_method) {
      const int method = *command->grouping_method;
      if (method < 0 || static_cast<std::size_t>(method) >= identity.group_labels.size()) {
        throw std::runtime_error("dump_xyz grouping method is out of range");
      }
      const int maximum_group = *std::max_element(
          identity.group_labels[method].begin(), identity.group_labels[method].end());
      if (*command->group_id > maximum_group) {
        throw std::runtime_error("dump_xyz group ID is out of range");
      }
      if (identity.group_labels[method][atom] != *command->group_id) continue;
    }
    order.push_back(atom);
  }
  std::sort(order.begin(), order.end(), [&snapshot](std::size_t left, std::size_t right) {
    return snapshot.global_id[left] < snapshot.global_id[right];
  });
  return order;
}

void print_tensor(FILE* file, const char* name, const char* format, const double* tensor)
{
  std::fprintf(file, " %s=\"", name);
  for (int component = 0; component < 9; ++component) {
    std::fprintf(file, component == 0 ? format + 1 : format, tensor[component]);
  }
  std::fprintf(file, "\"");
}

void write_thermo_header(FILE* file, int interval, const HostAtoms& atoms, double time_step)
{
  std::fprintf(file, "# dump_thermo %d\n", interval);
  std::fprintf(file, "# format_version 1\n");
  std::fprintf(file, "# num_atoms %zu\n", atoms.counts.global_count);
  std::fprintf(file, "# dt_output %.10e fs\n", time_step * interval * TIME_UNIT_CONVERSION);
  std::fprintf(file,
               "# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz\n");
}

void write_thermo_row(FILE* file, const ThermoState& thermo, const HostAtoms& atoms, const Box& box)
{
  const double kinetic = 1.5 * atoms.counts.global_count * K_B * thermo.values[0];
  std::fprintf(
      file,
      "%20.10e%20.10e%20.10e%20.10e%20.10e%20.10e%20.10e%20.10e%20.10e",
      thermo.values[0], kinetic, thermo.values[1],
      thermo.values[2] * PRESSURE_UNIT_CONVERSION,
      thermo.values[3] * PRESSURE_UNIT_CONVERSION,
      thermo.values[4] * PRESSURE_UNIT_CONVERSION,
      thermo.values[7] * PRESSURE_UNIT_CONVERSION,
      thermo.values[6] * PRESSURE_UNIT_CONVERSION,
      thermo.values[5] * PRESSURE_UNIT_CONVERSION);
  std::fprintf(file,
               "%20.10e%20.10e%20.10e%20.10e%20.10e%20.10e%20.10e%20.10e%20.10e\n",
               box.cpu_h[0], box.cpu_h[3], box.cpu_h[6], box.cpu_h[1], box.cpu_h[4],
               box.cpu_h[7], box.cpu_h[2], box.cpu_h[5], box.cpu_h[8]);
  std::fflush(file);
}

void write_xyz(
    const DumpXyzCommand& command,
    int step,
    double global_time,
    const Box& box,
    const HostAtoms& identity,
    const HostSnapshot& snapshot,
    const ThermoState& thermo)
{
  const bool separated = !command.filename.empty() && command.filename.back() == '*';
  std::string filename = command.filename;
  if (separated) {
    filename.pop_back();
    filename += std::to_string(step + 1);
  }
  FILE* file = std::fopen(filename.c_str(), separated ? "w" : "a");
  if (!file) throw std::runtime_error("cannot open dump_xyz output '" + filename + "'");
  const char* format = command.precision == OutputPrecision::single ? " %.9g" : " %.17g";
  const auto order = output_order(identity, snapshot, &command);
  const std::size_t stride = identity.local_stride();
  std::fprintf(file, "%zu\n", order.size());
  std::fprintf(file, "Time=%.8f", global_time * TIME_UNIT_CONVERSION);
  std::fprintf(file, " pbc=\"%c %c %c\"", box.pbc_x ? 'T' : 'F', box.pbc_y ? 'T' : 'F',
               box.pbc_z ? 'T' : 'F');
  const double lattice[9] = {box.cpu_h[0], box.cpu_h[3], box.cpu_h[6], box.cpu_h[1],
                             box.cpu_h[4], box.cpu_h[7], box.cpu_h[2], box.cpu_h[5],
                             box.cpu_h[8]};
  print_tensor(file, "Lattice", format, lattice);
  std::fprintf(file, " energy=");
  std::fprintf(file, format + 1, thermo.values[1]);
  std::array<double, 6> virial_sum{};
  for (std::size_t atom = 0; atom < identity.counts.owned_count; ++atom) {
    for (std::size_t component = 0; component < 6; ++component) {
      virial_sum[component] += snapshot.virial[component * stride + atom];
    }
  }
  const double virial[9] = {virial_sum[0], virial_sum[3], virial_sum[4],
                            virial_sum[3], virial_sum[1], virial_sum[5],
                            virial_sum[4], virial_sum[5], virial_sum[2]};
  print_tensor(file, "virial", format, virial);
  const double stress[9] = {thermo.values[2], thermo.values[5], thermo.values[6],
                            thermo.values[5], thermo.values[3], thermo.values[7],
                            thermo.values[6], thermo.values[7], thermo.values[4]};
  print_tensor(file, "stress", format, stress);
  std::fprintf(file, " Properties=species:S:1:pos:R:3");
  if (command.quantities.mass) std::fprintf(file, ":mass:R:1");
  if (command.quantities.charge) std::fprintf(file, ":charge:R:1");
  if (command.quantities.velocity) std::fprintf(file, ":vel:R:3");
  if (command.quantities.force) std::fprintf(file, ":forces:R:3");
  if (command.quantities.potential) std::fprintf(file, ":energy_atom:R:1");
  if (command.quantities.unwrapped_position) std::fprintf(file, ":unwrapped_position:R:3");
  if (command.quantities.virial) std::fprintf(file, ":virial:R:9");
  if (command.quantities.group_labels) {
    std::fprintf(file, ":group:I:%zu", identity.group_labels.size());
  }
  std::fprintf(file, "\n");

  constexpr int virial_index[9] = {0, 3, 4, 6, 1, 5, 7, 8, 2};
  for (const std::size_t atom : order) {
    std::fprintf(file, "%s", identity.species[atom].c_str());
    for (std::size_t axis = 0; axis < 3; ++axis) {
      std::fprintf(file, format, snapshot.position[axis * stride + atom]);
    }
    if (command.quantities.mass) std::fprintf(file, format, identity.mass[atom]);
    if (command.quantities.charge) std::fprintf(file, format, identity.charge[atom]);
    if (command.quantities.velocity) {
      for (std::size_t axis = 0; axis < 3; ++axis) {
        std::fprintf(file, format,
                     snapshot.velocity[axis * stride + atom] / TIME_UNIT_CONVERSION);
      }
    }
    if (command.quantities.force) {
      for (std::size_t axis = 0; axis < 3; ++axis) {
        std::fprintf(file, format, snapshot.force[axis * stride + atom]);
      }
    }
    if (command.quantities.potential) std::fprintf(file, format, snapshot.potential[atom]);
    if (command.quantities.unwrapped_position) {
      if (snapshot.unwrapped.empty()) {
        std::fclose(file);
        throw std::logic_error("unwrapped position output was not initialized");
      }
      for (std::size_t axis = 0; axis < 3; ++axis) {
        std::fprintf(file, format, snapshot.unwrapped[axis * stride + atom]);
      }
    }
    if (command.quantities.virial) {
      for (int component : virial_index) {
        std::fprintf(file, format, snapshot.virial[component * stride + atom]);
      }
    }
    if (command.quantities.group_labels) {
      for (const auto& labels : identity.group_labels) {
        std::fprintf(file, " %d", labels[atom]);
      }
    }
    std::fprintf(file, "\n");
  }
  std::fflush(file);
  std::fclose(file);
}

void write_restart(
    const Box& box,
    const HostAtoms& identity,
    const HostSnapshot& snapshot)
{
  FILE* file = std::fopen("restart.xyz", "w");
  if (!file) throw std::runtime_error("cannot open restart.xyz");
  const auto order = output_order(identity, snapshot, nullptr);
  const std::size_t stride = identity.local_stride();
  std::fprintf(file, "%zu\n", identity.counts.global_count);
  std::fprintf(file, "pbc=\"%c %c %c\" ", box.pbc_x ? 'T' : 'F', box.pbc_y ? 'T' : 'F',
               box.pbc_z ? 'T' : 'F');
  std::fprintf(file, "Lattice=\"%g %g %g %g %g %g %g %g %g\" ", box.cpu_h[0],
               box.cpu_h[3], box.cpu_h[6], box.cpu_h[1], box.cpu_h[4], box.cpu_h[7],
               box.cpu_h[2], box.cpu_h[5], box.cpu_h[8]);
  if (identity.group_labels.empty()) {
    std::fprintf(file, "Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3\n");
  } else {
    std::fprintf(file, "Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3:group:I:%zu\n",
                 identity.group_labels.size());
  }
  for (const std::size_t atom : order) {
    std::fprintf(file, "%s %g %g %g %g %g %g %g ", identity.species[atom].c_str(),
                 snapshot.position[atom], snapshot.position[stride + atom],
                 snapshot.position[2 * stride + atom], identity.mass[atom],
                 snapshot.velocity[atom] / TIME_UNIT_CONVERSION,
                 snapshot.velocity[stride + atom] / TIME_UNIT_CONVERSION,
                 snapshot.velocity[2 * stride + atom] / TIME_UNIT_CONVERSION);
    for (const auto& labels : identity.group_labels) std::fprintf(file, "%d ", labels[atom]);
    std::fprintf(file, "\n");
  }
  std::fflush(file);
  std::fclose(file);
}

}  // namespace detail

void run_replicated(
    const RunProgram& program,
    Model model,
    const std::string& potential_filename,
    MpiRuntime& mpi)
{
  using gpumd_compat::NEP;
  // Mode dispatcher (docs/plans/domain-decomposition.md section 3.3): the
  // potential is parsed exactly once here; eligibility is decided from the
  // parsed cutoffs and the box; P=1 always keeps the existing path, P>1
  // inputs that fail an M2a criterion keep the M1 replicated-full runtime as
  // the compatibility fallback (never a new error), and triclinic /
  // non-periodic inputs at P>1 keep the M1 unsupported errors.
  if (model.atoms.counts.ghost_count != 0 ||
      model.atoms.counts.owned_count != model.atoms.counts.global_count) {
    throw std::logic_error(
        "replicated initialization requires a complete input model and no ghosts");
  }
  const std::filesystem::path absolute_potential =
      std::filesystem::absolute(potential_filename);
  mpi.assert_same_fingerprint(detail::file_fingerprint("run.in"), "run.in");
  mpi.assert_same_fingerprint(detail::file_fingerprint("model.xyz"), "model.xyz");
  mpi.assert_same_fingerprint(
      detail::file_fingerprint(absolute_potential), "potential file");
  mpi.initialize_device();
  gpumd_compat::Box box = detail::make_box(model.box);

  if (mpi.world_size() > 1) {
    if (!box.is_orthogonal) {
      throw std::runtime_error(
          "M1 spatial slab ownership supports only orthogonal boxes; a "
          "triclinic lattice must be reported before any decomposition");
    }
    if (box.pbc_x != 1 || box.pbc_y != 1 || box.pbc_z != 1) {
      throw std::runtime_error(
          "M1 spatial slab ownership requires periodicity in all three "
          "directions; a non-periodic direction is unsupported");
    }
  }

  // Parse the potential exactly once on every rank. Workspaces are sized
  // only after the mode decision (deferred-workspace NEP constructor).
  auto potential = std::make_unique<NEP>(absolute_potential.c_str());

  DomainEligibility eligibility;
  {
    const auto& params = potential->params();
    CutoffSet cutoffs;
    cutoffs.num_types = params.num_types;
    cutoffs.rc_radial.assign(params.rc_radial, params.rc_radial + params.num_types);
    cutoffs.rc_angular.assign(params.rc_angular, params.rc_angular + params.num_types);
    cutoffs.zbl_enabled = potential->zbl_params().enabled;
    cutoffs.zbl_flexible = potential->zbl_params().flexibled;
    cutoffs.zbl_rc_outer = potential->zbl_params().rc_outer;
    cutoffs.zbl_typewise = params.use_typewise_cutoff_zbl;
    const DomainRadii radii = compute_domain_radii(cutoffs);
    eligibility = evaluate_domain_eligibility(
        mpi.world_size(), model.box.h, radii, params.rc_radial_max);
  }
  if (mpi.is_root()) {
    static const char* axis_names[3] = {"x", "y", "z"};
    std::cout << "DMGMD_DOMAIN mode="
              << (eligibility.eligible ? "m2a" : "m1-fallback")
              << " axis="
              << (eligibility.axis >= 0 ? axis_names[eligibility.axis] : "none")
              << " d_dep=" << eligibility.d_dep << " d_coord=" << eligibility.d_coord;
    if (eligibility.eligible) {
      std::cout << " axis_thickness=" << eligibility.axis_thickness
                << " slab_width=" << eligibility.slab_width;
    }
    std::cout << " ranks=" << mpi.world_size()
              << " reason=" << eligibility.reason << '\n';
    std::cout.flush();
  }

  if (eligibility.eligible) {
    run_local_domain(
        program, std::move(model), box, std::move(potential), eligibility, mpi);
    return;
  }
  detail::run_m1_replicated(
      program, std::move(model), box, std::move(potential), potential_filename, mpi);
}

}  // namespace dmgmd
