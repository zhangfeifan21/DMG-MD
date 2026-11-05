#include "dmgmd/box_control.hpp"

#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const std::string& message)
{
  if (!condition) throw std::runtime_error(message);
}

template <class F>
void must_reject(F&& action, const std::string& label)
{
  bool rejected = false;
  try { action(); }
  catch (const std::exception&) { rejected = true; }
  require(rejected, label + " was accepted");
}

gpumd_compat::Box box_with(double x, double y, double z)
{
  gpumd_compat::Box box{};
  box.cpu_h[0] = x;
  box.cpu_h[4] = y;
  box.cpu_h[8] = z;
  box.get_inverse();
  box.set_is_orthogonal();
  return box;
}

}  // namespace

int main()
{
  try {
    auto box = box_with(10.0, 12.0, 14.0);
    const auto candidate = dmgmd::detail::prepare_scaled_box(box, 1.5);
    require(candidate.cpu_h[0] == 15.0 && candidate.cpu_h[4] == 18.0 &&
                candidate.cpu_h[8] == 21.0, "diagonal lengths were not scaled");
    require(candidate.get_volume() == box.get_volume() * 1.5 * 1.5 * 1.5,
            "candidate volume mismatch");
    require(candidate.float_h[0] == 15.0f && candidate.float_h[4] == 18.0f &&
                candidate.float_h[8] == 21.0f, "float copy was not refreshed");
    require(box.cpu_h[0] == 10.0 && box.cpu_h[4] == 12.0 && box.cpu_h[8] == 14.0,
            "candidate preparation mutated the original box");

    std::mt19937_64 random(0xB10C0A7ULL);
    std::uniform_real_distribution<double> lengths(7.0, 31.0);
    std::uniform_real_distribution<double> factors(0.55, 1.8);
    for (int trial = 0; trial < 64; ++trial) {
      const auto initial = box_with(lengths(random), lengths(random), lengths(random));
      const double factor = factors(random);
      const auto changed = dmgmd::detail::prepare_scaled_box(initial, factor);
      const auto restored = dmgmd::detail::prepare_scaled_box(changed, 1.0 / factor);
      for (int axis = 0; axis < 3; ++axis) {
        const int diagonal = 4 * axis;
        const double relative = std::abs(restored.cpu_h[diagonal] - initial.cpu_h[diagonal]) /
                                initial.cpu_h[diagonal];
        require(relative < 3.0e-16, "random orthogonal box round trip exceeded tolerance");
        require(std::abs(changed.cpu_h[diagonal] * changed.cpu_h[9 + 4 * axis] - 1.0) < 1.0e-15,
                "scaled inverse diagonal is inconsistent");
      }
    }

    must_reject([&] { dmgmd::detail::prepare_scaled_box(box, 0.0); }, "zero factor");
    must_reject([&] { dmgmd::detail::prepare_scaled_box(box, -1.0); }, "negative factor");
    must_reject([&] { dmgmd::detail::prepare_scaled_box(
                         box, std::numeric_limits<double>::quiet_NaN()); }, "NaN factor");
    must_reject([&] { dmgmd::detail::prepare_scaled_box(
                         box, std::numeric_limits<double>::infinity()); }, "infinite factor");
    must_reject([&] { dmgmd::detail::prepare_scaled_box(
                         box, std::numeric_limits<double>::max()); }, "box overflow");
    auto triclinic = box_with(10.0, 12.0, 14.0);
    triclinic.cpu_h[1] = 0.25;
    triclinic.get_inverse();
    triclinic.set_is_orthogonal();
    must_reject([&] { dmgmd::detail::prepare_scaled_box(triclinic, 1.1); }, "triclinic box");
    auto stale_flag = triclinic;
    stale_flag.is_orthogonal = true;
    must_reject([&] { dmgmd::detail::prepare_scaled_box(stale_flag, 1.1); },
                "triclinic matrix with stale orthogonal flag");
    auto nonperiodic = box;
    nonperiodic.pbc_z = 0;
    must_reject([&] { dmgmd::detail::prepare_scaled_box(nonperiodic, 1.1); }, "non-periodic box");
    auto float_overflow = box_with(2.0e38, 2.0e38, 2.0e38);
    must_reject([&] { dmgmd::detail::prepare_scaled_box(float_overflow, 2.0); },
                "float box overflow");
    auto float_underflow = box_with(1.0, 1.0, 1.0);
    must_reject([&] { dmgmd::detail::prepare_scaled_box(float_underflow, 1.0e-50); },
                "float box underflow");
    std::cout << "PASS host box candidate validation\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL host box candidate validation: " << error.what() << '\n';
    return 1;
  }
}
