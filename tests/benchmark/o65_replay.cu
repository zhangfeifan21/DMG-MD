// O6.5 standalone snapshot replay; each process owns exactly one visible GPU.
#include "gpumd_compat/nep.cuh"
#include <iostream>
#include <sched.h>
#include <stdexcept>

int main(int argc, char** argv)
{
  try {
    if (argc != 8) throw std::invalid_argument(
      "usage: dmgmd-o65-replay NEP SNAPSHOT OUTPUT radial|partial SEED WARMUP ITERATIONS");
    int count = 0;
    CHECK(cudaGetDeviceCount(&count));
    if (count != 1) throw std::runtime_error("replay requires one visible GPU");
    CHECK(cudaSetDevice(0));
    cudaDeviceProp property{};
    CHECK(cudaGetDeviceProperties(&property, 0));
    cpu_set_t cpus;
    CPU_ZERO(&cpus);
    if (sched_getaffinity(0, sizeof(cpus), &cpus)) throw std::runtime_error("getaffinity failed");
    std::cout << "O65_REPLAY_GPU cuda_uuid=" << std::hex;
    for (unsigned char byte: property.uuid.bytes) {
      std::cout.width(2); std::cout.fill('0'); std::cout << static_cast<unsigned>(byte);
    }
    std::cout << std::dec << " cpus=";
    for (int i=0; i<CPU_SETSIZE; ++i) if (CPU_ISSET(i, &cpus)) std::cout << i << ',';
    std::cout << '\n';
    gpumd_compat::NEP nep(argv[1]);
    nep.replay_o65(argv[2], argv[3], argv[4], std::stoul(argv[5]),
                   std::stoi(argv[6]), std::stoi(argv[7]));
    std::cout << "PASS O65 replay: bitwise target, intermediates and final force/virial\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL O65 replay: " << error.what() << '\n';
    return 1;
  }
}
