#pragma once

// Internal sharing boundary between src/runtime.cu (the M1 replicated-full
// path and the P=1 path) and src/domain_runtime.cu (the M2a local-domain
// path). Everything the two runtimes share lives here: small CUDA helpers,
// the box factory, the snapshot/thermo/measurement types, the rank I/O
// isolation protocol, the kernels whose launch domains are fully
// parameterized (wrap / scratch clear / thermo normalization), and the
// GPUMD-compatible output formatters plus the CPU velocity correction.
//
// NOT part of include/dmgmd: this header is allowed to depend on
// gpumd_compat and on the .cu translation-unit layout. The M2a module
// include/dmgmd/domain_layout.hpp stays pure CPU and must not include this.

#include "dmgmd/domain_layout.hpp"
#include "dmgmd/model.hpp"
#include "dmgmd/mpi_runtime.hpp"
#include "dmgmd/run_ir.hpp"

#include "gpumd_compat/box.cuh"
#include "gpumd_compat/common.cuh"
#include "gpumd_compat/gpu_vector.cuh"
#include "gpumd_compat/nep.cuh"

#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
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
#include <variant>
#include <vector>

namespace dmgmd {
namespace detail {

using gpumd_compat::Box;
using gpumd_compat::GPU_Vector;

inline void check_cuda(cudaError_t status, const char* operation)
{
  if (status != cudaSuccess) {
    throw std::runtime_error(
        std::string(operation) + ": " + cudaGetErrorString(status));
  }
}

inline int checked_int(std::size_t value, const char* name)
{
  if (value > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    throw std::length_error(std::string(name) + " exceeds GPUMD's int range");
  }
  return static_cast<int>(value);
}

inline std::uint64_t file_fingerprint(const std::filesystem::path& path)
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

inline Box make_box(const BoxData& input)
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

// Rank-0 snapshot of the uniquely owned atoms, already restored to the global
// (input-slot) order by the gathering path.
struct HostSnapshot {
  std::vector<unsigned long long> global_id;
  std::vector<double> position;
  std::vector<double> velocity;
  std::vector<double> force;
  std::vector<double> potential;
  std::vector<double> virial;
  std::vector<double> unwrapped;
};

struct ThermoState {
  std::array<double, 8> values{};
};

using Measurement = std::variant<DumpThermoCommand, DumpXyzCommand, DumpRestartCommand>;

// ---------------------------------------------------------------------------
// Rank I/O isolation (docs/standards/replicated-mpi.md "I/O 与
// NEP_MULTIGPU"). Shared verbatim by both runtimes: only world rank 0 may
// write the job directory, every other rank works inside a private local
// scratch because legacy NEP opens neighbor.out by relative name.
// ---------------------------------------------------------------------------

// mkdtemp replaces exactly six trailing 'X' characters with random text.
constexpr std::size_t kScratchRandomSuffixLength = 6;

// Test-only fault injection hook. Setting
// DMGMD_RANK_IO_FAULT="<world_rank>:<operation>" (operation in {mkdir, file,
// chdir, setup_restore, restore, cleanup}) makes exactly that rank fail
// exactly that step, with no filesystem side effect.
inline bool rank_io_fault_requested(const char* operation, int world_rank)
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
inline std::filesystem::path strip_trailing_separators(std::filesystem::path path)
{
  while (path.has_relative_path() && path.filename().empty()) {
    path = path.parent_path();
  }
  return path;
}

// Random hex token used only for scratch naming and diagnostics. Uniqueness of
// the scratch directories themselves is owned by mkdtemp, so even a repeated
// nonce across concurrent jobs cannot make two ranks share a directory.
inline std::string make_job_nonce()
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
inline bool is_hex_token(const std::string& text)
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
inline std::string rank_io_diagnostic(
    const MpiRuntime& mpi,
    const char* operation,
    const std::filesystem::path& target,
    const std::string& reason)
{
  return "rank=" + std::to_string(mpi.world_rank()) + " hostname=" + mpi.hostname() +
         " " + operation + " " + target.string() + ": " + reason;
}

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

  // Symmetric teardown; every rank must call it exactly once (both runtimes
  // do, after all segments completed successfully).
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

// ---------------------------------------------------------------------------
// Kernels whose launch domain is fully parameterized, shared by both paths.
// Internal (anonymous) linkage per including TU; each .cu gets its own
// instance, so no cross-TU device linking is required.
// ---------------------------------------------------------------------------
namespace {

constexpr int kThreads = 128;
constexpr int kThermoThreads = 1024;

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

}  // namespace

// ---------------------------------------------------------------------------
// Shared host functions (defined in src/runtime.cu, used by both runtimes).
// The formatters and the CPU velocity correction operate on the root's full
// identity model and a global-order HostSnapshot, so the M2a gathering path
// reuses them byte-for-byte.
// ---------------------------------------------------------------------------

void zero_linear_momentum(
    const std::vector<double>& mass,
    std::vector<double>& velocity,
    const std::vector<std::size_t>& atoms);

void correct_velocity_subset(
    const std::vector<double>& mass,
    const std::vector<double>& position,
    std::vector<double>& velocity,
    const std::vector<std::size_t>& atoms);

// Root-side full-system random velocity initialization (GPUMD-compatible
// rand() sequence over the whole model), reused by both runtimes.
void initialize_random_velocity(
    HostAtoms& atoms,
    double temperature,
    std::optional<int> seed);

std::vector<std::size_t> output_order(
    const HostAtoms& identity,
    const HostSnapshot& snapshot,
    const DumpXyzCommand* command);

void print_tensor(FILE* file, const char* name, const char* format, const double* tensor);

void write_thermo_header(FILE* file, int interval, const HostAtoms& atoms, double time_step);

void write_thermo_row(FILE* file, const ThermoState& thermo, const HostAtoms& atoms, const Box& box);

void write_xyz(
    const DumpXyzCommand& command,
    int step,
    double global_time,
    const Box& box,
    const HostAtoms& identity,
    const HostSnapshot& snapshot,
    const ThermoState& thermo);

void write_restart(
    const Box& box,
    const HostAtoms& identity,
    const HostSnapshot& snapshot);

}  // namespace detail

// M2a local-domain runtime (src/domain_runtime.cu). Takes ownership of the
// exactly-once parsed NEP (deferred workspace) and the eligibility decision.
void run_local_domain(
    const RunProgram& program,
    Model model,
    gpumd_compat::Box box,
    std::unique_ptr<gpumd_compat::NEP> potential,
    const DomainEligibility& eligibility,
    MpiRuntime& mpi);

}  // namespace dmgmd
