#include "runtime_internal.hpp"

#include <iostream>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

int main(int argc, char** argv)
{
  try {
    dmgmd::MpiRuntime mpi(argc, argv);
    if (!dmgmd::affine_budget_allows_reuse(1.0, 0.01, 5.0, 0.001, 0.001, 1.0) ||
        dmgmd::affine_budget_allows_reuse(0.5, 0.0, 5.0, 0.0, 0.0, 1.0) ||
        dmgmd::affine_budget_allows_reuse(2.0, 0.0, 5.0, 0.6, 0.5, 1.0) ||
        dmgmd::affine_budget_allows_reuse(1.0, 0.5, 5.0, 0.0, 0.0, 1.0) ||
        dmgmd::affine_budget_allows_reuse(std::numeric_limits<double>::quiet_NaN(),
                                         0.0, 5.0, 0.0, 0.0, 1.0))
      throw std::runtime_error("affine scalar budget failed its boundary cases");
    unsetenv("DMGMD_DOMAIN_NPT");
    if (dmgmd::detail::domain_npt_mode(mpi) != dmgmd::detail::DomainNptMode::off)
      throw std::runtime_error("unset domain NPT must default to off");
    for (const auto& [setting, expected] : {
             std::pair{"off", dmgmd::detail::DomainNptMode::off},
             std::pair{"rebuild", dmgmd::detail::DomainNptMode::rebuild},
             std::pair{"affine", dmgmd::detail::DomainNptMode::affine}}) {
      setenv("DMGMD_DOMAIN_NPT", setting, 1);
      if (dmgmd::detail::domain_npt_mode(mpi) != expected)
        throw std::runtime_error(std::string("incorrect domain NPT mode: ") + setting);
    }
    setenv("DMGMD_DOMAIN_NPT", "invalid", 1);
    bool invalid_rejected = false;
    try { static_cast<void>(dmgmd::detail::domain_npt_mode(mpi)); }
    catch (const std::invalid_argument&) { invalid_rejected = true; }
    if (!invalid_rejected) throw std::runtime_error("invalid domain NPT mode accepted");
    unsetenv("DMGMD_DOMAIN_NPT");
    for (const auto& [name, kind] : {
             std::pair{"npt_ber", dmgmd::EnsembleKind::npt_ber},
             std::pair{"npt_scr", dmgmd::EnsembleKind::npt_scr}}) {
      const std::string source = std::string("ensemble ") + name + " 300 300 100 0 1000 10";
      const dmgmd::SourceLocation ensemble_source{"guard.run", 1, source};
      const dmgmd::SourceLocation run_source{"guard.run", 2, "run 1"};
      dmgmd::RunProgram program{{
          {ensemble_source, dmgmd::EnsembleCommand{kind, 300, 300, 100, 0, 1000, 10}},
          {run_source, dmgmd::RunCommand{1}}}};
      dmgmd::Model model{};
      gpumd_compat::Box box{};
      dmgmd::DomainEligibility eligible{};
      eligible.eligible = true;
      eligible.axis = 0;
      const std::uint64_t allocations_before = gpumd_compat::gpu_vector_allocation_count();
      bool rejected = false;
      try {
        dmgmd::run_local_domain(program, std::move(model), box, nullptr, eligible, mpi);
      } catch (const std::runtime_error& error) {
        rejected = std::string(error.what()).find("run_local_domain rejects NPT") !=
                   std::string::npos;
      }
      if (!rejected) throw std::runtime_error(std::string("direct M2a ") + name + " was not rejected");
      if (gpumd_compat::gpu_vector_allocation_count() != allocations_before) {
        throw std::runtime_error(std::string("M2a ") + name + " guard allocated before rejecting");
      }
    }
    std::cout << "PASS direct M2a NPT guards before device allocation/force\n";
  } catch (const std::exception& error) {
    std::cerr << "M2a NPT guard test failure: " << error.what() << '\n';
    return 1;
  }
  return 0;
}
