#include "dmgmd/ensemble_control.hpp"
#include "gpumd_compat/common.cuh"
#include "gpumd_compat/thermostat_nhc.hpp"
#include "gpumd_compat/thermostat_svr.hpp"
#include "gpumd_compat/npt_scr.hpp"

#include <cmath>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>

namespace dmgmd {

struct EnsembleController::State {
  std::unique_ptr<gpumd_compat::NhcState> chain;
  std::unique_ptr<gpumd_compat::SvrState> svr;
  int global_count = 0;
  double time_step = 0.0;
};

namespace {
void require_positive(double value, const char* name)
{
  if (!std::isfinite(value) || value <= 0.0) {
    throw std::runtime_error(std::string(name) + " must be finite and positive");
  }
}

void require_target_temperatures(const EnsembleCommand& configuration, const char* ensemble)
{
  require_positive(configuration.initial_temperature, (std::string(ensemble) + " T1").c_str());
  require_positive(configuration.final_temperature, (std::string(ensemble) + " T2").c_str());
}
}  // namespace

std::uint32_t parse_ensemble_seed(const char* value)
{
  if (value == nullptr) return kDefaultEnsembleSeed;
  if (*value == '\0') {
    throw std::invalid_argument("DMGMD_ENSEMBLE_SEED must contain decimal digits");
  }
  std::uint32_t parsed = 0;
  for (const unsigned char* cursor = reinterpret_cast<const unsigned char*>(value); *cursor; ++cursor) {
    if (*cursor < '0' || *cursor > '9') {
      throw std::invalid_argument("DMGMD_ENSEMBLE_SEED must contain decimal digits only");
    }
    const std::uint32_t digit = static_cast<std::uint32_t>(*cursor - '0');
    if (parsed > (std::numeric_limits<std::uint32_t>::max() - digit) / 10u) {
      throw std::invalid_argument("DMGMD_ENSEMBLE_SEED exceeds UINT32_MAX");
    }
    parsed = parsed * 10u + digit;
  }
  return parsed;
}

EnsembleController::EnsembleController(
    const EnsembleCommand& configuration, int global_count,
    double initial_time_step, bool owns_state, std::uint32_t seed)
    : configuration_(configuration)
{
  switch (configuration_.kind) {
    case EnsembleKind::nve:
    case EnsembleKind::nvt_ber:
      break;
    case EnsembleKind::npt_ber:
    case EnsembleKind::npt_scr: {
      const bool scr = configuration_.kind == EnsembleKind::npt_scr;
      const char* name = scr ? "NPT stochastic cell rescaling" : "NPT Berendsen";
      require_target_temperatures(configuration_, name);
      if (!std::isfinite(configuration_.temperature_coupling) ||
          configuration_.temperature_coupling < 1.0 ||
          !std::isfinite(configuration_.target_pressure_gpa) ||
          !std::isfinite(configuration_.bulk_modulus_gpa) ||
          configuration_.bulk_modulus_gpa <= 0.0 ||
          !std::isfinite(configuration_.pressure_coupling) ||
          configuration_.pressure_coupling < 1.0) {
        throw std::invalid_argument(
            std::string(name) + " requires Tdamp >= 1, finite P, B > 0 and tau_p >= 1");
      }
      target_pressure_internal_ =
          configuration_.target_pressure_gpa / gpumd_compat::PRESSURE_UNIT_CONVERSION;
      if (configuration_.bulk_modulus_gpa > 2000.0) {
        pressure_coupling_internal_ = 0.0;
      } else {
        // Preserve GPUMD's operation order: form pc in GPa^-1, then convert
        // it to (eV/A^3)^-1 exactly once.
        pressure_coupling_internal_ =
            1.0 / (3.0 * configuration_.pressure_coupling *
                   configuration_.bulk_modulus_gpa);
        pressure_coupling_internal_ *= gpumd_compat::PRESSURE_UNIT_CONVERSION;
      }
      if (!std::isfinite(target_pressure_internal_) ||
          !std::isfinite(pressure_coupling_internal_) ||
          pressure_coupling_internal_ < 0.0) {
        throw std::invalid_argument(std::string(name) + " pressure conversion is not finite");
      }
      if (scr) {
        if (global_count <= 0 || global_count > std::numeric_limits<int>::max() / 3) {
          throw std::invalid_argument("NPT SCR requires 0 < N <= INT_MAX/3");
        }
        if (owns_state) {
          state_ = std::make_unique<State>();
          state_->svr = std::make_unique<gpumd_compat::SvrState>(seed);
          state_->global_count = global_count;
        }
      }
      break;
    }
    case EnsembleKind::nvt_nhc:
      require_target_temperatures(configuration_, "NHC");
      if (!std::isfinite(configuration_.temperature_coupling) ||
          configuration_.temperature_coupling < 1.0 || global_count <= 0 ||
          !std::isfinite(initial_time_step) || initial_time_step <= 0.0) {
        throw std::invalid_argument("NHC requires Tdamp >= 1, positive N and finite positive dt");
      }
      if (owns_state) {
        state_ = std::make_unique<State>();
        state_->chain = std::make_unique<gpumd_compat::NhcState>(
            gpumd_compat::make_nhc_state(global_count, configuration_.initial_temperature,
                                         configuration_.temperature_coupling, initial_time_step));
        state_->global_count = global_count;
        state_->time_step = initial_time_step;
        for (double mass : state_->chain->mass) require_positive(mass, "NHC chain mass");
      }
      break;
    case EnsembleKind::nvt_bdp:
      require_target_temperatures(configuration_, "BDP");
      if (global_count <= 0 || global_count > std::numeric_limits<int>::max() / 3 ||
          !std::isfinite(configuration_.temperature_coupling) ||
          configuration_.temperature_coupling < 1.0 ||
          !std::isfinite(initial_time_step) || initial_time_step <= 0.0) {
        throw std::invalid_argument(
            "BDP requires 0 < N <= INT_MAX/3, Tdamp >= 1 and finite positive dt");
      }
      if (owns_state) {
        state_ = std::make_unique<State>();
        state_->svr = std::make_unique<gpumd_compat::SvrState>(seed);
        state_->global_count = global_count;
      }
      break;
    default:
      throw std::invalid_argument("unsupported ensemble in the current version");
  }
}

EnsembleController::~EnsembleController() = default;

bool EnsembleController::needs_pre_force_kinetic_energy() const noexcept
{
  return configuration_.kind == EnsembleKind::nvt_nhc;
}

bool EnsembleController::uses_root_state() const noexcept
{
  return configuration_.kind == EnsembleKind::nvt_nhc ||
         configuration_.kind == EnsembleKind::nvt_bdp ||
         configuration_.kind == EnsembleKind::npt_scr;
}

bool EnsembleController::has_barostat() const noexcept
{
  return configuration_.kind == EnsembleKind::npt_ber ||
         configuration_.kind == EnsembleKind::npt_scr;
}

bool EnsembleController::needs_post_force_thermo() const noexcept
{
  return configuration_.kind != EnsembleKind::nve;
}

double EnsembleController::target_temperature(int step, int steps) const
{
  // Preserve the former replicated-full/local-domain host expression and operation order exactly.
  const double fraction = static_cast<double>(step) / static_cast<double>(steps);
  return configuration_.initial_temperature +
         (configuration_.final_temperature - configuration_.initial_temperature) * fraction;
}

std::optional<double> EnsembleController::before_force(
    double time_step, std::optional<double> global_kinetic_energy, int step, int steps) const
{
  if (!needs_pre_force_kinetic_energy()) return std::nullopt;
  if (!state_ || !state_->chain) throw std::logic_error("NHC hook requires root-owned chain state");
  if (!global_kinetic_energy) throw std::logic_error("NHC requires global owned kinetic energy");
  require_positive(*global_kinetic_energy, "NHC global kinetic energy");
  require_positive(time_step, "NHC time step");
  state_->time_step = time_step;
  const double factor = gpumd_compat::nhc_half_step(
      *state_->chain, *global_kinetic_energy * 2.0,
      K_B * target_temperature(step, steps), 3.0 * state_->global_count, time_step * 0.5);
  require_positive(factor, "NHC velocity factor");
  for (double value : state_->chain->momentum) {
    if (!std::isfinite(value)) throw std::runtime_error("NHC chain momentum is not finite");
  }
  return factor;
}

std::optional<double> EnsembleController::after_force(
    const std::array<double, 8>* global_thermo, int step, int steps) const
{
  if (configuration_.kind == EnsembleKind::nve) return std::nullopt;
  const double target = target_temperature(step, steps);
  if (configuration_.kind == EnsembleKind::nvt_nhc) {
    require_positive(target, "NHC target temperature");
    if (!state_ || !state_->chain) throw std::logic_error("NHC hook requires root-owned chain state");
    if (!global_thermo) throw std::logic_error("NHC requires normalized global thermo");
    double ek2 = (*global_thermo)[0];
    require_positive(ek2, "NHC global temperature");
    ek2 *= 3.0 * state_->global_count * K_B;
    const double factor = gpumd_compat::nhc_half_step(
        *state_->chain, ek2, K_B * target, 3.0 * state_->global_count, state_->time_step * 0.5);
    require_positive(factor, "NHC velocity factor");
    for (double value : state_->chain->momentum) {
      if (!std::isfinite(value)) throw std::runtime_error("NHC chain momentum is not finite");
    }
    return factor;
  }
  if (configuration_.kind == EnsembleKind::nvt_bdp ||
      configuration_.kind == EnsembleKind::npt_scr) {
    require_positive(target, configuration_.kind == EnsembleKind::npt_scr
                                 ? "NPT SCR target temperature" : "BDP target temperature");
    if (!state_ || !state_->svr) throw std::logic_error("BDP hook requires root-owned RNG state");
    if (!global_thermo) throw std::logic_error("BDP requires normalized global thermo");
    const int ndeg = 3 * state_->global_count;
    double old_KE = (*global_thermo)[0];
    require_positive(old_KE, "BDP global temperature");
    old_KE *= ndeg * K_B * 0.5;
    require_positive(old_KE, "BDP old kinetic energy");
    const double sigma = ndeg * K_B * target * 0.5;
    require_positive(sigma, "BDP target kinetic energy");
    const double new_KE = gpumd_compat::resamplekin(
        old_KE, sigma, ndeg, configuration_.temperature_coupling, *state_->svr);
    if (!std::isfinite(new_KE) || new_KE < 0.0) {
      throw std::runtime_error("BDP sampled kinetic energy must be finite and nonnegative");
    }
    const double factor = std::sqrt(new_KE / old_KE);
    require_positive(factor, "BDP velocity factor");
    return factor;
  }
  if (configuration_.kind != EnsembleKind::nvt_ber &&
      configuration_.kind != EnsembleKind::npt_ber) {
    throw std::logic_error("unsupported post-force ensemble hook");
  }
  if (configuration_.kind == EnsembleKind::npt_ber && global_thermo != nullptr) {
    require_positive((*global_thermo)[0], "NPT Berendsen instantaneous temperature");
  }
  const double coupling = 1.0 / configuration_.temperature_coupling;
  if (coupling > 1.0e-5) {
    if (global_thermo == nullptr) {
      throw std::logic_error("Berendsen requires normalized global thermo");
    }
    const double factor = std::sqrt(1.0 + coupling * (target / (*global_thermo)[0] - 1.0));
    if (configuration_.kind == EnsembleKind::npt_ber) {
      require_positive(factor, "NPT Berendsen velocity factor");
    }
    return factor;
  }
  return std::nullopt;
}

double EnsembleController::barostat_scale(
    const std::array<double, 8>* global_thermo, const double pre_scale_volume,
    const int step, const int steps, const std::uint64_t global_step) const
{
  if (!has_barostat()) throw std::logic_error("barostat hook requires an NPT ensemble");
  const double p_inst = global_thermo == nullptr
      ? std::numeric_limits<double>::quiet_NaN()
      : ((*global_thermo)[2] + (*global_thermo)[3] + (*global_thermo)[4]) *
            0.3333333333333333;
  const double target = target_temperature(step, steps);
  double gaussian = 0.0;
  double scale = std::numeric_limits<double>::quiet_NaN();
  if (configuration_.kind == EnsembleKind::npt_scr) {
    require_positive(target, "NPT SCR target temperature");
    require_positive(pre_scale_volume, "NPT SCR pre-scale volume");
    if (!state_ || !state_->svr) {
      throw std::logic_error("NPT SCR barostat hook requires root-owned RNG state");
    }
    // Even with pc==0, consume the pressure draw so the shared BDP/SCR stream
    // advances in the documented order for the B>2000 fixed-box branch.
    gaussian = gpumd_compat::gasdev(*state_->svr);
    last_barostat_gaussian_ = gaussian;
    scale = gpumd_compat::npt_scr_isotropic_scale(
        target_pressure_internal_, pressure_coupling_internal_, p_inst,
        target, pre_scale_volume, gaussian);
  } else {
    scale = 1.0 - pressure_coupling_internal_ *
        (target_pressure_internal_ - p_inst);
  }
  if (!std::isfinite(p_inst) || !std::isfinite(scale) || scale <= 0.0) {
    std::ostringstream message;
    message << "invalid " << (configuration_.kind == EnsembleKind::npt_scr ? "npt_scr" : "npt_ber")
            << " box factor step=" << global_step
            << " P_GPa=" << configuration_.target_pressure_gpa
            << " P_inst_internal=" << p_inst
            << " T_target=" << target
            << " V_pre_scale=" << pre_scale_volume
            << " gaussian=" << gaussian
            << " factor=" << scale;
    throw std::runtime_error(message.str());
  }
  return scale;
}

}  // namespace dmgmd
