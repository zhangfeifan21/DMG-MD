#pragma once

#include "dmgmd/partition.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace dmgmd {

enum class CommunicationBackend {
  host_staged,
  cuda_aware,
};

// Keep this public boundary synchronized with docs/replicated-mpi.md: callers
// supply replicated input buffers plus an OwnedRange, while MpiRuntime owns
// backend selection, staging, collective calls, and byte accounting.
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

  // Kept separate from MPI initialization so run/model syntax errors retain
  // the single-rank fail-fast behavior and do not require a working GPU.
  void initialize_device();
  void barrier() const;
  std::string broadcast_string(std::string value, int root = 0) const;
  void broadcast_doubles(double* values, std::size_t count, int root = 0) const;
  void assert_same_fingerprint(std::uint64_t fingerprint, const char* name) const;
  void verify_and_log_center_partition(std::size_t global_count, OwnedRange owned) const;

  void allgather_owned_device_soa(
      double* device_values,
      int components,
      std::size_t stride,
      OwnedRange owned,
      CommunicationVolume& volume);

  std::vector<double> gather_owned_device_soa_to_root(
      const double* device_values,
      int components,
      std::size_t stride,
      OwnedRange owned,
      CommunicationVolume& volume);

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

  [[noreturn]] void abort(int error_code) const;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace dmgmd
