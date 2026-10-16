// CUDA regression for the M2a Neighbor center-range API.  In particular,
// center_begin is deliberately non-zero so ELL row selection must use
// slot * stride + center rather than stride * center_begin.
#include "gpumd_compat/box.cuh"
#include "gpumd_compat/gpu_vector.cuh"
#include "gpumd_compat/neighbor.cuh"
#include "gpumd_compat/domain_partition.cuh"
#include "gpumd_compat/nep.cuh"
#include "gpumd_compat/domain_partition_verify.cuh"

#include <cuda_runtime.h>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {

using gpumd_compat::Box;
using gpumd_compat::GPU_Vector;
using gpumd_compat::Neighbor;

void require(bool condition, const char* message)
{
  if (!condition) throw std::runtime_error(message);
}

Box make_box()
{
  Box box{};
  box.cpu_h[0] = 64.0;
  box.cpu_h[4] = 24.0;
  box.cpu_h[8] = 24.0;
  box.get_inverse();
  box.set_is_orthogonal();
  return box;
}

void check_nonzero_center_begin()
{
  constexpr int count = 4;
  const std::vector<int> host_type(count, 0);
  const std::vector<unsigned long long> host_gid{40, 30, 20, 10};
  const std::vector<double> host_position{
      1.0, 2.0, 3.0, 4.0,
      12.0, 12.0, 12.0, 12.0,
      12.0, 12.0, 12.0, 12.0};

  GPU_Vector<int> type(count);
  GPU_Vector<unsigned long long> gid(count);
  GPU_Vector<double> position(3 * count);
  type.copy_from_host(host_type.data());
  gid.copy_from_host(host_gid.data());
  position.copy_from_host(host_position.data());

  Neighbor neighbor;
  neighbor.initialize(7.0, count, 16);
  Box box = make_box();
  neighbor.find_neighbor_domain(
      7.0, box, type, position, gid, 1, 3, count,
      gpumd_compat::DomainNeighborAction::must_rebuild, 7);

  std::vector<int> nn(count, 0);
  std::vector<int> nl(neighbor.NL.size(), -1);
  neighbor.NN.copy_to_host(nn.data());
  neighbor.NL.copy_to_host(nl.data());
  require(nn[1] == 3 && nn[2] == 3,
          "non-zero center range must build both selected rows");

  const auto row = [&](int center) {
    std::vector<int> values;
    for (int slot = 0; slot < nn[center]; ++slot) {
      values.push_back(nl[static_cast<std::size_t>(slot) * count + center]);
    }
    return values;
  };
  require(row(1) == std::vector<int>({3, 2, 0}),
          "center 1 row must be sorted by candidate global ID");
  require(row(2) == std::vector<int>({3, 1, 0}),
          "center 2 row must be sorted by candidate global ID");

  // The runtime owns the displacement decision. Moving a candidate far away
  // and explicitly confirming reuse must leave the cached rows untouched.
  std::vector<double> moved = host_position;
  moved[3] = 30.0;
  position.copy_from_host(moved.data());
  neighbor.find_neighbor_domain(
      7.0, box, type, position, gid, 1, 3, count,
      gpumd_compat::DomainNeighborAction::confirmed_reuse, 7);
  std::vector<int> reused(count, 0);
  neighbor.NN.copy_to_host(reused.data());
  require(reused == nn, "confirmed reuse must not rebuild cached rows");

  bool stale_epoch_rejected = false;
  try {
    neighbor.find_neighbor_domain(
        7.0, box, type, position, gid, 1, 3, count,
        gpumd_compat::DomainNeighborAction::confirmed_reuse, 8);
  } catch (const std::logic_error&) {
    stale_epoch_rejected = true;
  }
  require(stale_epoch_rejected, "confirmed reuse with a stale layout epoch must fail");

  Neighbor missing_cache;
  missing_cache.initialize(7.0, count, 16);
  bool rejected = false;
  try {
    missing_cache.find_neighbor_domain(
        7.0, box, type, position, gid, 1, 3, count,
        gpumd_compat::DomainNeighborAction::confirmed_reuse, 7);
  } catch (const std::logic_error&) {
    rejected = true;
  }
  require(rejected, "confirmed reuse without a reference cache must fail");
}

void check_gpu_vector_capacity_reuse()
{
  GPU_Vector<int> values;
  const std::uint64_t allocations_before =
      gpumd_compat::gpu_vector_allocation_count();
  values.resize_reuse(8, 3);
  require(values.size() == 8 && values.capacity() >= 8,
          "capacity-aware resize must retain an exact logical size");
  require(gpumd_compat::gpu_vector_allocation_count() == allocations_before + 1,
          "first capacity-aware resize must allocate once");
  int* const storage = values.data();
  const std::size_t capacity = values.capacity();

  values.resize_reuse(4, 7);
  require(values.data() == storage && values.size() == 4 && values.capacity() == capacity,
          "shrinking logical size must reuse physical storage");
  require(gpumd_compat::gpu_vector_allocation_count() == allocations_before + 1,
          "reused storage must not increment the allocation counter");
  std::vector<int> host(4, 0);
  values.copy_to_host(host.data());
  require(host == std::vector<int>(4, 7),
          "initialized resize must fill the complete reused logical range");

  values.resize_reuse(capacity + 1);
  require(values.size() == capacity + 1 && values.capacity() >= values.size(),
          "growth must preserve exact logical size and add sufficient capacity");
  require(gpumd_compat::gpu_vector_allocation_count() == allocations_before + 2,
          "capacity growth must increment the allocation counter once");
  int* const grown_storage = values.data();
  values.resize_reuse(0);
  require(values.size() == 0 && values.data() == grown_storage,
          "logical clear must retain reusable physical storage");
}

void check_partition_graphs()
{
  using gpumd_compat::DomainPartition;
  using gpumd_compat::DomainPartitionKey;
  GPU_Vector<int> nn(5), nl(25);
  GPU_Vector<unsigned long long> gids(5);
  std::vector<unsigned long long> gid{40,10,50,20,30};
  gids.copy_from_host(gid.data());
  DomainPartition p;
  DomainPartitionKey key;
  key.local = key.dependency = 5;
  for (int bits = 0; bits < 1024; ++bits) {
    std::vector<std::vector<int>> rows(5);
    int edge = 0;
    for (int i = 0; i < 5; ++i) for (int j = i+1; j < 5; ++j, ++edge)
      if (bits & (1 << edge)) { rows[i].push_back(j); rows[j].push_back(i); }
    std::vector<int> counts(5), entries(25, -1);
    for (int c = 0; c < 5; ++c) {
      std::sort(rows[c].begin(), rows[c].end(), [&](int a, int b) { return gid[a] < gid[b]; });
      counts[c] = rows[c].size();
      for (std::size_t e = 0; e < rows[c].size(); ++e) entries[e*5+c] = rows[c][e];
    }
    nn.copy_from_host(counts.data()); nl.copy_from_host(entries.data());
    ++key.neighbor; // same layout/shape, new Verlet generation
    for (int owned = 0; owned <= 5; ++owned) {
      key.owned = owned;
      require(p.classify(key, 5, nn.data(), nl.data(), gids.data()), "new key must classify");
      auto want = gpumd_compat::partition_graph(rows, owned, 5, 5);
      std::vector<int> got(5);
      p.interior.copy_to_host(got.data()); require(got == want.interior, "device interior vs graph oracle");
      p.early.copy_to_host(got.data()); require(got == want.early, "device auxiliary vs graph oracle");
      require(!p.classify(key, 5, nn.data(), nl.data(), gids.data()), "same key reuses mask");
    }
  }
  auto next = key; ++next.storage;
  require(p.classify(next, 5, nn.data(), nl.data(), gids.data()), "storage invalidation");
  next = key; ++next.layout;
  require(p.classify(next, 5, nn.data(), nl.data(), gids.data()), "mapping invalidation");
  next = key; next.box[0] = 64;
  require(p.classify(next, 5, nn.data(), nl.data(), gids.data()), "box invalidation");
  std::vector<int> invalid(5, 6); nn.copy_from_host(invalid.data()); ++next.neighbor;
  bool rejected = false;
  try { p.classify(next, 5, nn.data(), nl.data(), gids.data()); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && !p.valid, "invalid row count must fail before NL read");
  gpumd_compat::DomainPartitionKey empty;
  require(p.classify(empty, 0, nullptr, nullptr, nullptr), "empty rank classification");
  require(p.interior_count == 0 && p.early_count == 0, "empty rank no padding reads");
}

void check_partition_force_preconditions()
{
  constexpr int L = 4;
  gpumd_compat::NEP nep(DMGMD_SOURCE_DIR "/tests/baseline/inputs/potentials/nep_C.txt", L);
  nep.N1 = nep.ND1 = 0; nep.N2 = 3; nep.ND2 = 4;
  nep.neighbor_record_sink = [](int, int, int) {};
  GPU_Vector<int> type(L, 0);
  GPU_Vector<unsigned long long> gid(L);
  const unsigned long long ids[]{40,10,30,20}; gid.copy_from_host(ids);
  GPU_Vector<double> pos(3*L), pe(L, 0.0), force(3*L, 0.0), virial(9*L, 0.0);
  const double coords[]{17,23,29,35, 12,12,12,12, 12,12,12,12};
  pos.copy_from_host(coords);
  Box box = make_box();
  nep.compute_domain(box, L, type, pos, pe, force, virial, gid,
                    gpumd_compat::DomainNeighborAction::must_rebuild, 7);
  bool rejected = false;
  try {
    nep.compute_domain(box, L, type, pos, pe, force, virial, gid,
      gpumd_compat::DomainNeighborAction::confirmed_reuse, 7,
      {gpumd_compat::DomainPartitionMode::serial, true, 1, 0});
  } catch (const std::logic_error&) { rejected = true; }
  require(rejected, "layout epoch alone cannot prove this step's halo readiness");
  pe.fill(0); force.fill(0); virial.fill(0);
  nep.compute_domain(box, L, type, pos, pe, force, virial, gid,
    gpumd_compat::DomainNeighborAction::confirmed_reuse, 7,
    {gpumd_compat::DomainPartitionMode::verify, true, 1, 1});
  require(nep.partition_split_calls == 1, "force precondition test must actually split");
  gpumd_compat::DomainForceContext overlap{gpumd_compat::DomainPartitionMode::serial, true, 2, 0};
  overlap.overlap = gpumd_compat::DomainOverlapMode::wait;
  overlap.mapping_epoch = 7;
  int clears = 0, finishes = 0;
  overlap.begin_halo = [] { return 37; };
  overlap.clear_outputs = [&] { ++clears; pe.fill(0); force.fill(0); virial.fill(0); };
  overlap.finish_halo = [&](std::uint64_t generation) {
    require(generation == 37, "force bound wrong halo generation");
    ++finishes; require(cudaDeviceSynchronize() == cudaSuccess, "complete early CUDA"); return true;
  };
  nep.compute_domain(box, L, type, pos, pe, force, virial, gid,
    gpumd_compat::DomainNeighborAction::confirmed_reuse, 7, overlap);
  require(clears == 1 && finishes == 1 && nep.partition_split_calls == 2, "owned-ready early did not execute");
  overlap.finish_halo = [](std::uint64_t) { return false; };
  rejected = false;
  try {
    nep.compute_domain(box, L, type, pos, pe, force, virial, gid,
      gpumd_compat::DomainNeighborAction::confirmed_reuse, 7, overlap);
  } catch (const std::logic_error&) { rejected = true; }
  require(rejected, "late consumed an uncompleted halo");
  require(cudaDeviceSynchronize() == cudaSuccess, "complete early CUDA");
  overlap.begin_halo = [&]() -> std::uint64_t { nep.allocate_workspace(L); return 38; };
  rejected = false;
  try {
    nep.compute_domain(box, L, type, pos, pe, force, virial, gid,
      gpumd_compat::DomainNeighborAction::confirmed_reuse, 7, overlap);
  } catch (const std::logic_error&) { rejected = true; }
  require(rejected, "force lease allowed workspace invalidation");
  box.cpu_h[0] += 1; box.get_inverse(); box.set_is_orthogonal();
  rejected = false;
  try {
    nep.compute_domain(box, L, type, pos, pe, force, virial, gid,
      gpumd_compat::DomainNeighborAction::confirmed_reuse, 7,
      {gpumd_compat::DomainPartitionMode::serial, true, 2, 2});
  } catch (const std::logic_error&) { rejected = true; }
  require(rejected, "box change cannot reuse a mask or unrebuilt Verlet cache");
  const int angular_counts[]{1,0,0,0};
  nep.nep_data.NN_angular.copy_from_host(angular_counts);
  nep.nep_data.NL_angular.fill(-1);
  const int missing_reverse = 1;
  nep.nep_data.NL_angular.copy_from_host(&missing_reverse, 1);
  rejected = false;
  try {
    gpumd_compat::verify_partition_filtered(nep.nep_data, L, 4, 3,
      nep.params().MN_radial, nep.params().MN_angular, nullptr, nullptr, false);
  } catch (const std::runtime_error&) { rejected = true; }
  require(rejected, "filtered reverse edge must be checked before gather");
}

}  // namespace

int main()
{
  int device_count = 0;
  const cudaError_t status = cudaGetDeviceCount(&device_count);
  if (status != cudaSuccess || device_count == 0) {
    std::cout << "domain_neighbor_cuda_tests: skipped (no CUDA device)\n";
    return 77;
  }
  try {
    check_partition_graphs();
    check_partition_force_preconditions();
    check_gpu_vector_capacity_reuse();
    check_nonzero_center_begin();
    std::cout << "domain_neighbor_cuda_tests: all checks passed\n";
  } catch (const std::exception& error) {
    std::cerr << "domain_neighbor_cuda_tests FAILED: " << error.what() << '\n';
    return 1;
  }
  return 0;
}
