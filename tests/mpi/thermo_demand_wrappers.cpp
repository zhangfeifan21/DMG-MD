#include <mpi.h>
#include <cstdio>

namespace {
unsigned long long thermo_sums = 0;
unsigned long long reason_ors = 0;
}

extern "C" int MPI_Allreduce(const void* input, void* output, int count,
                              MPI_Datatype type, MPI_Op operation, MPI_Comm communicator)
{
  const int status = PMPI_Allreduce(input, output, count, type, operation, communicator);
  if (status == MPI_SUCCESS && communicator == MPI_COMM_WORLD) {
    if (count == 8 && type == MPI_DOUBLE && operation == MPI_SUM) ++thermo_sums;
    if (count == 1 && type == MPI_UINT32_T && operation == MPI_BOR) ++reason_ors;
  }
  return status;
}

extern "C" int MPI_Finalize()
{
  int rank = -1;
  PMPI_Comm_rank(MPI_COMM_WORLD, &rank);
  std::printf("O8_MPI rank=%d thermo_sums=%llu reason_ors=%llu\n",
              rank, thermo_sums, reason_ors);
  std::fflush(stdout);
  return PMPI_Finalize();
}
