#pragma once

#include "dmgmd/model.hpp"
#include "dmgmd/run_ir.hpp"

#include <string>

namespace dmgmd {

void run_single_rank(
    const RunProgram& program,
    Model model,
    const std::string& potential_filename);

}  // namespace dmgmd
