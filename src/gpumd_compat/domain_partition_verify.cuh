// Test-only diagnostics for DMGMD_DOMAIN_PARTITION=verify. Full and split
// consume one snapshot, one V and one logical force call; no extra MPI sink.
#pragma once
#include <cmath>
#include <cstring>
#include <limits>

namespace gpumd_compat {
template<class T> std::vector<T> partition_download(const GPU_Vector<T>& v)
{
  std::vector<T> h(v.size());
  if (!h.empty()) CHECK(cudaMemcpy(h.data(), v.data(), h.size()*sizeof(T), cudaMemcpyDeviceToHost));
  return h;
}
template<class T> void partition_equal(T a, T b, const char* field)
{
  if (!std::isfinite(static_cast<double>(a)) ||
      !std::isfinite(static_cast<double>(b)) || std::memcmp(&a, &b, sizeof(T)))
    throw std::runtime_error(std::string("partition oracle mismatch: ") + field);
}

// Run before descriptor/force consumers, including the original oracle.
// A missing/unavailable reverse row must fail before the gather searches it.
inline void verify_partition_filtered(const NEP_Data& d, int L, int D, int O,
  int radial_capacity, int angular_capacity, const int* dep_mask,
  const int* force_mask, bool complement)
{
  auto nr = partition_download(d.NN_radial), na = partition_download(d.NN_angular);
  auto lr = partition_download(d.NL_radial), la = partition_download(d.NL_angular);
  std::vector<int> dep(L, 1), force(L, 1);
  if (dep_mask) CHECK(cudaMemcpy(dep.data(), dep_mask, L*sizeof(int), cudaMemcpyDeviceToHost));
  if (force_mask) CHECK(cudaMemcpy(force.data(), force_mask, L*sizeof(int), cudaMemcpyDeviceToHost));
  for (int c = 0; c < D; ++c) {
    if (dep_mask && !complement && !dep[c]) continue; // early: H has no producer yet
    if (nr[c] < 0 || nr[c] > radial_capacity || na[c] < 0 || na[c] > angular_capacity)
      throw std::runtime_error("partition unavailable/invalid filtered count");
    for (int e = 0; e < nr[c]; ++e) {
      int j = lr[static_cast<std::size_t>(e)*L+c];
      if (j < 0 || j >= L) throw std::runtime_error("partition invalid filtered slot");
    }
    for (int e = 0; e < na[c]; ++e) {
      int j = la[static_cast<std::size_t>(e)*L+c];
      if (j < 0 || j >= L) throw std::runtime_error("partition invalid angular slot");
    }
  }
  for (int c = 0; c < O; ++c) {
    if (force_mask && ((force[c] != 0) == complement)) continue;
    for (int e = 0; e < nr[c]; ++e) {
      int j = lr[static_cast<std::size_t>(e)*L+c];
      if (j >= D || (dep_mask && !complement && !dep[j]))
        throw std::runtime_error("partition unavailable Fp producer");
    }
    for (int e = 0; e < na[c]; ++e) {
      int j = la[static_cast<std::size_t>(e)*L+c];
      if (j >= D || (dep_mask && !complement && !dep[j]) || na[j] < 0 || na[j] > angular_capacity)
        throw std::runtime_error("partition unavailable reverse partial producer");
      int found = 0;
      for (int r = 0; r < na[j]; ++r) found += la[static_cast<std::size_t>(r)*L+j] == c;
      if (found != 1) throw std::runtime_error("partition missing/duplicate reverse angular edge");
    }
  }
}

inline void verify_partition_graph(const DomainPartition& p, const Neighbor& neighbor,
  const GPU_Vector<unsigned long long>& global_id)
{
  const auto& k = p.key;
  auto nn = partition_download(neighbor.NN), nl = partition_download(neighbor.NL);
  auto gid = partition_download(global_id);
  std::vector<std::vector<int>> rows(k.dependency);
  for (int c = 0; c < k.dependency; ++c) {
    if (nn[c] < 0 || nn[c] > neighbor.domain_row_capacity())
      throw std::runtime_error("partition invalid count");
    for (int e = 0; e < nn[c]; ++e) rows[c].push_back(nl[static_cast<std::size_t>(e)*k.local+c]);
  }
  auto expected = partition_graph(rows, k.owned, k.dependency, k.local);
  if (partition_download(p.safe) != expected.safe ||
      partition_download(p.interior) != expected.interior ||
      partition_download(p.early) != expected.early)
    throw std::runtime_error("partition coverage/intersection/closure mismatch");
  // Final-force neighbors must have dependency rows and the reverse edge.
  // This check precedes the unpartitioned oracle's binary search as well.
  for (int c = 0; c < k.owned; ++c) for (int j : rows[c]) {
    if (j >= k.dependency || std::find(rows[j].begin(), rows[j].end(), c) == rows[j].end())
      throw std::runtime_error("partition missing dependency/reverse Verlet edge");
  }
  for (int c = 0; c < k.dependency; ++c) for (std::size_t e = 1; e < rows[c].size(); ++e)
    if (gid[rows[c][e-1]] >= gid[rows[c][e]])
      throw std::runtime_error("partition gid ordering mismatch");
}

struct PartitionSnapshot {
  std::vector<double> pe, force, virial;
  std::vector<int> nr, na, lr, la;
  std::vector<float> fp, sums, px, py, pz;
  PartitionSnapshot(const NEP_Data& d, const GPU_Vector<double>& energy,
    const GPU_Vector<double>& f, const GPU_Vector<double>& v)
    : pe(partition_download(energy)), force(partition_download(f)), virial(partition_download(v)),
      nr(partition_download(d.NN_radial)), na(partition_download(d.NN_angular)),
      lr(partition_download(d.NL_radial)), la(partition_download(d.NL_angular)),
      fp(partition_download(d.Fp)), sums(partition_download(d.sum_fxyz)),
      px(partition_download(d.f12x)), py(partition_download(d.f12y)), pz(partition_download(d.f12z)) {}
};

inline void verify_partition_products(const PartitionSnapshot& want, const NEP_Data& d,
  const GPU_Vector<double>& pe, const GPU_Vector<double>& force, const GPU_Vector<double>& virial,
  int L, int D, int O, const std::vector<int>* early = nullptr,
  const std::vector<int>* interior = nullptr)
{
  PartitionSnapshot got(d, pe, force, virial);
  for (int c = 0; c < D; ++c) {
    if (early && !(*early)[c]) continue;
    partition_equal(want.nr[c], got.nr[c], "NN radial");
    partition_equal(want.na[c], got.na[c], "NN angular");
    for (int e = 0; e < want.nr[c]; ++e) {
      std::size_t q = static_cast<std::size_t>(e)*L+c;
      partition_equal(want.lr[q], got.lr[q], "NL radial/order");
    }
    for (int e = 0; e < want.na[c]; ++e) {
      std::size_t q = static_cast<std::size_t>(e)*L+c;
      partition_equal(want.la[q], got.la[q], "NL angular/order");
      partition_equal(want.px[q], got.px[q], "partial x");
      partition_equal(want.py[q], got.py[q], "partial y");
      partition_equal(want.pz[q], got.pz[q], "partial z");

    }
    for (std::size_t q = c; q < want.fp.size(); q += L)
      partition_equal(want.fp[q], got.fp[q], "Fp");
    for (std::size_t q = c; q < want.sums.size(); q += L)
      partition_equal(want.sums[q], got.sums[q], "angular sums");
  }
  for (int c = 0; c < O; ++c) {
    if (interior && !(*interior)[c]) continue;
    partition_equal(want.pe[c], got.pe[c], "per-atom energy");
    for (int axis = 0; axis < 3; ++axis)
      partition_equal(want.force[axis*L+c], got.force[axis*L+c], "per-atom force");
    for (int axis = 0; axis < 9; ++axis)
      partition_equal(want.virial[axis*L+c], got.virial[axis*L+c], "per-atom virial");
    for (int e = 0; e < got.na[c]; ++e) {
      int j = got.la[static_cast<std::size_t>(e)*L+c];
      if (j < 0 || j >= D || (early && !(*early)[j]))
        throw std::runtime_error("partition unavailable force dependency");
      int found = 0;
      for (int r = 0; r < got.na[j]; ++r)
        found += got.la[static_cast<std::size_t>(r)*L+j] == c;
      if (found != 1) throw std::runtime_error("partition reverse angular edge not unique");
    }
  }
  // Ghosts never receive final force or virial. Energy on D\O is descriptor
  // scratch and is compared after the complete split.
  if (!early) for (int c = O; c < L; ++c) {
    partition_equal(want.pe[c], got.pe[c], "ghost scratch energy");
    for (int a = 0; a < 3; ++a)
      partition_equal(want.force[a*L+c], got.force[a*L+c], "ghost force");
    for (int a = 0; a < 9; ++a)
      partition_equal(want.virial[a*L+c], got.virial[a*L+c], "ghost virial");
  }
}
} // namespace gpumd_compat
