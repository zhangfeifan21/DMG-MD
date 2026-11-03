#pragma once

#include "dmgmd/run_ir.hpp"

#include <array>
#include <cstdint>
#include <memory>
#include <optional>

namespace dmgmd {

inline constexpr std::uint32_t kDefaultEnsembleSeed = 12345678u;

// Parse DMGMD_ENSEMBLE_SEED's decimal uint32 value. nullptr selects the
// reproducible default; malformed, signed, whitespace, or overflowing values
// fail with an error naming the environment variable.
std::uint32_t parse_ensemble_seed(const char* value);

// Construct once for each run segment. Commands update configuration; a new
// run starts a new controller even when no new ensemble command intervenes.
// NVE and Berendsen own only scalar configuration, with no RNG/chain allocation.
// Stateful ensembles keep chain/RNG state on rank 0 and distribute
// their scalar results through the runtime. Berendsen remains a pure function
// of identical, already-reduced inputs on every rank: no broadcast is needed.
class EnsembleController {
 public:
  explicit EnsembleController(
      const EnsembleCommand& configuration, int global_count = 0,
      double initial_time_step = 0.0, bool owns_state = true,
      std::uint32_t seed = kDefaultEnsembleSeed);
  ~EnsembleController();
  EnsembleController(const EnsembleController&) = delete;
  EnsembleController& operator=(const EnsembleController&) = delete;

  bool needs_post_force_thermo() const noexcept;
  bool needs_pre_force_kinetic_energy() const noexcept;
  bool uses_root_state() const noexcept;

  // step is zero-based within this segment; 0 <= step < steps.
  double target_temperature(int step, int steps) const;

  // Called after velocity correction/adaptive dt and before VV1. Missing KE
  // means that the caller did not compute it; never substitute a local sum or
  // a fabricated zero. Only NHC consumes KE. Stateful hooks are called on
  // root only; the runtime broadcasts a checked factor before owned scaling.
  std::optional<double> before_force(
      double time_step, std::optional<double> global_kinetic_energy,
      int step = 0, int steps = 1) const;

  // Read-only view of the existing normalized global thermo (T is element 0).
  // A null view is valid for NVE steps without a thermo consumer. No view is
  // retained. An engaged factor, including 1.0, means launch owned scaling;
  // nullopt means preserve the old no-launch branch.
  std::optional<double> after_force(
      const std::array<double, 8>* global_thermo, int step, int steps) const;

 private:
  struct State;
  EnsembleCommand configuration_;
  std::unique_ptr<State> state_;
};

}  // namespace dmgmd
