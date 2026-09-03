#include "dmgmd/input_error.hpp"
#include "dmgmd/model.hpp"
#include "dmgmd/mpi_runtime.hpp"
#include "dmgmd/run_ir.hpp"
#include "dmgmd/runtime.hpp"

#include <cstdlib>
#include <iostream>
#include <optional>
#include <string>
#include <variant>

namespace {

void print_input_error(const dmgmd::InputError& error)
{
  const auto& location = error.location();
  std::cerr << "dmg-md input error";
  if (!location.file.empty()) {
    std::cerr << " at " << location.file;
    if (location.line != 0) std::cerr << ':' << location.line;
  }
  std::cerr << ": " << error.what() << '\n';
  if (!location.text.empty()) std::cerr << "  " << location.text << '\n';
}

}  // namespace

int main(int argc, char** argv)
{
  // The acceptance runner invokes this mode before staging any MD input.  It
  // exercises the exact startup/device/backend path used by a real job, which
  // keeps Open MPI/UCX failures separate from parser, NEP, and trajectory bugs.
  const bool environment_probe =
      argc == 2 && std::string(argv[1]) == "--probe-mpi-environment";
  try {
    dmgmd::MpiRuntime mpi(argc, argv);
    try {
      if (environment_probe) {
        mpi.initialize_device();
        if (mpi.is_root()) {
          std::cout << "DMGMD_MPI_ENVIRONMENT status=passed stack=OpenMPI+UCX"
                    << " backend=" << mpi.backend_name() << '\n';
        }
        return EXIT_SUCCESS;
      }
      if (argc != 1) {
        throw dmgmd::InputError(
            dmgmd::SourceLocation{},
            "usage: dmg-md [--probe-mpi-environment]");
      }
      const dmgmd::RunProgram program = dmgmd::parse_run_file("run.in");
      std::optional<std::string> potential_filename;
      for (const dmgmd::Command& command : program.commands) {
        if (const auto* potential = std::get_if<dmgmd::PotentialCommand>(&command.data)) {
          if (potential_filename) {
            throw dmgmd::InputError(
                command.source,
                "multiple potential commands are unsupported in the replicated runtime");
          }
          potential_filename = potential->filename;
        }
      }
      if (!potential_filename) {
        throw dmgmd::InputError(
            dmgmd::SourceLocation{"run.in", 0, {}},
            "run.in does not contain a potential command");
      }

      const dmgmd::PotentialMetadata potential =
          dmgmd::load_potential_metadata(*potential_filename);
      dmgmd::Model model = dmgmd::parse_model_file("model.xyz", potential);
      dmgmd::run_replicated(program, std::move(model), *potential_filename, mpi);
      return EXIT_SUCCESS;
    } catch (const dmgmd::InputError& error) {
      if (mpi.is_root()) print_input_error(error);
      if (mpi.world_size() > 1) mpi.abort(EXIT_FAILURE);
      return EXIT_FAILURE;
    } catch (const std::exception& error) {
      if (mpi.is_root()) {
        std::cerr << "dmg-md runtime error: " << error.what() << '\n';
      }
      if (mpi.world_size() > 1) mpi.abort(EXIT_FAILURE);
      return EXIT_FAILURE;
    }
  } catch (const std::exception& error) {
    std::cerr << "dmg-md MPI startup error: " << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
