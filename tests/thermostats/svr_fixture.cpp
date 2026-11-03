#include "gpumd_compat/thermostat_svr.hpp"

#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {

std::uint64_t bits(double x)
{
  std::uint64_t value;
  std::memcpy(&value, &x, sizeof(value));
  return value;
}

void write_case(std::uint32_t seed, int ndeg, double taut)
{
  gpumd_compat::SvrState state(seed);
  const double kk = 1.234567890123;
  const double sigma = 9.876543210987;
  std::cout << "{\"seed\":" << seed << ",\"ndeg\":" << ndeg
            << ",\"taut\":" << std::setprecision(17) << taut
            << ",\"kk\":" << std::setprecision(17) << kk
            << ",\"sigma\":" << std::setprecision(17) << sigma
            << ",\"gasdev_count\":6,\"sumnoises_nn\":[0,1,2,3,10],\"resamplekin_steps\":8"
            << ",\"gasdev_bits\":[";
  for (int i = 0; i < 6; ++i) {
    if (i) std::cout << ',';
    std::cout << bits(gpumd_compat::gasdev(state));
  }
  std::cout << "],\"sumnoises\":[";
  const int nn_values[] = {0, 1, 2, 3, 10};
  for (std::size_t i = 0; i < sizeof(nn_values) / sizeof(nn_values[0]); ++i) {
    if (i) std::cout << ',';
    std::cout << "{\"nn\":" << nn_values[i] << ",\"bits\":"
              << bits(gpumd_compat::resamplekin_sumnoises(nn_values[i], state)) << '}';
  }
  std::cout << "],\"resamplekin_bits\":[";
  for (int i = 0; i < 8; ++i) {
    if (i) std::cout << ',';
    std::cout << bits(gpumd_compat::resamplekin(kk, sigma, ndeg, taut, state));
  }
  std::cout << "]}";
}

}  // namespace

int main()
{
  bool rejects_invalid_shape = false;
  try {
    gpumd_compat::SvrState invalid_state(0);
    (void)gpumd_compat::gamdev(0, invalid_state);
  } catch (const std::invalid_argument&) {
    rejects_invalid_shape = true;
  }
  if (!rejects_invalid_shape) return 2;

  const std::uint32_t seeds[] = {12345678u, 0u, 42u, UINT32_MAX};
  const int degrees[] = {3, 6, 9, 48};
  const double taus[] = {0.1, 0.01, 1.0, 100.0};
  std::cout << "{\"schema\":1,\"cases\":[";
  bool first = true;
  for (std::uint32_t seed : seeds) {
    for (int i = 0; i < 4; ++i) {
      if (!first) std::cout << ',';
      first = false;
      write_case(seed, degrees[i], taus[i]);
    }
  }
  std::cout << "]}\n";
  return std::cout ? 0 : 1;
}
