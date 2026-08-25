#include "newmd/devicebuffer.cuh"

#include <cuda_runtime.h>

#include <cstddef>
#include <iostream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

static_assert(!std::is_copy_constructible_v<DeviceBuffer<int>>);
static_assert(!std::is_copy_assignable_v<DeviceBuffer<int>>);
static_assert(std::is_nothrow_move_constructible_v<DeviceBuffer<int>>);
static_assert(std::is_nothrow_move_assignable_v<DeviceBuffer<int>>);

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

__global__ void add_scalar(int* values, std::size_t count, int increment) {
  const std::size_t index = blockIdx.x * blockDim.x + threadIdx.x;
  if (index < count) {
    values[index] += increment;
  }
}

void run_add_kernel(DeviceBuffer<int>& buffer, int increment) {
  if (buffer.size() == 0) {
    return;
  }

  constexpr unsigned int threads_per_block = 128;
  const unsigned int block_count = static_cast<unsigned int>(
      (buffer.size() + threads_per_block - 1) / threads_per_block);
  add_scalar<<<block_count, threads_per_block>>>(buffer.data(), buffer.size(),
                                                 increment);
  check_cuda(cudaGetLastError(), "launch add_scalar");
  check_cuda(cudaDeviceSynchronize(), "synchronize add_scalar");
}

void expect_values(const std::vector<int>& actual,
                   const std::vector<int>& expected,
                   const char* test_name) {
  expect(actual == expected, std::string(test_name) + " produced wrong values");
}

void test_host_device_kernel_round_trip() {
  const std::vector<int> input{1, 2, 3, 4, 5, 6, 7, 8};
  DeviceBuffer<int> buffer(input.size());

  buffer.copy_from_host(input.data(), input.size());
  run_add_kernel(buffer, 10);

  std::vector<int> output(input.size());
  buffer.copy_to_host(output.data(), output.size());
  expect_values(output, {11, 12, 13, 14, 15, 16, 17, 18},
                "H2D/kernel/D2H");
}

void test_resize() {
  DeviceBuffer<int> buffer(4);
  int* const initial_pointer = buffer.data();

  buffer.resize(4);
  expect(buffer.size() == 4, "same-size resize changed the size");
  expect(buffer.data() == initial_pointer,
         "same-size resize unexpectedly reallocated storage");

  const std::vector<int> grown_input{0, 1, 2, 3, 4, 5, 6};
  buffer.resize(grown_input.size());
  expect(buffer.size() == grown_input.size(), "grow resize has wrong size");
  expect(buffer.data() != nullptr, "grow resize returned null storage");
  buffer.copy_from_host(grown_input.data(), grown_input.size());
  run_add_kernel(buffer, 2);

  std::vector<int> grown_output(grown_input.size());
  buffer.copy_to_host(grown_output.data(), grown_output.size());
  expect_values(grown_output, {2, 3, 4, 5, 6, 7, 8}, "grow resize");

  const std::vector<int> shrunk_input{8, 9, 10};
  buffer.resize(shrunk_input.size());
  expect(buffer.size() == shrunk_input.size(), "shrink resize has wrong size");
  buffer.copy_from_host(shrunk_input.data(), shrunk_input.size());
  run_add_kernel(buffer, -1);

  std::vector<int> shrunk_output(shrunk_input.size());
  buffer.copy_to_host(shrunk_output.data(), shrunk_output.size());
  expect_values(shrunk_output, {7, 8, 9}, "shrink resize");
}

void test_move_constructor() {
  const std::vector<int> input{3, 6, 9, 12};
  DeviceBuffer<int> source(input.size());
  source.copy_from_host(input.data(), input.size());
  int* const source_pointer = source.data();

  DeviceBuffer<int> destination(std::move(source));
  expect(source.data() == nullptr, "move constructor did not clear source data");
  expect(source.size() == 0, "move constructor did not clear source size");
  expect(destination.data() == source_pointer,
         "move constructor did not transfer ownership");
  expect(destination.size() == input.size(),
         "move constructor transferred the wrong size");

  run_add_kernel(destination, 1);
  std::vector<int> output(input.size());
  destination.copy_to_host(output.data(), output.size());
  expect_values(output, {4, 7, 10, 13}, "move constructor");
}

void test_move_assignment() {
  const std::vector<int> input{5, 10, 15, 20, 25};
  DeviceBuffer<int> source(input.size());
  source.copy_from_host(input.data(), input.size());
  int* const source_pointer = source.data();

  DeviceBuffer<int> destination(2);
  destination = std::move(source);
  expect(source.data() == nullptr, "move assignment did not clear source data");
  expect(source.size() == 0, "move assignment did not clear source size");
  expect(destination.data() == source_pointer,
         "move assignment did not transfer ownership");
  expect(destination.size() == input.size(),
         "move assignment transferred the wrong size");

  destination = std::move(destination);
  expect(destination.data() == source_pointer,
         "self move assignment changed the allocation");
  expect(destination.size() == input.size(),
         "self move assignment changed the size");

  run_add_kernel(destination, -5);
  std::vector<int> output(input.size());
  destination.copy_to_host(output.data(), output.size());
  expect_values(output, {0, 5, 10, 15, 20}, "move assignment");
}

void test_zero_size() {
  DeviceBuffer<int> default_buffer;
  expect(default_buffer.size() == 0, "default buffer is not empty");
  expect(default_buffer.data() == nullptr, "default buffer data is not null");

  DeviceBuffer<int> zero_buffer(0);
  expect(zero_buffer.size() == 0, "zero-size buffer is not empty");
  expect(zero_buffer.data() == nullptr, "zero-size buffer data is not null");
  zero_buffer.copy_from_host(nullptr, 0);
  zero_buffer.copy_to_host(nullptr, 0);
  run_add_kernel(zero_buffer, 1);

  DeviceBuffer<int> resized_buffer(3);
  resized_buffer.resize(0);
  expect(resized_buffer.size() == 0, "resize(0) did not clear the size");
  expect(resized_buffer.data() == nullptr, "resize(0) did not clear the data");
  resized_buffer.resize(0);

  int value = 0;
  bool host_to_device_threw = false;
  try {
    zero_buffer.copy_from_host(&value, 1);
  } catch (const std::out_of_range&) {
    host_to_device_threw = true;
  }
  expect(host_to_device_threw, "zero-size H2D overflow was not rejected");

  bool device_to_host_threw = false;
  try {
    zero_buffer.copy_to_host(&value, 1);
  } catch (const std::out_of_range&) {
    device_to_host_threw = true;
  }
  expect(device_to_host_threw, "zero-size D2H overflow was not rejected");
}

}  // namespace

int main() {
  try {
    check_cuda(cudaSetDevice(0), "select CUDA device 0");
    test_host_device_kernel_round_trip();
    test_resize();
    test_move_constructor();
    test_move_assignment();
    test_zero_size();
    check_cuda(cudaDeviceSynchronize(), "final synchronization");
    check_cuda(cudaDeviceReset(), "reset CUDA device");
  } catch (const std::exception& error) {
    std::cerr << "DeviceBuffer test failure: " << error.what() << '\n';
    cudaDeviceReset();
    return 1;
  }

  std::cout << "All DeviceBuffer tests passed.\n";
  return 0;
}
