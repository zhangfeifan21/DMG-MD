#include "dmgmd/runtime.hpp"

// The NEP force path is the DMG-MD-owned replication of the minimal GPUMD
// subset in src/gpumd_compat (copied from the pinned reference commit
// 9d23496e41319b9e2af5221a7df6285387401d1e, numerics unchanged).  DMG-MD
// must never include or link ../gpumd-reference directly.
#include "gpumd_compat/box.cuh"
#include "gpumd_compat/common.cuh"
#include "gpumd_compat/gpu_vector.cuh"
#include "gpumd_compat/nep.cuh"

#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

namespace dmgmd {
namespace {

using gpumd_compat::Box;
using gpumd_compat::GPU_Vector;
using gpumd_compat::NEP;
using gpumd_compat::Potential;
// K_B is a #define in gpumd_compat/common.cuh and needs no using-declaration.
using gpumd_compat::PRESSURE_UNIT_CONVERSION;
using gpumd_compat::TIME_UNIT_CONVERSION;

constexpr int kThreads = 128;
constexpr int kThermoThreads = 1024;

void check_cuda(cudaError_t status, const char* operation)
{
  if (status != cudaSuccess) {
    throw std::runtime_error(
        std::string(operation) + ": " + cudaGetErrorString(status));
  }
}

int checked_int(std::size_t value, const char* name)
{
  if (value > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    throw std::length_error(std::string(name) + " exceeds GPUMD's int range");
  }
  return static_cast<int>(value);
}

std::uint64_t file_fingerprint(const std::filesystem::path& path)
{
  std::ifstream input(path, std::ios::binary);
  if (!input) throw std::runtime_error("cannot fingerprint input file '" + path.string() + "'");
  std::uint64_t hash = UINT64_C(1469598103934665603);
  char byte = 0;
  while (input.get(byte)) {
    hash ^= static_cast<unsigned char>(byte);
    hash *= UINT64_C(1099511628211);
  }
  return hash;
}

// mkdtemp replaces exactly six trailing 'X' characters with random text.
constexpr std::size_t kScratchRandomSuffixLength = 6;

// Test-only fault injection hook. Setting
// DMGMD_RANK_IO_FAULT="<world_rank>:<operation>" (operation in {mkdir, file,
// chdir, setup_restore, restore, cleanup}) makes exactly that rank fail
// exactly that step, with no filesystem side effect.
// tests/mpi/run_rank_io_isolation.py uses it to prove that one rank's local
// failure routes every rank through the same bounded failure exit instead of
// hanging in a collective. Unset or non-matching values disable injection.
bool rank_io_fault_requested(const char* operation, int world_rank)
{
  const char* value = std::getenv("DMGMD_RANK_IO_FAULT");
  if (value == nullptr) return false;
  const std::string specification(value);
  const std::size_t separator = specification.find(':');
  if (separator == std::string::npos) return false;
  return specification.substr(0, separator) == std::to_string(world_rank) &&
         specification.substr(separator + 1) == operation;
}

// Normalizes a trailing separator (e.g. TMPDIR="/tmp/") so the safety
// boundary's parent comparison against the recorded temp root is exact.
std::filesystem::path strip_trailing_separators(std::filesystem::path path)
{
  while (path.has_relative_path() && path.filename().empty()) {
    path = path.parent_path();
  }
  return path;
}

// Random hex token used only for scratch naming and diagnostics. Uniqueness of
// the scratch directories themselves is owned by mkdtemp, so even a repeated
// nonce across concurrent jobs cannot make two ranks share a directory.
std::string make_job_nonce()
{
  // The nonce is diagnostic only; mkdtemp owns directory uniqueness. Keep
  // generation free of entropy-device failures because rank 0 produces it
  // before the first isolation collective.
  std::uint64_t value = static_cast<std::uint64_t>(
      std::chrono::high_resolution_clock::now().time_since_epoch().count());
  value ^= static_cast<std::uint64_t>(
      std::chrono::steady_clock::now().time_since_epoch().count()) << 1;
  static constexpr char kHexadecimal[] = "0123456789abcdef";
  std::string nonce(16, '0');
  for (std::size_t index = nonce.size(); index-- > 0;) {
    nonce[index] = kHexadecimal[value & UINT64_C(0xF)];
    value >>= 4;
  }
  return nonce;
}

// Guards the broadcast nonce against anything that is not a safe, fixed-shape
// path component. A violation is identical on every rank (they all received
// the same broadcast), so throwing here is already a symmetric exit.
bool is_hex_token(const std::string& text)
{
  if (text.size() != 16) return false;
  for (const unsigned char character : text) {
    if (!((character >= '0' && character <= '9') ||
          (character >= 'a' && character <= 'f'))) {
      return false;
    }
  }
  return true;
}

// One self-describing diagnostic per rank. World rank and hostname are what
// an operator needs to locate the failing node in a multi-node job; the
// target path and reason identify the local filesystem failure.
std::string rank_io_diagnostic(
    const MpiRuntime& mpi,
    const char* operation,
    const std::filesystem::path& target,
    const std::string& reason)
{
  return "rank=" + std::to_string(mpi.world_rank()) + " hostname=" + mpi.hostname() +
         " " + operation + " " + target.string() + ": " + reason;
}

// File ownership is part of docs/standards/replicated-mpi.md, not merely a
// test setup: only world rank 0 may write the user's job directory, while
// legacy GPUMD-derived code opens files by relative name (ordinary NEP
// appends neighbor.out every 1000 force calls). Every non-root rank therefore
// executes inside a private scratch directory on its OWN node. The previous
// design let rank 0 create one shared temp root and broadcast its path, which
// silently assumed a cross-node visible /tmp and could hang when a remote
// chdir failed; no filesystem path is broadcast anymore.
//
// Protocol: docs/standards/replicated-mpi.md "I/O 与 NEP_MULTIGPU"; remaining
// dual-node verification: docs/plans/multi-node-io.md.
//   * Setup is a two-phase collective. Each non-root rank locally creates
//     <local tmp>/dmgmd-rank-io-<nonce>-r<rank>-XXXXXX with mkdtemp - one
//     atomic step that is unique by construction and owner-only (mode 0700,
//     independent of umask) - creates a plain neighbor.out inside, and chdirs
//     there. All local failures are captured, never thrown directly; one
//     success-flag Allreduce decides the outcome. On any failure every rank
//     restores its own cwd first, then all ranks leave through one shared
//     failure exit (identical exception everywhere -> bounded MPI_Abort, no
//     rank left waiting in a collective).
//   * finish() runs three ordered phases: (1) restore-cwd reduction, where
//     any failure fails the job; (2) local deletion of the rank's OWN
//     directory behind a safety boundary; (3) a cleanup status summary, where
//     failure only warns and keeps the exact directory for diagnosis.
//   * The destructor is local, best-effort and MPI-free; collectives run only
//     in finish(), never during stack unwinding.
class RankIoIsolation {
 public:
  explicit RankIoIsolation(MpiRuntime& mpi)
      : mpi_(mpi)
  {
    if (mpi_.world_size() == 1) return;

    // Capturing cwd is itself a rank-local filesystem operation. Keep its
    // error inside the same setup handshake as TMPDIR/mkdtemp/file/chdir so a
    // single broken cwd cannot strand peers in a later collective.
    std::string setup_error;
    if (!mpi_.is_root()) {
      std::error_code error;
      original_ = std::filesystem::current_path(error);
      if (error) {
        setup_error = rank_io_diagnostic(
            mpi_, "resolve original cwd", std::filesystem::path("<cwd>"), error.message());
      }
    }

    // The nonce is a naming/diagnostic token only (see make_job_nonce).
    std::string nonce;
    if (mpi_.is_root()) nonce = make_job_nonce();
    nonce = mpi_.broadcast_string(std::move(nonce));
    if (!is_hex_token(nonce)) {
      throw std::logic_error("rank I/O isolation received a malformed job nonce");
    }
    name_prefix_ =
        "dmgmd-rank-io-" + nonce + "-r" + std::to_string(mpi_.world_rank()) + "-";

    // Phase 1: local preparation with captured errors. A direct throw here
    // would strand the other ranks inside the status Allreduce below.
    if (!mpi_.is_root() && setup_error.empty()) {
      setup_error = prepare_local_scratch();
    }

    // Phase 2: one collective outcome for the whole world. On any local
    // failure every rank restores its cwd FIRST (a rank must not unwind while
    // still sitting inside the directory being diagnosed), removes its own
    // half-built or completed scratch best-effort, and only then all ranks
    // throw the same aggregated error.
    if (!mpi_.allreduce_all_passed(setup_error.empty(),
                                   "reduce rank I/O isolation setup status")) {
      std::string error = setup_error;
      const std::string restore = restore_cwd("setup_restore");
      if (!restore.empty()) error += error.empty() ? restore : "; " + restore;
      std::string removal_note;
      if (restore.empty()) {
        try_delete_scratch(&removal_note);
      } else if (!scratch_.empty()) {
        removal_note = rank_io_diagnostic(
            mpi_, "keep scratch after failed cwd restore", scratch_, "cleanup skipped");
      }
      if (!removal_note.empty()) error += error.empty() ? removal_note : "; " + removal_note;
      fail_together("setup", error);
    }
    active_ = true;
  }

  // Local, best-effort, MPI-free unwind guarantee. finish() owns all
  // collective teardown; this covers exceptions thrown between setup and
  // finish(), where running collectives during unwinding could deadlock.
  // Failures are not reported - the originating exception owns the exit.
  ~RankIoIsolation() noexcept
  {
    if (!active_) return;
    try {
      if (in_scratch_ && !restore_cwd().empty()) return;
      if (!scratch_.empty()) {
        std::string ignored;
        try_delete_scratch(&ignored);
      }
    } catch (...) {
      // Best-effort destructors must never replace the exception currently
      // unwinding the runtime. A failed cleanup deliberately leaves scratch.
    }
  }

  // Symmetric teardown; every rank must call it exactly once (run_replicated
  // does, after all segments completed successfully).
  void finish()
  {
    if (!active_) return;

    // Phase 1 - restore reduction. A rank that cannot leave its scratch
    // directory must not have that directory removed, and a job with unknown
    // cwd state must not report success: any restore failure fails the job
    // through the shared exit.
    std::string restore_error;
    if (!mpi_.is_root() && in_scratch_) {
      restore_error = restore_cwd("restore");
    }
    if (!mpi_.allreduce_all_passed(restore_error.empty(),
                                   "reduce rank I/O isolation restore status")) {
      // A genuinely failing rank may still sit in its scratch directory; the
      // destructor retries that restore locally during unwinding.
      fail_together("restore", restore_error);
    }

    // Phase 2 - local delete. Every rank is now provably outside its scratch
    // directory, so each non-root rank removes its OWN directory behind the
    // safety boundary in try_delete_scratch. rank 0 never touches another
    // node's paths.
    std::string cleanup_error;
    if (!mpi_.is_root()) {
      if (rank_io_fault_requested("cleanup", mpi_.world_rank())) {
        cleanup_error =
            rank_io_diagnostic(mpi_, "remove scratch directory", scratch_, "injected");
      } else {
        try_delete_scratch(&cleanup_error);
      }
    }

    // Phase 3 - cleanup summary. The MD results are already complete, so a
    // cleanup failure is a warning, not a job failure: rank 0 reports each
    // affected rank and the exact directory is kept for diagnosis.
    if (!mpi_.allreduce_all_passed(cleanup_error.empty(),
                                   "reduce rank I/O isolation cleanup status")) {
      const std::vector<std::string> diagnostics = mpi_.gather_strings(cleanup_error);
      if (mpi_.is_root()) {
        for (int rank = 0; rank < mpi_.world_size(); ++rank) {
          const std::string& entry = diagnostics[static_cast<std::size_t>(rank)];
          if (!entry.empty()) {
            std::cout << "DMGMD_RANK_IO_CLEANUP status=warning " << entry << '\n';
            std::cout.flush();
          }
        }
      }
    }
    active_ = false;
  }

 private:
  // Creates this rank's private scratch directory on the LOCAL node and
  // enters it. Returns an empty string on success, or a self-describing
  // diagnostic; nothing here throws, and on failure the rank is never left
  // inside a directory it did not fully set up.
  std::string prepare_local_scratch()
  {
    std::error_code error;
    std::filesystem::path temp_root = std::filesystem::temp_directory_path(error);
    if (error) {
      return rank_io_diagnostic(mpi_, "resolve temporary directory",
                                std::filesystem::path("<TMPDIR>"), error.message());
    }
    temp_root_ = strip_trailing_separators(std::move(temp_root));
    if (temp_root_.is_relative()) {
      temp_root_ = (original_ / temp_root_).lexically_normal();
    }

    if (rank_io_fault_requested("mkdir", mpi_.world_rank())) {
      return rank_io_diagnostic(mpi_, "create scratch directory",
                                temp_root_ / (name_prefix_ + "XXXXXX"), "injected");
    }
    // mkdtemp is the atomic-unique, owner-only primitive: it creates the
    // final directory in one uninterruptible step with mode 0700 (regardless
    // of umask) and re-randomizes the six trailing X characters until unique.
    // Remnants of an abnormally ended earlier job can never be reused, and
    // uniqueness never depends on the broadcast nonce or a timestamp.
    std::string pattern = (temp_root_ / (name_prefix_ + "XXXXXX")).string();
    std::vector<char> mutable_pattern(pattern.begin(), pattern.end());
    mutable_pattern.push_back('\0');
    if (mkdtemp(mutable_pattern.data()) == nullptr) {
      return rank_io_diagnostic(mpi_, "create scratch directory", temp_root_,
                                std::strerror(errno));
    }
    scratch_ = std::filesystem::path(mutable_pattern.data());

    // A plain regular neighbor.out: legacy NEP opens it by relative name and
    // appends, and the whole directory is deleted at teardown. This replaces
    // the old neighbor.out -> /dev/null symlink, removing that extra per-rank
    // failure point while keeping multi-rank append impossible.
    const std::filesystem::path neighbor = scratch_ / "neighbor.out";
    if (rank_io_fault_requested("file", mpi_.world_rank())) {
      return rank_io_diagnostic(mpi_, "create scratch neighbor.out", neighbor, "injected");
    }
    if (FILE* sink = std::fopen(neighbor.c_str(), "a"); sink == nullptr) {
      return rank_io_diagnostic(mpi_, "create scratch neighbor.out", neighbor,
                                std::strerror(errno));
    } else {
      std::fclose(sink);
    }

    if (rank_io_fault_requested("chdir", mpi_.world_rank())) {
      return rank_io_diagnostic(mpi_, "enter scratch directory", scratch_, "injected");
    }
    std::filesystem::current_path(scratch_, error);
    if (error) {
      return rank_io_diagnostic(mpi_, "enter scratch directory", scratch_, error.message());
    }
    in_scratch_ = true;
    return std::string();
  }

  // Best-effort return to the original working directory: empty string on
  // success (or when this rank never chdir'd), a diagnostic on failure.
  // Never throws - callers are already handling a failure.
  std::string restore_cwd(const char* fault_operation = nullptr)
  {
    if (!in_scratch_) return std::string();
    if (fault_operation != nullptr &&
        rank_io_fault_requested(fault_operation, mpi_.world_rank())) {
      return rank_io_diagnostic(mpi_, "restore cwd", original_, "injected");
    }
    std::error_code error;
    std::filesystem::current_path(original_, error);
    if (error) {
      return rank_io_diagnostic(mpi_, "restore cwd", original_, error.message());
    }
    in_scratch_ = false;
    return std::string();
  }

  // Deletes this rank's OWN scratch directory behind the safety boundary: the
  // target must still sit directly inside the temp root recorded at setup,
  // its basename must be exactly the expected prefix plus mkdtemp's six
  // random characters, and it must be a real directory (symlink_status does
  // not follow links, so a swapped-in symlink is rejected). Any mismatch or
  // removal error keeps the directory and reports; deletion never widens.
  bool try_delete_scratch(std::string* diagnostic)
  {
    if (scratch_.empty()) return true;  // rank 0, or the directory never existed
    if (in_scratch_) {
      *diagnostic = rank_io_diagnostic(
          mpi_, "scratch safety check failed; directory kept", scratch_,
          "process cwd is still inside scratch");
      return false;
    }
    std::error_code error;
    const std::string name = scratch_.filename().string();
    const bool parent_matches = scratch_.parent_path() == temp_root_;
    const bool name_matches =
        name.size() == name_prefix_.size() + kScratchRandomSuffixLength &&
        name.compare(0, name_prefix_.size(), name_prefix_) == 0;
    const std::filesystem::file_status status =
        std::filesystem::symlink_status(scratch_, error);
    const bool target_is_directory =
        !error && status.type() == std::filesystem::file_type::directory;
    if (parent_matches && name_matches && target_is_directory) {
      std::filesystem::remove_all(scratch_, error);
      if (!error) {
        scratch_.clear();
        return true;
      }
      *diagnostic =
          rank_io_diagnostic(mpi_, "remove scratch directory", scratch_, error.message());
      return false;
    }
    const char* reason = !parent_matches ? "parent is not the recorded temp root"
                        : !name_matches ? "basename does not match this rank's scratch prefix"
                                        : "target is not a plain directory";
    *diagnostic = rank_io_diagnostic(mpi_, "scratch safety check failed; directory kept",
                                     scratch_, reason);
    return false;
  }

  // Shared failure exit, reached only after a completed status Allreduce, so
  // the diagnostic gather and message broadcast below cannot deadlock. All
  // ranks throw one identical exception; main() logs it and the job ends
  // through the regular MPI_Abort path with nobody stuck in a collective.
  [[noreturn]] void fail_together(const char* phase, const std::string& local_error)
  {
    const std::vector<std::string> diagnostics = mpi_.gather_strings(local_error);
    std::string message = std::string("rank I/O isolation ") + phase + " failed";
    if (mpi_.is_root()) {
      for (int rank = 0; rank < mpi_.world_size(); ++rank) {
        const std::string& entry = diagnostics[static_cast<std::size_t>(rank)];
        if (!entry.empty()) message += "; " + entry;
      }
    }
    throw std::runtime_error(mpi_.broadcast_string(std::move(message)));
  }

  MpiRuntime& mpi_;
  std::filesystem::path original_;  // non-root absolute cwd captured during setup
  std::filesystem::path temp_root_;  // this rank's local temp root, recorded at setup
  std::filesystem::path scratch_;    // empty on rank 0 (no scratch, no chdir)
  std::string name_prefix_;          // "dmgmd-rank-io-<nonce>-r<rank>-"
  bool in_scratch_ = false;
  bool active_ = false;
};

Box make_box(const BoxData& input)
{
  Box box{};
  box.pbc_x = input.periodic[0];
  box.pbc_y = input.periodic[1];
  box.pbc_z = input.periodic[2];
  std::copy(input.h.begin(), input.h.end(), box.cpu_h);
  box.get_inverse();
  box.set_is_orthogonal();
  if (!std::isfinite(box.get_volume()) || box.get_volume() <= 0.0) {
    throw std::runtime_error("model.xyz lattice has non-positive volume");
  }
  return box;
}

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

struct HostSnapshot {
  std::vector<unsigned long long> global_id;
  std::vector<double> position;
  std::vector<double> velocity;
  std::vector<double> force;
  std::vector<double> potential;
  std::vector<double> virial;
  std::vector<double> unwrapped;
};

HostSnapshot gather_owned_snapshot(
    DeviceAtoms& atoms,
    OwnedRange owned,
    MpiRuntime& mpi,
    CommunicationVolume& communication)
{
  // Replicated device arrays are inputs; the snapshot is reconstructed only
  // from uniquely owned slices, preserving global atom order on rank 0.
  const std::size_t local = atoms.counts.local_count();
  HostSnapshot snapshot;
  if (mpi.is_root()) {
    snapshot.global_id.resize(local);
    atoms.global_id.copy_to_host(snapshot.global_id.data());
  }
  snapshot.position = mpi.gather_owned_device_soa_to_root(
      atoms.position.data(), 3, local, owned, communication);
  snapshot.velocity = mpi.gather_owned_device_soa_to_root(
      atoms.velocity.data(), 3, local, owned, communication);
  snapshot.force = mpi.gather_owned_device_soa_to_root(
      atoms.force.data(), 3, local, owned, communication);
  snapshot.potential = mpi.gather_owned_device_soa_to_root(
      atoms.potential.data(), 1, local, owned, communication);
  snapshot.virial = mpi.gather_owned_device_soa_to_root(
      atoms.virial.data(), 9, local, owned, communication);
  if (atoms.has_unwrapped()) {
    snapshot.unwrapped = mpi.gather_owned_device_soa_to_root(
        atoms.unwrapped.data(), 3, local, owned, communication);
  }
  return snapshot;
}

__global__ void wrap_positions(
    int local_count,
    int stride,
    Box box,
    double* position)
{
  const int atom = blockIdx.x * blockDim.x + threadIdx.x;
  if (atom >= local_count) {
    return;
  }
  double x = position[atom];
  double y = position[stride + atom];
  double z = position[2 * stride + atom];
  double sx = box.cpu_h[9] * x + box.cpu_h[10] * y + box.cpu_h[11] * z;
  double sy = box.cpu_h[12] * x + box.cpu_h[13] * y + box.cpu_h[14] * z;
  double sz = box.cpu_h[15] * x + box.cpu_h[16] * y + box.cpu_h[17] * z;
  if (box.pbc_x == 1) {
    if (sx < 0.0) sx += 1.0;
    else if (sx > 1.0) sx -= 1.0;
  }
  if (box.pbc_y == 1) {
    if (sy < 0.0) sy += 1.0;
    else if (sy > 1.0) sy -= 1.0;
  }
  if (box.pbc_z == 1) {
    if (sz < 0.0) sz += 1.0;
    else if (sz > 1.0) sz -= 1.0;
  }
  position[atom] = box.cpu_h[0] * sx + box.cpu_h[1] * sy + box.cpu_h[2] * sz;
  position[stride + atom] =
      box.cpu_h[3] * sx + box.cpu_h[4] * sy + box.cpu_h[5] * sz;
  position[2 * stride + atom] =
      box.cpu_h[6] * sx + box.cpu_h[7] * sy + box.cpu_h[8] * sz;
}

__global__ void clear_owned_properties(
    int begin,
    int end,
    int stride,
    double* force,
    double* potential,
    double* virial)
{
  const int atom = blockIdx.x * blockDim.x + threadIdx.x + begin;
  if (atom >= end) {
    return;
  }
  force[atom] = 0.0;
  force[stride + atom] = 0.0;
  force[2 * stride + atom] = 0.0;
  potential[atom] = 0.0;
  for (int component = 0; component < 9; ++component) {
    virial[component * stride + atom] = 0.0;
  }
}

__global__ void velocity_verlet(
    bool first_half,
    int begin,
    int end,
    int stride,
    double time_step,
    const double* mass,
    double* position,
    double* velocity,
    const double* force)
{
  const int atom = blockIdx.x * blockDim.x + threadIdx.x + begin;
  if (atom >= end) {
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
    position[atom] += vx * time_step;
    position[stride + atom] += vy * time_step;
    position[2 * stride + atom] += vz * time_step;
  }
}

__global__ void update_unwrapped(
    int begin,
    int end,
    int stride,
    const double* position,
    const double* previous,
    double* unwrapped)
{
  const int atom = blockIdx.x * blockDim.x + threadIdx.x + begin;
  if (atom >= end) {
    return;
  }
  for (int axis = 0; axis < 3; ++axis) {
    const int index = axis * stride + atom;
    unwrapped[index] += position[index] - previous[index];
  }
}

__global__ void find_owned_thermo_sums(
    int begin,
    int end,
    int stride,
    const double* mass,
    const double* potential,
    const double* velocity,
    const double* virial,
    double* thermo)
{
  const int tid = threadIdx.x;
  const int quantity = blockIdx.x;
  const int owned_count = end - begin;
  const int patches = owned_count == 0 ? 0 : (owned_count - 1) / kThermoThreads + 1;
  __shared__ double values[kThermoThreads];
  double sum = 0.0;
  for (int patch = 0; patch < patches; ++patch) {
    const int atom = begin + tid + patch * kThermoThreads;
    if (atom >= end) {
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

__global__ void normalize_global_thermo(
    int global_count,
    double volume,
    double* thermo)
{
  const int quantity = threadIdx.x;
  if (quantity >= 8) return;
  if (quantity == 0) {
    thermo[quantity] /= 3.0 * global_count * K_B;
  } else if (quantity >= 2) {
    thermo[quantity] /= volume;
  }
}

__global__ void scale_owned_velocity(
    int begin,
    int end,
    int stride,
    double factor,
    double* velocity)
{
  const int atom = blockIdx.x * blockDim.x + threadIdx.x + begin;
  if (atom >= end) {
    return;
  }
  velocity[atom] *= factor;
  velocity[stride + atom] *= factor;
  velocity[2 * stride + atom] *= factor;
}

struct ThermoState {
  std::array<double, 8> values{};
};

ThermoState compute_thermo(
    DeviceAtoms& atoms,
    const Box& box,
    OwnedRange owned,
    GPU_Vector<double>& device_thermo,
    MpiRuntime& mpi,
    CommunicationVolume& communication)
{
  const int begin = checked_int(owned.begin, "owned_begin");
  const int end = checked_int(owned.end, "owned_end");
  const int stride = checked_int(atoms.counts.local_count(), "local_count");
  find_owned_thermo_sums<<<8, kThermoThreads>>>(
      begin, end, stride, atoms.mass.data(), atoms.potential.data(),
      atoms.velocity.data(), atoms.virial.data(), device_thermo.data());
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
  NepForce(const std::string& filename, const AtomCounts& counts)
      : nep_(filename.c_str(), checked_int(counts.local_count(), "local_count"))
  {
    // See the NEP completeness proof in docs/standards/replicated-mpi.md. The pinned
    // ordinary NEP implementation reads Fp(n2) and reverse
    // directed partials belonging to neighboring centers. Merely assigning a
    // rank-local N1/N2 leaves those arrays incomplete. Until phase-level
    // intermediate exchange exists, every rank evaluates full NEP scratch and
    // the runtime grants authority only to its OwnedRange outputs.
    nep_.N1 = 0;
    nep_.N2 = checked_int(counts.local_count(), "local_count");
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
    nep_.compute(box, atoms.type, atoms.position, atoms.potential, atoms.force, atoms.virial);
  }

 private:
  NEP nep_;
};

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

std::vector<std::size_t> all_owned_indices(const HostAtoms& atoms)
{
  std::vector<std::size_t> result(atoms.counts.owned_count);
  for (std::size_t index = 0; index < result.size(); ++index) result[index] = index;
  return result;
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

using Measurement = std::variant<DumpThermoCommand, DumpXyzCommand, DumpRestartCommand>;

double adaptive_time_step(
    DeviceAtoms& atoms,
    OwnedRange owned,
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
  for (std::size_t atom = owned.begin; atom < owned.end; ++atom) {
    const double vx = velocity[atom];
    const double vy = velocity[stride + atom];
    const double vz = velocity[2 * stride + atom];
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
    OwnedRange owned_range,
    MpiRuntime& mpi)
{
  // Per-step protocol (docs/standards/replicated-mpi.md): integrate owned positions,
  // allgather replicated coordinates, evaluate full NEP scratch, integrate
  // owned velocities, reduce owned thermo, then allgather velocities. Output
  // gathers are conditional and every collective contributes to the log.
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
  const int owned_begin = checked_int(owned_range.begin, "owned_begin");
  const int owned_end = checked_int(owned_range.end, "owned_end");
  const int owned_count = checked_int(owned_range.size(), "owned_count");
  const int stride = checked_int(atoms.counts.local_count(), "local_count");
  for (int step = 0; step < steps; ++step) {
    CommunicationVolume communication;
    if (velocity_correction && step % velocity_correction->interval == 0) {
      correct_device_velocity(atoms, identity, *velocity_correction, mpi, communication);
    }
    const double time_step = adaptive_time_step(
        atoms, owned_range, base_time_step, maximum_distance, mpi, communication);
    global_time += time_step;
    if (atoms.has_unwrapped()) {
      atoms.previous_position.copy_from_device(atoms.position.data());
    }
    if (owned_count != 0) {
      velocity_verlet<<<(owned_count + kThreads - 1) / kThreads, kThreads>>>(
          true, owned_begin, owned_end, stride, time_step, atoms.mass.data(),
          atoms.position.data(), atoms.velocity.data(), atoms.force.data());
      if (atoms.has_unwrapped()) {
        update_unwrapped<<<(owned_count + kThreads - 1) / kThreads, kThreads>>>(
            owned_begin, owned_end, stride, atoms.position.data(),
            atoms.previous_position.data(), atoms.unwrapped.data());
      }
    }
    check_cuda(cudaGetLastError(), "velocity-Verlet first half");
    mpi.allgather_owned_device_soa(
        atoms.position.data(), 3, atoms.counts.local_count(), owned_range, communication);
    force.compute(box, atoms);
    if (owned_count != 0) {
      velocity_verlet<<<(owned_count + kThreads - 1) / kThreads, kThreads>>>(
          false, owned_begin, owned_end, stride, time_step, atoms.mass.data(),
          atoms.position.data(), atoms.velocity.data(), atoms.force.data());
    }
    check_cuda(cudaGetLastError(), "velocity-Verlet second half");
    const ThermoState thermo = compute_thermo(
        atoms, box, owned_range, device_thermo, mpi, communication);

    if (ensemble.kind == EnsembleKind::nvt_ber) {
      const double fraction = static_cast<double>(step) / static_cast<double>(steps);
      const double target = ensemble.initial_temperature +
                            (ensemble.final_temperature - ensemble.initial_temperature) * fraction;
      const double coupling = 1.0 / ensemble.temperature_coupling;
      if (coupling > 1.0e-5) {
        const double factor = std::sqrt(1.0 + coupling * (target / thermo.values[0] - 1.0));
        if (owned_count != 0) {
          scale_owned_velocity<<<(owned_count + kThreads - 1) / kThreads, kThreads>>>(
              owned_begin, owned_end, stride, factor, atoms.velocity.data());
        }
        check_cuda(cudaGetLastError(), "Berendsen velocity scaling");
      }
    }
    mpi.allgather_owned_device_soa(
        atoms.velocity.data(), 3, atoms.counts.local_count(), owned_range, communication);

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
      snapshot = gather_owned_snapshot(atoms, owned_range, mpi, communication);
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

}  // namespace

void run_replicated(
    const RunProgram& program,
    Model model,
    const std::string& potential_filename,
    MpiRuntime& mpi)
{
  const auto total_started = std::chrono::steady_clock::now();
  // The full Model is replicated input. `owned` below is the only authority
  // for integration, thermodynamics and output; this phase has no ghosts or
  // atom migration. Keep this boundary aligned with docs/standards/replicated-mpi.md.
  if (model.atoms.counts.ghost_count != 0 ||
      model.atoms.counts.owned_count != model.atoms.counts.global_count) {
    throw std::logic_error(
        "replicated initialization requires a complete input model and no ghosts");
  }
  if (model.atoms.counts.global_count < static_cast<std::size_t>(mpi.world_size())) {
    throw std::logic_error(
        "replicated prototype requires at least one owned center atom per MPI rank");
  }

  const std::filesystem::path absolute_potential =
      std::filesystem::absolute(potential_filename);
  mpi.assert_same_fingerprint(file_fingerprint("run.in"), "run.in");
  mpi.assert_same_fingerprint(file_fingerprint("model.xyz"), "model.xyz");
  mpi.assert_same_fingerprint(file_fingerprint(absolute_potential), "potential file");
  mpi.initialize_device();
  const OwnedRange owned = balanced_owned_range(
      model.atoms.counts.global_count, mpi.world_rank(), mpi.world_size());
  mpi.verify_and_log_center_partition(model.atoms.counts.global_count, owned);

  if (!model.atoms.has_input_velocity) {
    if (mpi.is_root()) {
      std::srand(static_cast<unsigned int>(
          std::chrono::system_clock::now().time_since_epoch().count()));
      initialize_random_velocity(model.atoms, 300.0, std::nullopt);
    }
    mpi.broadcast_doubles(model.atoms.velocity.data(), model.atoms.velocity.size());
  }

  Box box = make_box(model.box);
  DeviceAtoms atoms(model.atoms);
  RankIoIsolation rank_io(mpi);
  auto force = std::make_unique<NepForce>(absolute_potential.string(), atoms.counts);
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
      if (const auto* potential = std::get_if<PotentialCommand>(&command.data)) {
        if (potential_seen || potential->filename != potential_filename) {
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
                    owned, mpi);
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

}  // namespace dmgmd
