#include "dmgmd/box_control.hpp"
#include "runtime_internal.hpp"
#include "gpumd_compat/neighbor.cuh"

#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

namespace {

void require(bool condition, const std::string& message)
{
  if (!condition) throw std::runtime_error(message);
}

void close_enough(double actual, double expected, const std::string& label)
{
  const double tolerance = 2.0e-13 * std::max(1.0, std::abs(expected));
  if (std::abs(actual - expected) > tolerance) {
    throw std::runtime_error(label + " differs: got " + std::to_string(actual) +
                             ", expected " + std::to_string(expected));
  }
}

gpumd_compat::Box make_box(bool orthogonal = true)
{
  gpumd_compat::Box box{};
  if (orthogonal) {
    box.cpu_h[0] = 10.0;
    box.cpu_h[4] = 12.0;
    box.cpu_h[8] = 14.0;
  } else {
    box.cpu_h[0] = 10.0;
    box.cpu_h[1] = 0.5;
    box.cpu_h[4] = 12.0;
    box.cpu_h[8] = 14.0;
  }
  box.get_inverse();
  box.set_is_orthogonal();
  return box;
}

template <class F>
void must_reject(F&& action, const std::string& label)
{
  bool rejected = false;
  try { action(); }
  catch (const std::exception&) { rejected = true; }
  require(rejected, label + " was accepted");
}

}  // namespace

int main()
{
  int device_count = 0;
  const cudaError_t device_status = cudaGetDeviceCount(&device_count);
  if (device_status != cudaSuccess || device_count == 0) return 77;
  try {
    using namespace dmgmd::detail;
    BoxScaleWorkspace scale_workspace;
    {
      // Cache-only fault injection: a box-only periodic geometry change makes
      // a previously absent pair close while coordinate displacement is zero.
      auto cache_box = make_box();
      cache_box.cpu_h[0] = 30.0;
      cache_box.cpu_h[4] = 30.0;
      cache_box.cpu_h[8] = 30.0;
      cache_box.get_inverse();
      cache_box.set_is_orthogonal();
      gpumd_compat::GPU_Vector<int> types(2);
      const int type_data[2] = {0, 0};
      types.copy_from_host(type_data);
      gpumd_compat::GPU_Vector<double> positions(6);
      const double position_data[6] = {1.0, 19.0, 0.0, 0.0, 0.0, 0.0};
      positions.copy_from_host(position_data);
      gpumd_compat::Neighbor neighbor;
      neighbor.initialize(5.0, 2, 1);
      neighbor.find_neighbor_global(5.0, cache_box, types, positions);
      int counts[2]{};
      neighbor.NN.copy_to_host(counts);
      require(counts[0] == 0 && counts[1] == 0, "initial large-box pair unexpectedly cached");
      cache_box.cpu_h[0] = 20.0;
      cache_box.cpu_h[4] = 20.0;
      cache_box.cpu_h[8] = 20.0;
      cache_box.get_inverse();
      cache_box.set_is_orthogonal();
      neighbor.find_neighbor_global(5.0, cache_box, types, positions);
      neighbor.NN.copy_to_host(counts);
      require(counts[0] == 0 && counts[1] == 0,
              "default box cache path unexpectedly rebuilt without notification");
      neighbor.notify_box_changed();
      neighbor.find_neighbor_global(5.0, cache_box, types, positions);
      neighbor.NN.copy_to_host(counts);
      require(counts[0] == 1 && counts[1] == 1,
              "box-change notification did not rebuild the global neighbor rows");

      // Real affine scaling plus notification must produce the same neighbor
      // rows as a fresh build from the scaled coordinates and box.
      auto affine_box = make_box();
      affine_box.cpu_h[0] = affine_box.cpu_h[4] = affine_box.cpu_h[8] = 30.0;
      affine_box.get_inverse();
      affine_box.set_is_orthogonal();
      gpumd_compat::GPU_Vector<double> affine_positions(6);
      const double affine_data[6] = {11.5, 18.5, 0.0, 0.0, 0.0, 0.0};
      affine_positions.copy_from_host(affine_data);
      gpumd_compat::Neighbor affine_neighbor;
      affine_neighbor.initialize(5.0, 2, 1);
      affine_neighbor.find_neighbor_global(5.0, affine_box, types, affine_positions);
      affine_neighbor.NN.copy_to_host(counts);
      require(counts[0] == 0 && counts[1] == 0, "affine cache fixture began with a neighbor");
      apply_isotropic_scale(affine_box, affine_positions, scale_workspace, 2, 2, 0.7);
      affine_neighbor.notify_box_changed();
      affine_neighbor.find_neighbor_global(5.0, affine_box, types, affine_positions);
      int actual_counts[2]{};
      int expected_counts[2]{};
      affine_neighbor.NN.copy_to_host(actual_counts);
      gpumd_compat::Neighbor fresh_neighbor;
      fresh_neighbor.initialize(5.0, 2, 1);
      fresh_neighbor.find_neighbor_global(5.0, affine_box, types, affine_positions);
      fresh_neighbor.NN.copy_to_host(expected_counts);
      require(actual_counts[0] == expected_counts[0] && actual_counts[1] == expected_counts[1] &&
                  actual_counts[0] == 1,
              "scaled notified list differs from fresh scaled-box neighbor list");
    }
    const double scale = 1.25;
    auto box = make_box();
    int bins[3]{};
    box.get_num_bins(1.0, bins);
    const double old_thickness_x = box.thickness_x;
    const auto original_box = box;
    const std::size_t global_count = 3;
    const std::size_t stride = 5;
    std::vector<double> source(3 * stride, -777.0);
    source[0] = 1.25; source[1] = 3.0; source[2] = 5.0;
    source[stride + 0] = 2.0; source[stride + 1] = 4.0; source[stride + 2] = 6.0;
    source[2 * stride + 0] = 3.0; source[2 * stride + 1] = 5.0; source[2 * stride + 2] = 7.0;
    gpumd_compat::GPU_Vector<double> device_position(source.size());
    device_position.copy_from_host(source.data());

    const auto candidate = prepare_scaled_box(box, scale);
    close_enough(candidate.get_volume(), original_box.get_volume() * scale * scale * scale,
                 "volume scaling");
    apply_isotropic_scale(box, device_position, scale_workspace, global_count, stride, scale);
    require(box.thickness_x == old_thickness_x,
            "box scaling unexpectedly changed cached thickness before neighbor setup");
    box.get_num_bins(1.0, bins);  // The next force/neighbor setup refreshes thickness.
    close_enough(box.thickness_x, old_thickness_x * scale,
                 "next neighbor setup thickness refresh");
    std::vector<double> scaled(source.size());
    device_position.copy_to_host(scaled.data());
    std::array<double, 9> inverse_before{};
    std::array<double, 9> inverse_after{};
    std::copy(original_box.cpu_h + 9, original_box.cpu_h + 18, inverse_before.begin());
    std::copy(box.cpu_h + 9, box.cpu_h + 18, inverse_after.begin());
    std::vector<int> owner_before(global_count), owner_after(global_count);
    const int axis_before = dmgmd::longest_box_axis({original_box.cpu_h[0], original_box.cpu_h[1],
        original_box.cpu_h[2], original_box.cpu_h[3], original_box.cpu_h[4], original_box.cpu_h[5],
        original_box.cpu_h[6], original_box.cpu_h[7], original_box.cpu_h[8]});
    const int axis_after = dmgmd::longest_box_axis({box.cpu_h[0], box.cpu_h[1], box.cpu_h[2],
        box.cpu_h[3], box.cpu_h[4], box.cpu_h[5], box.cpu_h[6], box.cpu_h[7], box.cpu_h[8]});
    for (std::size_t slot = 0; slot < global_count; ++slot) {
      owner_before[slot] = dmgmd::slab_owner_of_fractional(
          dmgmd::fractional_along_axis(inverse_before, axis_before, source[slot],
              source[stride + slot], source[2 * stride + slot]), 2);
      owner_after[slot] = dmgmd::slab_owner_of_fractional(
          dmgmd::fractional_along_axis(inverse_after, axis_after, scaled[slot],
              scaled[stride + slot], scaled[2 * stride + slot]), 2);
    }
    const std::vector<std::uint64_t> gids{0, 1, 2};
    const dmgmd::SpatialOwnership map_before(owner_before, gids, 0, 2);
    const dmgmd::SpatialOwnership map_after(owner_after, gids, 0, 2);
    require(map_before.map_hash() == map_after.map_hash(), "P=2 owner map changed after isotropic scale");
    const dmgmd::SpatialOwnership p1_before =
        dmgmd::SpatialOwnership::trivial(global_count, gids, 1);
    const dmgmd::SpatialOwnership p1_after =
        dmgmd::SpatialOwnership::trivial(global_count, gids, 1);
    require(p1_before.map_hash() == p1_after.map_hash(), "P=1 owner map changed after isotropic scale");

    // Snapshot contracts: new box and scaled wrapped positions are written,
    // while unwrapped coordinates retain their pre-scale values.
    namespace fs = std::filesystem;
    const fs::path snapshot_dir = fs::temp_directory_path() /
        ("dmgmd-box-contract-" + std::to_string(static_cast<long long>(::getpid())));
    fs::create_directories(snapshot_dir);
    const fs::path old_cwd = fs::current_path();
    fs::current_path(snapshot_dir);
    dmgmd::HostAtoms identity;
    identity.counts = {global_count, global_count, 0};
    identity.global_id = {0, 1, 2};
    identity.species = {"C", "C", "C"};
    identity.type = {0, 0, 0};
    identity.mass = {12.0, 12.0, 12.0};
    identity.position.assign(3 * global_count, 0.0);
    identity.velocity.assign(3 * global_count, 0.0);
    dmgmd::detail::HostSnapshot snapshot;
    snapshot.global_id = {0ULL, 1ULL, 2ULL};
    snapshot.position.resize(3 * global_count);
    snapshot.velocity.assign(3 * global_count, 0.0);
    snapshot.force.assign(3 * global_count, 0.0);
    snapshot.potential.assign(global_count, 0.0);
    snapshot.virial.assign(9 * global_count, 0.0);
    snapshot.unwrapped.resize(3 * global_count);
    for (int axis = 0; axis < 3; ++axis) {
      for (std::size_t slot = 0; slot < global_count; ++slot) {
        const auto index = static_cast<std::size_t>(axis) * global_count + slot;
        snapshot.position[index] = scaled[static_cast<std::size_t>(axis) * stride + slot];
        snapshot.unwrapped[index] = source[static_cast<std::size_t>(axis) * stride + slot];
      }
    }
    dmgmd::DumpXyzCommand xyz;
    xyz.filename = "snapshot.xyz";
    xyz.precision = dmgmd::OutputPrecision::double_precision;
    xyz.quantities.unwrapped_position = true;
    dmgmd::detail::write_xyz(xyz, 0, 0.0, box, identity, snapshot, {});
    dmgmd::detail::write_restart(box, identity, snapshot);
    std::ifstream xyz_file("snapshot.xyz");
    std::ifstream restart_file("restart.xyz");
    std::string xyz_count, xyz_header, xyz_atom;
    std::string restart_count, restart_header, restart_atom;
    std::getline(xyz_file, xyz_count); std::getline(xyz_file, xyz_header);
    std::getline(xyz_file, xyz_atom);
    std::getline(restart_file, restart_count); std::getline(restart_file, restart_header);
    std::getline(restart_file, restart_atom);
    require(xyz_count == "3" && restart_count == "3", "snapshot atom count mismatch");
    require(xyz_header.find("Lattice=\"12.5 0 0 0 15 0 0 0 17.5\"") != std::string::npos,
            "dump_xyz lattice does not match scaled box");
    require(restart_header.find("Lattice=\"12.5 0 0 0 15 0 0 0 17.5\"") != std::string::npos,
            "restart lattice does not match scaled box");
    require(xyz_header.find("unwrapped_position:R:3") != std::string::npos,
            "unwrapped snapshot field missing");
    std::string species;
    double x = 0.0, y = 0.0, z = 0.0, ux = 0.0, uy = 0.0, uz = 0.0;
    double mass = 0.0, vx = 0.0, vy = 0.0, vz = 0.0;
    // dump_xyz lists wrapped coordinates, then unwrapped coordinates.
    std::istringstream xyz_all(xyz_atom);
    xyz_all >> species >> x >> y >> z >> ux >> uy >> uz;
    require(x == scaled[0] && y == scaled[stride] && z == scaled[2 * stride],
            "dump_xyz wrapped coordinates do not match scaled positions");
    require(ux == source[0] && uy == source[stride] && uz == source[2 * stride],
            "unwrapped positions were scaled");
    std::istringstream restart_all(restart_atom);
    restart_all >> species >> x >> y >> z >> mass >> vx >> vy >> vz;
    require(x == scaled[0] && y == scaled[stride] && z == scaled[2 * stride],
            "restart positions do not match scaled wrapped coordinates");
    fs::current_path(old_cwd);
    fs::remove_all(snapshot_dir);
    require(box.cpu_h[0] == candidate.cpu_h[0] && box.cpu_h[4] == candidate.cpu_h[4] &&
                box.cpu_h[8] == candidate.cpu_h[8], "committed box differs from validated candidate");

    device_position.copy_to_host(scaled.data());
    for (std::size_t slot = 0; slot < global_count; ++slot) {
      for (int axis = 0; axis < 3; ++axis) {
        const auto index = static_cast<std::size_t>(axis) * stride + slot;
        close_enough(scaled[index], source[index] * scale, "scaled coordinate");
        close_enough(scaled[index] / box.cpu_h[4 * axis],
                     source[index] / original_box.cpu_h[4 * axis], "fractional coordinate");
      }
    }
    for (int axis = 0; axis < 3; ++axis) {
      for (std::size_t slot = global_count; slot < stride; ++slot) {
        require(scaled[static_cast<std::size_t>(axis) * stride + slot] ==
                    source[static_cast<std::size_t>(axis) * stride + slot],
                "padding slot was modified");
      }
    }
    // A second inverse scale returns the live coordinates and box within
    // double precision. Velocity and unwrapped buffers are absent from this
    // API and remain exclusively owned by the runtime caller.
    apply_isotropic_scale(box, device_position, scale_workspace, global_count, stride, 1.0 / scale);
    device_position.copy_to_host(scaled.data());
    for (std::size_t index = 0; index < source.size(); ++index) {
      close_enough(scaled[index], source[index], "round-trip coordinate");
    }
    close_enough(box.get_volume(), original_box.get_volume(), "round-trip volume");

    auto unchanged_box = box;
    const auto unchanged_position = scaled;
    must_reject([&] { apply_isotropic_scale(box, device_position, scale_workspace, global_count, stride, 0.0); },
                "zero factor");
    must_reject([&] { apply_isotropic_scale(box, device_position, scale_workspace, global_count, stride, -1.0); },
                "negative factor");
    must_reject([&] { apply_isotropic_scale(box, device_position, scale_workspace, global_count, stride,
                                             std::numeric_limits<double>::infinity()); },
                "infinite factor");
    auto triclinic = make_box(false);
    must_reject([&] { apply_isotropic_scale(triclinic, device_position, scale_workspace, global_count, stride, 1.1); },
                "triclinic box");
    auto stale_orthogonal_flag = make_box(false);
    stale_orthogonal_flag.is_orthogonal = true;
    must_reject([&] { apply_isotropic_scale(
                         stale_orthogonal_flag, device_position, scale_workspace, global_count, stride, 1.1); },
                "stale orthogonal flag on triclinic box");
    auto nonperiodic = box;
    nonperiodic.pbc_y = 0;
    must_reject([&] { apply_isotropic_scale(nonperiodic, device_position, scale_workspace, global_count, stride, 1.1); },
                "non-periodic box");
    std::vector<double> overflowing = source;
    overflowing[0] = std::numeric_limits<double>::max();
    gpumd_compat::GPU_Vector<double> overflow_position(overflowing.size());
    overflow_position.copy_from_host(overflowing.data());
    must_reject([&] { apply_isotropic_scale(box, overflow_position, scale_workspace, global_count, stride, 1.1); },
                "coordinate overflow");
    std::vector<double> overflow_after(overflowing.size());
    overflow_position.copy_to_host(overflow_after.data());
    require(overflow_after == overflowing, "overflow preflight mutated coordinates");
    device_position.copy_to_host(scaled.data());
    require(box.cpu_h[0] == unchanged_box.cpu_h[0] && box.cpu_h[4] == unchanged_box.cpu_h[4] &&
                box.cpu_h[8] == unchanged_box.cpu_h[8], "rejected scale changed box");
    require(scaled == unchanged_position, "rejected scale changed coordinates");

    std::cout << "box control geometry and validation passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "box control test failed: " << error.what() << '\n';
    return 1;
  }
}
