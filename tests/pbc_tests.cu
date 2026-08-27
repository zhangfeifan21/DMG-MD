#include "newmd/atom.cuh"
#include "newmd/pbc_cpu.hpp"
#include "newmd/pbc_gpu.cuh"
#include "newmd/simulation_box.cuh"

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

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

bool almost_equal(newmd::Real lhs, newmd::Real rhs) {
  const newmd::Real scale =
      std::fmax(1.0, std::fmax(std::fabs(lhs), std::fabs(rhs)));
  const newmd::Real tolerance =
      32.0 * std::numeric_limits<newmd::Real>::epsilon() * scale;
  return std::fabs(lhs - rhs) <= tolerance;
}

void expect_vectors_close(
    const std::vector<newmd::Real>& actual,
    const std::vector<newmd::Real>& expected,
    const std::string& test_name) {
  expect(actual.size() == expected.size(), test_name + " has the wrong size");
  for (std::size_t index = 0; index < actual.size(); ++index) {
    expect(almost_equal(actual[index], expected[index]),
           test_name + " differs at index " + std::to_string(index));
  }
}

class CudaStream {
public:
  CudaStream() {
    check_cuda(cudaStreamCreate(&stream_), "create CUDA stream");
  }

  ~CudaStream() {
    if (stream_ != nullptr) {
      cudaStreamDestroy(stream_);
    }
  }

  CudaStream(const CudaStream&) = delete;
  CudaStream& operator=(const CudaStream&) = delete;

  [[nodiscard]] cudaStream_t get() const noexcept {
    return stream_;
  }

  void synchronize() const {
    check_cuda(cudaStreamSynchronize(stream_), "synchronize CUDA stream");
  }

private:
  cudaStream_t stream_ = nullptr;
};

__global__ void minimum_image_kernel(
    newmd::VectorSoAView<newmd::Real> displacement,
    newmd::OrthorhombicBoxView box) {
  const std::size_t atom = blockIdx.x * blockDim.x + threadIdx.x;
  if (atom >= displacement.count) {
    return;
  }

  newmd::Real dx = displacement.x(atom);
  newmd::Real dy = displacement.y(atom);
  newmd::Real dz = displacement.z(atom);
  box.apply_minimum_image(dx, dy, dz);
  displacement.x(atom) = dx;
  displacement.y(atom) = dy;
  displacement.z(atom) = dz;
}

void test_cpu_wrap_validation() {
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(8.0, 16.0, 32.0);

  newmd::pbc::cpu::wrap_positions(box, nullptr, 0);

  bool threw = false;
  try {
    newmd::pbc::cpu::wrap_positions(box, nullptr, 1);
  } catch (const std::invalid_argument&) {
    threw = true;
  }
  expect(threw, "CPU PBC accepted a null non-empty position array");
}

void test_position_wrapping_cpu_gpu_parity() {
  constexpr std::size_t atom_count = 10;
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(8.0, 16.0, 32.0);

  const std::vector<newmd::Real> initial{
      0.0, 8.0, -1.0, 9.0, -17.0, 24.5, 7.5, 4.0, -8.0, 16.0,
      0.0, 16.0, -2.0, 18.0, -34.0, 48.5, 15.5, 8.0, -16.0, 32.0,
      0.0, 32.0, -3.0, 35.0, -67.0, 96.5, 31.5, 16.0, -32.0, 64.0,
  };
  const std::vector<newmd::Real> explicitly_expected{
      0.0, 0.0, 7.0, 1.0, 7.0, 0.5, 7.5, 4.0, 0.0, 0.0,
      0.0, 0.0, 14.0, 2.0, 14.0, 0.5, 15.5, 8.0, 0.0, 0.0,
      0.0, 0.0, 29.0, 3.0, 29.0, 0.5, 31.5, 16.0, 0.0, 0.0,
  };

  std::vector<newmd::Real> cpu_result = initial;
  newmd::pbc::cpu::wrap_positions(box, cpu_result.data(), atom_count);
  expect_vectors_close(cpu_result, explicitly_expected, "CPU position wrap");

  newmd::AtomStorage atoms(atom_count);
  atoms.copy_positions_from_host(initial.data());
  CudaStream stream;
  newmd::pbc::gpu::wrap_positions_async(
      atoms.view(), box.orthorhombic_view(), stream.get());
  stream.synchronize();

  std::vector<newmd::Real> gpu_result(3 * atom_count);
  atoms.copy_positions_to_host(gpu_result.data());
  expect_vectors_close(gpu_result, cpu_result, "GPU position wrap");

  for (std::size_t atom = 0; atom < atom_count; ++atom) {
    expect(gpu_result[atom] >= 0.0 && gpu_result[atom] < 8.0,
           "wrapped x coordinate is outside [0, Lx)");
    expect(gpu_result[atom + atom_count] >= 0.0 &&
               gpu_result[atom + atom_count] < 16.0,
           "wrapped y coordinate is outside [0, Ly)");
    expect(gpu_result[atom + 2 * atom_count] >= 0.0 &&
               gpu_result[atom + 2 * atom_count] < 32.0,
           "wrapped z coordinate is outside [0, Lz)");
  }
}

void test_minimum_image_cpu_gpu_parity() {
  constexpr std::size_t displacement_count = 8;
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(8.0, 16.0, 32.0);

  const std::vector<newmd::Real> initial{
      0.0, 4.0, -4.0, 4.25, -4.25, 7.5, -7.5, 25.25,
      0.0, 8.0, -8.0, 8.5, -8.5, 15.0, -15.0, -47.0,
      0.0, 16.0, -16.0, 16.5, -16.5, 31.0, -31.0, 97.25,
  };

  std::vector<newmd::Real> cpu_result = initial;
  for (std::size_t atom = 0; atom < displacement_count; ++atom) {
    newmd::pbc::cpu::apply_minimum_image(
        box,
        cpu_result[atom],
        cpu_result[atom + displacement_count],
        cpu_result[atom + 2 * displacement_count]);
  }

  expect(cpu_result[1] == 4.0 && cpu_result[2] == -4.0,
         "CPU x half-box ties changed sign");
  expect(cpu_result[1 + displacement_count] == 8.0 &&
             cpu_result[2 + displacement_count] == -8.0,
         "CPU y half-box ties changed sign");
  expect(cpu_result[1 + 2 * displacement_count] == 16.0 &&
             cpu_result[2 + 2 * displacement_count] == -16.0,
         "CPU z half-box ties changed sign");
  expect(cpu_result[7] == 1.25 &&
             cpu_result[7 + displacement_count] == 1.0 &&
             cpu_result[7 + 2 * displacement_count] == 1.25,
         "CPU minimum image is not invariant to integer box shifts");

  newmd::AtomStorage displacements(displacement_count);
  displacements.copy_positions_from_host(initial.data());
  CudaStream stream;
  minimum_image_kernel<<<1, 128, 0, stream.get()>>>(
      displacements.view().position, box.orthorhombic_view());
  check_cuda(cudaGetLastError(), "launch minimum_image_kernel");
  stream.synchronize();

  std::vector<newmd::Real> gpu_result(3 * displacement_count);
  displacements.copy_positions_to_host(gpu_result.data());
  expect_vectors_close(gpu_result, cpu_result, "GPU minimum image");
}

void test_zero_atom_gpu_wrap() {
  const newmd::SimulationBox box =
      newmd::SimulationBox::orthorhombic(8.0, 16.0, 32.0);
  newmd::AtomStorage empty_atoms;
  newmd::pbc::gpu::wrap_positions_async(
      empty_atoms.view(), box.orthorhombic_view());
  check_cuda(cudaDeviceSynchronize(), "synchronize zero-atom PBC");
}

}  // namespace

int main() {
  try {
    check_cuda(cudaSetDevice(0), "select CUDA device 0");
    test_cpu_wrap_validation();
    test_position_wrapping_cpu_gpu_parity();
    test_minimum_image_cpu_gpu_parity();
    test_zero_atom_gpu_wrap();
    check_cuda(cudaDeviceSynchronize(), "final synchronization");
    check_cuda(cudaDeviceReset(), "reset CUDA device");
  } catch (const std::exception& error) {
    std::cerr << "PBC test failure: " << error.what() << '\n';
    cudaDeviceReset();
    return 1;
  }

  std::cout << "CPU/GPU PBC tests passed.\n";
  return 0;
}
