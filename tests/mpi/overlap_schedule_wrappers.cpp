// Independent acceptance executable only. Production has no delay/fault switch.
#include <cuda_runtime.h>
#include <mpi.h>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace {
struct Receive { void* buffer; int count; MPI_Datatype type; int peer, tag; MPI_Comm comm; MPI_Request* target; };
std::vector<Receive> deferred;
std::array<cudaEvent_t, 4> timed{};
int created = 0;
bool early_recorded = false;
MPI_Request* sends = nullptr;
int query(void*, MPI_Status* status) { status->MPI_SOURCE = MPI_ANY_SOURCE; status->MPI_TAG = MPI_ANY_TAG; status->MPI_ERROR = MPI_SUCCESS; return MPI_SUCCESS; }
int release(void*) { return MPI_SUCCESS; }
int cancel(void*, int) { return MPI_SUCCESS; }
}
extern "C" cudaError_t __real_cudaEventCreate(cudaEvent_t*);
extern "C" cudaError_t __wrap_cudaEventCreate(cudaEvent_t* event)
{
  auto result = __real_cudaEventCreate(event);
  if (result == cudaSuccess) {
    if (created % 4 == 0) early_recorded = false;
    timed[created++ % 4] = *event;
  }
  return result;
}
extern "C" cudaError_t __real_cudaEventRecord(cudaEvent_t, cudaStream_t);
extern "C" cudaError_t __wrap_cudaEventRecord(cudaEvent_t event, cudaStream_t stream)
{
  auto result = __real_cudaEventRecord(event, stream);
  if (created && event == timed[1]) early_recorded = true;
  return result;
}
extern "C" int __real_MPI_Isend(const void*, int, MPI_Datatype, int, int, MPI_Comm, MPI_Request*);
extern "C" int __wrap_MPI_Isend(const void* buffer, int count, MPI_Datatype type, int peer, int tag, MPI_Comm comm, MPI_Request* request)
{
  if (created && tag == 1101) sends = request;
  return __real_MPI_Isend(buffer, count, type, peer, tag, comm, request);
}
extern "C" int __real_MPI_Irecv(void*, int, MPI_Datatype, int, int, MPI_Comm, MPI_Request*);
extern "C" int __wrap_MPI_Irecv(void* buffer, int count, MPI_Datatype type, int peer, int tag, MPI_Comm comm, MPI_Request* request)
{
  if (created && (tag == 1101 || tag == 1102)) {
    // A real incomplete generalized MPI request stands in for a deliberately
    // delayed transport post. This proves the state/dependency contract only.
    int result = MPI_Grequest_start(query, release, cancel, nullptr, request);
    if (result == MPI_SUCCESS) deferred.push_back({buffer, count, type, peer, tag, comm, request});
    return result;
  }
  return __real_MPI_Irecv(buffer, count, type, peer, tag, comm, request);
}
extern "C" int __real_MPI_Waitall(int, MPI_Request*, MPI_Status*);
extern "C" int __wrap_MPI_Waitall(int count, MPI_Request* requests, MPI_Status* statuses)
{
  if (requests == sends && !deferred.empty()) {
    int rank = -1; MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    if (early_recorded) {
      if (cudaEventSynchronize(timed[1]) != cudaSuccess) MPI_Abort(MPI_COMM_WORLD, 81);
      int completed = 1;
      MPI_Status status;
      MPI_Request_get_status(*deferred.front().target, &completed, &status);
      float milliseconds = 0;
      cudaEventElapsedTime(&milliseconds, timed[0], timed[1]);
      if (completed || milliseconds <= 0) MPI_Abort(MPI_COMM_WORLD, 82);
      std::printf("O4_DELAY_WITNESS rank=%d early_cuda_ms=%g receive_pending=1 kind=controlled-delayed-post\n", rank, milliseconds);
      std::fflush(stdout);
    }
    if (rank == 0 && std::getenv("O4_TEST_FAIL")) return MPI_ERR_OTHER;
    for (const auto& receive : deferred) {
      MPI_Grequest_complete(*receive.target);
      MPI_Request_free(receive.target);
      int result = __real_MPI_Irecv(receive.buffer, receive.count, receive.type, receive.peer,
                                   receive.tag, receive.comm, receive.target);
      if (result != MPI_SUCCESS) return result;
    }
    deferred.clear();
  }
  return __real_MPI_Waitall(count, requests, statuses);
}
