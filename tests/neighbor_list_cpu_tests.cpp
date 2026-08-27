#include "newmd/neighbor_list.hpp"
#include "newmd/neighbor_list_cpu.hpp"
#include "newmd/neighbor_list_csr.hpp"
#include "newmd/simulation_box.cuh"

#include <cmath>
#include <cstddef>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

void expect(bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

template <typename Exception, typename Function>
void expect_throws(Function&& function, const std::string& message) {
  bool threw = false;
  try {
    std::forward<Function>(function)();
  } catch (const Exception&) {
    threw = true;
  }
  expect(threw, message);
}

std::vector<newmd::NeighborIndex> row(
    const newmd::CsrNeighborList& list,
    std::size_t atom) {
  const newmd::CsrNeighborView view = list.view();
  std::vector<newmd::NeighborIndex> result;
  for (newmd::NeighborOffset ordinal = 0;
       ordinal < view.neighbor_count(atom);
       ++ordinal) {
    result.push_back(view.neighbor(atom, ordinal));
  }
  return result;
}

void expect_row(
    const newmd::CsrNeighborList& list,
    std::size_t atom,
    const std::vector<newmd::NeighborIndex>& expected) {
  expect(row(list, atom) == expected,
         "CSR row " + std::to_string(atom) + " differs");
}

void test_empty_and_single_atom_lists() {
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(10.0, 10.0, 10.0);

  const newmd::CsrNeighborList empty =
      newmd::build_neighbor_list_n2_cpu(box, nullptr, 0, 2.0);
  const newmd::NeighborListBase& empty_base = empty;
  expect(empty_base.layout() == newmd::NeighborListLayout::csr,
         "base class reports the wrong concrete layout");
  expect(empty_base.atom_count() == 0, "empty list has atoms");
  expect(empty_base.edge_count() == 0, "empty list has edges");
  expect(empty_base.storage_slots() == 0, "empty CSR has storage slots");
  expect(empty_base.padding_slots() == 0, "CSR reports padding");
  expect(empty.row_offsets() == std::vector<newmd::NeighborOffset>{0},
         "empty CSR offsets are invalid");

  const std::vector<newmd::Real> one_position{1.0, 2.0, 3.0};
  const newmd::CsrNeighborList single =
      newmd::build_neighbor_list_n2_cpu(
          box, one_position.data(), 1, 2.0);
  expect(single.atom_count() == 1, "single-atom list has wrong atom count");
  expect(single.row_offsets() ==
             std::vector<newmd::NeighborOffset>({0, 0}),
         "single-atom CSR offsets are invalid");
  expect(single.column_indices().empty(),
         "single atom was included as its own neighbor");
}

void test_periodic_full_directed_sorted_rows() {
  constexpr std::size_t atom_count = 4;
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(10.0, 10.0, 10.0);
  const std::vector<newmd::Real> positions{
      0.0, 1.0, 9.5, 5.0,
      0.0, 0.0, 0.0, 5.0,
      0.0, 0.0, 0.0, 5.0,
  };

  const newmd::CsrNeighborList list =
      newmd::build_neighbor_list_n2_cpu(
          box, positions.data(), atom_count, 1.5);

  // Atom 1 and atom 2 are exactly 1.5 apart under MIC and are excluded by the
  // strict distance_squared < cutoff_squared rule.
  expect_row(list, 0, {1, 2});
  expect_row(list, 1, {0});
  expect_row(list, 2, {0});
  expect_row(list, 3, {});

  expect(list.row_offsets() ==
             std::vector<newmd::NeighborOffset>({0, 2, 3, 4, 4}),
         "periodic CSR offsets are incorrect");
  expect(list.edge_count() == 4, "directed edge count is incorrect");
  expect(list.storage_slots() == 4, "CSR storage-slot count is incorrect");
  expect(list.padding_slots() == 0, "CSR must not contain layout padding");
}

void test_skin_and_unwrapped_positions() {
  constexpr std::size_t atom_count = 2;
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(10.0, 10.0, 10.0);

  // The x displacement is -29.5, which maps to +0.5 after repeated MIC
  // correction. y and z are already coincident.
  const std::vector<newmd::Real> unwrapped_positions{
      20.25, -9.25,
      0.0, 0.0,
      0.0, 0.0,
  };
  const newmd::CsrNeighborList unwrapped =
      newmd::build_neighbor_list_n2_cpu(
          box, unwrapped_positions.data(), atom_count, 0.75);
  expect_row(unwrapped, 0, {1});
  expect_row(unwrapped, 1, {0});

  const std::vector<newmd::Real> skin_positions{
      0.0, 1.4,
      0.0, 0.0,
      0.0, 0.0,
  };
  const newmd::CsrNeighborList with_skin =
      newmd::build_neighbor_list_n2_cpu(
          box, skin_positions.data(), atom_count, 1.0, 0.5);
  expect(with_skin.cutoff() == 1.0, "physical cutoff metadata changed");
  expect(with_skin.skin() == 0.5, "skin metadata is incorrect");
  expect(with_skin.build_radius() == 1.5,
         "build-radius metadata is incorrect");
  expect_row(with_skin, 0, {1});
  expect_row(with_skin, 1, {0});
}

void test_half_box_boundary_is_unambiguous() {
  constexpr std::size_t atom_count = 2;
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(10.0, 10.0, 10.0);
  const std::vector<newmd::Real> positions{
      0.0, 5.0,
      0.0, 0.0,
      0.0, 0.0,
  };

  // build_radius == L/2 is allowed. The two equally distant image choices lie
  // exactly on the strict cutoff boundary, so neither is stored.
  const newmd::CsrNeighborList list =
      newmd::build_neighbor_list_n2_cpu(
          box, positions.data(), atom_count, 5.0);
  expect_row(list, 0, {});
  expect_row(list, 1, {});
}

void test_diamond_conventional_cell_coordination() {
  constexpr std::size_t atom_count = 8;
  constexpr newmd::Real lattice_constant = 3.567;
  const newmd::SimulationBox box = newmd::SimulationBox::orthorhombic(
      lattice_constant,
      lattice_constant,
      lattice_constant);
  const std::vector<newmd::Real> positions{
      0.0, 0.89175, 0.0, 0.89175, 1.7835, 2.67525, 1.7835, 2.67525,
      0.0, 0.89175, 1.7835, 2.67525, 0.0, 0.89175, 1.7835, 2.67525,
      0.0, 0.89175, 1.7835, 2.67525, 1.7835, 2.67525, 0.0, 0.89175,
  };

  // The diamond nearest-neighbor distance is sqrt(3) * a / 4 ~= 1.5446 A.
  // A 1.6 A cutoff therefore gives four neighbors for every atom while still
  // satisfying the minimum-image large-box constraint.
  const newmd::CsrNeighborList list =
      newmd::build_neighbor_list_n2_cpu(
          box, positions.data(), atom_count, 1.6);

  expect(list.edge_count() == 4 * atom_count,
         "diamond directed-edge count is incorrect");
  const newmd::CsrNeighborView view = list.view();
  for (std::size_t atom = 0; atom < atom_count; ++atom) {
    expect(view.neighbor_count(atom) == 4,
           "diamond atom has the wrong nearest-neighbor coordination");
  }
}

void test_invalid_inputs_are_rejected() {
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(10.0, 10.0, 10.0);
  const std::vector<newmd::Real> position{0.0, 0.0, 0.0};

  expect_throws<std::invalid_argument>(
      [&] { newmd::build_neighbor_list_n2_cpu(box, nullptr, 1, 1.0); },
      "nonempty neighbor build accepted null positions");
  expect_throws<std::invalid_argument>(
      [&] {
        newmd::build_neighbor_list_n2_cpu(
            box, position.data(), 1, 0.0);
      },
      "neighbor build accepted a zero cutoff");
  expect_throws<std::invalid_argument>(
      [&] {
        newmd::build_neighbor_list_n2_cpu(
            box,
            position.data(),
            1,
            std::numeric_limits<newmd::Real>::infinity());
      },
      "neighbor build accepted an infinite cutoff");
  expect_throws<std::invalid_argument>(
      [&] {
        newmd::build_neighbor_list_n2_cpu(
            box, position.data(), 1, 1.0, -0.1);
      },
      "neighbor build accepted a negative skin");
  expect_throws<std::invalid_argument>(
      [&] {
        newmd::build_neighbor_list_n2_cpu(
            box, position.data(), 1, 5.01);
      },
      "neighbor build accepted an ambiguous small-box search radius");

  std::vector<newmd::Real> nonfinite_position = position;
  nonfinite_position[1] = std::numeric_limits<newmd::Real>::quiet_NaN();
  expect_throws<std::invalid_argument>(
      [&] {
        newmd::build_neighbor_list_n2_cpu(
            box, nonfinite_position.data(), 1, 1.0);
      },
      "neighbor build accepted a non-finite coordinate");
}

}  // namespace

int main() {
  try {
    test_empty_and_single_atom_lists();
    test_periodic_full_directed_sorted_rows();
    test_skin_and_unwrapped_positions();
    test_half_box_boundary_is_unambiguous();
    test_diamond_conventional_cell_coordination();
    test_invalid_inputs_are_rejected();
  } catch (const std::exception& error) {
    std::cerr << "CPU neighbor-list test failure: " << error.what() << '\n';
    return 1;
  }

  std::cout << "CPU CSR neighbor-list tests passed.\n";
  return 0;
}
