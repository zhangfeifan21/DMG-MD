// DMG-MD O2 graph classification. Does not read positions or change Verlet rows.
#pragma once
#include "domain_partition.hpp"
#include "gpu_vector.cuh"
#include <algorithm>

namespace gpumd_compat {
namespace partition_detail {
static __global__ void safe_rows(int O, int D, int L, int capacity,
  const int* nn, const int* nl, const unsigned long long* gid, int* safe, int* error)
{
  int c = blockIdx.x * blockDim.x + threadIdx.x;
  if (c >= D) return;
  int count = nn[c];
  if (count < 0 || count > capacity) { atomicExch(error, 1); return; }
  int s = c < O;
  unsigned long long previous = 0;
  for (int e = 0; e < count; ++e) {
    int j = nl[static_cast<std::size_t>(e) * L + c];
    if (j < 0 || j >= L || j == c) { atomicExch(error, 1); return; }
    if (e && gid[j] <= previous) { atomicExch(error, 1); return; }
    previous = gid[j];
    if (j >= O) s = 0;
  }
  safe[c] = s;
}
static __global__ void closed_rows(int O, int L, const int* nn, const int* nl,
  const int* safe, int* interior, int* early, int* counts)
{
  int c = blockIdx.x * blockDim.x + threadIdx.x;
  if (c >= O || !safe[c]) return;
  for (int e = 0; e < nn[c]; ++e)
    if (!safe[nl[static_cast<std::size_t>(e) * L + c]]) return;
  interior[c] = 1;
  atomicAdd(counts, 1);
  atomicExch(early + c, 1);
  for (int e = 0; e < nn[c]; ++e)
    atomicExch(early + nl[static_cast<std::size_t>(e) * L + c], 1);
}
static __global__ void count_early(int O, const int* early, int* counts)
{
  int c = blockIdx.x * blockDim.x + threadIdx.x;
  if (c < O && early[c]) atomicAdd(counts + 1, 1);
}
} // namespace partition_detail

struct DomainPartition {
  DomainPartitionKey key;
  bool valid = false;
  int interior_count = 0, early_count = 0;
  GPU_Vector<int> safe, interior, early, scratch;

  bool classify(const DomainPartitionKey& next, int capacity, const int* nn,
    const int* nl, const unsigned long long* gid)
  {
    if (valid && key == next) return false;
    valid = false; // never expose a partially regenerated mask
    safe.resize_reuse(next.local, 0);
    interior.resize_reuse(next.local, 0);
    early.resize_reuse(next.local, 0);
    scratch.resize_reuse(3, 0);
    if (next.dependency) {
      partition_detail::safe_rows<<<(next.dependency + 127) / 128, 128>>>(
        next.owned, next.dependency, next.local, capacity, nn, nl, gid,
        safe.data(), scratch.data() + 2);
      GPU_CHECK_KERNEL
    }
    int counts[3]{};
    scratch.copy_to_host(counts);
    if (counts[2]) throw std::runtime_error("partition invalid Verlet row/count/gid order");
    if (next.owned) {
      partition_detail::closed_rows<<<(next.owned + 127) / 128, 128>>>(
        next.owned, next.local, nn, nl, safe.data(), interior.data(), early.data(), scratch.data());
      GPU_CHECK_KERNEL
      partition_detail::count_early<<<(next.owned + 127) / 128, 128>>>(
        next.owned, early.data(), scratch.data());
      GPU_CHECK_KERNEL
    }
    scratch.copy_to_host(counts);
    key = next; interior_count = counts[0]; early_count = counts[1]; valid = true;
    return true;
  }
};
} // namespace gpumd_compat
