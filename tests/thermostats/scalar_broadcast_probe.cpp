// Test-only LD_PRELOAD probe for the two-double host ensemble-control message.
// It observes successful MPI_Bcast calls only; production buffers and control
// flow are never changed, and this wrapper does not inspect CUDA/device data.

#include <mpi.h>

#include <atomic>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstring>

extern "C" int MPI_Bcast(
  void* buffer, int count, MPI_Datatype datatype, int root, MPI_Comm communicator)
{
  const int result = PMPI_Bcast(buffer, count, datatype, root, communicator);
  if (result != MPI_SUCCESS || datatype != MPI_DOUBLE || count != 2 || root != 0) {
    return result;
  }

  int rank = -1;
  if (PMPI_Comm_rank(communicator, &rank) != MPI_SUCCESS) {
    return result;
  }

  std::uint64_t status_bits = 0;
  std::uint64_t factor_bits = 0;
  const auto* values = static_cast<const double*>(buffer);
  static_assert(sizeof(status_bits) == sizeof(values[0]), "double must be 64 bits");
  std::memcpy(&status_bits, &values[0], sizeof(status_bits));
  std::memcpy(&factor_bits, &values[1], sizeof(factor_bits));

  static std::atomic<unsigned long long> next_sequence{0};
  const unsigned long long sequence = next_sequence.fetch_add(1, std::memory_order_relaxed);
  std::fprintf(
    stderr,
    "DMGMD_ENSEMBLE_FACTOR rank=%d sequence=%llu status_bits=%016" PRIx64
    " factor_bits=%016" PRIx64 "\n",
    rank, sequence, status_bits, factor_bits);
  std::fflush(stderr);
  return result;
}
