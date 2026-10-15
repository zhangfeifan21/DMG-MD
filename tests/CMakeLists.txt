add_executable(dmgmd_run_parser_tests run_parser_tests.cpp)
target_link_libraries(dmgmd_run_parser_tests PRIVATE dmgmd_input)
add_test(NAME dmgmd.run_parser COMMAND $<TARGET_FILE:dmgmd_run_parser_tests>)

add_executable(dmgmd_model_parser_tests model_parser_tests.cpp)
target_link_libraries(dmgmd_model_parser_tests PRIVATE dmgmd_input)
target_compile_definitions(dmgmd_model_parser_tests PRIVATE
  DMGMD_SOURCE_DIR="${PROJECT_SOURCE_DIR}"
)
add_test(NAME dmgmd.model_parser COMMAND $<TARGET_FILE:dmgmd_model_parser_tests>)

add_executable(dmgmd_spatial_ownership_tests spatial_ownership_tests.cpp)
target_include_directories(dmgmd_spatial_ownership_tests PRIVATE ${PROJECT_SOURCE_DIR}/include)
add_test(NAME dmgmd.spatial_ownership COMMAND $<TARGET_FILE:dmgmd_spatial_ownership_tests>)

add_executable(dmgmd_domain_layout_tests domain_layout_tests.cpp)
target_include_directories(dmgmd_domain_layout_tests PRIVATE ${PROJECT_SOURCE_DIR}/include)
add_test(NAME dmgmd.domain_layout COMMAND $<TARGET_FILE:dmgmd_domain_layout_tests>)

add_executable(dmgmd_domain_partition_tests domain_partition_tests.cpp)
target_include_directories(dmgmd_domain_partition_tests PRIVATE ${PROJECT_SOURCE_DIR}/src)
add_test(NAME dmgmd.domain_partition COMMAND $<TARGET_FILE:dmgmd_domain_partition_tests>)

add_executable(dmgmd_domain_neighbor_cuda_tests domain_neighbor_cuda_tests.cu)
target_link_libraries(dmgmd_domain_neighbor_cuda_tests PRIVATE gpumd_compat CUDA::cudart)
target_compile_definitions(dmgmd_domain_neighbor_cuda_tests PRIVATE DMGMD_SOURCE_DIR="${PROJECT_SOURCE_DIR}")
add_test(
  NAME dmgmd.domain_neighbor_cuda
  COMMAND $<TARGET_FILE:dmgmd_domain_neighbor_cuda_tests>
)
set_tests_properties(dmgmd.domain_neighbor_cuda PROPERTIES SKIP_RETURN_CODE 77)

find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)
add_test(
  NAME dmgmd.long_nve_analysis
  COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/long_nve/test_long_nve.py
)

add_test(
  NAME dmgmd.benchmark_analysis
  COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/benchmark/test_benchmark.py
)

add_test(
  NAME dmgmd.cpu_sampling
  COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/benchmark/test_cpu_sampling.py
)

add_test(
  NAME dmgmd.lammps_reference
  COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/lammps_reference/test_lammps_reference.py
)

# Launched by tests/mpi/run_halo_lifecycle.py after the GPU environment gate.
# Link wrappers inject transport errors only into this test executable.
add_executable(dmgmd_mpi_halo_lifecycle_tests mpi_halo_lifecycle_tests.cu)
target_link_libraries(dmgmd_mpi_halo_lifecycle_tests PRIVATE dmgmd_runtime)
target_link_options(dmgmd_mpi_halo_lifecycle_tests PRIVATE
  -Wl,--wrap=MPI_Isend -Wl,--wrap=MPI_Waitall -Wl,--wrap=MPI_Testall
  -Wl,--wrap=cudaEventSynchronize -Wl,--wrap=cudaMemcpyAsync)
