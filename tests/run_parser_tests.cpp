#include "dmgmd/run_ir.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>

#include <stdlib.h>
#include <unistd.h>

namespace {

void expect(bool condition, const std::string& message)
{
  if (!condition) {
    throw std::runtime_error(message);
  }
}

class TemporaryInput {
 public:
  explicit TemporaryInput(const std::string& contents)
  {
    std::string pattern =
        (std::filesystem::temp_directory_path() / "dmgmd-run-parser-test-XXXXXX").string();
    const int descriptor = ::mkstemp(pattern.data());
    if (descriptor == -1) {
      throw std::runtime_error("cannot create temporary run parser input");
    }
    ::close(descriptor);
    path_ = pattern;
    std::ofstream output(path_);
    output << contents;
    output.close();
    if (!output) {
      std::filesystem::remove(path_);
      throw std::runtime_error("cannot write temporary run parser input");
    }
  }

  ~TemporaryInput()
  {
    std::error_code ignored;
    std::filesystem::remove(path_, ignored);
  }

  [[nodiscard]] const std::filesystem::path& path() const { return path_; }

 private:
  std::filesystem::path path_;
};

void test_ir_and_comments()
{
  const TemporaryInput input(
      "potential nep.txt # comment\n"
      "time_step 0.5\n"
      "ensemble nvt_ber 100 200 50\n"
      "dump_xyz 2 trajectory.xyz virial precision double velocity\n"
      "run 4\n");
  const auto program = dmgmd::parse_run_file(input.path().string());
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
  const TemporaryInput input("# first\ndftd3 pbe 10 5\n");
  try {
    static_cast<void>(dmgmd::parse_run_file(input.path().string()));
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
