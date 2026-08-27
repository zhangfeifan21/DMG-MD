add_test(NAME newmd.device_query COMMAND $<TARGET_FILE:newmd>)
set_tests_properties(newmd.device_query PROPERTIES
  PASS_REGULAR_EXPRESSION "^CUDA device 0: .+"
  FAIL_REGULAR_EXPRESSION "Failed to"
)

add_executable(newmd_device_buffer_tests device_buffer_tests.cu)
target_include_directories(newmd_device_buffer_tests PRIVATE
  ${PROJECT_SOURCE_DIR}/include
)

add_test(
  NAME newmd.device_buffer
  COMMAND $<TARGET_FILE:newmd_device_buffer_tests>
)

add_executable(newmd_atom_tests atom_tests.cu)
target_include_directories(newmd_atom_tests PRIVATE
  ${PROJECT_SOURCE_DIR}/include
)

add_test(
  NAME newmd.atom_position_pipeline
  COMMAND $<TARGET_FILE:newmd_atom_tests>
)

add_executable(newmd_simulation_box_tests simulation_box_tests.cpp)
target_link_libraries(newmd_simulation_box_tests PRIVATE newmd_core)

add_test(
  NAME newmd.simulation_box
  COMMAND $<TARGET_FILE:newmd_simulation_box_tests>
)

add_executable(newmd_pbc_tests pbc_tests.cu)
target_link_libraries(newmd_pbc_tests PRIVATE newmd_core)

add_test(
  NAME newmd.pbc_cpu_gpu
  COMMAND $<TARGET_FILE:newmd_pbc_tests>
)

add_executable(newmd_neighbor_list_cpu_tests neighbor_list_cpu_tests.cpp)
target_link_libraries(newmd_neighbor_list_cpu_tests PRIVATE newmd_core)

add_test(
  NAME newmd.neighbor_list_cpu
  COMMAND $<TARGET_FILE:newmd_neighbor_list_cpu_tests>
)

find_program(COMPUTE_SANITIZER_EXECUTABLE compute-sanitizer)
if(COMPUTE_SANITIZER_EXECUTABLE)
  add_test(
    NAME newmd.device_buffer.memcheck
    COMMAND
      ${COMPUTE_SANITIZER_EXECUTABLE}
      --tool memcheck
      --leak-check full
      --report-api-errors all
      --error-exitcode 1
      $<TARGET_FILE:newmd_device_buffer_tests>
  )
  set_tests_properties(newmd.device_buffer.memcheck PROPERTIES TIMEOUT 120)

  add_test(
    NAME newmd.atom_position_pipeline.memcheck
    COMMAND
      ${COMPUTE_SANITIZER_EXECUTABLE}
      --tool memcheck
      --leak-check full
      --report-api-errors all
      --error-exitcode 1
      $<TARGET_FILE:newmd_atom_tests>
  )
  set_tests_properties(
    newmd.atom_position_pipeline.memcheck
    PROPERTIES TIMEOUT 120
  )

  add_test(
    NAME newmd.pbc_cpu_gpu.memcheck
    COMMAND
      ${COMPUTE_SANITIZER_EXECUTABLE}
      --tool memcheck
      --leak-check full
      --report-api-errors all
      --error-exitcode 1
      $<TARGET_FILE:newmd_pbc_tests>
  )
  set_tests_properties(newmd.pbc_cpu_gpu.memcheck PROPERTIES TIMEOUT 120)
endif()
