#include "dmgmd/mpi_runtime.hpp"
#include <cuda_runtime.h>
#include <mpi.h>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
std::string fault;
int rank_id = -1, halo_posts = 0;
MPI_Request* send_requests = nullptr;
bool hold_sends = false, hold_receives = false;
void require(bool value, const char* message)
{
  if (!value) throw std::runtime_error(message);
}
void cuda_check(cudaError_t status)
{
  if (status != cudaSuccess) throw std::runtime_error(cudaGetErrorString(status));
}
}

// Test-executable-only failure injection; production contains no test switches.
extern "C" int __real_MPI_Isend(const void*, int, MPI_Datatype, int, int, MPI_Comm, MPI_Request*);
extern "C" int __wrap_MPI_Isend(const void* buffer, int count, MPI_Datatype type,
                               int dest, int tag, MPI_Comm comm, MPI_Request* request)
{
  if (tag == 1101 || tag == 1102) {
    ++halo_posts;
    if (tag == 1101) send_requests = request;
    if (rank_id == 0 && (fault == "partial-post" || fault == "selftest-post") && halo_posts == 2) return MPI_ERR_OTHER;
  }
  return __real_MPI_Isend(buffer, count, type, dest, tag, comm, request);
}
extern "C" int __real_MPI_Waitall(int, MPI_Request*, MPI_Status*);
extern "C" int __wrap_MPI_Waitall(int count, MPI_Request* requests, MPI_Status* statuses)
{
  if (rank_id == 0 && fault == "wait-error" && requests == send_requests) return MPI_ERR_OTHER;
  return __real_MPI_Waitall(count, requests, statuses);
}
extern "C" int __real_MPI_Testall(int, MPI_Request*, int*, MPI_Status*);
extern "C" int __wrap_MPI_Testall(int count, MPI_Request* requests, int* flag, MPI_Status* statuses)
{
  if (rank_id == 0 && requests == send_requests && fault == "test-error") return MPI_ERR_OTHER;
  if ((hold_sends && requests == send_requests) ||
      (hold_receives && send_requests != nullptr && requests == send_requests + 2)) {
    *flag = 0;
    return MPI_SUCCESS;
  }
  return __real_MPI_Testall(count, requests, flag, statuses);
}
extern "C" cudaError_t __real_cudaEventSynchronize(cudaEvent_t);
extern "C" cudaError_t __wrap_cudaEventSynchronize(cudaEvent_t event)
{
  // begin's D2H wait precedes posting. Fail finish's event only after MPI
  // requests exist (CudaAware has no begin event wait).
  if (rank_id == 0 && fault == "unpack-error" && halo_posts != 0) return cudaErrorUnknown;
  if (rank_id == 0 && fault == "d2h-error" && halo_posts == 0) return cudaErrorUnknown;
  return __real_cudaEventSynchronize(event);
}
extern "C" cudaError_t __real_cudaMemcpyAsync(void*, const void*, size_t, cudaMemcpyKind, cudaStream_t);
extern "C" cudaError_t __wrap_cudaMemcpyAsync(void* dst, const void* src, size_t bytes,
                                              cudaMemcpyKind kind, cudaStream_t stream)
{
  if (rank_id == 0 && fault == "h2d-error" && kind == cudaMemcpyHostToDevice) return cudaErrorUnknown;
  return __real_cudaMemcpyAsync(dst, src, bytes, kind, stream);
}

int main(int argc, char** argv)
{
  try {
    dmgmd::MpiRuntime mpi(argc, argv);
    try {
      rank_id = mpi.world_rank();
      if (argc > 1 && std::string(argv[1]) == "selftest-post") fault = "selftest-post";
      mpi.initialize_device();
      // Startup p2p uses the production direction tags too. Arm production
      // injections only after it has completed, with fresh request counters.
      halo_posts = 0;
      send_requests = nullptr;
      fault = argc > 1 ? argv[1] : "normal";
      const int size = mpi.world_size();
      const int left = (rank_id + size - 1) % size, right = (rank_id + 1) % size;
      // Distinct per-face values expose P=2 direction-tag swaps. Increase
      // counts between exchanges to force capacity growth only after finish.
      for (int count : {1, 0, 17, 2, 1}) {
        const int stride = 4 * (count == 0 ? 1 : count);
        std::vector<double> values(2 * stride, -1.0);
        std::vector<int> indices(stride);
        for (int i = 0; i < stride; ++i) indices[i] = i;
        for (int c = 0; c < 2; ++c) {
          for (int i = 0; i < 2 * count; ++i) values[c * stride + i] = 1000 * rank_id + 100 * c + i;
        }
        double* device_values = nullptr;
        int* device_indices = nullptr;
        cuda_check(cudaMalloc(&device_values, values.size() * sizeof(double)));
        cuda_check(cudaMalloc(&device_indices, indices.size() * sizeof(int)));
        cuda_check(cudaMemcpy(device_values, values.data(), values.size() * sizeof(double), cudaMemcpyHostToDevice));
        cuda_check(cudaMemcpy(device_indices, indices.data(), indices.size() * sizeof(int), cudaMemcpyHostToDevice));
        dmgmd::CommunicationVolume volume;
        auto begin = [&]() {
          return mpi.begin_p2p_indexed_device_soa(device_values, 2, stride,
              device_indices, count, device_indices + count, count,
              device_indices + 2 * count, count, device_indices + 3 * count, count,
              left, right, volume);
        };
        if (rank_id == 0 && fault == "invalid-count") {
          mpi.begin_p2p_indexed_device_soa(device_values, 2, stride,
              device_indices, -1, device_indices, 0, device_indices, 0,
              device_indices, 0, left, right, volume);
        }
        const auto token = begin();
        if (rank_id == 0 && fault == "abandon") return 0; // destructor must abort
        if (rank_id == 0 && fault == "double-begin") begin();
        if (rank_id == 0 && fault == "host-active") {
          mpi.exchange_p2p_host_bytes(nullptr, 0, nullptr, 0, nullptr, 0, nullptr, 0,
                                     left, right, dmgmd::ByteClass::control, volume);
        }
        if (rank_id == 0 && fault == "stale-token") mpi.finish_halo({token.generation + 1});
        auto state = mpi.halo_state(token);
        require(state.pack_ready && state.send_source_ready, "begin published an unready send source");
        if (size > 1) require(!state.reusable && !state.sends_complete && !state.receives_complete,
                              "begin prematurely released the slot");
        if (size > 1 && (fault == "receive-first" || fault == "send-first")) {
          hold_sends = fault == "receive-first";
          hold_receives = fault == "send-first";
          bool reached = false;
          for (int i = 0; i < 1000000; ++i) {
            state = mpi.progress_halo(token);
            if (hold_sends ? state.unpack_complete : state.sends_complete) { reached = true; break; }
          }
          require(reached, "progress did not reach the unheld completion group");
          require(!state.reusable, "one completion group released the entire slot");
          if (hold_sends) require(state.receive_gpu_ready && !state.sends_complete, "receive/send states conflated");
          if (hold_receives) require(!state.receives_complete && !state.unpack_complete, "send/receive states conflated");
          if (rank_id == 0) std::printf("HALO_WITNESS mode=%s count=%d sends=%d receives=%d gpu=%d unpack=%d reusable=%d\n",
              fault.c_str(), count, state.sends_complete, state.receives_complete,
              state.receive_gpu_ready, state.unpack_complete, state.reusable);
          hold_sends = hold_receives = false;
        } else if (fault == "test-error" || fault == "progress-only") {
          for (int i = 0; i < 1000000 && !state.reusable; ++i) state = mpi.progress_halo(token);
          require(state.reusable, "progress-only exchange failed to complete");
        }
        state = mpi.finish_halo(token);
        require(state.sends_complete && state.receives_complete && state.receive_gpu_ready &&
                state.unpack_complete && state.reusable, "finish did not prove reuse");
        const auto calls = volume.p2p_calls;
        mpi.finish_halo(token);
        mpi.progress_halo(token);
        require(calls == volume.p2p_calls && calls == (size > 1 ? 4 : 0), "finish double-accounted");
        require(volume.halo_send_bytes_local == (size > 1 ? 4ULL * count * sizeof(double) : 0), "wrong byte count");
        require(volume.halo_recv_bytes_local == volume.halo_send_bytes_local, "wrong receive byte count");
        if (rank_id == 0) std::printf("HALO_REUSE generation=%llu count=%d requests_complete=1 gpu_ready=1 unpack_complete=1 calls=%llu\n",
            static_cast<unsigned long long>(token.generation), count, static_cast<unsigned long long>(calls));
        cuda_check(cudaMemcpy(values.data(), device_values, values.size() * sizeof(double), cudaMemcpyDeviceToHost));
        if (size > 1) {
          for (int c = 0; c < 2; ++c) for (int i = 0; i < count; ++i) {
            require(values[c * stride + 2 * count + i] == 1000 * left + 100 * c + count + i,
                    "left receive has wrong direction/value");
            require(values[c * stride + 3 * count + i] == 1000 * right + 100 * c + i,
                    "right receive has wrong direction/value");
          }
        }
        cuda_check(cudaFree(device_indices));
        cuda_check(cudaFree(device_values));
      }
      if (mpi.is_root()) std::printf("HALO_LIFECYCLE PASS mode=%s ranks=%d backend=%s\n", fault.c_str(), size, mpi.backend_name());
    } catch (const std::exception& error) {
      mpi.report_error("halo-test", error.what());
      mpi.abort(1);
    }
  } catch (const std::exception& error) {
    std::fprintf(stderr, "%s\n", error.what());
    return 1;
  }
}
