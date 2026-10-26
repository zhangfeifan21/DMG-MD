// Test-only PMPI interposition: count actual operations, independently of
// CommunicationVolume. No interception is linked into the production binary.
#include <mpi.h>
#include <cstdio>

namespace {
unsigned long long reason_or = 0;
unsigned long long scalar_max = 0;
unsigned long long invalid_or = 0;
unsigned long long displacement_or = 0;

void report()
{
  int rank = -1;
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  std::printf("O7_MPI rank=%d reason_or=%llu scalar_max=%llu invalid_or=%llu displacement_or=%llu\n",
              rank, reason_or, scalar_max, invalid_or, displacement_or);
  std::fflush(stdout);
}
}

extern "C" int MPI_Allreduce(const void* input, void* output, int count,
                                     MPI_Datatype type, MPI_Op op, MPI_Comm comm)
{
  const int status = PMPI_Allreduce(input, output, count, type, op, comm);
  if (comm == MPI_COMM_WORLD && count == 1 && status == MPI_SUCCESS) {
    if (type == MPI_UINT32_T && op == MPI_BOR) {
      ++reason_or;
      const auto reasons = *static_cast<const unsigned int*>(output);
      if (reasons & (1u << 1)) ++invalid_or;
      if (reasons & (1u << 2)) ++displacement_or;
    }
    if (type == MPI_DOUBLE && op == MPI_MAX) ++scalar_max;
  }
  return status;
}

extern "C" int MPI_Finalize()
{
  report();
  return PMPI_Finalize();
}

extern "C" int MPI_Abort(MPI_Comm comm, int code)
{
  report();
  return PMPI_Abort(comm, code);
}
