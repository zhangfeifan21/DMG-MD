add_executable(dmgmd_run_parser_tests run_parser_tests.cpp)
target_link_libraries(dmgmd_run_parser_tests PRIVATE dmgmd_input)
add_test(NAME dmgmd.run_parser COMMAND $<TARGET_FILE:dmgmd_run_parser_tests>)

add_executable(dmgmd_model_parser_tests model_parser_tests.cpp)
target_link_libraries(dmgmd_model_parser_tests PRIVATE dmgmd_input)
target_compile_definitions(dmgmd_model_parser_tests PRIVATE
  DMGMD_SOURCE_DIR="${PROJECT_SOURCE_DIR}"
)
add_test(NAME dmgmd.model_parser COMMAND $<TARGET_FILE:dmgmd_model_parser_tests>)
