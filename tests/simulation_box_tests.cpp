#include "newmd/simulation_box.cuh"

#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

namespace {

void expect(bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void expect_close(
    newmd::Real actual,
    newmd::Real expected,
    const std::string& message) {
  const newmd::Real scale =
      std::fmax(1.0, std::fmax(std::fabs(actual), std::fabs(expected)));
  const newmd::Real tolerance =
      16.0 * std::numeric_limits<newmd::Real>::epsilon() * scale;
  expect(std::fabs(actual - expected) <= tolerance, message);
}

void expect_invalid_box(
    newmd::Real lx,
    newmd::Real ly,
    newmd::Real lz,
    const std::string& test_name) {
  bool threw = false;
  try {
    static_cast<void>(newmd::SimulationBox::orthorhombic(lx, ly, lz));
  } catch (const std::invalid_argument&) {
    threw = true;
  }
  expect(threw, test_name + " was not rejected");
}

void test_valid_orthorhombic_box() {
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(8.0, 16.0, 32.0);

  expect(box.geometry() == newmd::BoxGeometry::orthorhombic,
         "box has the wrong geometry");
  expect(box.length(newmd::Axis::x) == 8.0, "wrong x length");
  expect(box.length(newmd::Axis::y) == 16.0, "wrong y length");
  expect(box.length(newmd::Axis::z) == 32.0, "wrong z length");
  expect_close(box.inverse_length(newmd::Axis::x), 1.0 / 8.0,
               "wrong inverse x length");
  expect_close(box.inverse_length(newmd::Axis::y), 1.0 / 16.0,
               "wrong inverse y length");
  expect_close(box.inverse_length(newmd::Axis::z), 1.0 / 32.0,
               "wrong inverse z length");
  expect_close(box.volume(), 4096.0, "wrong box volume");

  expect(box.periodic(newmd::Axis::x), "x direction is not periodic");
  expect(box.periodic(newmd::Axis::y), "y direction is not periodic");
  expect(box.periodic(newmd::Axis::z), "z direction is not periodic");

  const newmd::OrthorhombicBoxView view = box.orthorhombic_view();
  expect(view.lx == 8.0 && view.ly == 16.0 && view.lz == 32.0,
         "box view has the wrong lengths");
  expect(view.inverse_lx == 1.0 / 8.0 &&
             view.inverse_ly == 1.0 / 16.0 &&
             view.inverse_lz == 1.0 / 32.0,
         "box view has the wrong inverse lengths");
}

void test_invalid_boxes() {
  const newmd::Real infinity =
      std::numeric_limits<newmd::Real>::infinity();
  const newmd::Real nan =
      std::numeric_limits<newmd::Real>::quiet_NaN();

  expect_invalid_box(0.0, 1.0, 1.0, "zero length");
  expect_invalid_box(1.0, -1.0, 1.0, "negative length");
  expect_invalid_box(1.0, 1.0, infinity, "infinite length");
  expect_invalid_box(nan, 1.0, 1.0, "NaN length");
  expect_invalid_box(
      std::numeric_limits<newmd::Real>::denorm_min(),
      1.0,
      1.0,
      "overflowing inverse length");
  expect_invalid_box(
      std::numeric_limits<newmd::Real>::max(),
      2.0,
      2.0,
      "overflowing volume");
}

}  // namespace

int main() {
  try {
    test_valid_orthorhombic_box();
    test_invalid_boxes();
  } catch (const std::exception& error) {
    std::cerr << "SimulationBox test failure: " << error.what() << '\n';
    return 1;
  }

  std::cout << "SimulationBox tests passed.\n";
  return 0;
}
