// Fixed scalar stream: isolates controller RNG and MPI broadcast from MD trajectories.
#include "dmgmd/ensemble_control.hpp"
#include <mpi.h>
#include <array>
#include <cmath>
#include <cstdio>
#include <stdexcept>

int main(int argc, char** argv) {
  MPI_Init(&argc, &argv);
  int rank = -1;
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  int failed = 0;
  try {
    dmgmd::EnsembleCommand c;
    c.kind = dmgmd::EnsembleKind::npt_scr;
    c.initial_temperature = c.final_temperature = 300;
    c.temperature_coupling = 40;
    c.bulk_modulus_gpa = 425;
    c.pressure_coupling = 200;
    auto fixed = c;
    fixed.bulk_modulus_gpa = 2001;
    dmgmd::EnsembleController normal(c, 5760, 0.025, rank == 0, 12345678);
    dmgmd::EnsembleController frozen(fixed, 5760, 0.025, rank == 0, 12345678);
    dmgmd::EnsembleController repeated(c, 5760, 0.025, rank == 0, 12345678);
    for (int i = 0; i < 12; ++i) {
      std::array<double, 8> thermo{{290.0 + i, 0, .01, .02, .03, 0, 0, 0}};
      double message[3] = {};
      if (rank == 0) {
        const double a = *normal.after_force(&thermo, i, 12);
        const double b = *frozen.after_force(&thermo, i, 12);
        const double r = *repeated.after_force(&thermo, i, 12);
        const double scale = normal.barostat_scale(&thermo, 100000, i, 12, i + 1);
        const double frozen_scale = frozen.barostat_scale(&thermo, 100000, i, 12, i + 1);
        const double repeated_scale = repeated.barostat_scale(&thermo, 100000, i, 12, i + 1);
        if (a != b || a != r || frozen_scale != 1.0 || scale != repeated_scale ||
            normal.last_barostat_gaussian() != frozen.last_barostat_gaussian() ||
            normal.last_barostat_gaussian() != repeated.last_barostat_gaussian())
          throw std::runtime_error("SCR stream or fixed-box draw diverged");
        message[0] = a; message[1] = scale; message[2] = normal.last_barostat_gaussian();
      }
      MPI_Bcast(message, 3, MPI_DOUBLE, 0, MPI_COMM_WORLD);
      for (double v : message) {
        double lo = 0.0, hi = 0.0;
        MPI_Allreduce(&v, &lo, 1, MPI_DOUBLE, MPI_MIN, MPI_COMM_WORLD);
        MPI_Allreduce(&v, &hi, 1, MPI_DOUBLE, MPI_MAX, MPI_COMM_WORLD);
        if (!std::isfinite(v) || lo != hi) failed = 1;
      }
      if (rank == 0) std::printf("%d %a %a %a\n", i, message[0], message[1], message[2]);
    }
  } catch (const std::exception& e) {
    std::fprintf(stderr, "rank %d: %s\n", rank, e.what());
    MPI_Abort(MPI_COMM_WORLD, 1);
  }
  int any = 0;
  MPI_Allreduce(&failed, &any, 1, MPI_INT, MPI_MAX, MPI_COMM_WORLD);
  MPI_Finalize();
  return any;
}
