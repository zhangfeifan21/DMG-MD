// Exercise the actual local-domain kernel and normalization/collective path.
// Including this TU exposes its internal helpers without production test hooks.
#define run_local_domain thermo_test_included_run_local_domain
#include "../../src/domain_runtime.cu"
#undef run_local_domain

#include <mpi.h>
#include <array>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {

void require(bool ok, const char* what)
{
  if (!ok) throw std::runtime_error(what);
}

int owned_for(int rank, int ranks)
{
  if (ranks > 1 && rank == ranks - 1) return 0;
  return rank == 0 ? 1041 : 7 + rank; // 1041 crosses the 1024-thread patch boundary.
}

struct Atom {
  double mass, potential;
  std::array<double, 3> velocity;
  std::array<double, 9> virial;
};

Atom atom(int rank, int index)
{
  Atom a{};
  a.mass = 1.0 + 0.125 * ((index + rank) % 5);
  a.potential = 0.125 * ((index % 13) - 6) + 0.25 * rank;
  a.velocity = {{0.125 * (1 + index % 7), -0.25 * (1 + rank), 0.0625 * (1 + index % 11)}};
  for (int c = 0; c < 9; ++c) a.virial[c] = 0.03125 * (c + 1) * (1 + (index + rank) % 9);
  return a;
}

std::array<double, 8> oracle(int ranks, int global_count, double volume)
{
  std::array<double, 8> sums{};
  for (int rank = 0; rank < ranks; ++rank) {
    for (int i = 0; i < owned_for(rank, ranks); ++i) {
      const auto a = atom(rank, i);
      const auto v = a.velocity;
      sums[0] += a.mass * (v[0]*v[0] + v[1]*v[1] + v[2]*v[2]);
      sums[1] += a.potential;
      const std::array<double, 6> kinetic{{a.mass*v[0]*v[0], a.mass*v[1]*v[1], a.mass*v[2]*v[2],
                                          a.mass*v[0]*v[1], a.mass*v[0]*v[2], a.mass*v[1]*v[2]}};
      for (int c = 0; c < 6; ++c) sums[2+c] += a.virial[c] + kinetic[c];
    }
  }
  sums[0] /= 3.0 * global_count * 8.617343e-5;
  for (int c = 2; c < 8; ++c) sums[c] /= volume;
  return sums;
}

void close(double actual, double expected, const char* what)
{
  if (!std::isfinite(actual) || std::abs(actual - expected) > 1e-10 * std::max(1.0, std::abs(expected)))
    throw std::runtime_error(std::string(what) + ": got " + std::to_string(actual) +
                             ", expected " + std::to_string(expected));
}

} // namespace

int main(int argc, char** argv)
{
  try {
    dmgmd::MpiRuntime mpi(argc, argv);
    const int rank = mpi.world_rank(), ranks = mpi.world_size();
    require(ranks == 1 || ranks == 2 || ranks == 4, "expected P1, P2 or P4");
    mpi.initialize_device();
    const int owned = owned_for(rank, ranks), ghosts = 9, stride = owned + ghosts;
    int global = 0;
    require(MPI_Allreduce(&owned, &global, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD) == MPI_SUCCESS,
            "owned count collective failed");
    int expected_count = 0;
    for (int r = 0; r < ranks; ++r) expected_count += owned_for(r, ranks);
    require(global == expected_count && global > 0 && stride != global, "owned count or stride invariant failed");

    dmgmd::detail::DomainState state;
    state.layout.owned.resize(owned);
    int first_id = 0;
    for (int r = 0; r < rank; ++r) first_id += owned_for(r, ranks);
    std::vector<int> local_ids(global, 0), global_ids(global, 0);
    for (int i = 0; i < owned; ++i) {
      state.layout.owned[i].global_id = first_id + i;
      local_ids[first_id + i] = 1;
    }
    require(MPI_Allreduce(local_ids.data(), global_ids.data(), global, MPI_INT, MPI_SUM, MPI_COMM_WORLD) == MPI_SUCCESS,
            "owned identity collective failed");
    for (int count : global_ids) require(count == 1, "owned global ID not unique");
    state.layout.dependency_ghosts.resize(4);
    state.layout.coordinate_ghosts.resize(5);
    state.device.mass.resize(stride); state.device.potential.resize(stride);
    state.device.velocity.resize(3 * stride); state.device.virial.resize(9 * stride);
    std::vector<double> mass(stride), potential(stride), velocity(3 * stride), virial(9 * stride);
    for (int i = 0; i < stride; ++i) {
      if (i < owned) {
        const auto a = atom(rank, i); mass[i] = a.mass; potential[i] = a.potential;
        for (int c = 0; c < 3; ++c) velocity[c * stride + i] = a.velocity[c];
        for (int c = 0; c < 9; ++c) virial[c * stride + i] = a.virial[c];
      } else {
        const int kind = (i - owned) % 3;
        const double poison = kind == 0 ? std::numeric_limits<double>::quiet_NaN() :
                              kind == 1 ? std::numeric_limits<double>::infinity() : 1e200;
        mass[i] = potential[i] = poison;
        for (int c = 0; c < 3; ++c) velocity[c * stride + i] = poison;
        for (int c = 0; c < 9; ++c) virial[c * stride + i] = poison;
      }
    }
    state.device.mass.copy_from_host(mass.data(), mass.size());
    state.device.potential.copy_from_host(potential.data(), potential.size());
    state.device.velocity.copy_from_host(velocity.data(), velocity.size());
    state.device.virial.copy_from_host(virial.data(), virial.size());
    gpumd_compat::GPU_Vector<double> thermo(8);
    // Direct zero-owned reduction on every rank, with nonempty poisoned arrays.
    dmgmd::detail::find_owned_thermo_sums_range<<<8, dmgmd::detail::kThermoThreads>>>(
        0, stride, state.device.mass.data(), state.device.potential.data(),
        state.device.velocity.data(), state.device.virial.data(), thermo.data());
    dmgmd::detail::check_cuda(cudaGetLastError(), "zero-owned thermo launch");
    std::array<double, 8> zero{}; thermo.copy_to_host(zero.data());
    for (double x : zero) close(x, 0., "zero-owned reduction");

    gpumd_compat::Box box{};
    box.cpu_h[0] = 10.; box.cpu_h[4] = 12.; box.cpu_h[8] = 14.;
    box.get_inverse(); box.set_is_orthogonal();
    dmgmd::HostAtoms identity; identity.counts.global_count = global;
    dmgmd::CommunicationVolume communication;
    const auto actual = dmgmd::detail::compute_domain_thermo(state, box, identity, thermo, mpi, communication);
    const auto expected = oracle(ranks, expected_count, 10. * 12. * 14.);
    for (int i = 0; i < 8; ++i) close(actual.values[i], expected[i], "normalized thermo");
    const double pressure = (actual.values[2] + actual.values[3] + actual.values[4]) / 3. * 160.2177;
    const double expected_pressure = (expected[2] + expected[3] + expected[4]) / 3. * 160.2177;
    close(pressure, expected_pressure, "isotropic pressure");
    if (rank == 0) std::cout << "OWNED_THERMO_POISON_PASS ranks=" << ranks << " backend="
                             << mpi.backend_name() << " global_owned=" << global << '\n';
    return 0;
  } catch (const std::exception& e) {
    std::cerr << "FAIL owned thermo poison: " << e.what() << '\n';
    MPI_Abort(MPI_COMM_WORLD, 1);
    return 1;
  }
}
