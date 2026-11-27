// Test-only witness of the owned GID payload that each rank gives to the
// local-domain snapshot gather. Forward the collective unchanged.
#include <mpi.h>

#include <cstdio>
#include <sstream>

extern "C" int MPI_Gatherv(
    const void* send, int count, MPI_Datatype send_type, void* receive,
    const int* counts, const int* displacements, MPI_Datatype receive_type,
    int root, MPI_Comm communicator)
{
  const int status = PMPI_Gatherv(send, count, send_type, receive, counts,
                                 displacements, receive_type, root, communicator);
  if (status == MPI_SUCCESS && communicator == MPI_COMM_WORLD &&
      send_type == MPI_UNSIGNED_LONG_LONG && receive_type == MPI_UNSIGNED_LONG_LONG) {
    static int snapshot = 0;
    int rank = -1;
    PMPI_Comm_rank(communicator, &rank);
    std::ostringstream line;
    line << "DMGMD_TEST_OWNED snapshot=" << ++snapshot << " rank=" << rank
         << " root=" << root << " count=" << count << " gids=\"";
    const auto* gids = static_cast<const unsigned long long*>(send);
    for (int i = 0; i < count; ++i) {
      if (i != 0) line << ',';
      line << gids[i];
    }
    line << "\"\n";
    std::fputs(line.str().c_str(), stdout);
    std::fflush(stdout);
  }
  return status;
}
