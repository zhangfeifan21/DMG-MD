#pragma once

#include "dmgmd/model.hpp"
#include "dmgmd/mpi_runtime.hpp"
#include "dmgmd/run_ir.hpp"

#include <string>

namespace dmgmd {

void run_replicated(
    const RunProgram& program,
    Model model,
    const std::string& potential_filename,
    MpiRuntime& mpi);

}  // namespace dmgmd
