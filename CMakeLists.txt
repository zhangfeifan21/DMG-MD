cmake_minimum_required(VERSION 3.18)

project(NewMD VERSION 0.1 LANGUAGES CXX CUDA)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(CMAKE_CUDA_STANDARD 17)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)
set(CMAKE_CUDA_EXTENSIONS OFF)

add_library(newmd_core STATIC
  src/neighbor_list.cpp
  src/neighbor_list_cpu.cpp
  src/simulation_box.cpp
  src/pbc_cpu.cpp
  src/pbc_gpu.cu
)
target_include_directories(newmd_core PUBLIC
  ${PROJECT_SOURCE_DIR}/include
)

add_executable(newmd src/main.cu)
target_link_libraries(newmd PRIVATE newmd_core)

include(CTest)
if(BUILD_TESTING)
  add_subdirectory(tests)
endif()
