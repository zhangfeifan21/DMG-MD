#include "gpumd_compat/thermostat_nhc.hpp"

#include <array>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>

namespace {
using gpumd_compat::NhcState;

constexpr char kOracleSourceSha1[] = "0d5a8e0074d399671f13a95d7e68f234172e1773";
constexpr double kBoltzmann = 8.617343e-5;
struct Case {
  int atom_count;
  double temperature;
  double damping;
  double dt;
  double initial_Ek2;
  std::array<double, 4> ramp_temperature;
};
constexpr Case kCases[] = {
  {1, 300.0, 1000.0, 0.001, 0.012, {{300.0, 300.0, 300.0, 300.0}}},
  {17, 725.0, 800.0, 0.0005, 0.19, {{725.0, 700.0, 675.0, 650.0}}},
  {103, 1200.0, 400.0, 0.002, 1.7, {{1200.0, 1225.0, 1250.0, 1275.0}}},
};

void emit_bits(double value)
{
  std::uint64_t bits;
  std::memcpy(&bits, &value, sizeof(bits));
  std::cout << '"' << std::hex << std::setw(16) << std::setfill('0') << bits
            << std::dec << '"';
}

void emit_array(const char* name, const std::array<double, 4>& values, const char* indent)
{
  std::cout << indent << '"' << name << "\": [";
  for (int i = 0; i < gpumd_compat::kNhcChainLength; ++i) {
    if (i != 0) std::cout << ", ";
    emit_bits(values[i]);
  }
  std::cout << ']';
}

void emit_case(int index, const Case& input)
{
  NhcState state = gpumd_compat::make_nhc_state(
    input.atom_count, input.temperature, input.damping, input.dt);
  double Ek2 = input.initial_Ek2;

  std::cout << "    {\n"
            << "      \"name\": \"case" << index << "\",\n"
            << "      \"N\": " << input.atom_count << ",\n"
            << "      \"T0\": " << input.temperature << ",\n"
            << "      \"Tdamp\": " << input.damping << ",\n"
            << "      \"dt\": " << input.dt << ",\n"
            << "      \"initial_Ek2\": " << input.initial_Ek2 << ",\n";
  emit_array("initial_position", state.position, "      ");
  std::cout << ",\n";
  emit_array("initial_momentum", state.momentum, "      ");
  std::cout << ",\n";
  emit_array("initial_mass", state.mass, "      ");
  std::cout << ",\n      \"steps\": [\n";

  for (int step = 0; step < 4; ++step) {
    const double target_temperature = input.ramp_temperature[step];
    const double factor = gpumd_compat::nhc_half_step(
      state, Ek2, kBoltzmann * target_temperature, 3.0 * input.atom_count, 0.5 * input.dt);
    Ek2 *= factor * factor;

    std::cout << "        {\n"
              << "          \"target_temperature\": " << target_temperature << ",\n"
              << "          \"factor\": ";
    emit_bits(factor);
    std::cout << ",\n          \"Ek2_after\": ";
    emit_bits(Ek2);
    std::cout << ",\n";
    emit_array("position", state.position, "          ");
    std::cout << ",\n";
    emit_array("momentum", state.momentum, "          ");
    std::cout << ",\n";
    emit_array("mass", state.mass, "          ");
    std::cout << "\n        }" << (step == 3 ? "\n" : ",\n");
  }
  std::cout << "      ]\n    }";
}
} // namespace

int main()
{
  std::cout << "{\n  \"source_sha1\": \"" << kOracleSourceSha1 << "\",\n  \"cases\": [\n";
  for (std::size_t i = 0; i < sizeof(kCases) / sizeof(kCases[0]); ++i) {
    if (i != 0) std::cout << ",\n";
    emit_case(static_cast<int>(i), kCases[i]);
  }
  std::cout << "\n  ]\n}\n";
}
