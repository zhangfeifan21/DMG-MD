#include "dmgmd/ensemble_control.hpp"
#include "dmgmd/run_ir.hpp"
#include "gpumd_compat/common.cuh"

#include <array>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

namespace {
struct Options {
  std::string output;
  std::uint64_t seed = 12345678;
  std::string ensemble = "nvt_nhc";
  int steps = 100000;
  double dt = 0.01;
  double damping = 100.0;
  int natoms = 8;
  double temperature = 300.0;
};

[[noreturn]] void usage_error(const std::string& message)
{
  throw std::invalid_argument(message +
    "\nusage: harmonic_sampler --output PATH [--seed N] [--steps N] [--dt X] "
    "[--damping X] [--natoms N] [--temperature K] [--ensemble nvt_nhc|nvt_bdp]");
}

double parse_double(const std::string& text, const char* option)
{
  std::size_t consumed = 0;
  double value;
  try {
    value = std::stod(text, &consumed);
  } catch (const std::exception&) {
    usage_error(std::string("invalid value for ") + option);
  }
  if (consumed != text.size() || !std::isfinite(value)) {
    usage_error(std::string("invalid finite value for ") + option);
  }
  return value;
}

template <typename Integer>
Integer parse_integer(const std::string& text, const char* option)
{
  std::size_t consumed = 0;
  unsigned long long value;
  try {
    value = std::stoull(text, &consumed);
  } catch (const std::exception&) {
    usage_error(std::string("invalid value for ") + option);
  }
  if (consumed != text.size() || text.empty() || text[0] == '-' ||
      value > static_cast<unsigned long long>(std::numeric_limits<Integer>::max())) {
    usage_error(std::string("invalid value for ") + option);
  }
  return static_cast<Integer>(value);
}

Options parse_options(int argc, char** argv)
{
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string option = argv[i];
    if (option == "--help") {
      std::cout << "usage: harmonic_sampler --output PATH [--seed N] [--steps N] [--dt X] "
                   "[--damping X] [--natoms N] [--temperature K] [--ensemble nvt_nhc|nvt_bdp]\n";
      std::exit(0);
    }
    if (i + 1 >= argc) usage_error("missing value after " + option);
    const std::string value = argv[++i];
    if (option == "--output") options.output = value;
    else if (option == "--seed") options.seed = parse_integer<std::uint64_t>(value, "--seed");
    else if (option == "--steps") options.steps = parse_integer<int>(value, "--steps");
    else if (option == "--dt") options.dt = parse_double(value, "--dt");
    else if (option == "--damping") options.damping = parse_double(value, "--damping");
    else if (option == "--natoms") options.natoms = parse_integer<int>(value, "--natoms");
    else if (option == "--temperature") options.temperature = parse_double(value, "--temperature");
    else if (option == "--ensemble") options.ensemble = value;
    else usage_error("unknown option " + option);
  }
  if (options.output.empty()) usage_error("--output is required");
  if (options.ensemble != "nvt_nhc" && options.ensemble != "nvt_bdp") {
    usage_error("--ensemble must be nvt_nhc or nvt_bdp");
  }
  if (options.ensemble == "nvt_bdp" &&
      options.seed > std::numeric_limits<std::uint32_t>::max()) {
    usage_error("--seed must fit uint32 for nvt_bdp");
  }
  if (options.steps <= 0 || options.natoms <= 0 || options.dt <= 0.0 ||
      options.damping < 1.0 || options.temperature <= 0.0) {
    usage_error("steps/natoms/dt/temperature must be positive and damping must be >= 1");
  }
  if (options.natoms > std::numeric_limits<int>::max() / 3) {
    usage_error("--natoms is too large");
  }
  return options;
}

struct Mode {
  double position;
  double velocity;
  double omega;
};

double kinetic_energy(const std::vector<Mode>& modes)
{
  double value = 0.0;
  for (const Mode& mode : modes) value += 0.5 * mode.velocity * mode.velocity;
  if (!std::isfinite(value) || value <= 0.0) {
    throw std::runtime_error("kinetic energy became non-finite or non-positive");
  }
  return value;
}

void scale_velocities(std::vector<Mode>& modes, double factor)
{
  if (!std::isfinite(factor) || factor <= 0.0) {
    throw std::runtime_error("NHC hook returned a non-finite or non-positive scale factor");
  }
  for (Mode& mode : modes) {
    mode.velocity *= factor;
    if (!std::isfinite(mode.velocity)) throw std::runtime_error("velocity became non-finite");
  }
}

std::array<double, 3> axis_mean_mv2(const std::vector<Mode>& modes, int natoms)
{
  std::array<double, 3> sums{{0.0, 0.0, 0.0}};
  for (std::size_t mode = 0; mode < modes.size(); ++mode) {
    const std::size_t axis = mode % 3;
    sums[axis] += modes[mode].velocity * modes[mode].velocity;
  }
  for (double& value : sums) {
    value /= natoms;
    if (!std::isfinite(value) || value <= 0.0) {
      throw std::runtime_error("axis mean(m*v^2) became non-finite or non-positive");
    }
  }
  return sums;
}

FILE* create_new_output(const std::string& path)
{
  const int descriptor = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0644);
  if (descriptor < 0) {
    throw std::runtime_error("cannot create output without overwriting: " + path + ": " +
                             std::strerror(errno));
  }
  FILE* file = ::fdopen(descriptor, "w");
  if (file == nullptr) {
    const int saved_errno = errno;
    ::close(descriptor);
    throw std::runtime_error("cannot open output stream: " + std::string(std::strerror(saved_errno)));
  }
  return file;
}

} // namespace

int main(int argc, char** argv)
{
  FILE* output = nullptr;
  try {
    const Options options = parse_options(argc, argv);
    const int mode_count = 3 * options.natoms;

    dmgmd::EnsembleCommand configuration{};
    configuration.kind = options.ensemble == "nvt_nhc"
      ? dmgmd::EnsembleKind::nvt_nhc : dmgmd::EnsembleKind::nvt_bdp;
    configuration.initial_temperature = options.temperature;
    configuration.final_temperature = options.temperature;
    configuration.temperature_coupling = options.damping;
    const dmgmd::EnsembleController controller(
      configuration, options.natoms, options.dt, true,
      static_cast<std::uint32_t>(options.seed));

    std::mt19937_64 generator(options.seed);
    std::normal_distribution<double> standard_normal(0.0, 1.0);
    std::vector<Mode> modes;
    modes.reserve(static_cast<std::size_t>(mode_count));
    const double kT = K_B * options.temperature;
    for (int mode = 0; mode < mode_count; ++mode) {
      const double omega = 0.15 + 0.01 * mode;
      modes.push_back(Mode{
        std::sqrt(kT) / omega * standard_normal(generator),
        std::sqrt(kT) * standard_normal(generator),
        omega});
    }

    output = create_new_output(options.output);
    std::fprintf(output, "# ensemble=%s\n", options.ensemble.c_str());
    std::fprintf(output, "# seed=%llu\n", static_cast<unsigned long long>(options.seed));
    std::fprintf(output, "# natoms=%d\n# modes=%d\n# steps=%d\n", options.natoms, mode_count, options.steps);
    std::fprintf(output, "# temperature=%.17g\n# damping=%.17g\n# dt=%.17g\n", options.temperature, options.damping, options.dt);
    std::fprintf(output, "# sample_count=%d\n# kB=%.17g\n", options.steps, K_B);
    std::fprintf(output, "step,kinetic_energy,mean_mv2_x,mean_mv2_y,mean_mv2_z\n");

    for (int step = 0; step < options.steps; ++step) {
      if (controller.needs_pre_force_kinetic_energy()) {
        const double pre_force_kinetic = kinetic_energy(modes);
        const auto before = controller.before_force(options.dt, pre_force_kinetic, step, options.steps);
        if (!before) throw std::runtime_error("NHC pre-force hook did not return a scale factor");
        scale_velocities(modes, *before);
      }

      for (Mode& mode : modes) {
        mode.velocity += -0.5 * options.dt * mode.omega * mode.omega * mode.position;
        mode.position += options.dt * mode.velocity;
        mode.velocity += -0.5 * options.dt * mode.omega * mode.omega * mode.position;
      }

      const double kinetic_after_vv = kinetic_energy(modes);
      std::array<double, 8> thermo{};
      thermo[0] = 2.0 * kinetic_after_vv / (mode_count * K_B);
      const auto after = controller.after_force(&thermo, step, options.steps);
      if (!after) throw std::runtime_error("NVT post-force hook did not return a scale factor");
      scale_velocities(modes, *after);

      const double post_scaling_kinetic = kinetic_energy(modes);
      const auto axis_values = axis_mean_mv2(modes, options.natoms);
      std::fprintf(output, "%d,%.17g,%.17g,%.17g,%.17g\n", step, post_scaling_kinetic,
                   axis_values[0], axis_values[1], axis_values[2]);
      if (std::ferror(output)) throw std::runtime_error("failed writing output samples");
    }

    const int flush_status = std::fflush(output);
    const int close_status = std::fclose(output);
    output = nullptr;
    if (flush_status != 0 || close_status != 0) {
      throw std::runtime_error("failed to flush or close output samples");
    }
    return 0;
  } catch (const std::exception& error) {
    if (output != nullptr) std::fclose(output);
    std::cerr << "harmonic_sampler: " << error.what() << '\n';
    return 2;
  }
}
