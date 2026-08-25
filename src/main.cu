#include <cuda_runtime.h>

#include <iostream>

int main() {
  constexpr int device_id = 0;

  const cudaError_t set_device_status = cudaSetDevice(device_id);
  if (set_device_status != cudaSuccess) {
    std::cerr << "Failed to select CUDA device 0: "
              << cudaGetErrorString(set_device_status) << '\n';
    return 1;
  }

  cudaDeviceProp properties{};
  const cudaError_t properties_status =
      cudaGetDeviceProperties(&properties, device_id);
  if (properties_status != cudaSuccess) {
    std::cerr << "Failed to query CUDA device 0: "
              << cudaGetErrorString(properties_status) << '\n';
    return 1;
  }

  std::cout << "CUDA device 0: " << properties.name << '\n';
  return 0;
}
