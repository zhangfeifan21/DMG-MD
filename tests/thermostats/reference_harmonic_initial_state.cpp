#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

int main(int argc, char** argv)
{
  if (argc != 4) {
    std::cerr << "usage: reference_harmonic_initial_state OUTPUT SEED TEMPERATURE_K\n";
    return 2;
  }
  const std::string output = argv[1];
  const auto seed = static_cast<std::uint64_t>(std::stoull(argv[2]));
  const double temperature = std::stod(argv[3]);
  if (!std::isfinite(temperature) || temperature <= 0.0) return 2;
  constexpr int natoms = 8;
  constexpr double kB = 8.617343e-5;
  const double kT = kB * temperature;
  std::mt19937_64 generator(seed);
  std::normal_distribution<double> standard_normal(0.0, 1.0);
  double x[natoms][3]{};
  double v[natoms][3]{};
  for (int mode = 0; mode < 3 * natoms; ++mode) {
    const double omega = 0.15 + 0.01 * mode;
    x[mode / 3][mode % 3] = std::sqrt(kT) / omega * standard_normal(generator);
    v[mode / 3][mode % 3] = std::sqrt(kT) * standard_normal(generator);
  }
  std::ofstream data(output, std::ios::out | std::ios::trunc);
  if (!data) throw std::runtime_error("cannot create " + output);
  data << std::setprecision(17)
       << "LAMMPS harmonic sampler shared initial state, seed " << seed << "\n\n"
       << natoms << " atoms\n1 atom types\n\n"
       << "-100 100 xlo xhi\n-100 100 ylo yhi\n-100 100 zlo zhi\n\n"
       << "Masses\n\n1 1.0\n\nAtoms # atomic\n\n";
  for (int atom = 0; atom < natoms; ++atom) {
    data << atom + 1 << " 1 " << x[atom][0] << ' ' << x[atom][1] << ' ' << x[atom][2] << '\n';
  }
  data << "\nVelocities\n\n";
  for (int atom = 0; atom < natoms; ++atom) {
    data << atom + 1 << ' ' << v[atom][0] << ' ' << v[atom][1] << ' ' << v[atom][2] << '\n';
  }
  if (!data) throw std::runtime_error("failed writing " + output);
}
