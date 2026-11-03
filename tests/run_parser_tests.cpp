#include "dmgmd/run_ir.hpp"

#include <array>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>

#include <stdlib.h>
#include <unistd.h>

namespace {

std::size_t ensemble_cases = 0;

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

void report_ensemble_case(
    const std::string& input, const std::string& expected, const std::string& actual)
{
  std::string one_line;
  for (char character : input) {
    if (character == '\n') one_line += "\\n";
    else one_line += character;
  }
  std::cout << "PASS ensemble_case=" << ++ensemble_cases
            << " input=" << std::quoted(one_line)
            << " expected=" << std::quoted(expected)
            << " actual=" << std::quoted(actual) << '\n';
}

void expect_source(
    const dmgmd::SourceLocation& source, const TemporaryInput& input,
    std::size_t line, const std::string& text)
{
  expect(source.file == input.path().string(), "error/IR lost source filename");
  expect(source.line == line, "error/IR lost source line number");
  expect(source.text == text, "error/IR lost original input line");
}

void expect_parse_error(const std::string& line, const std::string& message)
{
  const TemporaryInput input("# source line 1\n\n" + line + "\n");
  try {
    static_cast<void>(dmgmd::parse_run_file(input.path().string()));
  } catch (const dmgmd::InputError& error) {
    expect_source(error.location(), input, 3, line);
    expect(error.what() == message, "unexpected parser error for: " + line);
    report_ensemble_case(line, message, error.what());
    return;
  }
  throw std::runtime_error("invalid ensemble was accepted: " + line);
}

void expect_ensemble_fields(
    const dmgmd::EnsembleCommand& actual, const dmgmd::EnsembleCommand& expected)
{
  expect(actual.kind == expected.kind, "wrong ensemble kind");
  expect(actual.initial_temperature == expected.initial_temperature,
         "wrong initial temperature");
  expect(actual.final_temperature == expected.final_temperature,
         "wrong final temperature");
  expect(actual.temperature_coupling == expected.temperature_coupling,
         "wrong temperature coupling");
}

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

void test_nvt_syntax_and_boundaries()
{
  struct Ensemble {
    const char* name;
    dmgmd::EnsembleKind kind;
  };
  const std::array<Ensemble, 3> ensembles{{
      {"nvt_ber", dmgmd::EnsembleKind::nvt_ber},
      {"nvt_nhc", dmgmd::EnsembleKind::nvt_nhc},
      {"nvt_bdp", dmgmd::EnsembleKind::nvt_bdp}}};
  struct ValidParameters {
    const char* text;
    double initial;
    double final;
    double coupling;
  };
  const std::array<ValidParameters, 4> valid{{
      {"300 300 1", 300.0, 300.0, 1.0},
      {"100 200 50", 100.0, 200.0, 50.0},
      {"200 100 1000000000", 200.0, 100.0, 1e9},
      {"1e-6 1e6 1e12", 1e-6, 1e6, 1e12}}};

  for (const auto& ensemble : ensembles) {
    const std::string prefix = std::string("ensemble ") + ensemble.name;
    for (const auto& parameters : valid) {
      const std::string line = prefix + " " + parameters.text;
      const TemporaryInput input("# comment\n\n" + line + "\n");
      const auto program = dmgmd::parse_run_file(input.path().string());
      expect(program.commands.size() == 1, "wrong NVT command count");
      expect_source(program.commands[0].source, input, 3, line);
      expect_ensemble_fields(std::get<dmgmd::EnsembleCommand>(program.commands[0].data),
                             {ensemble.kind, parameters.initial,
                              parameters.final, parameters.coupling});
      report_ensemble_case(line, "typed NVT fields", "typed NVT fields");
    }
    const std::string commented = prefix + " 300 300 1 # ignored extra tokens";
    const TemporaryInput input(commented + "\n");
    const auto program = dmgmd::parse_run_file(input.path().string());
    expect_ensemble_fields(std::get<dmgmd::EnsembleCommand>(program.commands[0].data),
                           {ensemble.kind, 300.0, 300.0, 1.0});
    report_ensemble_case(commented, "comment excluded from token count",
                         "comment excluded from token count");

    for (const std::string& arguments : {"", " 300", " 300 300", " 300 300 1 extra"}) {
      expect_parse_error(prefix + arguments, prefix + " should have 3 parameters");
    }
    for (const std::string& arguments : {"0 300 1", "-1 300 1", "300 0 1", "300 -1 1"}) {
      expect_parse_error(prefix + " " + arguments,
                         "initial and final temperatures should be > 0");
    }
    for (const std::string& coupling : {"0", "0.5", "-1"}) {
      expect_parse_error(prefix + " 300 300 " + coupling,
                         "temperature coupling should be >= 1");
    }
    const std::array<const char*, 3> names{{
        "initial temperature", "final temperature", "temperature coupling"}};
    for (std::size_t index = 0; index < names.size(); ++index) {
      for (const std::string& invalid : {"nan", "inf", "-inf", "1e309", "oops"}) {
        std::array<std::string, 3> values{{"300", "300", "1"}};
        values[index] = invalid;
        expect_parse_error(prefix + " " + values[0] + " " + values[1] + " " + values[2],
                           std::string(names[index]) + " should be a finite real number");
      }
    }
  }
}

void test_legacy_ensemble_sequence()
{
  static_assert(static_cast<int>(dmgmd::EnsembleKind::nve) == 0);
  static_assert(static_cast<int>(dmgmd::EnsembleKind::nvt_ber) == 1);
  expect_ensemble_fields(dmgmd::EnsembleCommand{}, {dmgmd::EnsembleKind::nve, 0, 0, 0});
  const std::array<std::string, 9> lines{{
      "ensemble nve", "ensemble nvt_ber 100 200 50", "run 2", "run 3",
      "ensemble nve", "ensemble nve", "run 4", "ensemble nvt_ber 200 100 1", "run 5"}};
  std::string contents;
  for (const auto& line : lines) contents += line + "\n";
  const TemporaryInput input(contents);
  const auto program = dmgmd::parse_run_file(input.path().string());
  expect(program.commands.size() == lines.size(), "legacy commands were lost or combined");
  const std::array<dmgmd::EnsembleCommand, 4> expected_at_run{{
      {dmgmd::EnsembleKind::nvt_ber, 100, 200, 50},
      {dmgmd::EnsembleKind::nvt_ber, 100, 200, 50},
      {dmgmd::EnsembleKind::nve, 0, 0, 0},
      {dmgmd::EnsembleKind::nvt_ber, 200, 100, 1}}};
  const std::array<int, 4> steps{{2, 3, 4, 5}};
  dmgmd::EnsembleCommand selected;
  std::size_t runs = 0;
  for (std::size_t index = 0; index < program.commands.size(); ++index) {
    const auto& command = program.commands[index];
    expect_source(command.source, input, index + 1, lines[index]);
    if (const auto* ensemble = std::get_if<dmgmd::EnsembleCommand>(&command.data)) {
      selected = *ensemble;
      if (ensemble->kind == dmgmd::EnsembleKind::nve) {
        expect_ensemble_fields(*ensemble, {dmgmd::EnsembleKind::nve, 0, 0, 0});
      }
    } else {
      expect(runs < expected_at_run.size(), "unexpected legacy run");
      expect(std::get<dmgmd::RunCommand>(command.data).steps == steps[runs],
             "legacy run steps changed");
      expect_ensemble_fields(selected, expected_at_run[runs++]);
    }
  }
  expect(runs == expected_at_run.size(), "legacy run lost");
  dmgmd::validate_runtime_ensembles(program);
  report_ensemble_case(contents, "legacy duplicate/segment fields and guard pass",
                       "legacy duplicate/segment fields and guard pass");
  expect_parse_error("ensemble", "ensemble type is missing");
  expect_parse_error("ensemble nve 1", "ensemble nve should have 0 parameters");
}

void test_unsupported_ensembles()
{
  for (const std::string& name : {"nvt_lan", "nvt_bao", "npt_ber", "npt_scr", "npt_mttk", "heat"}) {
    const std::string arguments = name.rfind("npt_", 0) == 0
                                     ? " 300 300 100 0 100 1000" : " 300 300 100";
    expect_parse_error("ensemble " + name + arguments,
                       "unsupported command 'ensemble " + name + "' in the current version");
  }
}

void test_runtime_ensemble_preflight()
{
  for (const std::string& name : {"nvt_nhc", "nvt_bdp"}) {
    for (const auto& text : {
        "ensemble " + name + " 300 500 100\nrun 2\nrun 3\n",
        "ensemble nve\ntime_step 1 0.5\ncorrect_velocity 10\nrun 1\ntime_step 0.1\nensemble " + name + " 300 300 100\nrun 2\n"}) {
      const TemporaryInput input(text);
      dmgmd::validate_runtime_ensembles(dmgmd::parse_run_file(input.path().string()));
    }
    for (const auto& setting : {"correct_velocity 10", "time_step 1 0.5", "time_step 0", "time_step -1"}) {
      const TemporaryInput input(std::string("ensemble nve\nrun 1\n") + setting +
                                 "\nensemble " + name + " 300 300 100\nrun 1\n");
      bool rejected = false;
      try {
        dmgmd::validate_runtime_ensembles(dmgmd::parse_run_file(input.path().string()));
      } catch (const dmgmd::InputError& error) {
        expect(error.location().line == 5, "new NVT preflight lost run source");
        expect(std::string(error.what()).find("ensemble " + name) != std::string::npos,
               "new NVT preflight has wrong ensemble diagnostic");
        rejected = true;
      }
      expect(rejected, "unverified new NVT combination was accepted");
    }
  }
  // The reserved NPT kinds are not parsed, but a constructed IR must fail closed.
  const std::array<dmgmd::EnsembleKind, 3> rejected_kinds{{
      dmgmd::EnsembleKind::npt_ber, dmgmd::EnsembleKind::npt_scr,
      static_cast<dmgmd::EnsembleKind>(999)}};
  for (const auto kind : rejected_kinds) {
    const dmgmd::SourceLocation source{"constructed.run", 42, "constructed ensemble"};
    const dmgmd::RunProgram program{{{source, dmgmd::EnsembleCommand{kind, 300, 300, 1}}}};
    bool rejected = false;
    try {
      dmgmd::validate_runtime_ensembles(program);
    } catch (const dmgmd::InputError& error) {
      expect(error.location().file == source.file && error.location().line == source.line &&
                 error.location().text == source.text,
             "constructed IR preflight lost source");
      expect(std::string(error.what()).find("unsupported") != std::string::npos,
             "reserved/unknown ensemble is not unsupported");
      report_ensemble_case("constructed kind " + std::to_string(static_cast<int>(kind)),
                           "unsupported", "unsupported");
      rejected = true;
    }
    expect(rejected, "reserved/unknown ensemble escaped preflight");
  }
}

}  // namespace

int main()
{
  try {
    test_ir_and_comments();
    test_unsupported_has_line();
    test_nvt_syntax_and_boundaries();
    test_legacy_ensemble_sequence();
    test_unsupported_ensembles();
    test_runtime_ensemble_preflight();
  } catch (const std::exception& error) {
    std::cerr << "run parser test failure: " << error.what() << '\n';
    return 1;
  }
  std::cout << "run parser tests passed; ensemble cases=" << ensemble_cases << '\n';
  return 0;
}
