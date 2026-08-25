#include "newmd/atom.cuh"

#include <cuda_runtime.h>

#include <cstddef>
#include <iostream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

static_assert(!std::is_copy_constructible_v<newmd::AtomStorage>);
static_assert(!std::is_copy_assignable_v<newmd::AtomStorage>);
static_assert(std::is_nothrow_move_constructible_v<newmd::AtomStorage>);
static_assert(std::is_nothrow_move_assignable_v<newmd::AtomStorage>);

namespace {

void expect(bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void check_cuda(cudaError_t status, const char* operation) {
  if (status != cudaSuccess) {
    throw std::runtime_error(std::string(operation) + ": " +
                             cudaGetErrorString(status));
  }
}

__global__ void transform_positions(newmd::AtomView atoms) {
  const std::size_t atom = blockIdx.x * blockDim.x + threadIdx.x;
  if (atom >= atoms.size()) {
    return;
  }

  const newmd::FPFormat x = atoms.position.x(atom);
  const newmd::FPFormat y = atoms.position.y(atom);
  const newmd::FPFormat z = atoms.position.z(atom);

  atoms.position.x(atom) = x + 0.5;
  atoms.position.y(atom) = y * 2.0;
  atoms.position.z(atom) = z - 3.0;
}

void test_position_pipeline() {
  constexpr std::size_t atom_count = 5;

  // GPUMD-compatible SoA layout: all x, followed by all y, then all z.
  const std::vector<newmd::FPFormat> initial_positions{
      0.0, 1.0, 2.0, 3.0, 4.0,
      10.0, 20.0, 30.0, 40.0, 50.0,
      -1.0, -2.0, -3.0, -4.0, -5.0,
  };

  newmd::AtomStorage atoms(atom_count);
  expect(atoms.size() == atom_count, "AtomStorage has the wrong size");
  atoms.copy_positions_from_host(initial_positions.data());

  newmd::AtomView view = atoms.view();
  expect(view.size() == atom_count, "AtomView has the wrong size");
  expect(view.position.component_data(newmd::Axis::x) ==
             view.position.data(),
         "x component has the wrong offset");
  expect(view.position.component_data(newmd::Axis::y) ==
             view.position.data() + atom_count,
         "y component has the wrong offset");
  expect(view.position.component_data(newmd::Axis::z) ==
             view.position.data() + 2 * atom_count,
         "z component has the wrong offset");

  constexpr unsigned int threads_per_block = 128;
  transform_positions<<<1, threads_per_block>>>(view);
  check_cuda(cudaGetLastError(), "launch transform_positions");
  check_cuda(cudaDeviceSynchronize(), "synchronize transform_positions");

  std::vector<newmd::FPFormat> result(3 * atom_count);
  atoms.copy_positions_to_host(result.data());

  const std::vector<newmd::FPFormat> expected{
      0.5, 1.5, 2.5, 3.5, 4.5,
      20.0, 40.0, 60.0, 80.0, 100.0,
      -4.0, -5.0, -6.0, -7.0, -8.0,
  };
  expect(result == expected,
         "CPU/AtomStorage/kernel/CPU position pipeline produced wrong values");

  const newmd::ConstAtomView const_view =
      static_cast<const newmd::AtomStorage&>(atoms).view();
  expect(const_view.position.data() == view.position.data(),
         "ConstAtomView does not reference the same position allocation");
}

}  // namespace

int main() {
  try {
    check_cuda(cudaSetDevice(0), "select CUDA device 0");
    test_position_pipeline();
    check_cuda(cudaDeviceSynchronize(), "final synchronization");
    check_cuda(cudaDeviceReset(), "reset CUDA device");
  } catch (const std::exception& error) {
    std::cerr << "Atom test failure: " << error.what() << '\n';
    cudaDeviceReset();
    return 1;
  }

  std::cout << "Atom position pipeline test passed.\n";
  return 0;
}
