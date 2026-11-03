#include "dmgmd/model.hpp"

#include <cmath>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

#ifndef DMGMD_SOURCE_DIR
#error "DMGMD_SOURCE_DIR must be defined"
#endif

namespace {

void expect(bool condition, const std::string& message)
{
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void test_committed_models()
{
  const std::filesystem::path root = DMGMD_SOURCE_DIR;
  struct Case {
    const char* model;
    const char* potential;
    std::size_t atoms;
  };
  const Case cases[] = {
      {"tests/baseline/inputs/single_small_static/model.xyz",
       "tests/baseline/inputs/potentials/nep_C.txt", 8},
      {"tests/baseline/inputs/single_large_nve/model.xyz",
       "tests/baseline/inputs/potentials/nep_C.txt", 8},
      {"tests/baseline/inputs/multi_nvt_restart/model.xyz",
       "tests/baseline/inputs/potentials/nep_water.txt", 9},
      {"tests/baseline/inputs/nep_zbl_boundary/model.xyz",
       "tests/baseline/inputs/potentials/nep_BaTiO3_zbl.txt", 8},
  };

  for (const Case& item : cases) {
    const auto metadata = dmgmd::load_potential_metadata((root / item.potential).string());
    const auto model = dmgmd::parse_model_file((root / item.model).string(), metadata);
    expect(model.atoms.counts.global_count == item.atoms, "wrong global_count");
    expect(model.atoms.counts.owned_count == item.atoms, "wrong owned_count");
    expect(model.atoms.counts.ghost_count == 0, "single rank should have no ghosts");
    expect(model.atoms.local_stride() == item.atoms, "wrong local stride");
    for (std::size_t atom = 0; atom < item.atoms; ++atom) {
      expect(model.atoms.global_id[atom] == atom, "global IDs are not input-order stable");
    }
    expect(model.atoms.has_input_velocity, "baseline model velocity was not detected");
  }
}

void test_units_and_defaults()
{
  const std::filesystem::path root = DMGMD_SOURCE_DIR;
  const auto metadata = dmgmd::load_potential_metadata(
      (root / "tests/baseline/inputs/potentials/nep_C.txt").string());
  const auto model = dmgmd::parse_model_file(
      (root / "tests/baseline/inputs/single_large_nve/model.xyz").string(), metadata);
  expect(model.atoms.mass[0] == 12.011, "GPUMD default carbon mass changed");
  expect(std::abs(model.atoms.velocity[0] - 0.004 * 10.18051) < 1e-15,
         "velocity was not converted from A/fs to natural units");
  expect(model.box.h[0] == 24.0 && model.box.h[4] == 24.0 && model.box.h[8] == 24.0,
         "lattice layout is wrong");
}

}  // namespace

int main()
{
  try {
    test_committed_models();
    test_units_and_defaults();
  } catch (const std::exception& error) {
    std::cerr << "model parser test failure: " << error.what() << '\n';
    return 1;
  }
  std::cout << "model parser tests passed\n";
  return 0;
}
