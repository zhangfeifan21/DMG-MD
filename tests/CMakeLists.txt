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
endif()
