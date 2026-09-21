#include "dmgmd/mpi_runtime.hpp"

#include <cuda_runtime.h>
#include <mpi.h>
#include <mpi-ext.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdio>
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

// Runtime contract and accounting formulas: docs/standards/replicated-mpi.md. Keep the
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
  // Keep default runtime output low-frequency. Tests or investigations that
  // parse every communication record opt in explicitly with interval=1.
  if (text == nullptr || text[0] == '\0') return 1000;
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

// M1 indexed pack: item = atom-major AoS position of one owned slot's
// component. The owned index list is sorted by global_id and identical in
// plan order on every rank, so the gathered stream is deterministic.
__global__ void pack_indexed_soa(
    const int* owned_indices,
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
  packed[item] = soa[component * stride + owned_indices[atom]];
}

// M1 indexed unpack: item = atom-major AoS position of one gathered atom's
// component; scatter_slots[item's atom] restores the replicated slot so the
// SoA is rebuilt in slot order rather than rank concatenation order.
__global__ void unpack_indexed_soa(
    const int* scatter_slots,
    int global_count,
    int stride,
    int components,
    const double* packed,
    double* soa)
{
  const int item = blockIdx.x * blockDim.x + threadIdx.x;
  const int count = global_count * components;
  if (item >= count) return;
  const int atom = item / components;
  const int component = item - atom * components;
  soa[component * stride + scatter_slots[atom]] = packed[item];
}

// M2a prefix pack: the owned prefix [0, owned_count) of a local SoA into an
// atom-major AoS stream (identity version of pack_indexed_soa; the owned
// section of the local layout is contiguous and global_id ordered).
__global__ void pack_prefix_soa(
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
  packed[item] = soa[component * stride + atom];
}

// M2a prefix unpack: an atom-major AoS stream onto the owned prefix of a
// local SoA (used by the correct_velocity scatter).
__global__ void unpack_prefix_soa(
    int owned_count,
    int stride,
    int components,
    const double* packed,
    double* soa)
{
  const int item = blockIdx.x * blockDim.x + threadIdx.x;
  const int count = owned_count * components;
  if (item >= count) return;
  const int atom = item / components;
  const int component = item - atom * components;
  soa[component * stride + atom] = packed[item];
}

// M2a point-to-point tags. Directions are independent so P=2's identical
// left/right peer can never mismatch messages; the self-test uses its own tag.
constexpr int kTagSendLeft = 1101;
constexpr int kTagSendRight = 1102;
constexpr int kTagP2pSelfTest = 2101;

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
      // preflight, and docs/standards/replicated-mpi.md. Failing here prevents a stale
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

    // M2a gate: a real device-buffer point-to-point Send/Recv pair with the
    // production tag scheme (independent left/right tags, which also covers
    // P=2's identical peer and P=1's self-message). Each rank sends its rank
    // index to the left peer and rank+1000 to the right peer, then verifies
    // both received values. A failure here fails the whole self-test, so
    // CudaAware falls back to HostStaged exactly like a collective failure.
    {
      DeviceBuffer<double> p2p_send;
      DeviceBuffer<double> p2p_receive;
      p2p_send.reserve(2);
      p2p_receive.reserve(2);
      const double payload[2] = {static_cast<double>(rank),
                                 static_cast<double>(rank + 1000)};
      check_cuda(cudaMemcpy(p2p_send.data(), payload, 2 * sizeof(double),
                            cudaMemcpyHostToDevice),
                 "initialize CUDA-aware p2p self-test");
      check_cuda(cudaDeviceSynchronize(), "synchronize CUDA-aware p2p self-test input");
      const int left_peer = (rank - 1 + size) % size;
      const int right_peer = (rank + 1) % size;
      MPI_Request requests[4] = {MPI_REQUEST_NULL, MPI_REQUEST_NULL,
                                 MPI_REQUEST_NULL, MPI_REQUEST_NULL};
      bool p2p_pass = MPI_Isend(p2p_send.data(), 1, MPI_DOUBLE, left_peer,
                                kTagSendLeft, MPI_COMM_WORLD, &requests[0]) == MPI_SUCCESS;
      p2p_pass = p2p_pass && MPI_Isend(p2p_send.data() + 1, 1, MPI_DOUBLE, right_peer,
                                       kTagSendRight, MPI_COMM_WORLD, &requests[1]) == MPI_SUCCESS;
      p2p_pass = p2p_pass && MPI_Irecv(p2p_receive.data(), 1, MPI_DOUBLE, left_peer,
                                       kTagSendRight, MPI_COMM_WORLD, &requests[2]) == MPI_SUCCESS;
      p2p_pass = p2p_pass && MPI_Irecv(p2p_receive.data() + 1, 1, MPI_DOUBLE, right_peer,
                                       kTagSendLeft, MPI_COMM_WORLD, &requests[3]) == MPI_SUCCESS;
      MPI_Status statuses[4];
      p2p_pass = p2p_pass && MPI_Waitall(4, requests, statuses) == MPI_SUCCESS;
      p2p_pass = p2p_pass && cudaDeviceSynchronize() == cudaSuccess;
      double received[2] = {-1.0, -1.0};
      p2p_pass = p2p_pass && cudaMemcpy(received, p2p_receive.data(), 2 * sizeof(double),
                                        cudaMemcpyDeviceToHost) == cudaSuccess;
      p2p_pass = p2p_pass && received[0] == static_cast<double>(left_peer + 1000);
      p2p_pass = p2p_pass && received[1] == static_cast<double>(right_peer);
      cuda_aware_p2p_self_test_status = p2p_pass ? "passed" : "failed";
      local_pass = local_pass && p2p_pass;
    }

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
         << " cuda_aware_p2p_self_test=" << cuda_aware_p2p_self_test_status
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

  void validate_indexed_plan(
      const IndexedOwnershipPlan& plan,
      int components,
      std::size_t stride) const
  {
    if (components <= 0) {
      throw std::invalid_argument("invalid indexed ownership gather shape");
    }
    validate_indexed_ownership_plan_host(plan, rank, size, stride);
    // Zero-count (empty slab) plans may legitimately carry null device
    // mirrors; every rank with atoms must provide both.
    if ((plan.owned_count != 0 && plan.device_owned_indices == nullptr) ||
        (plan.global_count != 0 && plan.device_scatter_slots == nullptr)) {
      throw std::invalid_argument("indexed ownership plan lacks device mirrors");
    }
  }

  // Per-call MPI counts/displacements in elements (doubles). Derived from the
  // atom-granular plan so the same plan serves position (3), potential (1)
  // and virial (9) gathers of one ownership epoch.
  void indexed_counts_and_displacements(
      const IndexedOwnershipPlan& plan,
      int components,
      std::vector<int>& counts,
      std::vector<int>& displacements) const
  {
    counts.resize(static_cast<std::size_t>(size));
    displacements.resize(static_cast<std::size_t>(size));
    for (int source = 0; source < size; ++source) {
      counts[source] = checked_mpi_count(
          static_cast<std::size_t>(plan.atom_counts[source]) *
              static_cast<std::size_t>(components),
          "indexed MPI element count");
      displacements[source] = checked_mpi_count(
          static_cast<std::size_t>(plan.atom_displacements[source]) *
              static_cast<std::size_t>(components),
          "indexed MPI element displacement");
    }
  }

  // Four-request point-to-point round with the production tag scheme:
  // send-to-left (kTagSendLeft), send-to-right (kTagSendRight), and the
  // matching receives from each peer's opposite direction. Independent tags
  // and buffers make P=2's identical peer safe; zero byte counts are legal
  // no-ops (null buffers are replaced by a static dummy that MPI never
  // touches at count 0).
  void p2p_exchange_bytes(
      const void* send_left,
      int send_left_bytes,
      const void* send_right,
      int send_right_bytes,
      void* recv_left,
      int recv_left_bytes,
      void* recv_right,
      int recv_right_bytes,
      int left_peer,
      int right_peer) const
  {
    static const char kUnused = 0;
    const void* left_buffer = send_left_bytes == 0 ? &kUnused : send_left;
    const void* right_buffer = send_right_bytes == 0 ? &kUnused : send_right;
    void* left_receive = recv_left_bytes == 0 ? const_cast<char*>(&kUnused) : recv_left;
    void* right_receive = recv_right_bytes == 0 ? const_cast<char*>(&kUnused) : recv_right;
    MPI_Request requests[4] = {MPI_REQUEST_NULL, MPI_REQUEST_NULL,
                               MPI_REQUEST_NULL, MPI_REQUEST_NULL};
    check_mpi(MPI_Isend(const_cast<void*>(left_buffer), send_left_bytes, MPI_BYTE,
                        left_peer, kTagSendLeft, MPI_COMM_WORLD, &requests[0]),
              "point-to-point MPI_Isend to the left peer");
    check_mpi(MPI_Isend(const_cast<void*>(right_buffer), send_right_bytes, MPI_BYTE,
                        right_peer, kTagSendRight, MPI_COMM_WORLD, &requests[1]),
              "point-to-point MPI_Isend to the right peer");
    check_mpi(MPI_Irecv(left_receive, recv_left_bytes, MPI_BYTE, left_peer,
                        kTagSendRight, MPI_COMM_WORLD, &requests[2]),
              "point-to-point MPI_Irecv from the left peer");
    check_mpi(MPI_Irecv(right_receive, recv_right_bytes, MPI_BYTE, right_peer,
                        kTagSendLeft, MPI_COMM_WORLD, &requests[3]),
              "point-to-point MPI_Irecv from the right peer");
    MPI_Status statuses[4];
    check_mpi(MPI_Waitall(4, requests, statuses), "point-to-point MPI_Waitall");
  }

  void validate_peers(int left_peer, int right_peer) const
  {
    if (size <= 1) {
      throw std::invalid_argument("point-to-point exchanges require world_size > 1");
    }
    if (left_peer < 0 || left_peer >= size || left_peer == rank ||
        right_peer < 0 || right_peer >= size || right_peer == rank) {
      throw std::invalid_argument("point-to-point peer is invalid");
    }
    if ((size == 2) != (left_peer == right_peer)) {
      throw std::invalid_argument(
          "the left/right peers must coincide exactly at P=2 and differ otherwise");
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
  std::string cuda_aware_p2p_self_test_status = "not-run";
  bool cuda_aware_fallback = false;
  bool device_initialized = false;
  std::uint64_t communication_log_interval = 1;
  DeviceBuffer<double> device_send;
  DeviceBuffer<double> device_receive;
  PinnedBuffer<double> host_send;
  PinnedBuffer<double> host_receive;
  // Dedicated M2a staging so a halo exchange interleaved with the collectives
  // can never clobber the collective buffers.
  DeviceBuffer<double> device_p2p_send;
  DeviceBuffer<double> device_p2p_receive;
  PinnedBuffer<double> host_p2p_send;
  PinnedBuffer<double> host_p2p_receive;
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
const std::string& MpiRuntime::hostname() const noexcept { return impl_->hostname; }
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

bool MpiRuntime::allreduce_all_passed(bool local_passed, const char* operation) const
{
  const int local_value = local_passed ? 1 : 0;
  int all_passed = 0;
  check_mpi(MPI_Allreduce(&local_value, &all_passed, 1, MPI_INT, MPI_MIN, MPI_COMM_WORLD),
            operation);
  return all_passed == 1;
}

std::vector<std::string> MpiRuntime::gather_strings(const std::string& value) const
{
  const int local_length = checked_mpi_count(value.size(), "gather diagnostic length");
  const bool root = is_root();
  std::vector<int> lengths(root ? static_cast<std::size_t>(world_size()) : 0);
  check_mpi(MPI_Gather(&local_length, 1, MPI_INT, root ? lengths.data() : nullptr, 1,
                       MPI_INT, 0, MPI_COMM_WORLD),
            "gather diagnostic lengths");
  std::vector<int> displacements(root ? static_cast<std::size_t>(world_size()) : 0);
  std::vector<char> buffer;
  if (root) {
    int total = 0;
    for (int rank = 0; rank < world_size(); ++rank) {
      displacements[static_cast<std::size_t>(rank)] = total;
      total += lengths[static_cast<std::size_t>(rank)];
    }
    buffer.resize(static_cast<std::size_t>(total));
  }
  char* receive_buffer = root && !buffer.empty() ? buffer.data() : nullptr;
  check_mpi(MPI_Gatherv(value.c_str(), local_length, MPI_CHAR, receive_buffer,
                        root ? lengths.data() : nullptr,
                        root ? displacements.data() : nullptr, MPI_CHAR, 0,
                        MPI_COMM_WORLD),
            "gather diagnostic records");
  if (!root) return {};

  std::vector<std::string> gathered(static_cast<std::size_t>(world_size()));
  for (int rank = 0; rank < world_size(); ++rank) {
    if (lengths[static_cast<std::size_t>(rank)] != 0) {
      gathered[static_cast<std::size_t>(rank)].assign(
          buffer.data() + displacements[static_cast<std::size_t>(rank)],
          static_cast<std::size_t>(lengths[static_cast<std::size_t>(rank)]));
    }
  }
  return gathered;
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
    const SpatialOwnership& ownership,
    int partition_axis) const
{
  const std::size_t global_count = ownership.global_count();
  if (partition_axis < -1 || partition_axis > 2) {
    throw std::logic_error("partition axis is outside x/y/z");
  }
  // A mask coverage sum alone cannot prove complete-map agreement for P>=3:
  // non-owner ranks could disagree with each other while exactly one rank's
  // local mask still selects the slot. Gate the initial variable-count plan
  // with the same fixed-size map-hash handshake used by every later epoch.
  assert_same_ownership_map(ownership.map_hash(), true);

  // This second collective explicitly proves that every replicated slot is
  // authoritative on exactly one rank; it is not inferred from the slab
  // formula or from count totals.
  std::vector<int> local(global_count, 0);
  const std::vector<char>& mask = ownership.owned_mask();
  for (std::size_t slot = 0; slot < global_count; ++slot) {
    local[slot] = mask[slot];
  }
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
              << " ranks=" << world_size();
    if (partition_axis >= 0) {
      static const char* axis_names[3] = {"x", "y", "z"};
      std::cout << " partition=spatial-slab axis=" << axis_names[partition_axis]
                << " slab_rule=equal-width-fractional";
    }
    std::cout << " missing=" << missing
              << " overlapping=" << overlapping
              << " owned_output_coverage=complete"
              << " nep_kernel_centers=replicated-full"
              << " nep_N1_N2_shard_complete=false"
              << " reason=remote-Fp-and-reverse-partial-dependencies\n";
    const std::vector<std::size_t>& counts = ownership.owned_counts_by_rank();
    for (int source = 0; source < world_size(); ++source) {
      std::cout << "DMGMD_CENTER_OWNERSHIP rank=" << source
                << " owned_count=" << counts[static_cast<std::size_t>(source)] << '\n';
    }
    std::cout.flush();
  }
}

void MpiRuntime::assert_same_ownership_map(
    std::uint64_t map_hash,
    bool locally_valid,
    CommunicationVolume& volume) const
{
  if (world_size() == 1) return;
  // One fixed-size Allreduce, so it is safe to call even when local map
  // construction already failed or the maps disagree: every rank enters with
  // the same shape and leaves through the same branch. Valid hashes are
  // 63-bit (SpatialOwnership::map_hash), so the all-ones sentinel for an
  // invalid local map can never collide with a real hash.
  constexpr std::uint64_t kInvalidMapSentinel = UINT64_MAX;
  const std::uint64_t value = locally_valid ? map_hash : kInvalidMapSentinel;
  const unsigned long long local[2] = {static_cast<unsigned long long>(value),
                                       ~static_cast<unsigned long long>(value)};
  unsigned long long maximum[2] = {0, 0};
  check_mpi(MPI_Allreduce(local, maximum, 2, MPI_UNSIGNED_LONG_LONG, MPI_MAX,
                          MPI_COMM_WORLD),
            "reduce spatial ownership map hash");
  ++volume.collective_calls;
  volume.mpi_input_bytes_global += 2 * sizeof(unsigned long long) *
                                   static_cast<std::uint64_t>(world_size());
  volume.mpi_output_bytes_global += 2 * sizeof(unsigned long long) *
                                    static_cast<std::uint64_t>(world_size());
  // max{h} == ~max{~h} holds iff every rank contributed the same value. With
  // the sentinel in play this also fails whenever any rank was locally
  // invalid while at least one other rank stayed valid; if every rank was
  // invalid the local flag below still fails the check symmetrically.
  const bool consistent = maximum[0] == ~maximum[1];
  if (!locally_valid || !consistent) {
    throw std::runtime_error(
        "spatial ownership map is invalid on a rank or inconsistent between ranks");
  }
}

void MpiRuntime::assert_same_ownership_map(
    std::uint64_t map_hash,
    bool locally_valid) const
{
  CommunicationVolume ignored;
  assert_same_ownership_map(map_hash, locally_valid, ignored);
}

void MpiRuntime::allgather_indexed_device_soa(
    double* device_values,
    int components,
    std::size_t stride,
    const IndexedOwnershipPlan& plan,
    CommunicationVolume& volume) const
{
  impl_->validate_indexed_plan(plan, components, stride);
  const std::size_t send_elements =
      plan.owned_count * static_cast<std::size_t>(components);
  const std::size_t receive_elements =
      plan.global_count * static_cast<std::size_t>(components);
  // Kernel launch sizes are derived from these element counts, so they must
  // both fit the MPI/kernel int range before any launch.
  static_cast<void>(checked_mpi_count(send_elements, "indexed pack items"));
  static_cast<void>(checked_mpi_count(receive_elements, "indexed unpack items"));
  impl_->device_send.reserve(std::max<std::size_t>(send_elements, 1));
  impl_->device_receive.reserve(std::max<std::size_t>(receive_elements, 1));
  if (send_elements != 0) {
    pack_indexed_soa<<<(send_elements + kThreads - 1) / kThreads, kThreads>>>(
        plan.device_owned_indices, checked_mpi_count(plan.owned_count, "owned count"),
        checked_mpi_count(stride, "SoA stride"), components, device_values,
        impl_->device_send.data());
    check_cuda(cudaGetLastError(), "pack indexed owned field");
  }

  std::vector<int> counts;
  std::vector<int> displacements;
  impl_->indexed_counts_and_displacements(plan, components, counts, displacements);
  // HostStaged follows device -> pinned send -> MPI -> pinned receive ->
  // device. CudaAware changes only transport, never layout or ownership.
  if (backend() == CommunicationBackend::host_staged) {
    impl_->host_send.reserve(std::max<std::size_t>(send_elements, 1));
    impl_->host_receive.reserve(std::max<std::size_t>(receive_elements, 1));
    if (send_elements != 0) {
      check_cuda(cudaMemcpy(impl_->host_send.data(), impl_->device_send.data(),
                            checked_bytes(send_elements), cudaMemcpyDeviceToHost),
                 "stage indexed owned field from CUDA device to pinned host");
    }
    check_mpi(MPI_Allgatherv(
                  impl_->host_send.data(), checked_mpi_count(send_elements, "allgather send"),
                  MPI_DOUBLE, impl_->host_receive.data(), counts.data(), displacements.data(),
                  MPI_DOUBLE, MPI_COMM_WORLD),
              "HostStaged MPI_Allgatherv");
    check_cuda(cudaMemcpy(impl_->device_receive.data(), impl_->host_receive.data(),
                          checked_bytes(receive_elements), cudaMemcpyHostToDevice),
               "stage indexed replicated field from pinned host to CUDA device");
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
  unpack_indexed_soa<<<(receive_elements + kThreads - 1) / kThreads, kThreads>>>(
      plan.device_scatter_slots, checked_mpi_count(plan.global_count, "global count"),
      checked_mpi_count(stride, "SoA stride"), components,
      impl_->device_receive.data(), device_values);
  check_cuda(cudaGetLastError(), "unpack indexed replicated field");

  ++volume.collective_calls;
  volume.mpi_input_bytes_global += checked_bytes(receive_elements);
  volume.mpi_output_bytes_global +=
      checked_bytes(receive_elements) * static_cast<std::uint64_t>(world_size());
}

std::vector<double> MpiRuntime::gather_indexed_device_soa_to_root(
    const double* device_values,
    int components,
    std::size_t stride,
    const IndexedOwnershipPlan& plan,
    CommunicationVolume& volume) const
{
  impl_->validate_indexed_plan(plan, components, stride);
  const std::size_t send_elements =
      plan.owned_count * static_cast<std::size_t>(components);
  const std::size_t receive_elements =
      plan.global_count * static_cast<std::size_t>(components);
  static_cast<void>(checked_mpi_count(send_elements, "indexed pack items"));
  static_cast<void>(checked_mpi_count(receive_elements, "indexed gather items"));
  impl_->device_send.reserve(std::max<std::size_t>(send_elements, 1));
  if (is_root() && backend() == CommunicationBackend::cuda_aware) {
    impl_->device_receive.reserve(std::max<std::size_t>(receive_elements, 1));
  }
  if (send_elements != 0) {
    pack_indexed_soa<<<(send_elements + kThreads - 1) / kThreads, kThreads>>>(
        plan.device_owned_indices, checked_mpi_count(plan.owned_count, "owned count"),
        checked_mpi_count(stride, "SoA stride"), components, device_values,
        impl_->device_send.data());
    check_cuda(cudaGetLastError(), "pack indexed output field");
  }

  std::vector<int> counts;
  std::vector<int> displacements;
  impl_->indexed_counts_and_displacements(plan, components, counts, displacements);
  std::vector<double> packed(is_root() ? receive_elements : 0);
  if (backend() == CommunicationBackend::host_staged) {
    impl_->host_send.reserve(std::max<std::size_t>(send_elements, 1));
    if (is_root()) impl_->host_receive.reserve(std::max<std::size_t>(receive_elements, 1));
    if (send_elements != 0) {
      check_cuda(cudaMemcpy(impl_->host_send.data(), impl_->device_send.data(),
                            checked_bytes(send_elements), cudaMemcpyDeviceToHost),
                 "stage indexed output from CUDA device to pinned host");
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
                 "download gathered indexed output");
      volume.output_download_bytes += checked_bytes(receive_elements);
    }
  }

  // Scatter the rank-concatenated stream back into replicated slot order on
  // the root so downstream formatters address slots exactly as the host
  // identity model does. The scatter map is validated against the stride by
  // validate_indexed_plan.
  std::vector<double> soa(is_root() ? stride * static_cast<std::size_t>(components) : 0);
  if (is_root()) {
    for (std::size_t atom = 0; atom < plan.global_count; ++atom) {
      const std::size_t slot =
          static_cast<std::size_t>(plan.host_scatter_slots[atom]);
      for (int component = 0; component < components; ++component) {
        soa[static_cast<std::size_t>(component) * stride + slot] =
            packed[atom * static_cast<std::size_t>(components) +
                   static_cast<std::size_t>(component)];
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

void MpiRuntime::exchange_p2p_indexed_device_soa(
    double* device_values,
    int components,
    std::size_t stride,
    const int* send_left_indices,
    int send_left_count,
    const int* send_right_indices,
    int send_right_count,
    const int* recv_left_slots,
    int recv_left_count,
    const int* recv_right_slots,
    int recv_right_count,
    int left_peer,
    int right_peer,
    CommunicationVolume& volume) const
{
  if (world_size() <= 1) return;
  if (components <= 0) {
    throw std::invalid_argument("invalid p2p exchange component count");
  }
  impl_->validate_peers(left_peer, right_peer);
  const std::size_t send_left_elements =
      static_cast<std::size_t>(send_left_count) * static_cast<std::size_t>(components);
  const std::size_t send_right_elements =
      static_cast<std::size_t>(send_right_count) * static_cast<std::size_t>(components);
  const std::size_t recv_left_elements =
      static_cast<std::size_t>(recv_left_count) * static_cast<std::size_t>(components);
  const std::size_t recv_right_elements =
      static_cast<std::size_t>(recv_right_count) * static_cast<std::size_t>(components);
  static_cast<void>(checked_mpi_count(send_left_elements, "p2p left send items"));
  static_cast<void>(checked_mpi_count(send_right_elements, "p2p right send items"));
  static_cast<void>(checked_mpi_count(recv_left_elements, "p2p left receive items"));
  static_cast<void>(checked_mpi_count(recv_right_elements, "p2p right receive items"));
  const std::size_t send_elements = send_left_elements + send_right_elements;
  const std::size_t recv_elements = recv_left_elements + recv_right_elements;
  const int mpi_stride = checked_mpi_count(stride, "SoA stride");
  impl_->device_p2p_send.reserve(std::max<std::size_t>(send_elements, 1));
  impl_->device_p2p_receive.reserve(std::max<std::size_t>(recv_elements, 1));
  if (send_left_elements != 0) {
    pack_indexed_soa<<<(send_left_elements + kThreads - 1) / kThreads, kThreads>>>(
        send_left_indices, send_left_count, mpi_stride, components, device_values,
        impl_->device_p2p_send.data());
    check_cuda(cudaGetLastError(), "pack left halo send list");
  }
  if (send_right_elements != 0) {
    pack_indexed_soa<<<(send_right_elements + kThreads - 1) / kThreads, kThreads>>>(
        send_right_indices, send_right_count, mpi_stride, components, device_values,
        impl_->device_p2p_send.data() + send_left_elements);
    check_cuda(cudaGetLastError(), "pack right halo send list");
  }

  if (backend() == CommunicationBackend::host_staged) {
    impl_->host_p2p_send.reserve(std::max<std::size_t>(send_elements, 1));
    impl_->host_p2p_receive.reserve(std::max<std::size_t>(recv_elements, 1));
    if (send_elements != 0) {
      check_cuda(cudaMemcpy(impl_->host_p2p_send.data(), impl_->device_p2p_send.data(),
                            checked_bytes(send_elements), cudaMemcpyDeviceToHost),
                 "stage halo send list from CUDA device to pinned host");
    }
    impl_->p2p_exchange_bytes(
        impl_->host_p2p_send.data(), checked_mpi_count(checked_bytes(send_left_elements), "p2p send bytes"),
        impl_->host_p2p_send.data() + send_left_elements,
        checked_mpi_count(checked_bytes(send_right_elements), "p2p send bytes"),
        impl_->host_p2p_receive.data(), checked_mpi_count(checked_bytes(recv_left_elements), "p2p recv bytes"),
        impl_->host_p2p_receive.data() + recv_left_elements,
        checked_mpi_count(checked_bytes(recv_right_elements), "p2p recv bytes"),
        left_peer, right_peer);
    if (recv_elements != 0) {
      check_cuda(cudaMemcpy(impl_->device_p2p_receive.data(), impl_->host_p2p_receive.data(),
                            checked_bytes(recv_elements), cudaMemcpyHostToDevice),
                 "stage halo receive stream from pinned host to CUDA device");
    }
  } else {
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware p2p input");
    impl_->p2p_exchange_bytes(
        impl_->device_p2p_send.data(), checked_mpi_count(checked_bytes(send_left_elements), "p2p send bytes"),
        impl_->device_p2p_send.data() + send_left_elements,
        checked_mpi_count(checked_bytes(send_right_elements), "p2p send bytes"),
        impl_->device_p2p_receive.data(), checked_mpi_count(checked_bytes(recv_left_elements), "p2p recv bytes"),
        impl_->device_p2p_receive.data() + recv_left_elements,
        checked_mpi_count(checked_bytes(recv_right_elements), "p2p recv bytes"),
        left_peer, right_peer);
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware p2p output");
  }

  if (recv_left_elements != 0) {
    unpack_indexed_soa<<<(recv_left_elements + kThreads - 1) / kThreads, kThreads>>>(
        recv_left_slots, recv_left_count, mpi_stride, components,
        impl_->device_p2p_receive.data(), device_values);
    check_cuda(cudaGetLastError(), "unpack left halo receive stream");
  }
  if (recv_right_elements != 0) {
    unpack_indexed_soa<<<(recv_right_elements + kThreads - 1) / kThreads, kThreads>>>(
        recv_right_slots, recv_right_count, mpi_stride, components,
        impl_->device_p2p_receive.data() + recv_left_elements, device_values);
    check_cuda(cudaGetLastError(), "unpack right halo receive stream");
  }
  volume.p2p_calls += 4;
  volume.add_p2p_bytes(ByteClass::halo, checked_bytes(send_elements),
                       checked_bytes(recv_elements));
}

void MpiRuntime::exchange_p2p_host_bytes(
    const void* send_left,
    std::size_t send_left_bytes,
    const void* send_right,
    std::size_t send_right_bytes,
    void* recv_left,
    std::size_t recv_left_bytes,
    void* recv_right,
    std::size_t recv_right_bytes,
    int left_peer,
    int right_peer,
    ByteClass byte_class,
    CommunicationVolume& volume) const
{
  if (world_size() <= 1) return;
  impl_->validate_peers(left_peer, right_peer);
  impl_->p2p_exchange_bytes(
      send_left, checked_mpi_count(send_left_bytes, "p2p send bytes"),
      send_right, checked_mpi_count(send_right_bytes, "p2p send bytes"),
      recv_left, checked_mpi_count(recv_left_bytes, "p2p receive bytes"),
      recv_right, checked_mpi_count(recv_right_bytes, "p2p receive bytes"),
      left_peer, right_peer);
  volume.p2p_calls += 4;
  volume.add_p2p_bytes(byte_class, send_left_bytes + send_right_bytes,
                       recv_left_bytes + recv_right_bytes);
}

std::vector<int> MpiRuntime::alltoall_ints(
    const std::vector<int>& send_values,
    ByteClass byte_class,
    CommunicationVolume& volume) const
{
  if (send_values.size() != static_cast<std::size_t>(world_size())) {
    throw std::invalid_argument("alltoall values must be world-sized");
  }
  std::vector<int> received(static_cast<std::size_t>(world_size()));
  check_mpi(MPI_Alltoall(send_values.data(), 1, MPI_INT, received.data(), 1, MPI_INT,
                         MPI_COMM_WORLD),
            "MPI_Alltoall ints");
  const std::uint64_t bytes = 4 * static_cast<std::uint64_t>(world_size());
  volume.add_p2p_bytes(byte_class, bytes, bytes);
  return received;
}

std::vector<int> MpiRuntime::allgather_int(
    int value,
    ByteClass byte_class,
    CommunicationVolume& volume) const
{
  std::vector<int> received(static_cast<std::size_t>(world_size()));
  check_mpi(MPI_Allgather(&value, 1, MPI_INT, received.data(), 1, MPI_INT, MPI_COMM_WORLD),
            "MPI_Allgather int");
  volume.add_p2p_bytes(byte_class, 4, 4 * static_cast<std::uint64_t>(world_size()));
  return received;
}

void MpiRuntime::alltoallv_host_bytes(
    const void* send_buffer,
    const std::vector<int>& send_counts_bytes,
    const std::vector<int>& send_displacements_bytes,
    void* recv_buffer,
    const std::vector<int>& recv_counts_bytes,
    const std::vector<int>& recv_displacements_bytes,
    ByteClass byte_class,
    CommunicationVolume& volume) const
{
  const std::size_t world = static_cast<std::size_t>(world_size());
  if (send_counts_bytes.size() != world || send_displacements_bytes.size() != world ||
      recv_counts_bytes.size() != world || recv_displacements_bytes.size() != world) {
    throw std::invalid_argument("alltoallv plans must be world-sized");
  }
  static const char kUnused = 0;
  const void* send = send_buffer == nullptr ? &kUnused : send_buffer;
  void* receive = recv_buffer == nullptr ? const_cast<char*>(&kUnused) : recv_buffer;
  check_mpi(MPI_Alltoallv(const_cast<void*>(send), send_counts_bytes.data(),
                         send_displacements_bytes.data(), MPI_BYTE, receive,
                         recv_counts_bytes.data(), recv_displacements_bytes.data(),
                         MPI_BYTE, MPI_COMM_WORLD),
            "MPI_Alltoallv bytes");
  std::uint64_t sent = 0;
  std::uint64_t received = 0;
  for (int count : send_counts_bytes) {
    if (count < 0) throw std::invalid_argument("negative alltoallv send count");
    sent += static_cast<std::uint64_t>(count);
  }
  for (int count : recv_counts_bytes) {
    if (count < 0) throw std::invalid_argument("negative alltoallv receive count");
    received += static_cast<std::uint64_t>(count);
  }
  volume.add_p2p_bytes(byte_class, sent, received);
}

std::vector<double> MpiRuntime::gather_prefix_device_soa_to_root(
    const double* device_values,
    int components,
    int owned_count,
    std::size_t stride,
    const std::vector<int>& owned_counts,
    CommunicationVolume& volume) const
{
  if (components <= 0) {
    throw std::invalid_argument("invalid prefix gather component count");
  }
  if (owned_counts.size() != static_cast<std::size_t>(world_size())) {
    throw std::invalid_argument("prefix gather counts must be world-sized");
  }
  if (owned_counts[static_cast<std::size_t>(world_rank())] != owned_count) {
    throw std::invalid_argument("prefix gather count disagrees with this rank");
  }
  std::size_t total_atoms = 0;
  for (int count : owned_counts) {
    if (count < 0) throw std::invalid_argument("negative prefix gather count");
    total_atoms += static_cast<std::size_t>(count);
  }
  const std::size_t send_elements =
      static_cast<std::size_t>(owned_count) * static_cast<std::size_t>(components);
  const std::size_t receive_elements = total_atoms * static_cast<std::size_t>(components);
  static_cast<void>(checked_mpi_count(send_elements, "prefix gather send items"));
  static_cast<void>(checked_mpi_count(receive_elements, "prefix gather receive items"));
  impl_->device_send.reserve(std::max<std::size_t>(send_elements, 1));
  if (is_root() && backend() == CommunicationBackend::cuda_aware) {
    impl_->device_receive.reserve(std::max<std::size_t>(receive_elements, 1));
  }
  if (send_elements != 0) {
    pack_prefix_soa<<<(send_elements + kThreads - 1) / kThreads, kThreads>>>(
        owned_count, checked_mpi_count(stride, "SoA stride"), components, device_values,
        impl_->device_send.data());
    check_cuda(cudaGetLastError(), "pack owned prefix field");
  }

  std::vector<int> counts(static_cast<std::size_t>(world_size()));
  std::vector<int> displacements(static_cast<std::size_t>(world_size()));
  int displacement = 0;
  for (int source = 0; source < world_size(); ++source) {
    counts[static_cast<std::size_t>(source)] = checked_mpi_count(
        static_cast<std::size_t>(owned_counts[static_cast<std::size_t>(source)]) *
            static_cast<std::size_t>(components),
        "prefix gather count");
    displacements[static_cast<std::size_t>(source)] = displacement;
    displacement += counts[static_cast<std::size_t>(source)];
  }
  std::vector<double> packed(is_root() ? receive_elements : 0);
  if (backend() == CommunicationBackend::host_staged) {
    impl_->host_send.reserve(std::max<std::size_t>(send_elements, 1));
    if (is_root()) impl_->host_receive.reserve(std::max<std::size_t>(receive_elements, 1));
    if (send_elements != 0) {
      check_cuda(cudaMemcpy(impl_->host_send.data(), impl_->device_send.data(),
                            checked_bytes(send_elements), cudaMemcpyDeviceToHost),
                 "stage owned prefix from CUDA device to pinned host");
    }
    static const double kUnused = 0.0;
    const void* send = send_elements == 0 ? &kUnused : impl_->host_send.data();
    check_mpi(MPI_Gatherv(const_cast<void*>(send),
                          checked_mpi_count(send_elements, "gather send"), MPI_DOUBLE,
                          is_root() ? impl_->host_receive.data() : nullptr, counts.data(),
                          displacements.data(), MPI_DOUBLE, 0, MPI_COMM_WORLD),
              "HostStaged MPI_Gatherv owned prefix");
    if (is_root() && receive_elements != 0) {
      std::copy_n(impl_->host_receive.data(), receive_elements, packed.data());
    }
    volume.device_to_host_bytes_global += checked_bytes(receive_elements);
  } else {
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware prefix gather input");
    static const double kUnused = 0.0;
    const void* send = send_elements == 0 ? &kUnused : impl_->device_send.data();
    check_mpi(MPI_Gatherv(const_cast<void*>(send),
                          checked_mpi_count(send_elements, "gather send"), MPI_DOUBLE,
                          is_root() ? impl_->device_receive.data() : nullptr, counts.data(),
                          displacements.data(), MPI_DOUBLE, 0, MPI_COMM_WORLD),
              "CudaAware MPI_Gatherv owned prefix");
    check_cuda(cudaDeviceSynchronize(), "synchronize CudaAware prefix gather output");
    if (is_root()) {
      check_cuda(cudaMemcpy(packed.data(), impl_->device_receive.data(),
                            checked_bytes(receive_elements), cudaMemcpyDeviceToHost),
                 "download gathered owned prefix");
      volume.output_download_bytes += checked_bytes(receive_elements);
    }
  }
  ++volume.collective_calls;
  volume.mpi_input_bytes_global += checked_bytes(receive_elements);
  volume.mpi_output_bytes_global += checked_bytes(receive_elements);
  return packed;
}

std::vector<unsigned long long> MpiRuntime::gather_u64_to_root(
    const unsigned long long* host_values,
    int count,
    const std::vector<int>& counts,
    CommunicationVolume& volume) const
{
  if (counts.size() != static_cast<std::size_t>(world_size())) {
    throw std::invalid_argument("u64 gather counts must be world-sized");
  }
  if (counts[static_cast<std::size_t>(world_rank())] != count) {
    throw std::invalid_argument("u64 gather count disagrees with this rank");
  }
  std::size_t total = 0;
  std::vector<int> displacements(static_cast<std::size_t>(world_size()));
  int displacement = 0;
  for (int source = 0; source < world_size(); ++source) {
    const int value = counts[static_cast<std::size_t>(source)];
    if (value < 0) throw std::invalid_argument("negative u64 gather count");
    displacements[static_cast<std::size_t>(source)] = displacement;
    displacement += value;
    total += static_cast<std::size_t>(value);
  }
  static const unsigned long long kUnused = 0;
  const unsigned long long* send = count == 0 ? &kUnused : host_values;
  std::vector<unsigned long long> packed(is_root() ? total : 0);
  check_mpi(MPI_Gatherv(const_cast<unsigned long long*>(send), count,
                        MPI_UNSIGNED_LONG_LONG,
                        is_root() ? packed.data() : nullptr, counts.data(),
                        displacements.data(), MPI_UNSIGNED_LONG_LONG, 0, MPI_COMM_WORLD),
            "MPI_Gatherv u64 ids");
  ++volume.collective_calls;
  const std::uint64_t bytes = 8 * static_cast<std::uint64_t>(total);
  volume.mpi_input_bytes_global += bytes;
  volume.mpi_output_bytes_global += bytes;
  return packed;
}

void MpiRuntime::scatterv_prefix_device_soa_from_root(
    const std::vector<double>& root_payload,
    int components,
    const std::vector<int>& counts,
    double* device_values,
    std::size_t stride,
    CommunicationVolume& volume) const
{
  if (components <= 0) {
    throw std::invalid_argument("invalid prefix scatter component count");
  }
  if (counts.size() != static_cast<std::size_t>(world_size())) {
    throw std::invalid_argument("prefix scatter counts must be world-sized");
  }
  std::size_t total_atoms = 0;
  std::vector<int> element_counts(static_cast<std::size_t>(world_size()));
  std::vector<int> displacements(static_cast<std::size_t>(world_size()));
  int displacement = 0;
  for (int source = 0; source < world_size(); ++source) {
    const int atoms = counts[static_cast<std::size_t>(source)];
    if (atoms < 0) throw std::invalid_argument("negative prefix scatter count");
    element_counts[static_cast<std::size_t>(source)] = checked_mpi_count(
        static_cast<std::size_t>(atoms) * static_cast<std::size_t>(components),
        "prefix scatter count");
    displacements[static_cast<std::size_t>(source)] = displacement;
    displacement += element_counts[static_cast<std::size_t>(source)];
    total_atoms += static_cast<std::size_t>(atoms);
  }
  if (is_root() &&
      root_payload.size() != total_atoms * static_cast<std::size_t>(components)) {
    throw std::invalid_argument("prefix scatter payload does not cover every atom");
  }
  const int my_elements =
      element_counts[static_cast<std::size_t>(world_rank())];
  std::vector<double> received(static_cast<std::size_t>(my_elements));
  static const double kUnused = 0.0;
  const void* send = (is_root() && total_atoms != 0) ? root_payload.data() : &kUnused;
  void* receive = my_elements == 0 ? const_cast<double*>(&kUnused) : received.data();
  check_mpi(MPI_Scatterv(const_cast<void*>(send), element_counts.data(),
                         displacements.data(), MPI_DOUBLE, receive, my_elements,
                         MPI_DOUBLE, 0, MPI_COMM_WORLD),
            "MPI_Scatterv corrected velocities");
  if (my_elements != 0) {
    impl_->device_send.reserve(static_cast<std::size_t>(my_elements));
    check_cuda(cudaMemcpy(impl_->device_send.data(), received.data(),
                          checked_bytes(static_cast<std::size_t>(my_elements)),
                          cudaMemcpyHostToDevice),
               "upload scattered owned prefix stream");
    unpack_prefix_soa<<<(static_cast<std::size_t>(my_elements) + kThreads - 1) / kThreads,
                        kThreads>>>(
        counts[static_cast<std::size_t>(world_rank())],
        checked_mpi_count(stride, "SoA stride"), components, impl_->device_send.data(),
        device_values);
    check_cuda(cudaGetLastError(), "unpack scattered owned prefix stream");
  }
  ++volume.collective_calls;
  const std::uint64_t bytes =
      checked_bytes(total_atoms * static_cast<std::size_t>(components));
  volume.mpi_input_bytes_global += bytes;
  volume.mpi_output_bytes_global += bytes;
  volume.host_to_device_bytes_global += bytes;
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
            << " output_download_bytes=" << volume.output_download_bytes
            << " p2p_calls=" << volume.p2p_calls
            << " halo_send_bytes_local=" << volume.halo_send_bytes_local
            << " halo_recv_bytes_local=" << volume.halo_recv_bytes_local
            << " migration_send_bytes_local=" << volume.migration_send_bytes_local
            << " migration_recv_bytes_local=" << volume.migration_recv_bytes_local
            << " control_send_bytes_local=" << volume.control_send_bytes_local
            << " control_recv_bytes_local=" << volume.control_recv_bytes_local << '\n';
  std::cout.flush();
}

void MpiRuntime::log_step_domain_communication(
    std::uint64_t step,
    const CommunicationVolume& volume) const
{
  // Every rank reports its own LOCAL p2p classes (rank 0's DMGMD_COMM line
  // only carries its own local values); the mpirun I/O forwarding collects
  // the per-rank lines so tests can verify each rank exactly.
  if (step % impl_->communication_log_interval != 0) return;
  std::cout << "DMGMD_DOMAIN_COMM rank=" << world_rank() << " step=" << step
            << " backend=" << backend_name()
            << " p2p_calls=" << volume.p2p_calls
            << " halo_send_bytes_local=" << volume.halo_send_bytes_local
            << " halo_recv_bytes_local=" << volume.halo_recv_bytes_local
            << " migration_send_bytes_local=" << volume.migration_send_bytes_local
            << " migration_recv_bytes_local=" << volume.migration_recv_bytes_local
            << " control_send_bytes_local=" << volume.control_send_bytes_local
            << " control_recv_bytes_local=" << volume.control_recv_bytes_local << '\n';
  std::cout.flush();
}

void MpiRuntime::log_timing(
    const char* phase,
    std::uint64_t sequence,
    std::uint64_t steps,
    std::size_t atoms,
    double elapsed_seconds) const
{
  if (phase == nullptr || phase[0] == '\0') {
    throw std::invalid_argument("timing phase must not be empty");
  }
  if (!std::isfinite(elapsed_seconds) || elapsed_seconds < 0.0) {
    throw std::invalid_argument("timing duration must be finite and non-negative");
  }
  double minimum = 0.0;
  double maximum = 0.0;
  double sum = 0.0;
  check_mpi(MPI_Reduce(&elapsed_seconds, &minimum, 1, MPI_DOUBLE, MPI_MIN, 0,
                       MPI_COMM_WORLD),
            "reduce minimum runtime timing");
  check_mpi(MPI_Reduce(&elapsed_seconds, &maximum, 1, MPI_DOUBLE, MPI_MAX, 0,
                       MPI_COMM_WORLD),
            "reduce maximum runtime timing");
  check_mpi(MPI_Reduce(&elapsed_seconds, &sum, 1, MPI_DOUBLE, MPI_SUM, 0,
                       MPI_COMM_WORLD),
            "reduce mean runtime timing");
  if (!is_root()) return;

  const double mean = sum / static_cast<double>(world_size());
  const double atom_steps =
      static_cast<double>(atoms) * static_cast<double>(steps);
  const double throughput = maximum > 0.0 ? atom_steps / maximum : 0.0;
  std::ostringstream line;
  line << std::fixed << std::setprecision(9)
       << "DMGMD_TIMING phase=" << phase
       << " sequence=" << sequence
       << " steps=" << steps
       << " atoms=" << atoms
       << " ranks=" << world_size()
       << " backend=" << backend_name()
       << " seconds_min=" << minimum
       << " seconds_mean=" << mean
       << " seconds_max=" << maximum
       << " global_atom_steps_per_second=" << throughput;
  std::cout << line.str() << '\n';
  std::cout.flush();
}

void MpiRuntime::report_error(const char* category, const std::string& message) const
{
  // Do not use an MPI collective here. A peer may still be blocked inside a
  // failed collective or CUDA call, so gathering diagnostics before MPI_Abort
  // could deadlock and hide the original failure. Open MPI/PRRTE forwards each
  // remote rank's stderr to mpirun; the launcher-side test process captures the
  // merged stream on the node from which the job was started.
  std::string escaped;
  escaped.reserve(message.size());
  for (const char value : message) {
    switch (value) {
      case '\\': escaped += "\\\\"; break;
      case '"': escaped += "\\\""; break;
      case '\n': escaped += "\\n"; break;
      case '\r': escaped += "\\r"; break;
      default: escaped += value; break;
    }
  }
  std::ostringstream record;
  record << "DMGMD_ERROR rank=" << world_rank()
         << " local_rank=" << local_rank()
         << " hostname=" << impl_->hostname
         << " category=" << (category == nullptr ? "unknown" : category)
         << " message=\"" << escaped << "\"\n";
  const std::string line = record.str();
  std::fwrite(line.data(), sizeof(char), line.size(), stderr);
  std::fflush(stderr);
}

[[noreturn]] void MpiRuntime::abort(int error_code) const
{
  MPI_Abort(MPI_COMM_WORLD, error_code);
  std::abort();
}

}  // namespace dmgmd
