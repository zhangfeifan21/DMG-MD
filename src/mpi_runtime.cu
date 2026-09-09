#include "dmgmd/mpi_runtime.hpp"

#include <cuda_runtime.h>
#include <mpi.h>
#include <mpi-ext.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

// Runtime contract and accounting formulas: docs/replicated-mpi.md. Keep the
// DMGMD_MPI, DMGMD_CENTER_* and DMGMD_COMM records stable because the MPI
// differential test parses them as executable evidence for that document.
namespace dmgmd {
namespace {

constexpr int kThreads = 128;

void check_cuda(cudaError_t status, const char* operation)
{
  if (status != cudaSuccess) {
    throw std::runtime_error(
        std::string(operation) + ": " + cudaGetErrorString(status));
  }
}

void check_mpi(int status, const char* operation)
{
  if (status == MPI_SUCCESS) return;
  std::array<char, MPI_MAX_ERROR_STRING> message{};
  int length = 0;
  MPI_Error_string(status, message.data(), &length);
  throw std::runtime_error(
      std::string(operation) + ": " + std::string(message.data(), length));
}

int checked_mpi_count(std::size_t count, const char* name)
{
  if (count > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
    throw std::length_error(std::string(name) + " exceeds the MPI int count range");
  }
  return static_cast<int>(count);
}

std::uint64_t checked_bytes(std::size_t elements)
{
  if (elements > std::numeric_limits<std::uint64_t>::max() / sizeof(double)) {
    throw std::overflow_error("communication byte count overflow");
  }
  return static_cast<std::uint64_t>(elements) * sizeof(double);
}

std::string lowercase(std::string text)
{
  std::transform(text.begin(), text.end(), text.begin(), [](unsigned char value) {
    return static_cast<char>(std::tolower(value));
  });
  return text;
}

std::string required_environment(const char* name)
{
  const char* value = std::getenv(name);
  if (value == nullptr || value[0] == '\0') {
    throw std::runtime_error(
        std::string(name) + " is unset; source ../env/md-mpi.sh before running dmg-md");
  }
  return value;
}

std::uint64_t positive_environment_interval(const char* name)
{
  const char* text = std::getenv(name);
  if (text == nullptr || text[0] == '\0') return 1;
  try {
    for (const unsigned char value : std::string(text)) {
      if (!std::isdigit(value)) throw std::invalid_argument("not a positive integer");
    }
    std::size_t consumed = 0;
    const unsigned long long value = std::stoull(text, &consumed);
    if (consumed != std::strlen(text) || value == 0) {
      throw std::invalid_argument("not a positive integer");
    }
    return static_cast<std::uint64_t>(value);
  } catch (const std::exception&) {
    throw std::runtime_error(std::string(name) + " must be a positive integer");
  }
}

template <typename T>
class DeviceBuffer {
 public:
  DeviceBuffer() = default;
  ~DeviceBuffer()
  {
    if (data_ != nullptr) cudaFree(data_);
  }

  DeviceBuffer(const DeviceBuffer&) = delete;
  DeviceBuffer& operator=(const DeviceBuffer&) = delete;

  void reserve(std::size_t size)
  {
    if (size <= capacity_) return;
    if (data_ != nullptr) check_cuda(cudaFree(data_), "free MPI device buffer");
    data_ = nullptr;
    capacity_ = 0;
    check_cuda(cudaMalloc(reinterpret_cast<void**>(&data_), size * sizeof(T)),
               "allocate MPI device buffer");
    capacity_ = size;
  }

  [[nodiscard]] T* data() noexcept { return data_; }
  [[nodiscard]] const T* data() const noexcept { return data_; }

 private:
  T* data_ = nullptr;
  std::size_t capacity_ = 0;
};

template <typename T>
class PinnedBuffer {
 public:
  PinnedBuffer() = default;
  ~PinnedBuffer()
  {
    if (data_ != nullptr) cudaFreeHost(data_);
  }

  PinnedBuffer(const PinnedBuffer&) = delete;
  PinnedBuffer& operator=(const PinnedBuffer&) = delete;

  void reserve(std::size_t size)
  {
    if (size <= capacity_) return;
    if (data_ != nullptr) check_cuda(cudaFreeHost(data_), "free MPI pinned buffer");
    data_ = nullptr;
    capacity_ = 0;
    check_cuda(cudaHostAlloc(reinterpret_cast<void**>(&data_), size * sizeof(T),
                             cudaHostAllocPortable),
               "allocate MPI pinned buffer");
    capacity_ = size;
  }

  [[nodiscard]] T* data() noexcept { return data_; }
  [[nodiscard]] const T* data() const noexcept { return data_; }

 private:
  T* data_ = nullptr;
  std::size_t capacity_ = 0;
};

__global__ void pack_owned_soa(
    int begin,
    int owned_count,
    int stride,
    int components,
    const double* soa,
    double* packed)
{
  const int item = blockIdx.x * blockDim.x + threadIdx.x;
  const int count = owned_count * components;
  if (item >= count) return;
  const int atom = item / components;
  const int component = item - atom * components;
  packed[item] = soa[component * stride + begin + atom];
}

__global__ void unpack_global_soa(
    int global_count,
    int components,
    const double* packed,
    double* soa)
{
  const int item = blockIdx.x * blockDim.x + threadIdx.x;
  const int count = global_count * components;
  if (item >= count) return;
  const int atom = item / components;
  const int component = item - atom * components;
  soa[component * global_count + atom] = packed[item];
}

std::string cuda_uuid_string(const cudaUUID_t& uuid)
{
  const auto* bytes = reinterpret_cast<const unsigned char*>(uuid.bytes);
  std::ostringstream output;
  output << std::hex << std::setfill('0');
  for (int index = 0; index < 16; ++index) {
    output << std::setw(2) << static_cast<unsigned int>(bytes[index]);
  }
  return output.str();
}

}  // namespace

class MpiRuntime::Impl {
 public:
  Impl(int& argc, char**& argv)
  {
    int initialized = 0;
    check_mpi(MPI_Initialized(&initialized), "MPI_Initialized");
    if (!initialized) {
      int provided = MPI_THREAD_SINGLE;
      check_mpi(MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provided),
                "MPI_Init_thread");
      owns_mpi = true;
      if (provided < MPI_THREAD_FUNNELED) {
        MPI_Finalize();
        throw std::runtime_error("MPI does not provide MPI_THREAD_FUNNELED");
      }
    }
    try {
      check_mpi(MPI_Comm_set_errhandler(MPI_COMM_WORLD, MPI_ERRORS_RETURN),
                "set MPI_ERRORS_RETURN");
      check_mpi(MPI_Comm_rank(MPI_COMM_WORLD, &rank), "MPI_Comm_rank");
      check_mpi(MPI_Comm_size(MPI_COMM_WORLD, &size), "MPI_Comm_size");
      check_mpi(MPI_Comm_split_type(
                    MPI_COMM_WORLD, MPI_COMM_TYPE_SHARED, rank, MPI_INFO_NULL, &local_comm),
                "MPI_Comm_split_type(MPI_COMM_TYPE_SHARED)");
      check_mpi(MPI_Comm_rank(local_comm, &local_rank), "local MPI_Comm_rank");
      check_mpi(MPI_Comm_size(local_comm, &local_size), "local MPI_Comm_size");

      communication_log_interval =
          positive_environment_interval("DMGMD_COMM_LOG_INTERVAL");
      unsigned long long local_interval = communication_log_interval;
      unsigned long long minimum_interval = 0;
      unsigned long long maximum_interval = 0;
      check_mpi(MPI_Allreduce(&local_interval, &minimum_interval, 1,
                              MPI_UNSIGNED_LONG_LONG, MPI_MIN, MPI_COMM_WORLD),
                "validate minimum communication log interval");
      check_mpi(MPI_Allreduce(&local_interval, &maximum_interval, 1,
                              MPI_UNSIGNED_LONG_LONG, MPI_MAX, MPI_COMM_WORLD),
                "validate maximum communication log interval");
      if (minimum_interval != maximum_interval) {
        throw std::runtime_error("DMGMD_COMM_LOG_INTERVAL differs between MPI ranks");
      }

      int hostname_length = 0;
      std::array<char, MPI_MAX_PROCESSOR_NAME> hostname_buffer{};
      check_mpi(MPI_Get_processor_name(hostname_buffer.data(), &hostname_length),
                "MPI_Get_processor_name");
      hostname.assign(hostname_buffer.data(), hostname_length);

      int library_length = 0;
      std::array<char, MPI_MAX_LIBRARY_VERSION_STRING> library_buffer{};
      check_mpi(MPI_Get_library_version(library_buffer.data(), &library_length),
                "MPI_Get_library_version");
      mpi_library.assign(library_buffer.data(), library_length);
      while (!mpi_library.empty() && mpi_library.back() == '\0') mpi_library.pop_back();
      std::replace(mpi_library.begin(), mpi_library.end(), '\n', ' ');
      std::replace(mpi_library.begin(), mpi_library.end(), '\r', ' ');

      // This is a deliberate deployment contract, mirrored by CMake, the MPI
      // preflight, and docs/replicated-mpi.md.  Failing here prevents a stale
      // system libmpi from masquerading as a force/trajectory regression.
      if (mpi_library.find("Open MPI") == std::string::npos) {
        throw std::runtime_error("dmg-md requires the Open MPI + UCX runtime stack");
      }
      ompi_home = required_environment("OMPI_HOME");
      ucx_home = required_environment("UCX_HOME");
      if (required_environment("OMPI_MCA_pml") != "ucx") {
        throw std::runtime_error("OMPI_MCA_pml must select ucx; source ../env/md-mpi.sh");
      }
      if (required_environment("OMPI_MCA_coll") != "^hcoll") {
        throw std::runtime_error(
            "OMPI_MCA_coll must exclude hcoll for CUDA buffers; source ../env/md-mpi.sh");
      }

    } catch (...) {
      cleanup_mpi();
      throw;
    }
  }

  ~Impl()
  {
    cleanup_mpi();
  }

  void cleanup_mpi() noexcept
  {
    int finalized = 0;
    if (MPI_Finalized(&finalized) != MPI_SUCCESS || finalized) return;
    if (local_comm != MPI_COMM_NULL) {
      MPI_Comm_free(&local_comm);
      local_comm = MPI_COMM_NULL;
    }
    if (owns_mpi) {
      MPI_Finalize();
      owns_mpi = false;
    }
  }

  void bind_cuda_device()
  {
    int device_count = 0;
    check_cuda(cudaGetDeviceCount(&device_count), "query visible CUDA devices");
    if (device_count <= 0) throw std::runtime_error("no visible CUDA device");

    // CUDA_VISIBLE_DEVICES may expose one rank-private device or a shared
    // node-wide list. Both layouts map one local rank to exactly one GPU; the
    // UUID allgather below rejects accidental aliasing.
    if (device_count == 1) {
      device = 0;
    } else {
      if (local_rank >= device_count) {
        throw std::runtime_error(
            "local MPI rank exceeds visible CUDA device count; one rank requires one GPU");
      }
      device = local_rank;
    }
    check_cuda(cudaSetDevice(device), "select CUDA device for local MPI rank");

    cudaDeviceProp properties{};
    check_cuda(cudaGetDeviceProperties(&properties, device), "query selected CUDA device");
    device_name = properties.name;
    device_uuid = cuda_uuid_string(properties.uuid);

    constexpr int uuid_width = 33;
    std::array<char, uuid_width> local_uuid{};
    std::copy(device_uuid.begin(), device_uuid.end(), local_uuid.begin());
    std::vector<char> all_uuids(static_cast<std::size_t>(local_size) * uuid_width);
    check_mpi(MPI_Allgather(local_uuid.data(), uuid_width, MPI_CHAR,
                            all_uuids.data(), uuid_width, MPI_CHAR, local_comm),
              "allgather selected CUDA UUIDs");
    for (int left = 0; left < local_size; ++left) {
      for (int right = left + 1; right < local_size; ++right) {
        const char* lhs = all_uuids.data() + left * uuid_width;
        const char* rhs = all_uuids.data() + right * uuid_width;
        if (std::strncmp(lhs, rhs, uuid_width) == 0) {
          throw std::runtime_error(
              "two local MPI ranks selected the same CUDA device UUID; enforce one rank per GPU");
        }
      }
    }
  }

  bool cuda_aware_self_test()
  {
    DeviceBuffer<double> send;
    DeviceBuffer<double> reduction_receive;
    DeviceBuffer<double> gathered_receive;
    send.reserve(1);
    reduction_receive.reserve(1);
    gathered_receive.reserve(static_cast<std::size_t>(size));
    const double input = static_cast<double>(rank + 1);
    check_cuda(cudaMemcpy(send.data(), &input, sizeof(double), cudaMemcpyHostToDevice),
               "initialize CUDA-aware MPI self-test");
    check_cuda(cudaMemcpy(reduction_receive.data(), &input, sizeof(double),
                          cudaMemcpyHostToDevice),
               "initialize in-place CUDA-aware reduction self-test");
    check_cuda(cudaDeviceSynchronize(), "synchronize CUDA-aware MPI self-test input");

    // Probe every collective that the CudaAware backend will receive a device
    // pointer for, including production's in-place Allreduce and out-of-place
    // gather forms. A successful reduction alone does not imply that
    // gather/broadcast collectives are supported by the selected transport.
    const int allreduce_status = MPI_Allreduce(
        MPI_IN_PLACE, reduction_receive.data(), 1, MPI_DOUBLE, MPI_SUM, MPI_COMM_WORLD);
    bool local_pass = allreduce_status == MPI_SUCCESS;
    local_pass = local_pass && cudaDeviceSynchronize() == cudaSuccess;
    double reduction_result = 0.0;
    local_pass = local_pass &&
                 cudaMemcpy(&reduction_result, reduction_receive.data(), sizeof(double),
                            cudaMemcpyDeviceToHost) == cudaSuccess;
    const double expected = 0.5 * static_cast<double>(size) * static_cast<double>(size + 1);
    local_pass = local_pass && reduction_result == expected;

    std::vector<int> counts(static_cast<std::size_t>(size), 1);
    std::vector<int> displacements(static_cast<std::size_t>(size));
    for (int source = 0; source < size; ++source) displacements[source] = source;
    const int allgatherv_status = MPI_Allgatherv(
        send.data(), 1, MPI_DOUBLE, gathered_receive.data(), counts.data(),
        displacements.data(), MPI_DOUBLE, MPI_COMM_WORLD);
    local_pass = local_pass && allgatherv_status == MPI_SUCCESS;
    local_pass = local_pass && cudaDeviceSynchronize() == cudaSuccess;
    std::vector<double> allgathered(static_cast<std::size_t>(size));
    local_pass = local_pass &&
                 cudaMemcpy(allgathered.data(), gathered_receive.data(),
                            checked_bytes(allgathered.size()), cudaMemcpyDeviceToHost) ==
                     cudaSuccess;
    for (int source = 0; source < size; ++source) {
      local_pass = local_pass &&
                   allgathered[static_cast<std::size_t>(source)] ==
                       static_cast<double>(source + 1);
    }

    const int gatherv_status = MPI_Gatherv(
        send.data(), 1, MPI_DOUBLE, rank == 0 ? gathered_receive.data() : nullptr,
        counts.data(), displacements.data(), MPI_DOUBLE, 0, MPI_COMM_WORLD);
    local_pass = local_pass && gatherv_status == MPI_SUCCESS;
    local_pass = local_pass && cudaDeviceSynchronize() == cudaSuccess;
    if (rank == 0) {
      std::vector<double> gathered(static_cast<std::size_t>(size));
      local_pass = local_pass &&
                   cudaMemcpy(gathered.data(), gathered_receive.data(),
                              checked_bytes(gathered.size()), cudaMemcpyDeviceToHost) ==
                       cudaSuccess;
      for (int source = 0; source < size; ++source) {
        local_pass = local_pass &&
                     gathered[static_cast<std::size_t>(source)] ==
                         static_cast<double>(source + 1);
      }
    }

    const int broadcast_status = MPI_Bcast(send.data(), 1, MPI_DOUBLE, 0, MPI_COMM_WORLD);
    local_pass = local_pass && broadcast_status == MPI_SUCCESS;
    local_pass = local_pass && cudaDeviceSynchronize() == cudaSuccess;
    double broadcast_result = 0.0;
    local_pass = local_pass &&
                 cudaMemcpy(&broadcast_result, send.data(), sizeof(double),
                            cudaMemcpyDeviceToHost) == cudaSuccess;
    local_pass = local_pass && broadcast_result == 1.0;

    int all_pass = 0;
    const int local_value = local_pass ? 1 : 0;
    check_mpi(MPI_Allreduce(&local_value, &all_pass, 1, MPI_INT, MPI_MIN, MPI_COMM_WORLD),
              "reduce CUDA-aware MPI self-test status");
    return all_pass == 1;
  }

  bool query_cuda_aware_support()
  {
    // Open MPI's extension query is safe because it does not hand MPI a CUDA
    // pointer.  The active numerical self-test remains a second, stricter
    // gate: no production device pointer is used on the strength of this
    // Open MPI capability bit alone.
    const int local_support = MPIX_Query_cuda_support() == 1 ? 1 : 0;
    int all_support = 0;
    check_mpi(MPI_Allreduce(
                  &local_support, &all_support, 1, MPI_INT, MPI_MIN, MPI_COMM_WORLD),
              "reduce Open MPI CUDA-aware capability query");
    return all_support == 1;
  }

  void select_backend()
  {
    const char* requested_environment = std::getenv("DMGMD_COMM_BACKEND");
    std::string requested = requested_environment == nullptr
                                ? "hoststaged"
                                : lowercase(requested_environment);
    int requested_code = -1;
    if (requested == "hoststaged" || requested == "host_staged") requested_code = 0;
    if (requested == "cudaaware" || requested == "cuda_aware") requested_code = 1;
    if (requested_code < 0) {
      throw std::runtime_error(
          "DMGMD_COMM_BACKEND must be HostStaged or CudaAware");
    }
    int minimum = 0;
    int maximum = 0;
    check_mpi(MPI_Allreduce(
                  &requested_code, &minimum, 1, MPI_INT, MPI_MIN, MPI_COMM_WORLD),
              "validate minimum requested communication backend");
    check_mpi(MPI_Allreduce(
                  &requested_code, &maximum, 1, MPI_INT, MPI_MAX, MPI_COMM_WORLD),
              "validate maximum requested communication backend");
    if (minimum != maximum) {
      throw std::runtime_error("DMGMD_COMM_BACKEND differs between MPI ranks");
    }

    cuda_aware_capability = query_cuda_aware_support() ? "supported" : "unsupported";

    const char* probe_environment = std::getenv("DMGMD_CUDA_AWARE_PROBE");
    const bool probe_requested =
        requested_code == 1 ||
        (probe_environment != nullptr && std::string(probe_environment) == "1");
    if (probe_requested && cuda_aware_capability == "supported") {
      cuda_aware_self_test_status = cuda_aware_self_test() ? "passed" : "failed";
    }

    // The Open MPI query and every production collective form a two-key gate.
    // HostStaged remains the default and never passes a device pointer to MPI.
    if (requested_code == 1 && cuda_aware_self_test_status != "passed") {
      backend = CommunicationBackend::host_staged;
      cuda_aware_fallback = true;
    } else {
      backend = requested_code == 1 ? CommunicationBackend::cuda_aware
                                    : CommunicationBackend::host_staged;
    }
  }

  void log_startup()
  {
    std::ostringstream line;
    line << "DMGMD_MPI rank=" << rank << " world_size=" << size
         << " local_rank=" << local_rank << " local_size=" << local_size
         << " hostname=" << hostname << " cuda_device=" << device
         << " cuda_uuid=" << device_uuid << " cuda_name=\"" << device_name << "\""
         << " cuda_aware_capability=" << cuda_aware_capability
         << " cuda_aware_self_test=" << cuda_aware_self_test_status
         << " backend="
         << (backend == CommunicationBackend::host_staged ? "HostStaged" : "CudaAware")
         << " nep_device_policy=ordinary-NEP-single-selected-device";
    if (cuda_aware_fallback) line << " requested_CudaAware_fallback=HostStaged";
    const std::string local_line = line.str();
    const int local_length = checked_mpi_count(local_line.size(), "startup log line");
    std::vector<int> lengths(rank == 0 ? static_cast<std::size_t>(size) : 0);
    check_mpi(MPI_Gather(&local_length, 1, MPI_INT,
                         rank == 0 ? lengths.data() : nullptr, 1, MPI_INT, 0,
                         MPI_COMM_WORLD),
              "gather MPI startup log lengths");
    std::vector<int> displacements(rank == 0 ? static_cast<std::size_t>(size) : 0);
    std::vector<char> lines;
    if (rank == 0) {
      int total = 0;
      for (int index = 0; index < size; ++index) {
        displacements[index] = total;
        total += lengths[index];
      }
      lines.resize(static_cast<std::size_t>(total));
    }
    check_mpi(MPI_Gatherv(local_line.data(), local_length, MPI_CHAR,
                          rank == 0 ? lines.data() : nullptr,
                          rank == 0 ? lengths.data() : nullptr,
                          rank == 0 ? displacements.data() : nullptr,
                          MPI_CHAR, 0, MPI_COMM_WORLD),
              "gather MPI startup log records");
    if (rank == 0) {
      std::cout << "DMGMD_MPI implementation=\"" << mpi_library << "\"\n";
      std::cout << "DMGMD_MPI_STACK provider=OpenMPI transport=UCX"
                << " ompi_home=\"" << ompi_home << "\""
                << " ucx_home=\"" << ucx_home << "\""
                << " pml=ucx coll_hcoll=disabled\n";
      for (int index = 0; index < size; ++index) {
        std::cout.write(lines.data() + displacements[index], lengths[index]);
        std::cout << '\n';
      }
      std::cout << "DMGMD_COMM accounting=collective-buffer-bytes "
                   "physical-wire-bytes=openmpi-ucx-algorithm-dependent"
                << " log_interval=" << communication_log_interval << '\n';
      std::cout.flush();
    }
  }

  void counts_and_displacements(
      std::size_t global_count,
      int components,
      std::vector<int>& counts,
      std::vector<int>& displacements) const
  {
    counts.resize(static_cast<std::size_t>(size));
    displacements.resize(static_cast<std::size_t>(size));
    for (int source = 0; source < size; ++source) {
      const OwnedRange range = balanced_owned_range(global_count, source, size);
      counts[source] = checked_mpi_count(
          range.size() * static_cast<std::size_t>(components), "MPI shard element count");
      displacements[source] = checked_mpi_count(
          range.begin * static_cast<std::size_t>(components), "MPI shard displacement");
    }
  }

  bool owns_mpi = false;
  int rank = 0;
  int size = 1;
  int local_rank = 0;
  int local_size = 1;
  int device = 0;
  MPI_Comm local_comm = MPI_COMM_NULL;
  CommunicationBackend backend = CommunicationBackend::host_staged;
  std::string hostname;
  std::string mpi_library;
  std::string ompi_home;
  std::string ucx_home;
  std::string device_name;
  std::string device_uuid;
  std::string cuda_aware_capability = "unknown";
  std::string cuda_aware_self_test_status = "not-run";
  bool cuda_aware_fallback = false;
  bool device_initialized = false;
  std::uint64_t communication_log_interval = 1;
  DeviceBuffer<double> device_send;
  DeviceBuffer<double> device_receive;
  PinnedBuffer<double> host_send;
  PinnedBuffer<double> host_receive;
};

MpiRuntime::MpiRuntime(int& argc, char**& argv)
    : impl_(std::make_unique<Impl>(argc, argv))
{
}

MpiRuntime::~MpiRuntime() = default;

int MpiRuntime::world_rank() const noexcept { return impl_->rank; }
int MpiRuntime::world_size() const noexcept { return impl_->size; }
int MpiRuntime::local_rank() const noexcept { return impl_->local_rank; }
int MpiRuntime::local_size() const noexcept { return impl_->local_size; }
int MpiRuntime::cuda_device() const noexcept { return impl_->device; }
bool MpiRuntime::is_root() const noexcept { return impl_->rank == 0; }
CommunicationBackend MpiRuntime::backend() const noexcept { return impl_->backend; }
const char* MpiRuntime::backend_name() const noexcept
{
  return backend() == CommunicationBackend::host_staged ? "HostStaged" : "CudaAware";
}

void MpiRuntime::initialize_device()
{
  if (impl_->device_initialized) return;
  impl_->bind_cuda_device();
  impl_->select_backend();
  impl_->device_initialized = true;
  impl_->log_startup();
}

void MpiRuntime::barrier() const
{
  check_mpi(MPI_Barrier(MPI_COMM_WORLD), "MPI_Barrier");
}

std::string MpiRuntime::broadcast_string(std::string value, int root) const
{
  int length = world_rank() == root ? checked_mpi_count(value.size(), "broadcast string") : 0;
  check_mpi(MPI_Bcast(&length, 1, MPI_INT, root, MPI_COMM_WORLD),
            "broadcast string length");
  value.resize(static_cast<std::size_t>(length));
  check_mpi(MPI_Bcast(value.data(), length, MPI_CHAR, root, MPI_COMM_WORLD),
            "broadcast string data");
  return value;
}

void MpiRuntime::broadcast_doubles(double* values, std::size_t count, int root) const
{
  check_mpi(MPI_Bcast(values, checked_mpi_count(count, "double broadcast"), MPI_DOUBLE,
                      root, MPI_COMM_WORLD),
            "broadcast replicated doubles");
}

void MpiRuntime::assert_same_fingerprint(std::uint64_t fingerprint, const char* name) const
{
  static_assert(sizeof(std::uint64_t) == sizeof(unsigned long long));
  const auto local = static_cast<unsigned long long>(fingerprint);
  unsigned long long minimum = 0;
  unsigned long long maximum = 0;
  check_mpi(MPI_Allreduce(&local, &minimum, 1, MPI_UNSIGNED_LONG_LONG, MPI_MIN,
                          MPI_COMM_WORLD),
            "reduce minimum input fingerprint");
  check_mpi(MPI_Allreduce(&local, &maximum, 1, MPI_UNSIGNED_LONG_LONG, MPI_MAX,
                          MPI_COMM_WORLD),
            "reduce maximum input fingerprint");
  if (minimum != maximum) {
    throw std::runtime_error(std::string(name) + " differs between MPI ranks");
  }
}

void MpiRuntime::verify_and_log_center_partition(
    std::size_t global_count,
    OwnedRange owned) const
{
  if (owned.end > global_count || owned.begin > owned.end) {
    throw std::logic_error("owned center range is outside replicated input");
  }
  // This collective is deliberately explicit proof, not an inference from
  // balanced_owned_range(): every global center must have exactly one owner.
  std::vector<int> local(global_count, 0);
  std::fill(local.begin() + static_cast<std::ptrdiff_t>(owned.begin),
            local.begin() + static_cast<std::ptrdiff_t>(owned.end), 1);
  std::vector<int> coverage(global_count, 0);
  check_mpi(MPI_Allreduce(local.data(), coverage.data(),
                          checked_mpi_count(global_count, "center coverage"), MPI_INT,
                          MPI_SUM, MPI_COMM_WORLD),
            "allreduce center coverage");
  std::size_t missing = 0;
  std::size_t overlapping = 0;
  for (int owners : coverage) {
    if (owners == 0) ++missing;
    if (owners > 1) ++overlapping;
  }
  if (missing != 0 || overlapping != 0) {
    throw std::logic_error("MPI center ownership is incomplete or overlapping");
  }
  if (is_root()) {
    std::cout << "DMGMD_CENTER_PARTITION global_count=" << global_count
              << " ranks=" << world_size() << " missing=" << missing
              << " overlapping=" << overlapping
              << " owned_output_coverage=complete"
              << " nep_kernel_centers=replicated-full"
              << " nep_N1_N2_shard_complete=false"
              << " reason=remote-Fp-and-reverse-partial-dependencies\n";
    for (int source = 0; source < world_size(); ++source) {
      const OwnedRange range = balanced_owned_range(global_count, source, world_size());
      std::cout << "DMGMD_CENTER_RANGE rank=" << source << " begin=" << range.begin
                << " end=" << range.end << " count=" << range.size() << '\n';
    }
    std::cout.flush();
  }
}

void MpiRuntime::allgather_owned_device_soa(
    double* device_values,
    int components,
    std::size_t stride,
    OwnedRange owned,
    CommunicationVolume& volume)
{
  if (components <= 0 || stride == 0 || owned.end > stride) {
    throw std::invalid_argument("invalid owned SoA allgather shape");
  }
  const std::size_t send_elements = owned.size() * static_cast<std::size_t>(components);
  const std::size_t receive_elements = stride * static_cast<std::size_t>(components);
  impl_->device_send.reserve(std::max<std::size_t>(send_elements, 1));
  impl_->device_receive.reserve(std::max<std::size_t>(receive_elements, 1));
  if (send_elements != 0) {
    pack_owned_soa<<<(send_elements + kThreads - 1) / kThreads, kThreads>>>(
        checked_mpi_count(owned.begin, "owned begin"),
        checked_mpi_count(owned.size(), "owned count"),
        checked_mpi_count(stride, "SoA stride"), components,
        device_values, impl_->device_send.data());
    check_cuda(cudaGetLastError(), "pack owned replicated field");
  }

  std::vector<int> counts;
  std::vector<int> displacements;
  impl_->counts_and_displacements(stride, components, counts, displacements);
  // HostStaged follows device -> pinned send -> MPI -> pinned receive ->
  // device. CudaAware changes only transport, never layout or ownership.
  if (backend() == CommunicationBackend::host_staged) {
    impl_->host_send.reserve(std::max<std::size_t>(send_elements, 1));
    impl_->host_receive.reserve(std::max<std::size_t>(receive_elements, 1));
    if (send_elements != 0) {
      check_cuda(cudaMemcpy(impl_->host_send.data(), impl_->device_send.data(),
                            checked_bytes(send_elements), cudaMemcpyDeviceToHost),
                 "stage owned field from CUDA device to pinned host");
    }
    check_mpi(MPI_Allgatherv(
                  impl_->host_send.data(), checked_mpi_count(send_elements, "allgather send"),
                  MPI_DOUBLE, impl_->host_receive.data(), counts.data(), displacements.data(),
                  MPI_DOUBLE, MPI_COMM_WORLD),
              "HostStaged MPI_Allgatherv");
    check_cuda(cudaMemcpy(impl_->device_receive.data(), impl_->host_receive.data(),
                          checked_bytes(receive_elements), cudaMemcpyHostToDevice),
               "stage replicated field from pinned host to CUDA device");
    volume.device_to_host_bytes_global += checked_bytes(receive_elements);
    volume.host_to_device_bytes_global +=
        checked_bytes(receive_elements) * static_cast<std::uint64_t>(world_size());
  } else {
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware allgather input");
    check_mpi(MPI_Allgatherv(
                  impl_->device_send.data(), checked_mpi_count(send_elements, "allgather send"),
                  MPI_DOUBLE, impl_->device_receive.data(), counts.data(), displacements.data(),
                  MPI_DOUBLE, MPI_COMM_WORLD),
              "CudaAware MPI_Allgatherv");
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware allgather output");
  }
  unpack_global_soa<<<(receive_elements + kThreads - 1) / kThreads, kThreads>>>(
      checked_mpi_count(stride, "SoA stride"), components,
      impl_->device_receive.data(), device_values);
  check_cuda(cudaGetLastError(), "unpack replicated field");

  ++volume.collective_calls;
  volume.mpi_input_bytes_global += checked_bytes(receive_elements);
  volume.mpi_output_bytes_global +=
      checked_bytes(receive_elements) * static_cast<std::uint64_t>(world_size());
}

std::vector<double> MpiRuntime::gather_owned_device_soa_to_root(
    const double* device_values,
    int components,
    std::size_t stride,
    OwnedRange owned,
    CommunicationVolume& volume)
{
  if (components <= 0 || stride == 0 || owned.end > stride) {
    throw std::invalid_argument("invalid owned SoA gather shape");
  }
  const std::size_t send_elements = owned.size() * static_cast<std::size_t>(components);
  const std::size_t receive_elements = stride * static_cast<std::size_t>(components);
  impl_->device_send.reserve(std::max<std::size_t>(send_elements, 1));
  if (is_root() && backend() == CommunicationBackend::cuda_aware) {
    impl_->device_receive.reserve(std::max<std::size_t>(receive_elements, 1));
  }
  if (send_elements != 0) {
    pack_owned_soa<<<(send_elements + kThreads - 1) / kThreads, kThreads>>>(
        checked_mpi_count(owned.begin, "owned begin"),
        checked_mpi_count(owned.size(), "owned count"),
        checked_mpi_count(stride, "SoA stride"), components,
        device_values, impl_->device_send.data());
    check_cuda(cudaGetLastError(), "pack owned output field");
  }

  std::vector<int> counts;
  std::vector<int> displacements;
  impl_->counts_and_displacements(stride, components, counts, displacements);
  std::vector<double> packed(is_root() ? receive_elements : 0);
  if (backend() == CommunicationBackend::host_staged) {
    impl_->host_send.reserve(std::max<std::size_t>(send_elements, 1));
    if (is_root()) impl_->host_receive.reserve(std::max<std::size_t>(receive_elements, 1));
    if (send_elements != 0) {
      check_cuda(cudaMemcpy(impl_->host_send.data(), impl_->device_send.data(),
                            checked_bytes(send_elements), cudaMemcpyDeviceToHost),
                 "stage owned output from CUDA device to pinned host");
    }
    check_mpi(MPI_Gatherv(
                  impl_->host_send.data(), checked_mpi_count(send_elements, "gather send"),
                  MPI_DOUBLE, is_root() ? impl_->host_receive.data() : nullptr,
                  counts.data(), displacements.data(), MPI_DOUBLE, 0, MPI_COMM_WORLD),
              "HostStaged MPI_Gatherv");
    if (is_root()) {
      std::copy_n(impl_->host_receive.data(), receive_elements, packed.data());
    }
    volume.device_to_host_bytes_global += checked_bytes(receive_elements);
  } else {
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware gather input");
    check_mpi(MPI_Gatherv(
                  impl_->device_send.data(), checked_mpi_count(send_elements, "gather send"),
                  MPI_DOUBLE, is_root() ? impl_->device_receive.data() : nullptr,
                  counts.data(), displacements.data(), MPI_DOUBLE, 0, MPI_COMM_WORLD),
              "CudaAware MPI_Gatherv");
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware gather output");
    if (is_root()) {
      check_cuda(cudaMemcpy(packed.data(), impl_->device_receive.data(),
                            checked_bytes(receive_elements), cudaMemcpyDeviceToHost),
                 "download gathered owned output");
      volume.output_download_bytes += checked_bytes(receive_elements);
    }
  }

  std::vector<double> soa(is_root() ? receive_elements : 0);
  if (is_root()) {
    for (std::size_t atom = 0; atom < stride; ++atom) {
      for (int component = 0; component < components; ++component) {
        soa[static_cast<std::size_t>(component) * stride + atom] =
            packed[atom * static_cast<std::size_t>(components) + component];
      }
    }
  }
  ++volume.collective_calls;
  volume.mpi_input_bytes_global += checked_bytes(receive_elements);
  volume.mpi_output_bytes_global += checked_bytes(receive_elements);
  return soa;
}

void MpiRuntime::allreduce_sum_device(
    double* device_values,
    int count,
    CommunicationVolume& volume)
{
  if (count <= 0) throw std::invalid_argument("invalid device allreduce count");
  const std::size_t elements = static_cast<std::size_t>(count);
  if (backend() == CommunicationBackend::host_staged) {
    impl_->host_send.reserve(elements);
    check_cuda(cudaMemcpy(impl_->host_send.data(), device_values, checked_bytes(elements),
                          cudaMemcpyDeviceToHost),
               "stage reduction input from CUDA device to pinned host");
    check_mpi(MPI_Allreduce(MPI_IN_PLACE, impl_->host_send.data(), count, MPI_DOUBLE,
                            MPI_SUM, MPI_COMM_WORLD),
              "HostStaged MPI_Allreduce");
    check_cuda(cudaMemcpy(device_values, impl_->host_send.data(), checked_bytes(elements),
                          cudaMemcpyHostToDevice),
               "stage reduction output from pinned host to CUDA device");
    volume.device_to_host_bytes_global +=
        checked_bytes(elements) * static_cast<std::uint64_t>(world_size());
    volume.host_to_device_bytes_global +=
        checked_bytes(elements) * static_cast<std::uint64_t>(world_size());
  } else {
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware allreduce input");
    check_mpi(MPI_Allreduce(MPI_IN_PLACE, device_values, count, MPI_DOUBLE,
                            MPI_SUM, MPI_COMM_WORLD),
              "CudaAware MPI_Allreduce");
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware allreduce output");
  }
  ++volume.collective_calls;
  volume.mpi_input_bytes_global +=
      checked_bytes(elements) * static_cast<std::uint64_t>(world_size());
  volume.mpi_output_bytes_global +=
      checked_bytes(elements) * static_cast<std::uint64_t>(world_size());
}

void MpiRuntime::broadcast_device(
    double* device_values,
    std::size_t count,
    CommunicationVolume& volume,
    int root)
{
  const int mpi_count = checked_mpi_count(count, "device broadcast");
  if (root < 0 || root >= world_size()) throw std::invalid_argument("invalid broadcast root");
  if (backend() == CommunicationBackend::host_staged) {
    impl_->host_receive.reserve(std::max<std::size_t>(count, 1));
    if (world_rank() == root && count != 0) {
      check_cuda(cudaMemcpy(impl_->host_receive.data(), device_values, checked_bytes(count),
                            cudaMemcpyDeviceToHost),
                 "stage broadcast input from CUDA device to pinned host");
    }
    check_mpi(MPI_Bcast(impl_->host_receive.data(), mpi_count, MPI_DOUBLE, root,
                        MPI_COMM_WORLD),
              "HostStaged MPI_Bcast");
    if (count != 0) {
      check_cuda(cudaMemcpy(device_values, impl_->host_receive.data(), checked_bytes(count),
                            cudaMemcpyHostToDevice),
                 "stage broadcast output from pinned host to CUDA device");
    }
    volume.device_to_host_bytes_global += checked_bytes(count);
    volume.host_to_device_bytes_global +=
        checked_bytes(count) * static_cast<std::uint64_t>(world_size());
  } else {
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware broadcast input");
    check_mpi(MPI_Bcast(device_values, mpi_count, MPI_DOUBLE, root, MPI_COMM_WORLD),
              "CudaAware MPI_Bcast");
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware broadcast output");
  }
  ++volume.collective_calls;
  volume.mpi_input_bytes_global += checked_bytes(count);
  volume.mpi_output_bytes_global +=
      checked_bytes(count) * static_cast<std::uint64_t>(world_size());
}

double MpiRuntime::allreduce_max_host(double value, CommunicationVolume& volume) const
{
  double result = 0.0;
  check_mpi(MPI_Allreduce(&value, &result, 1, MPI_DOUBLE, MPI_MAX, MPI_COMM_WORLD),
            "MPI_Allreduce adaptive timestep maximum");
  ++volume.collective_calls;
  volume.mpi_input_bytes_global += sizeof(double) * static_cast<std::uint64_t>(world_size());
  volume.mpi_output_bytes_global += sizeof(double) * static_cast<std::uint64_t>(world_size());
  return result;
}

void MpiRuntime::log_step_communication(
    std::uint64_t step,
    const CommunicationVolume& volume) const
{
  if (!is_root()) return;
  if (step % impl_->communication_log_interval != 0) return;
  std::cout << "DMGMD_COMM step=" << step << " backend=" << backend_name()
            << " collective_calls=" << volume.collective_calls
            << " mpi_input_bytes_global=" << volume.mpi_input_bytes_global
            << " mpi_output_bytes_global=" << volume.mpi_output_bytes_global
            << " device_to_host_bytes_global=" << volume.device_to_host_bytes_global
            << " host_to_device_bytes_global=" << volume.host_to_device_bytes_global
            << " output_download_bytes=" << volume.output_download_bytes << '\n';
  std::cout.flush();
}

[[noreturn]] void MpiRuntime::abort(int error_code) const
{
  MPI_Abort(MPI_COMM_WORLD, error_code);
  std::abort();
}

}  // namespace dmgmd
