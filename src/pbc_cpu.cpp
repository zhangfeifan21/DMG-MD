#include "newmd/pbc_cpu.hpp"

#include <stdexcept>

namespace newmd::pbc::cpu {
namespace {

Real wrap_coordinate(Real coordinate, Real length) {
  // Deliberately simple reference code. Repeated correction supports positions
  // displaced by multiple box lengths and defines the result as [0, L).
  while (coordinate < 0) {
    coordinate += length;
  }
  while (coordinate >= length) {
    coordinate -= length;
  }
  return coordinate;
}

void apply_minimum_image_component(Real length, Real& displacement) {
  const Real half_length = 0.5 * length;
  while (displacement < -half_length) {
    displacement += length;
  }
  while (displacement > half_length) {
    displacement -= length;
  }
}

}  // namespace

void wrap_positions(
    const SimulationBox& box,
    Real* position_soa,
    std::size_t atom_count) {
  if (atom_count == 0) {
    return;
  }
  if (position_soa == nullptr) {
    throw std::invalid_argument(
        "Position pointer must not be null when atom_count is nonzero.");
  }

  Real* const x = position_soa;
  Real* const y = position_soa + atom_count;
  Real* const z = position_soa + 2 * atom_count;

  const Real lx = box.length(Axis::x);
  const Real ly = box.length(Axis::y);
  const Real lz = box.length(Axis::z);

  for (std::size_t atom = 0; atom < atom_count; ++atom) {
    x[atom] = wrap_coordinate(x[atom], lx);
    y[atom] = wrap_coordinate(y[atom], ly);
    z[atom] = wrap_coordinate(z[atom], lz);
  }
}

void apply_minimum_image(
    const SimulationBox& box,
    Real& dx,
    Real& dy,
    Real& dz) {
  apply_minimum_image_component(box.length(Axis::x), dx);
  apply_minimum_image_component(box.length(Axis::y), dy);
  apply_minimum_image_component(box.length(Axis::z), dz);
}

}  // namespace newmd::pbc::cpu
