// DMG-MD O2 graph classification. Does not read positions or change Verlet rows.
#pragma once
#include "domain_partition.hpp"
#include "gpu_vector.cuh"
#include <algorithm>
#include <cub/device/device_scan.cuh>

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
// Geometry uses the same immutable q as V. No S/I/B construction is needed.
static __global__ void geometry_rows(int O, int D, int L, int capacity,
  const int* nn, const int* nl, const unsigned long long* gid, const double* q,
  double lo, double hi, double inset, int* early, int* error)
{
  int c = blockIdx.x * blockDim.x + threadIdx.x;
  if (c >= D) return;
  const bool selected = c < O && q[c] > lo && q[c] < hi &&
    fmin(q[c]-lo, hi-q[c]) > inset;
  early[c] = selected;
  int count = nn[c];
  if (count < 0 || count > capacity) { atomicExch(error, 1); return; }
  unsigned long long previous = 0;
  for (int e = 0; e < count; ++e) {
    int j = nl[static_cast<std::size_t>(e)*L+c];
    if (j < 0 || j >= L || j == c) { atomicExch(error, 1); return; }
    if ((e && gid[j] <= previous) || (selected && j >= O)) atomicExch(error, 1);
    previous = gid[j];
  }
}
// Exclusive prefix sum makes both lists strictly increasing in original slot.
static __global__ void scatter_centers(int D, const int* early, const int* offset,
  int* early_idx, int* late_idx, int* counts)
{
  int c = blockIdx.x * blockDim.x + threadIdx.x;
  if (c >= D) return;
  if (early[c]) early_idx[offset[c]] = c;
  else late_idx[c-offset[c]] = c;
  if (c == D-1) counts[1] = offset[c] + early[c];
}
} // namespace partition_detail

struct DomainPartition {
  DomainPartitionKey key;
  bool valid = false;
  int interior_count = 0, early_count = 0;
  GPU_Vector<int> safe, interior, early, scratch;
  GPU_Vector<int> early_idx, late_idx, offsets;
  GPU_Vector<unsigned char> scan_temp;
  std::size_t logical_bytes() const {
    return 4*(safe.size()+interior.size()+early.size()+scratch.size()+
      early_idx.size()+late_idx.size()+offsets.size()) + scan_temp.size();
  }
  std::size_t capacity_bytes() const {
    return 4*(safe.capacity()+interior.capacity()+early.capacity()+scratch.capacity()+
      early_idx.capacity()+late_idx.capacity()+offsets.capacity()) + scan_temp.capacity();
  }

  bool classify(const DomainPartitionKey& next, int capacity, const int* nn,
    const int* nl, const unsigned long long* gid, const double* reference = nullptr)
  {
    if (valid && key == next) return false;
    valid = false;
    if (next.owned < 0 || next.dependency < next.owned || next.local < next.dependency)
      throw std::invalid_argument("partition invalid shape");
    const bool geometry = next.rule == DomainSchedule::producer_geometry;
    const bool compact = compact_schedule(next.rule);
    if (geometry && (next.slab_axis < 0 || next.slab_axis > 2 ||
        !(next.slab_lo >= 0) || !(next.slab_hi > next.slab_lo) ||
        !(next.inset > 0) || !std::isfinite(next.inset) ||
        (next.dependency && !reference)))
      throw std::invalid_argument("geometric producer lacks reference/slab proof");
    safe.resize_reuse(geometry ? 0 : next.local, 0);
    interior.resize_reuse(geometry ? 0 : next.local, 0);
    early.resize_reuse(next.local, 0);
    scratch.resize_reuse(3, 0);
    int counts[3]{};
    if (next.dependency) {
      if (geometry) {
        partition_detail::geometry_rows<<<(next.dependency+127)/128,128>>>(
          next.owned,next.dependency,next.local,capacity,nn,nl,gid,reference,
          next.slab_lo,next.slab_hi,next.inset,early.data(),scratch.data()+2);
      } else {
        partition_detail::safe_rows<<<(next.dependency+127)/128,128>>>(
          next.owned,next.dependency,next.local,capacity,nn,nl,gid,safe.data(),scratch.data()+2);
      }
      GPU_CHECK_KERNEL
    }
    if (!geometry) {
      scratch.copy_to_host(counts);
      if (counts[2]) throw std::runtime_error("partition invalid Verlet row/count/gid order");
      if (next.owned) {
        partition_detail::closed_rows<<<(next.owned+127)/128,128>>>(
          next.owned,next.local,nn,nl,safe.data(),interior.data(),early.data(),scratch.data());
        GPU_CHECK_KERNEL
        if (!compact) {
          partition_detail::count_early<<<(next.owned+127)/128,128>>>(next.owned,early.data(),scratch.data());
          GPU_CHECK_KERNEL
        }
      }
    }
    early_idx.resize_reuse(compact ? next.dependency : 0);
    late_idx.resize_reuse(compact ? next.dependency : 0);
    offsets.resize_reuse(compact ? next.dependency : 0);
    if (compact && next.dependency) {
      std::size_t bytes = 0;
      CHECK(cub::DeviceScan::ExclusiveSum(nullptr,bytes,early.data(),offsets.data(),next.dependency));
      scan_temp.resize_reuse(bytes);
      CHECK(cub::DeviceScan::ExclusiveSum(scan_temp.data(),bytes,early.data(),offsets.data(),next.dependency));
      partition_detail::scatter_centers<<<(next.dependency+127)/128,128>>>(next.dependency,
        early.data(),offsets.data(),early_idx.data(),late_idx.data(),scratch.data());
      GPU_CHECK_KERNEL
    }
    scratch.copy_to_host(counts);
    if (counts[2]) throw std::runtime_error("partition invalid Verlet row or geometric producer closure");
    key = next; interior_count = counts[0]; early_count = counts[1]; valid = true;
    return true;
  }
};
} // namespace gpumd_compat
