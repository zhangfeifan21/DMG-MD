#include "dmgmd/ensemble_control.hpp"
#include "gpumd_compat/common.cuh"
#include "gpumd_compat/thermostat_svr.hpp"
#include "gpumd_compat/npt_scr.hpp"

#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <new>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <tuple>

namespace {
bool count_allocations = false;
std::size_t allocations = 0;
std::size_t cases = 0;
}

void* operator new(std::size_t size)
{
  if (count_allocations) ++allocations;
  if (void* value = std::malloc(size == 0 ? 1 : size)) return value;
  throw std::bad_alloc();
}
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* value) noexcept { std::free(value); }
void operator delete[](void* value) noexcept { std::free(value); }
void operator delete(void* value, std::size_t) noexcept { std::free(value); }
void operator delete[](void* value, std::size_t) noexcept { std::free(value); }

namespace {

void expect(bool condition, const char* message)
{
  if (!condition) throw std::runtime_error(message);
}

std::uint64_t bits(double value)
{
  std::uint64_t result = 0;
  static_assert(sizeof(result) == sizeof(value));
  std::memcpy(&result, &value, sizeof(result));
  return result;
}

// Frozen scalar expressions from the pre-controller replicated-full/local-domain Berendsen blocks.
// Runtime A/B scientific-file hashes additionally check the CUDA host build.
double legacy_target(const dmgmd::EnsembleCommand& ensemble, int step, int steps)
{
  const double fraction = static_cast<double>(step) / static_cast<double>(steps);
  return ensemble.initial_temperature +
         (ensemble.final_temperature - ensemble.initial_temperature) * fraction;
}

std::optional<double> legacy_factor(
    const dmgmd::EnsembleCommand& ensemble, double temperature, int step, int steps)
{
  const double fraction = static_cast<double>(step) / static_cast<double>(steps);
  const double target = ensemble.initial_temperature +
                       (ensemble.final_temperature - ensemble.initial_temperature) * fraction;
  const double coupling = 1.0 / ensemble.temperature_coupling;
  if (coupling > 1.0e-5) {
    return std::sqrt(1.0 + coupling * (target / temperature - 1.0));
  }
  return std::nullopt;
}

void test_temperature_schedule()
{
  for (const auto& temperatures : std::array<std::array<double, 2>, 4>{{
           {{300, 300}}, {{100, 500}}, {{500, 100}}, {{1e-6, 1e6}}}}) {
    const dmgmd::EnsembleCommand configuration{
        dmgmd::EnsembleKind::nvt_ber, temperatures[0], temperatures[1], 100};
    const dmgmd::EnsembleController controller(configuration);
    for (int steps : {1, 2, 7, 1000003}) {
      for (int step : {0, steps / 2, steps - 1}) {
        const double expected = legacy_target(configuration, step, steps);
        const double actual = controller.target_temperature(step, steps);
        expect(bits(actual) == bits(expected), "target-temperature bits changed");
        std::cout << "PASS schedule case=" << ++cases << " T1=" << temperatures[0]
                  << " T2=" << temperatures[1] << " step=" << step << " steps=" << steps
                  << " expected_bits=" << bits(expected) << " actual_bits=" << bits(actual) << '\n';
      }
    }
  }
}

void test_berendsen_factor()
{
  const std::array<double, 8> couplings{{
      1.0, 10.0, 1000.0, std::nextafter(100000.0, 0.0), 100000.0,
      std::nextafter(100000.0, std::numeric_limits<double>::infinity()), 100001.0, 1e6}};
  for (double tau : couplings) {
    const dmgmd::EnsembleCommand configuration{dmgmd::EnsembleKind::nvt_ber, 300, 600, tau};
    const dmgmd::EnsembleController controller(configuration);
    expect(controller.needs_post_force_thermo(), "weak NVT lost its thermo consumer");
    expect(!controller.before_force(0.1, std::nullopt), "Berendsen pre-force hook is active");
    for (double temperature : {100.0, 300.0, 900.0}) {
      const std::array<double, 8> thermo{{temperature, 9, 8, 7, 6, 5, 4, 3}};
      for (int step : {0, 3, 6}) {
        const auto expected = legacy_factor(configuration, temperature, step, 7);
        const auto actual = controller.after_force(&thermo, step, 7);
        expect(actual.has_value() == expected.has_value(), "Berendsen launch branch changed");
        if (actual) expect(bits(*actual) == bits(*expected), "Berendsen factor bits changed");
        std::cout << "PASS factor case=" << ++cases << " tau_bits=" << bits(tau)
                  << " T=" << temperature << " step=" << step
                  << " apply=" << actual.has_value();
        if (actual) std::cout << " expected_bits=" << bits(*expected) << " actual_bits=" << bits(*actual);
        std::cout << '\n';
      }
    }
  }

  const dmgmd::EnsembleController active({dmgmd::EnsembleKind::nvt_ber, 300, 300, 100});
  const std::array<double, 8> at_target{{300}};
  const auto one = active.after_force(&at_target, 0, 1);
  expect(one && bits(*one) == bits(1.0), "active factor=1 must still launch scaling");
  const dmgmd::EnsembleController weak({dmgmd::EnsembleKind::nvt_ber, 300, 300, 1e6});
  expect(!weak.after_force(nullptr, 0, 1), "disabled coupling consumed thermo or launched scaling");
  bool rejected = false;
  try {
    static_cast<void>(active.after_force(nullptr, 0, 1));
  } catch (const std::logic_error&) {
    rejected = true;
  }
  expect(rejected, "active NVT accepted missing global thermo");
  std::cout << "PASS factor-one/disabled-coupling/missing-thermo case=" << ++cases << '\n';
}

void test_npt_ber_scalar_contract()
{
  const dmgmd::EnsembleCommand configuration{
      dmgmd::EnsembleKind::npt_ber, 300, 600, 50, -2.5, 1.0, 1.0};
  const dmgmd::EnsembleController controller(configuration);
  expect(controller.has_barostat(), "npt_ber controller lacks barostat");
  const std::array<double, 8> thermo{{300.0, 0.0, 1.25, 2.75, 8.5, 0.0, 0.0, 0.0}};
  // Frozen scalar fixture evaluated from the pinned cpu_pressure_isotropic
  // sequence: mean=(p0+p1+p2)*0.3333333333333333, target=P/160.2177,
  // pc=(1/(3*tau_p*B))*160.2177, scale=1-pc*(target-mean).
  expect(bits(controller.barostat_scale(&thermo, 1000.0, 0, 1, 17)) ==
             UINT64_C(0x406c0b740da740da),
         "npt_ber scalar fixture differs from pinned pressure equation");
  const auto temp_factor = controller.after_force(&thermo, 0, 2);
  expect(temp_factor && *temp_factor > 0.0, "npt_ber Berendsen temperature feedback missing");

  const dmgmd::EnsembleController fixed_box({
      dmgmd::EnsembleKind::npt_ber, 300, 300, 10, -5.0, 2000.0001, 10.0});
  expect(bits(fixed_box.barostat_scale(&thermo, 1000.0, 0, 1, 1)) == bits(1.0),
         "B>2000 did not disable the barostat exactly");

  auto zero_temperature = thermo;
  zero_temperature[0] = 0.0;
  bool rejected_zero_temperature = false;
  try {
    static_cast<void>(controller.after_force(&zero_temperature, 0, 2));
  } catch (const std::runtime_error& error) {
    rejected_zero_temperature =
        std::string(error.what()).find("instantaneous temperature") != std::string::npos;
  }
  expect(rejected_zero_temperature, "npt_ber accepted zero kinetic temperature");

  const dmgmd::EnsembleController invalid_scale({
      dmgmd::EnsembleKind::npt_ber, 300, 300, 10, 1e9, 1.0, 1.0});
  auto low_pressure = thermo;
  low_pressure[2] = low_pressure[3] = low_pressure[4] = 0.0;
  bool rejected_scale = false;
  try {
    static_cast<void>(invalid_scale.barostat_scale(&low_pressure, 1000.0, 0, 1, 23));
  } catch (const std::runtime_error& error) {
    const std::string message = error.what();
    rejected_scale = message.find("step=23") != std::string::npos &&
                     message.find("P_GPa=1e+09") != std::string::npos &&
                     message.find("P_inst_internal=0") != std::string::npos &&
                     message.find("factor=") != std::string::npos;
  }
  expect(rejected_scale, "invalid box factor lacks detailed failure context");
  std::cout << "PASS npt_ber scalar contract case=" << ++cases << '\n';
}

void test_npt_scr_single_step_oracles()
{
  // Layer one is a formula-only oracle. The Gaussian is injected and is not
  // evidence for production RNG behavior; the expected value is independently
  // frozen from the pinned equation with internal pressure units.
  constexpr double injected_gaussian = 1.0348526110957657;
  const double injected_scale = gpumd_compat::npt_scr_isotropic_scale(
      -2.5 / 1.602177e+2, (1.0 / (3.0 * 2.0 * 10.0)) * 1.602177e+2,
      (0.1 + 0.2 + 0.3) * 0.3333333333333333,
      300.0, 1000.0, injected_gaussian);
  expect(std::abs(injected_scale - 1.5827460335479948) < 2.0e-15,
         "fixed-Gaussian pressure/volume/temperature/unit oracle changed");
  expect(gpumd_compat::npt_scr_isotropic_scale(2.0, 1.0, 0.0, 300.0, 1000.0, -100.0) < 0.0,
         "fixed-Gaussian negative scale fixture did not exercise rejection domain");

  // Layer two exercises the real controller-owned SvrState: BDP first, then
  // pressure gasdev. N=1 makes BDP's ndeg=3 noise consume gasdev's first
  // sample and one gamdev(1) uniform, leaving its paired Gaussian cached for
  // the pressure draw.
  const dmgmd::EnsembleCommand configuration{
      dmgmd::EnsembleKind::npt_scr, 300, 300, 100, -2.5, 10, 2};
  dmgmd::EnsembleController controller(configuration, 1, 0.01, true, 12345678u);
  expect(controller.uses_root_state() && controller.has_barostat(),
         "npt_scr must use root RNG state and a barostat");
  const std::array<double, 8> thermo{{250.0, 0.0, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0}};
  const auto bdp_factor = controller.after_force(&thermo, 0, 1);
  expect(bdp_factor && std::abs(*bdp_factor - 0.96235819655887289) < 2.0e-15,
         "npt_scr first BDP factor differs from independent seeded oracle");
  const double scr_factor = controller.barostat_scale(&thermo, 1000.0, 0, 1, 1);
  expect(std::abs(scr_factor - 1.5827460335479948) < 2.0e-15,
         "npt_scr pressure draw/order differs from independent seeded oracle");

  // pc==0 suppresses the noise contribution, but the pressure Gaussian must
  // still be consumed. The next BDP factor distinguishes the advanced cached
  // stream from one where the fixed-box pressure draw was skipped.
  const dmgmd::EnsembleCommand fixed_configuration{
      dmgmd::EnsembleKind::npt_scr, 300, 300, 100, 0, 2001, 2};
  dmgmd::EnsembleController fixed(fixed_configuration, 1, 0.01, true, 12345678u);
  static_cast<void>(fixed.after_force(&thermo, 0, 1));
  expect(bits(fixed.barostat_scale(&thermo, 1000.0, 0, 1, 1)) == bits(1.0),
         "npt_scr B>2000 did not yield an exact fixed box");
  const auto next_bdp = fixed.after_force(&thermo, 0, 1);
  expect(next_bdp && std::abs(*next_bdp - 0.88910040747385666) < 2.0e-15,
         "npt_scr B>2000 failed to consume the pressure Gaussian/cache");

  const dmgmd::EnsembleCommand invalid_configuration{
      dmgmd::EnsembleKind::npt_scr, 300, 300, 100, 1e9, 1, 1};
  dmgmd::EnsembleController invalid(invalid_configuration, 1, 0.01, true, 12345678u);
  auto invalid_thermo = thermo;
  invalid_thermo[2] = invalid_thermo[3] = invalid_thermo[4] = 0.0;
  bool rejected = false;
  try {
    static_cast<void>(invalid.barostat_scale(&invalid_thermo, 1000.0, 0, 1, 9));
  } catch (const std::runtime_error& error) {
    const std::string message = error.what();
    rejected = message.find("npt_scr") != std::string::npos &&
               message.find("step=9") != std::string::npos &&
               message.find("P_GPa=1e+09") != std::string::npos &&
               message.find("P_inst_internal=0") != std::string::npos &&
               message.find("T_target=300") != std::string::npos &&
               message.find("V_pre_scale=1000") != std::string::npos &&
               message.find("gaussian=") != std::string::npos &&
               message.find("factor=") != std::string::npos;
  }
  expect(rejected, "npt_scr invalid scale did not fail with complete context");
  std::cout << "PASS npt_scr fixed-Gaussian/RNG-cache/safety oracles case=" << ++cases << '\n';
}

void test_segment_lifecycle()
{
  dmgmd::EnsembleCommand configuration{dmgmd::EnsembleKind::nvt_ber, 100, 200, 50};
  const dmgmd::EnsembleController first(configuration);
  const std::array<double, 8> thermo{{150}};
  for (int step = 0; step < 7; ++step) {
    static_cast<void>(first.before_force(0.1, std::nullopt));
    static_cast<void>(first.after_force(&thermo, step, 7));
  }
  const dmgmd::EnsembleController repeated(configuration);
  expect(bits(first.target_temperature(0, 7)) == bits(100.0), "segment origin changed");
  expect(bits(repeated.target_temperature(0, 3)) == bits(100.0), "consecutive run did not reset ramp");
  expect(bits(first.target_temperature(6, 7)) == bits(legacy_target(configuration, 6, 7)),
         "last step incorrectly reached T2");
  configuration.initial_temperature = 200;
  configuration.final_temperature = 100;
  const dmgmd::EnsembleController changed(configuration);
  expect(bits(first.target_temperature(0, 7)) == bits(100.0), "controller retained mutable configuration");
  expect(bits(changed.target_temperature(0, 3)) == bits(200.0), "new ensemble configuration was lost");
  std::cout << "PASS segment-reset/configuration-copy case=" << ++cases << '\n';
}

void test_no_state_allocation()
{
  // Stateful controllers own an optional heap state; legacy construction and
  // hooks must still allocate nothing, including construction/destruction.
  const std::array<double, 8> thermo{{300}};
  bool hooks_ok = true;
  const auto before = allocations;
  count_allocations = true;
  {
    const dmgmd::EnsembleController nve(dmgmd::EnsembleCommand{});
    const dmgmd::EnsembleController nvt({dmgmd::EnsembleKind::nvt_ber, 300, 300, 100});
    for (int step = 0; step < 7; ++step) {
      hooks_ok = hooks_ok && !nve.needs_post_force_thermo();
      hooks_ok = hooks_ok && !nve.before_force(0.1, std::nullopt);
      hooks_ok = hooks_ok && !nve.before_force(0.1, 123.0);
      hooks_ok = hooks_ok && !nve.after_force(nullptr, step, 7);
      hooks_ok = hooks_ok && !nve.after_force(&thermo, step, 7);
      hooks_ok = hooks_ok && !nvt.before_force(0.1, std::nullopt);
      hooks_ok = hooks_ok && nvt.after_force(&thermo, step, 7).has_value();
    }
  }
  count_allocations = false;
  expect(hooks_ok, "NVE/no-op hook behavior changed");
  expect(allocations == before, "NVE/Berendsen allocated hidden state");
  std::cout << "PASS no-chain-or-RNG/heap-allocation case=" << ++cases
            << " allocation_delta=" << allocations - before << '\n';
}

void test_unimplemented_kinds()
{
  for (const auto kind : {static_cast<dmgmd::EnsembleKind>(999)}) {
    bool rejected = false;
    try {
      const dmgmd::EnsembleController controller({kind, 300, 300, 100});
      static_cast<void>(controller);
    } catch (const std::invalid_argument&) {
      rejected = true;
    }
    expect(rejected, "unknown controller kind was accepted");
    std::cout << "PASS unsupported-controller case=" << ++cases
              << " kind=" << static_cast<int>(kind) << '\n';
  }
  bool bad_scr_bulk_rejected = false;
  try {
    static_cast<void>(dmgmd::EnsembleController(
        {dmgmd::EnsembleKind::npt_scr, 300, 300, 100, 0, 0, 10},
        1, 0.01, true, 123));
  } catch (const std::invalid_argument& error) {
    bad_scr_bulk_rejected = std::string(error.what()).find("B > 0") != std::string::npos;
  }
  expect(bad_scr_bulk_rejected, "npt_scr controller accepted an invalid bulk modulus");
  std::cout << "PASS npt_scr constructor guards case=" << ++cases << '\n';
}

void test_seed_parser()
{
  expect(dmgmd::parse_ensemble_seed(nullptr) == dmgmd::kDefaultEnsembleSeed,
         "null seed did not select the default");
  expect(dmgmd::parse_ensemble_seed("0") == 0u, "seed zero rejected");
  expect(dmgmd::parse_ensemble_seed("00042") == 42u, "decimal leading zeros changed value");
  expect(dmgmd::parse_ensemble_seed("4294967295") == UINT32_MAX, "UINT32_MAX seed rejected");
  for (const char* invalid : {"", "+1", "-1", " 1", "1 ", "1x", "4294967296"}) {
    bool rejected = false;
    try { static_cast<void>(dmgmd::parse_ensemble_seed(invalid)); }
    catch (const std::invalid_argument& error) {
      rejected = std::string(error.what()).find("DMGMD_ENSEMBLE_SEED") != std::string::npos;
    }
    expect(rejected, "malformed seed was accepted or error omitted variable name");
  }
  std::cout << "PASS strict-seed-parser case=" << ++cases << '\n';
}

void test_bdp_control()
{
  for (const int atoms : {1, 2, 3}) {
    const auto configuration = dmgmd::EnsembleCommand{dmgmd::EnsembleKind::nvt_bdp, 300, 600, 100};
    const std::uint32_t seed = static_cast<std::uint32_t>(1000 + atoms);
    const dmgmd::EnsembleController controller(configuration, atoms, 0.01, true, seed);
    gpumd_compat::SvrState expected_rng(seed);
    expect(controller.uses_root_state() && controller.needs_post_force_thermo() &&
               !controller.needs_pre_force_kinetic_energy(), "BDP hook/state requirements incorrect");
    expect(!controller.before_force(0.01, std::nullopt), "BDP pre-force hook must be inert");
    for (int step = 0; step < 12; ++step) {
      const double temperature = 170.0 + 3.25 * step;
      const std::array<double, 8> thermo{{temperature}};
      const int ndeg = 3 * atoms;
      const double target = controller.target_temperature(step, 12);
      double old_KE = temperature;
      old_KE *= ndeg * K_B * 0.5;
      const double sigma = ndeg * K_B * target * 0.5;
      const double new_KE = gpumd_compat::resamplekin(
          old_KE, sigma, ndeg, configuration.temperature_coupling, expected_rng);
      const double expected_factor = std::sqrt(new_KE / old_KE);
      const auto actual = controller.after_force(&thermo, step, 12);
      expect(actual && bits(*actual) == bits(expected_factor),
             "BDP controller factor differs from independent compat state");
    }
    std::cout << "PASS BDP compat-bit-sequence case=" << ++cases << " N=" << atoms
              << " ndeg=" << 3 * atoms << '\n';
  }

  const dmgmd::EnsembleCommand configuration{dmgmd::EnsembleKind::nvt_bdp, 300, 300, 100};
  const dmgmd::EnsembleController same_a(configuration, 2, 0.01, true, 77);
  const dmgmd::EnsembleController same_b(configuration, 2, 0.01, true, 77);
  const dmgmd::EnsembleController different(configuration, 2, 0.01, true, 78);
  const std::array<double, 8> thermo{{300}};
  const auto a = same_a.after_force(&thermo, 0, 1);
  const auto b = same_b.after_force(&thermo, 0, 1);
  const auto c = different.after_force(&thermo, 0, 1);
  expect(a && b && c && bits(*a) == bits(*b), "equal BDP seeds are not reproducible");
  expect(bits(*a) != bits(*c), "different BDP seeds produced the same first factor");
  for (int step = 1; step < 8; ++step) {
    const std::array<double, 8> later_thermo{{290.0 + step}};
    const auto later_a = same_a.after_force(&later_thermo, step, 8);
    const auto later_b = same_b.after_force(&later_thermo, step, 8);
    expect(later_a && later_b && bits(*later_a) == bits(*later_b),
           "interleaved equal-seed BDP instances diverged");
  }
  const auto bdp_sequence = [&](std::uint32_t sequence_seed) {
    const dmgmd::EnsembleController sequence_controller(
        configuration, 2, 0.01, true, sequence_seed);
    std::array<std::uint64_t, 8> sequence{};
    for (int step = 0; step < 8; ++step) {
      const std::array<double, 8> input{{285.0 + step * 2.0}};
      sequence[step] = bits(*sequence_controller.after_force(&input, step, 8));
    }
    return sequence;
  };
  expect(bdp_sequence(77) == bdp_sequence(77), "reconstructed BDP controller did not reset full RNG/cache sequence");
  expect(bdp_sequence(77) != bdp_sequence(78), "different seeds produced identical BDP sequences");

  const dmgmd::EnsembleController root(configuration, 2, 0.01, true, 1234);
  const auto root_before = allocations;
  count_allocations = true;
  {
    const dmgmd::EnsembleController nonroot(configuration, 2, 0.01, false, 1234);
    expect(nonroot.uses_root_state(), "nonroot BDP lost scalar broadcast requirement");
  }
  count_allocations = false;
  expect(allocations == root_before, "nonroot BDP allocated state/RNG");
  bool nonroot_rejected = false;
  try { static_cast<void>(dmgmd::EnsembleController(configuration, 2, 0.01, false, 1234)
                              .after_force(&thermo, 0, 1)); }
  catch (const std::logic_error&) { nonroot_rejected = true; }
  expect(nonroot_rejected, "nonroot BDP hook did not fail closed");

  for (double temperature : {0.0, -1.0, std::numeric_limits<double>::infinity(),
                              std::numeric_limits<double>::quiet_NaN()}) {
    bool rejected = false;
    try {
      const std::array<double, 8> invalid_thermo{{temperature}};
      static_cast<void>(root.after_force(&invalid_thermo, 0, 1));
    } catch (const std::exception&) { rejected = true; }
    expect(rejected, "BDP accepted invalid old temperature/kinetic energy");
  }

  for (const auto& invalid : {
           std::tuple<int, double, double, double>{0, 300, 300, 100},
           {std::numeric_limits<int>::max(), 300, 300, 100},
           {1, 0, 300, 100}, {1, 300, std::numeric_limits<double>::infinity(), 100},
           {1, 300, 300, 0.99}}) {
    bool rejected = false;
    try {
      const dmgmd::EnsembleCommand bad{dmgmd::EnsembleKind::nvt_bdp,
          std::get<1>(invalid), std::get<2>(invalid), std::get<3>(invalid)};
      static_cast<void>(dmgmd::EnsembleController(
          bad, std::get<0>(invalid), 0.01, true, 123));
    } catch (const std::exception&) { rejected = true; }
    expect(rejected, "BDP accepted invalid initialization parameters");
  }
  bool bad_dt_rejected = false;
  try {
    static_cast<void>(dmgmd::EnsembleController(configuration, 2, 0.0, true, 123));
  } catch (const std::invalid_argument&) { bad_dt_rejected = true; }
  expect(bad_dt_rejected, "BDP accepted nonpositive initialization dt");
  std::cout << "PASS BDP seed/reproducibility/root-ownership/guards case=" << ++cases << '\n';
}

void test_nhc_instance_isolation_and_configuration_guards()
{
  const dmgmd::EnsembleCommand configuration{dmgmd::EnsembleKind::nvt_nhc, 300, 400, 100};
  for (const auto& invalid : {std::pair<int, double>{0, 0.01}, {3, 0.0}, {3, -0.01}}) {
    bool rejected = false;
    try { static_cast<void>(dmgmd::EnsembleController(configuration, invalid.first, invalid.second)); }
    catch (const std::invalid_argument&) { rejected = true; }
    expect(rejected, "NHC accepted missing count or invalid initialization dt");
  }

  const dmgmd::EnsembleController a(configuration, 5, 0.01, true, 31415);
  const dmgmd::EnsembleController b(configuration, 5, 0.01, true, 31415);
  const dmgmd::EnsembleController reference(configuration, 5, 0.01, true, 31415);
  std::uint64_t first_pre_bits = 0;
  std::uint64_t first_post_bits = 0;
  for (int step = 0; step < 7; ++step) {
    const auto refpre = reference.before_force(0.01, 0.8, step, 7);
    const auto apre = a.before_force(0.01, 0.8, step, 7);
    const auto bpre = b.before_force(0.01, 0.8, step, 7);
    expect(apre && bpre && refpre && bits(*apre) == bits(*refpre) && bits(*bpre) == bits(*refpre),
           "interleaved NHC controller state contaminated another instance");
    if (step == 0) first_pre_bits = bits(*apre);
    const std::array<double, 8> thermo{{275.0 + step}};
    const auto refpost = reference.after_force(&thermo, step, 7);
    const auto apost = a.after_force(&thermo, step, 7);
    const auto bpost = b.after_force(&thermo, step, 7);
    expect(apost && bpost && refpost && bits(*apost) == bits(*refpost) && bits(*bpost) == bits(*refpost),
           "interleaved NHC chain state contaminated another instance");
    if (step == 0) first_post_bits = bits(*apost);
  }
  const dmgmd::EnsembleController restarted(configuration, 5, 0.01, true, 31415);
  const dmgmd::EnsembleController restarted_again(configuration, 5, 0.01, true, 31415);
  const auto first = restarted.before_force(0.01, 0.8, 0, 7);
  const auto first_again = restarted_again.before_force(0.01, 0.8, 0, 7);
  const std::array<double, 8> restart_thermo{{275.0}};
  const auto first_post = restarted.after_force(&restart_thermo, 0, 7);
  expect(first && first_again && first_post && bits(*first) == first_pre_bits &&
             bits(*first_again) == first_pre_bits && bits(*first_post) == first_post_bits,
         "new controller did not reset chain state");
  std::cout << "PASS NHC config/instance-isolation/reset case=" << ++cases << '\n';
}

void test_nhc_control()
{
  const dmgmd::EnsembleCommand configuration{dmgmd::EnsembleKind::nvt_nhc, 300, 600, 100};
  const dmgmd::EnsembleController root(configuration, 17, 0.01, true);
  expect(root.needs_pre_force_kinetic_energy() && root.uses_root_state() &&
             root.needs_post_force_thermo(), "NHC lost its control consumers");
  const dmgmd::EnsembleController repeated(configuration, 17, 0.01, true);
  const std::array<double, 8> thermo{{300}};
  for (int step = 0; step < 8; ++step) {
    expect(bits(*root.before_force(0.01, 0.75, step, 8)) ==
               bits(*repeated.before_force(0.01, 0.75, step, 8)), "NHC pre-factor not reproducible");
    expect(bits(*root.after_force(&thermo, step, 8)) ==
               bits(*repeated.after_force(&thermo, step, 8)), "NHC post-factor not reproducible");
  }
  const auto before = allocations;
  count_allocations = true;
  {
    const dmgmd::EnsembleController nonroot(configuration, 17, 0.01, false);
    expect(nonroot.uses_root_state(), "nonroot lost broadcast requirement");
  }
  count_allocations = false;
  expect(allocations == before, "nonroot instantiated a second NHC chain");
  for (const auto kinetic : {std::optional<double>{}, std::optional<double>{0},
                              std::optional<double>{-1},
                              std::optional<double>{std::numeric_limits<double>::infinity()},
                              std::optional<double>{std::numeric_limits<double>::quiet_NaN()}}) {
    bool rejected = false;
    try { static_cast<void>(root.before_force(0.01, kinetic)); }
    catch (const std::exception&) { rejected = true; }
    expect(rejected, "NHC accepted invalid global kinetic energy");
  }
  std::cout << "PASS NHC consumers/root-only state/reproducibility/safety case=" << ++cases << '\n';
}

}  // namespace

int main()
{
  try {
    test_temperature_schedule();
    test_berendsen_factor();
    test_npt_ber_scalar_contract();
    test_npt_scr_single_step_oracles();
    test_segment_lifecycle();
    test_no_state_allocation();
    test_unimplemented_kinds();
    test_seed_parser();
    test_nhc_control();
    test_bdp_control();
    test_nhc_instance_isolation_and_configuration_guards();
  } catch (const std::exception& error) {
    count_allocations = false;
    std::cerr << "ensemble control test failure: " << error.what() << '\n';
    return 1;
  }
  std::cout << "ensemble control tests passed; cases=" << cases << '\n';
  return 0;
}
