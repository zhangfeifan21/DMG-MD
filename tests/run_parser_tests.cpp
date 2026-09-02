#include "dmgmd/run_ir.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

void expect(bool condition, const std::string& message)
{
  if (!condition) {
    throw std::runtime_error(message);
  }
}

std::filesystem::path write_input(const std::string& contents)
{
  const auto path = std::filesystem::temp_directory_path() / "dmgmd-run-parser-test.in";
  std::ofstream output(path);
  output << contents;
  return path;
}

void test_ir_and_comments()
{
  const auto path = write_input(
      "potential nep.txt # comment\n"
      "time_step 0.5\n"
      "ensemble nvt_ber 100 200 50\n"
      "dump_xyz 2 trajectory.xyz virial precision double velocity\n"
      "run 4\n");
  const auto program = dmgmd::parse_run_file(path.string());
  expect(program.commands.size() == 5, "wrong command count");
  expect(std::get<dmgmd::PotentialCommand>(program.commands[0].data).filename == "nep.txt",
         "wrong potential filename");
  const auto& dump = std::get<dmgmd::DumpXyzCommand>(program.commands[3].data);
  expect(dump.interval == 2, "wrong dump interval");
  expect(dump.precision == dmgmd::OutputPrecision::double_precision,
         "wrong dump precision");
  expect(dump.quantities.velocity && dump.quantities.virial,
         "wrong dump quantities");
}

void test_unsupported_has_line()
{
  const auto path = write_input("# first\ndftd3 pbe 10 5\n");
  try {
    static_cast<void>(dmgmd::parse_run_file(path.string()));
  } catch (const dmgmd::InputError& error) {
    expect(error.location().line == 2, "unsupported error lost line number");
    expect(std::string(error.what()).find("dftd3") != std::string::npos,
           "unsupported error lost command");
    return;
  }
  throw std::runtime_error("unsupported command was accepted");
}

}  // namespace

int main()
{
  try {
    test_ir_and_comments();
    test_unsupported_has_line();
  } catch (const std::exception& error) {
    std::cerr << "run parser test failure: " << error.what() << '\n';
    return 1;
  }
  std::cout << "run parser tests passed\n";
  return 0;
}
