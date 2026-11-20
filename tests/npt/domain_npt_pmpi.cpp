// Independent, test-only collective census, loaded via LD_PRELOAD.
#include <mpi.h>
#include <cstdio>
namespace {
unsigned long long thermo = 0, reason = 0, broadcasts2 = 0, allreduce_other = 0, preflight = 0;
unsigned long long gather = 0, gatherv = 0, allgather = 0, allgatherv = 0;
}
extern "C" int MPI_Allreduce(const void* a, void* b, int n, MPI_Datatype t, MPI_Op op, MPI_Comm c) {
  if (c == MPI_COMM_WORLD) {
    if (n == 8 && t == MPI_DOUBLE && op == MPI_SUM) ++thermo;
    else if (n == 1 && t == MPI_UINT32_T && op == MPI_BOR) ++reason;
    else if (n == 1 && t == MPI_INT && op == MPI_MIN) ++preflight;
    else ++allreduce_other;
  }
  return PMPI_Allreduce(a, b, n, t, op, c);
}
extern "C" int MPI_Bcast(void* b, int n, MPI_Datatype t, int root, MPI_Comm c) {
  if (c == MPI_COMM_WORLD && n == 2 && t == MPI_DOUBLE) ++broadcasts2;
  return PMPI_Bcast(b, n, t, root, c);
}
extern "C" int MPI_Gather(const void* a, int n, MPI_Datatype t, void* b, int m, MPI_Datatype u, int root, MPI_Comm c) {
  if (c == MPI_COMM_WORLD) ++gather;
  return PMPI_Gather(a,n,t,b,m,u,root,c);
}
extern "C" int MPI_Gatherv(const void* a, int n, MPI_Datatype t, void* b, const int* m, const int* d, MPI_Datatype u, int root, MPI_Comm c) {
  if (c == MPI_COMM_WORLD) ++gatherv;
  return PMPI_Gatherv(a,n,t,b,m,d,u,root,c);
}
extern "C" int MPI_Allgather(const void* a, int n, MPI_Datatype t, void* b, int m, MPI_Datatype u, MPI_Comm c) {
  if (c == MPI_COMM_WORLD) ++allgather;
  return PMPI_Allgather(a,n,t,b,m,u,c);
}
extern "C" int MPI_Allgatherv(const void* a, int n, MPI_Datatype t, void* b, const int* m, const int* d, MPI_Datatype u, MPI_Comm c) {
  if (c == MPI_COMM_WORLD) ++allgatherv;
  return PMPI_Allgatherv(a,n,t,b,m,d,u,c);
}
extern "C" int MPI_Finalize() {
  int rank = -1; PMPI_Comm_rank(MPI_COMM_WORLD, &rank);
  std::printf("DN4_PMPI rank=%d thermo=%llu reason=%llu bcast2=%llu preflight=%llu other_allreduce=%llu gather=%llu gatherv=%llu allgather=%llu allgatherv=%llu\n",rank,thermo,reason,broadcasts2,preflight,allreduce_other,gather,gatherv,allgather,allgatherv);
  std::fflush(stdout);
  return PMPI_Finalize();
}
