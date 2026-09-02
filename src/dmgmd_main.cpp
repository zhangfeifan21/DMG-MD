#include "dmgmd/input_error.hpp"
#include "dmgmd/model.hpp"
#include "dmgmd/run_ir.hpp"
#include "dmgmd/runtime.hpp"

#include <cstdlib>
#include <iostream>
#include <optional>
#include <string>
#include <variant>

int main()
{
  try {
    const dmgmd::RunProgram program = dmgmd::parse_run_file("run.in");
    std::optional<std::string> potential_filename;
    for (const dmgmd::Command& command : program.commands) {
      if (const auto* potential = std::get_if<dmgmd::PotentialCommand>(&command.data)) {
        if (potential_filename) {
          throw dmgmd::InputError(
              command.source,
              "multiple potential commands are unsupported in the single-rank runtime");
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
    dmgmd::run_single_rank(program, std::move(model), *potential_filename);
    return EXIT_SUCCESS;
  } catch (const dmgmd::InputError& error) {
    const auto& location = error.location();
    std::cerr << "dmg-md input error";
    if (!location.file.empty()) {
      std::cerr << " at " << location.file;
      if (location.line != 0) std::cerr << ':' << location.line;
    }
    std::cerr << ": " << error.what() << '\n';
    if (!location.text.empty()) std::cerr << "  " << location.text << '\n';
    return EXIT_FAILURE;
  } catch (const std::exception& error) {
    std::cerr << "dmg-md runtime error: " << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
