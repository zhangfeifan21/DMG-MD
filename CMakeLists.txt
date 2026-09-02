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
  src/runtime.cu
)
target_include_directories(dmgmd_runtime PUBLIC
  ${PROJECT_SOURCE_DIR}/include
  "${GPUMD_SOURCE_DIR}/src"
)
target_link_libraries(dmgmd_runtime PUBLIC dmgmd_input gpumd_nep_core CUDA::cudart)
set_target_properties(dmgmd_runtime PROPERTIES CUDA_SEPARABLE_COMPILATION ON)

add_executable(dmg-md src/dmgmd_main.cpp)
target_link_libraries(dmg-md PRIVATE dmgmd_runtime)

include(CTest)
if(BUILD_TESTING)
  add_subdirectory(tests)
endif()
