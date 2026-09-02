#pragma once

#include "dmgmd/input_error.hpp"

#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace dmgmd {

struct PotentialCommand {
  std::string filename;
};

struct VelocityCommand {
  double temperature = 300.0;
  std::optional<int> seed;
};

struct TimeStepCommand {
  double femtoseconds = 1.0;
  std::optional<double> maximum_distance_angstrom;
};

enum class EnsembleKind {
  nve,
  nvt_ber,
};

struct EnsembleCommand {
  EnsembleKind kind = EnsembleKind::nve;
  double initial_temperature = 0.0;
  double final_temperature = 0.0;
  double temperature_coupling = 0.0;
};

struct CorrectVelocityCommand {
  int interval = 0;
  std::optional<int> grouping_method;
};

struct DumpThermoCommand {
  int interval = 0;
};

enum class OutputPrecision {
  single,
  double_precision,
};

struct DumpXyzQuantities {
  bool mass = false;
  bool charge = false;
  bool velocity = false;
  bool force = false;
  bool potential = false;
  bool unwrapped_position = false;
  bool virial = false;
  bool group_labels = false;
};

struct DumpXyzCommand {
  int interval = 0;
  std::string filename;
  OutputPrecision precision = OutputPrecision::single;
  std::optional<int> grouping_method;
  std::optional<int> group_id;
  DumpXyzQuantities quantities;
};

struct DumpRestartCommand {
  int interval = 0;
};

struct RunCommand {
  int steps = 0;
};

using CommandData = std::variant<
    PotentialCommand,
    VelocityCommand,
    TimeStepCommand,
    EnsembleCommand,
    CorrectVelocityCommand,
    DumpThermoCommand,
    DumpXyzCommand,
    DumpRestartCommand,
    RunCommand>;

struct Command {
  SourceLocation source;
  CommandData data;
};

struct RunProgram {
  std::vector<Command> commands;
};

RunProgram parse_run_file(const std::string& filename);

}  // namespace dmgmd
