#include "dmgmd/run_ir.hpp"
#include "utilities/error.cuh"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <string_view>
#include <unordered_set>
#include <utility>

namespace dmgmd {
namespace {

std::vector<std::string> tokenize_run_line(const std::string& line)
{
  std::vector<std::string> tokens;
  for (std::string token : ::get_tokens(line)) {
    if (!token.empty() && token.front() == '#') {
      break;
    }
    tokens.push_back(std::move(token));
  }
  return tokens;
}

[[noreturn]] void fail(const SourceLocation& source, std::string message)
{
  throw InputError(source, std::move(message));
}

int parse_int(const std::string& token, const SourceLocation& source, const char* name)
{
  char* end = nullptr;
  errno = 0;
  const long value = std::strtol(token.c_str(), &end, 0);
  if (errno != 0 || end == token.c_str() || *end != '\0' ||
      value < static_cast<long>(std::numeric_limits<int>::min()) ||
      value > static_cast<long>(std::numeric_limits<int>::max())) {
    fail(source, std::string(name) + " should be an integer");
  }
  return static_cast<int>(value);
}

double parse_real(const std::string& token, const SourceLocation& source, const char* name)
{
  char* end = nullptr;
  errno = 0;
  const double value = std::strtod(token.c_str(), &end);
  if (errno != 0 || end == token.c_str() || *end != '\0' || !std::isfinite(value)) {
    fail(source, std::string(name) + " should be a finite real number");
  }
  return value;
}

void require_size(
    const std::vector<std::string>& tokens,
    std::size_t expected,
    const SourceLocation& source,
    std::string message)
{
  if (tokens.size() != expected) {
    fail(source, std::move(message));
  }
}

[[noreturn]] void unsupported(
    const SourceLocation& source,
    const std::string& command)
{
  fail(source, "unsupported command '" + command + "'");
}

DumpXyzCommand parse_dump_xyz(
    const std::vector<std::string>& tokens,
    const SourceLocation& source)
{
  if (tokens.size() < 3) {
    fail(source, "dump_xyz should have at least 2 parameters");
  }

  DumpXyzCommand command;
  command.interval = parse_int(tokens[1], source, "dump interval");
  if (command.interval <= 0) {
    fail(source, "dump interval should be > 0");
  }
  command.filename = tokens[2];
  if (command.filename.empty()) {
    fail(source, "dump_xyz filename should not be empty");
  }

  bool group_seen = false;
  bool precision_seen = false;
  for (std::size_t index = 3; index < tokens.size(); ++index) {
    const std::string& token = tokens[index];
    if (token == "group") {
      if (group_seen) {
        fail(source, "option 'group' is specified more than once in dump_xyz");
      }
      if (index + 2 >= tokens.size()) {
        fail(source, "option 'group' requires a grouping method and group ID");
      }
      command.grouping_method =
          parse_int(tokens[index + 1], source, "grouping method");
      command.group_id = parse_int(tokens[index + 2], source, "group ID");
      if (*command.grouping_method < 0 || *command.group_id < 0) {
        fail(source, "grouping method and group ID should be >= 0");
      }
      group_seen = true;
      index += 2;
    } else if (token == "precision") {
      if (precision_seen) {
        fail(source, "option 'precision' is specified more than once in dump_xyz");
      }
      if (index + 1 >= tokens.size()) {
        fail(source, "option 'precision' requires single or double");
      }
      if (tokens[index + 1] == "single") {
        command.precision = OutputPrecision::single;
      } else if (tokens[index + 1] == "double") {
        command.precision = OutputPrecision::double_precision;
      } else {
        fail(source, "invalid dump_xyz precision '" + tokens[index + 1] + "'");
      }
      precision_seen = true;
      ++index;
    } else if (token == "mass") {
      command.quantities.mass = true;
    } else if (token == "charge") {
      command.quantities.charge = true;
    } else if (token == "velocity") {
      command.quantities.velocity = true;
    } else if (token == "force") {
      command.quantities.force = true;
    } else if (token == "potential") {
      command.quantities.potential = true;
    } else if (token == "unwrapped_position") {
      command.quantities.unwrapped_position = true;
    } else if (token == "virial") {
      command.quantities.virial = true;
    } else if (token == "group_labels") {
      command.quantities.group_labels = true;
    } else if (token == "bec") {
      unsupported(source, "dump_xyz bec");
    } else {
      fail(source, "unrecognized dump_xyz argument '" + token + "'");
    }
  }
  return command;
}

CommandData parse_command(
    const std::vector<std::string>& tokens,
    const SourceLocation& source)
{
  const std::string& keyword = tokens.front();
  if (keyword == "potential") {
    if (tokens.size() == 3) {
      unsupported(source, "potential partition direction");
    }
    require_size(tokens, 2, source, "potential should have 1 parameter");
    return PotentialCommand{tokens[1]};
  }
  if (keyword == "velocity") {
    if (tokens.size() != 2 && tokens.size() != 4) {
      fail(source, "velocity should have 1 or 3 parameters");
    }
    VelocityCommand command;
    command.temperature = parse_real(tokens[1], source, "initial temperature");
    if (command.temperature <= 0.0) {
      fail(source, "initial temperature should be > 0");
    }
    if (tokens.size() == 4) {
      command.seed = parse_int(tokens[3], source, "seed");
    }
    return command;
  }
  if (keyword == "time_step") {
    if (tokens.size() != 2 && tokens.size() != 3) {
      fail(source, "time_step should have 1 or 2 parameters");
    }
    TimeStepCommand command;
    command.femtoseconds = parse_real(tokens[1], source, "time_step");
    if (tokens.size() == 3) {
      const double value = parse_real(tokens[2], source, "maximum distance per step");
      if (value <= 0.0) {
        fail(source, "maximum distance per step should be > 0");
      }
      command.maximum_distance_angstrom = value;
    }
    return command;
  }
  if (keyword == "ensemble") {
    if (tokens.size() < 2) {
      fail(source, "ensemble type is missing");
    }
    if (tokens[1] == "nve") {
      require_size(tokens, 2, source, "ensemble nve should have 0 parameters");
      return EnsembleCommand{EnsembleKind::nve, 0.0, 0.0, 0.0};
    }
    if (tokens[1] == "nvt_ber") {
      require_size(tokens, 5, source, "ensemble nvt_ber should have 3 parameters");
      EnsembleCommand command;
      command.kind = EnsembleKind::nvt_ber;
      command.initial_temperature = parse_real(tokens[2], source, "initial temperature");
      command.final_temperature = parse_real(tokens[3], source, "final temperature");
      command.temperature_coupling =
          parse_real(tokens[4], source, "temperature coupling");
      if (command.initial_temperature <= 0.0 || command.final_temperature <= 0.0) {
        fail(source, "initial and final temperatures should be > 0");
      }
      if (command.temperature_coupling < 1.0) {
        fail(source, "temperature coupling should be >= 1");
      }
      return command;
    }
    unsupported(source, "ensemble " + tokens[1]);
  }
  if (keyword == "correct_velocity") {
    if (tokens.size() != 2 && tokens.size() != 3) {
      fail(source, "correct_velocity should have 1 or 2 parameters");
    }
    CorrectVelocityCommand command;
    command.interval = parse_int(tokens[1], source, "velocity correction interval");
    if (command.interval < 10) {
      fail(source, "velocity correction interval should be >= 10");
    }
    if (tokens.size() == 3) {
      command.grouping_method =
          parse_int(tokens[2], source, "velocity correction grouping method");
      if (*command.grouping_method < 0) {
        fail(source, "velocity correction grouping method should be >= 0");
      }
    }
    return command;
  }
  if (keyword == "dump_thermo") {
    require_size(tokens, 2, source, "dump_thermo should have 1 parameter");
    const int interval = parse_int(tokens[1], source, "thermo dump interval");
    if (interval <= 0) {
      fail(source, "thermo dump interval should be > 0");
    }
    return DumpThermoCommand{interval};
  }
  if (keyword == "dump_xyz") {
    return parse_dump_xyz(tokens, source);
  }
  if (keyword == "dump_restart") {
    require_size(tokens, 2, source, "dump_restart should have 1 parameter");
    const int interval = parse_int(tokens[1], source, "restart dump interval");
    if (interval <= 0) {
      fail(source, "restart dump interval should be > 0");
    }
    return DumpRestartCommand{interval};
  }
  if (keyword == "run") {
    require_size(tokens, 2, source, "run should have 1 parameter");
    return RunCommand{parse_int(tokens[1], source, "number of steps")};
  }

  static const std::unordered_set<std::string> known_unsupported{
      "active", "add_efield", "add_force", "add_random_force", "add_spring",
      "change_box", "compute", "compute_adf", "compute_angular_rdf",
      "compute_chunk", "compute_cohesive", "compute_dos", "compute_dpdt",
      "compute_elastic", "compute_es", "compute_extrapolation", "compute_gkma",
      "compute_hac", "compute_hnema", "compute_hnemd", "compute_hnemdec",
      "compute_ic", "compute_lsqt", "compute_msd", "compute_orientorder",
      "compute_phonon", "compute_rdf", "compute_sdc", "compute_shc",
      "compute_viscosity", "deform", "dftd3", "dump_beads", "dump_cg",
      "dump_dipole", "dump_exyz", "dump_force", "dump_netcdf", "dump_observer",
      "dump_polarizability", "dump_position", "dump_shock_nemd", "dump_velocity",
      "electron_stop", "fix", "kspace", "mc", "minimize", "move", "neighbor",
      "plumed", "replicate"};
  if (known_unsupported.count(keyword) != 0) {
    unsupported(source, keyword);
  }
  unsupported(source, keyword + " (unknown)");
}

}  // namespace

RunProgram parse_run_file(const std::string& filename)
{
  std::ifstream input(filename);
  if (!input) {
    throw InputError(SourceLocation{filename, 0, {}}, "cannot open run.in");
  }

  RunProgram program;
  std::string line;
  std::size_t line_number = 0;
  while (std::getline(input, line)) {
    ++line_number;
    std::vector<std::string> tokens = tokenize_run_line(line);
    if (tokens.empty()) {
      continue;
    }
    const SourceLocation source{filename, line_number, line};
    if (tokens.size() > 32) {
      fail(source, "the number of tokens should be <= 32");
    }
    program.commands.push_back(Command{source, parse_command(tokens, source)});
  }
  return program;
}

}  // namespace dmgmd
