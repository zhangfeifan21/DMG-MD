cmake_minimum_required(VERSION 3.24)

# GPUMD's current small-box kernels require native double atomicAdd, available
# from compute capability 6.0. Keep a modern default that also works for
# headless/cross-compilation; callers can override this cache value.
set(CMAKE_CUDA_ARCHITECTURES "75;80;86;89;90" CACHE STRING
    "CUDA compute capabilities built by DMG-MD")

project(DMGMD VERSION 0.1 LANGUAGES CXX CUDA)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(CMAKE_CUDA_STANDARD 17)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)
set(CMAKE_CUDA_EXTENSIONS OFF)

find_package(CUDAToolkit REQUIRED)

# DMG-MD's MPI runtime is intentionally pinned to the sibling env/md-mpi.sh
# stack.  Fail during configure instead of silently accepting a cached system
# MPI: mixing an executable, libmpi and UCX from different installations can
# look like a numerical/runtime bug once CUDA device pointers enter MPI.
foreach(DMGMD_REQUIRED_ENV OMPI_HOME UCX_HOME CUDA_HOME)
  if(NOT DEFINED ENV{${DMGMD_REQUIRED_ENV}} OR
     "$ENV{${DMGMD_REQUIRED_ENV}}" STREQUAL "")
    message(FATAL_ERROR
      "${DMGMD_REQUIRED_ENV} is unset; run 'source ../env/md-mpi.sh' before CMake"
    )
  endif()
endforeach()

file(REAL_PATH "$ENV{OMPI_HOME}" DMGMD_OMPI_HOME)
file(REAL_PATH "$ENV{UCX_HOME}" DMGMD_UCX_HOME)
file(REAL_PATH "$ENV{CUDA_HOME}" DMGMD_CUDA_HOME)

find_package(MPI 3.1 REQUIRED COMPONENTS CXX)

file(REAL_PATH "${MPI_CXX_COMPILER}" DMGMD_MPI_CXX_COMPILER)
file(REAL_PATH "${MPIEXEC_EXECUTABLE}" DMGMD_MPIEXEC_EXECUTABLE)
string(FIND "${DMGMD_MPI_CXX_COMPILER}" "${DMGMD_OMPI_HOME}/bin/" DMGMD_MPI_CXX_PREFIX)
string(FIND "${DMGMD_MPIEXEC_EXECUTABLE}" "${DMGMD_OMPI_HOME}/bin/" DMGMD_MPIEXEC_PREFIX)
if(NOT DMGMD_MPI_CXX_PREFIX EQUAL 0 OR NOT DMGMD_MPIEXEC_PREFIX EQUAL 0)
  message(FATAL_ERROR
    "FindMPI did not select the Open MPI installation from OMPI_HOME. "
    "Use a fresh build directory after 'source ../env/md-mpi.sh'. "
    "MPI_CXX_COMPILER='${MPI_CXX_COMPILER}', "
    "MPIEXEC_EXECUTABLE='${MPIEXEC_EXECUTABLE}'"
  )
endif()

find_program(DMGMD_OMPI_INFO ompi_info
  HINTS "${DMGMD_OMPI_HOME}/bin" NO_DEFAULT_PATH REQUIRED)
find_program(DMGMD_UCX_INFO ucx_info
  HINTS "${DMGMD_UCX_HOME}/bin" NO_DEFAULT_PATH REQUIRED)
execute_process(
  COMMAND "${MPIEXEC_EXECUTABLE}" --version
  RESULT_VARIABLE DMGMD_MPI_VERSION_RESULT
  OUTPUT_VARIABLE DMGMD_MPI_VERSION_OUTPUT
  ERROR_VARIABLE DMGMD_MPI_VERSION_ERROR
)
if(NOT DMGMD_MPI_VERSION_RESULT EQUAL 0 OR
   NOT DMGMD_MPI_VERSION_OUTPUT MATCHES "Open MPI")
  message(FATAL_ERROR
    "DMG-MD requires Open MPI; '${MPIEXEC_EXECUTABLE} --version' returned: "
    "${DMGMD_MPI_VERSION_OUTPUT}${DMGMD_MPI_VERSION_ERROR}"
  )
endif()

execute_process(
  COMMAND "${DMGMD_OMPI_INFO}" --config
  RESULT_VARIABLE DMGMD_OMPI_INFO_RESULT
  OUTPUT_VARIABLE DMGMD_OMPI_CONFIG
  ERROR_VARIABLE DMGMD_OMPI_INFO_ERROR
)
if(NOT DMGMD_OMPI_INFO_RESULT EQUAL 0 OR
   NOT DMGMD_OMPI_CONFIG MATCHES "--with-cuda=$ENV{CUDA_HOME}" OR
   NOT DMGMD_OMPI_CONFIG MATCHES "--with-ucx=$ENV{UCX_HOME}")
  message(FATAL_ERROR
    "Open MPI must be configured with CUDA_HOME and UCX_HOME from "
    "../env/md-mpi.sh. ompi_info reported: "
    "${DMGMD_OMPI_CONFIG}${DMGMD_OMPI_INFO_ERROR}"
  )
endif()

execute_process(
  COMMAND "${DMGMD_UCX_INFO}" -v
  RESULT_VARIABLE DMGMD_UCX_INFO_RESULT
  OUTPUT_VARIABLE DMGMD_UCX_VERSION_OUTPUT
  ERROR_VARIABLE DMGMD_UCX_INFO_ERROR
)
if(NOT DMGMD_UCX_INFO_RESULT EQUAL 0 OR
   NOT DMGMD_UCX_VERSION_OUTPUT MATCHES "${DMGMD_UCX_HOME}")
  message(FATAL_ERROR
    "ucx_info is not reporting the UCX_HOME installation: "
    "${DMGMD_UCX_VERSION_OUTPUT}${DMGMD_UCX_INFO_ERROR}"
  )
endif()

if(NOT EXISTS "${DMGMD_OMPI_HOME}/include/mpi-ext.h")
  message(FATAL_ERROR "Open MPI CUDA extension header mpi-ext.h is missing")
endif()
message(STATUS
  "DMG-MD MPI stack: Open MPI='${DMGMD_OMPI_HOME}', UCX='${DMGMD_UCX_HOME}'"
)

# DMG-MD replicates the minimal GPUMD subset it needs inside
# src/gpumd_compat (tokenizer, Box math, GPU_Vector, neighbor builder,
# Potential base and the NEP/NEP-ZBL CUDA kernels).  The build must never
# compile sources from or link against ../gpumd-reference; that checkout is
# only a read-only reference for audits and golden-baseline generation.
# GPUMD's tokenizer/error implementation is host-only despite its .cu
# suffix.  Build it as C++ so the parser unit tests stay CPU-only (same
# treatment the reference build applies to utilities/error.cu).
set_source_files_properties(src/gpumd_compat/error.cu PROPERTIES LANGUAGE CXX)

add_library(gpumd_compat STATIC
  src/gpumd_compat/box.cu
  src/gpumd_compat/error.cu
  src/gpumd_compat/neighbor.cu
  src/gpumd_compat/nep.cu
  src/gpumd_compat/potential.cu
)
target_include_directories(gpumd_compat PUBLIC
  ${PROJECT_SOURCE_DIR}/src
)
target_link_libraries(gpumd_compat PUBLIC CUDA::cudart)
# Whole-program CUDA compilation (like the reference GPUMD build): the static
# archive is then linkable from plain C++ test executables without a separate
# device-link step.
set_target_properties(gpumd_compat PROPERTIES
  CUDA_SEPARABLE_COMPILATION OFF
)

add_library(dmgmd_input STATIC
  src/model_parser.cpp
  src/run_parser.cpp
)
target_include_directories(dmgmd_input PUBLIC
  ${PROJECT_SOURCE_DIR}/include
  ${PROJECT_SOURCE_DIR}/src
)
target_link_libraries(dmgmd_input PUBLIC gpumd_compat)

add_library(dmgmd_runtime STATIC
  src/domain_runtime.cu
  src/mpi_runtime.cu
  src/runtime.cu
)
target_include_directories(dmgmd_runtime PUBLIC
  ${PROJECT_SOURCE_DIR}/include
  ${PROJECT_SOURCE_DIR}/src
)
target_link_libraries(dmgmd_runtime PUBLIC
  dmgmd_input
  gpumd_compat
  CUDA::cudart
  MPI::MPI_CXX
)
set_target_properties(dmgmd_runtime PROPERTIES CUDA_SEPARABLE_COMPILATION ON)

add_executable(dmg-md src/dmgmd_main.cpp)
target_link_libraries(dmg-md PRIVATE dmgmd_runtime)

include(CTest)
if(BUILD_TESTING)
  add_subdirectory(tests)
endif()
