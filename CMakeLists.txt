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

set(
  GPUMD_SOURCE_DIR
  "${PROJECT_SOURCE_DIR}/../gpumd-reference"
  CACHE PATH
  "Path to the pinned GPUMD source tree used by the compatibility core"
)

if(NOT EXISTS "${GPUMD_SOURCE_DIR}/src/force/nep.cu")
  message(FATAL_ERROR
    "GPUMD_SOURCE_DIR does not point to a GPUMD source tree: "
    "${GPUMD_SOURCE_DIR}"
  )
endif()

set(DMGMD_PINNED_GPUMD_COMMIT "9d23496e41319b9e2af5221a7df6285387401d1e")
execute_process(
  COMMAND git -C "${GPUMD_SOURCE_DIR}" rev-parse HEAD
  RESULT_VARIABLE GPUMD_GIT_RESULT
  OUTPUT_VARIABLE GPUMD_ACTUAL_COMMIT
  OUTPUT_STRIP_TRAILING_WHITESPACE
  ERROR_QUIET
)
if(NOT GPUMD_GIT_RESULT EQUAL 0 OR
   NOT GPUMD_ACTUAL_COMMIT STREQUAL DMGMD_PINNED_GPUMD_COMMIT)
  message(FATAL_ERROR
    "DMG-MD requires GPUMD commit ${DMGMD_PINNED_GPUMD_COMMIT}; "
    "${GPUMD_SOURCE_DIR} is at '${GPUMD_ACTUAL_COMMIT}'"
  )
endif()

# GPUMD's tokenizer implementation is host-only despite its .cu suffix. Build
# it as C++ so parser unit tests remain CPU-only.
set(GPUMD_ERROR_SOURCE "${GPUMD_SOURCE_DIR}/src/utilities/error.cu")
set_source_files_properties("${GPUMD_ERROR_SOURCE}" PROPERTIES LANGUAGE CXX)
add_library(gpumd_text_core STATIC "${GPUMD_ERROR_SOURCE}")
target_include_directories(gpumd_text_core PUBLIC "${GPUMD_SOURCE_DIR}/src")
target_link_libraries(gpumd_text_core PUBLIC CUDA::cudart)

# Compile the validated GPUMD implementation directly from its pinned source
# tree.  DMG-MD owns the runtime orchestration but does not carry a fork of the
# NEP parameter reader, neighbor builder, or CUDA math kernels.
add_library(gpumd_nep_core STATIC
  "${GPUMD_SOURCE_DIR}/src/force/dftd3.cu"
  "${GPUMD_SOURCE_DIR}/src/force/neighbor.cu"
  "${GPUMD_SOURCE_DIR}/src/force/nep.cu"
  "${GPUMD_SOURCE_DIR}/src/force/potential.cu"
  "${GPUMD_SOURCE_DIR}/src/model/box.cu"
)
target_include_directories(gpumd_nep_core PUBLIC
  "${GPUMD_SOURCE_DIR}/src"
)
target_link_libraries(gpumd_nep_core PUBLIC gpumd_text_core CUDA::cudart)
set_target_properties(gpumd_nep_core PROPERTIES
  CUDA_SEPARABLE_COMPILATION ON
)

add_library(dmgmd_input STATIC
  src/model_parser.cpp
  src/run_parser.cpp
)
target_include_directories(dmgmd_input PUBLIC
  ${PROJECT_SOURCE_DIR}/include
  "${GPUMD_SOURCE_DIR}/src"
)
target_link_libraries(dmgmd_input PUBLIC gpumd_text_core)

add_library(dmgmd_runtime STATIC
  src/mpi_runtime.cu
  src/runtime.cu
)
target_include_directories(dmgmd_runtime PUBLIC
  ${PROJECT_SOURCE_DIR}/include
  "${GPUMD_SOURCE_DIR}/src"
)
target_link_libraries(dmgmd_runtime PUBLIC
  dmgmd_input
  gpumd_nep_core
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
