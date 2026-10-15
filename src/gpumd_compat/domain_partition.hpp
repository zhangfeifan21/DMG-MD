// DMG-MD O2: selection metadata only; no particle layout or NEP arithmetic.
#pragma once
#include <array>
#include <cstdlib>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace gpumd_compat {
enum class DomainPartitionMode { off, serial, verify };
inline DomainPartitionMode domain_partition_mode()
{
  const char* value = std::getenv("DMGMD_DOMAIN_PARTITION");
  const std::string mode = value ? value : "off";
  if (mode == "off") return DomainPartitionMode::off;
  if (mode == "serial") return DomainPartitionMode::serial;
  if (mode == "verify") return DomainPartitionMode::verify;
  throw std::invalid_argument("DMGMD_DOMAIN_PARTITION must be off, serial or verify");
}
struct DomainForceContext {
  DomainPartitionMode mode = DomainPartitionMode::off;
  bool ordinary = false;
  std::uint64_t step = 0, halo_ready_step = 0;
};

struct DomainPartitionKey {
  std::uint64_t layout = 0, neighbor = 0, storage = 0;
  int owned = 0, dependency = 0, local = 0;
  // Both box representations matter: the filter uses float MIC.
  std::array<double, 39> box{};
  bool operator==(const DomainPartitionKey& b) const {
    return layout == b.layout && neighbor == b.neighbor && storage == b.storage &&
      owned == b.owned && dependency == b.dependency && local == b.local && box == b.box;
  }
};

// O2's ticket is confined to one completed-halo force call. O3 must extend
// it with request/buffer lifetimes before exposing begin/finish externally.
struct DomainForceTicket {
  std::uint64_t step;
  int call;
  bool early_done = false, late_done = false;
  void early() {
    if (early_done || late_done) throw std::logic_error("duplicate partition early");
    early_done = true;
  }
  void late() {
    if (!early_done || late_done) throw std::logic_error("invalid partition late");
    late_done = true;
  }
};

struct HostDomainPartition {
  std::vector<int> safe, interior, early;
};

// Independent graph oracle, also used by verify mode. Rows are actual full
// Verlet rows, including the skin band. Slot identity is fixed by key.layout.
inline HostDomainPartition partition_graph(
  const std::vector<std::vector<int>>& rows, int owned, int dependency, int local)
{
  if (owned < 0 || dependency < owned || local < dependency ||
      rows.size() != static_cast<std::size_t>(dependency))
    throw std::invalid_argument("partition graph shape");
  HostDomainPartition p{std::vector<int>(local), std::vector<int>(local),
                        std::vector<int>(local)};
  for (int c = 0; c < dependency; ++c)
    for (int j : rows[c])
      if (j < 0 || j >= local || j == c)
        throw std::invalid_argument("partition graph invalid neighbor");
  for (int c = 0; c < owned; ++c) {
    p.safe[c] = 1;
    for (int j : rows[c]) if (j >= owned) p.safe[c] = 0;
  }
  for (int c = 0; c < owned; ++c) {
    bool closed = p.safe[c];
    for (int j : rows[c]) closed = closed && p.safe[j];
    if (closed) {
      p.interior[c] = p.early[c] = 1;
      for (int j : rows[c]) p.early[j] = 1;
    }
  }
  return p;
}
} // namespace gpumd_compat
