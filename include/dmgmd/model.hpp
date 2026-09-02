#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace dmgmd {

struct AtomCounts {
  std::size_t global_count = 0;
  std::size_t owned_count = 0;
  std::size_t ghost_count = 0;

  [[nodiscard]] std::size_t local_count() const noexcept {
    return owned_count + ghost_count;
  }
};

struct BoxData {
  std::array<int, 3> periodic{1, 1, 1};
  // Same row-major h layout used by GPUMD's Box::cpu_h[0..8].
  std::array<double, 9> h{};
};

struct PotentialMetadata {
  std::string version;
  std::vector<std::string> symbols;
};

struct HostAtoms {
  AtomCounts counts;
  bool has_input_velocity = false;

  // Every per-atom array is local and uses counts.local_count() as its SoA
  // stride. global_count is metadata and is never an addressing stride.
  std::vector<std::uint64_t> global_id;
  std::vector<std::string> species;
  std::vector<int> type;
  std::vector<double> mass;
  std::vector<float> charge;
  std::vector<double> position;
  std::vector<double> velocity;
  std::vector<std::vector<int>> group_labels;

  [[nodiscard]] std::size_t local_stride() const noexcept {
    return counts.local_count();
  }

  void validate() const;
};

struct Model {
  BoxData box;
  HostAtoms atoms;
};

PotentialMetadata load_potential_metadata(const std::string& filename);
Model parse_model_file(
    const std::string& filename,
    const PotentialMetadata& potential);

}  // namespace dmgmd
