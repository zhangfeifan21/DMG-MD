// DMG-MD O2: selection metadata only; no particle layout or NEP arithmetic.
#pragma once
#include <array>
#include <algorithm>
#include <cmath>
#include <limits>
#include <cstdlib>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>
#include <functional>

namespace gpumd_compat {
enum class DomainPartitionMode { off, serial, verify };
enum class DomainOverlapMode { off, wait, poll };
enum class DomainSchedule { split, producer, producer_compact, producer_geometry };
inline bool producer_schedule(DomainSchedule mode) { return mode != DomainSchedule::split; }
inline bool compact_schedule(DomainSchedule mode) {
  return mode == DomainSchedule::producer_compact || mode == DomainSchedule::producer_geometry;
}
// Complete Verlet radius plus a conservative bound for double subtraction,
// float conversion/MIC, squared-distance rounding and float box conversion.
// Actual V(E) closure is checked on the GPU before any list is published.
inline double producer_inset(double build_radius, double axis_length) {
  if (!(build_radius > 0) || !(axis_length > 0) ||
      !std::isfinite(build_radius) || !std::isfinite(axis_length))
    throw std::invalid_argument("invalid geometric producer radius/box");
  const double e = 32.0 * std::numeric_limits<float>::epsilon();
  return std::nextafter(build_radius * (1.0 + e) + e * axis_length,
                        std::numeric_limits<double>::infinity());
}
inline bool geometric_early(double q, double lo, double hi, double inset) {
  return q > lo && q < hi && std::min(q-lo, hi-q) > inset;
}
inline DomainSchedule domain_schedule()
{
  const char* value = std::getenv("DMGMD_DOMAIN_SCHEDULE");
  const std::string mode = value ? value : "split";
  if (mode == "split") return DomainSchedule::split;
  if (mode == "producer") return DomainSchedule::producer;
  if (mode == "producer-compact") return DomainSchedule::producer_compact;
  if (mode == "producer-geometry") return DomainSchedule::producer_geometry;
  throw std::invalid_argument("DMGMD_DOMAIN_SCHEDULE must be split or producer or producer-compact or producer-geometry");
}
inline const char* schedule_name(DomainSchedule mode)
{
  switch (mode) {
    case DomainSchedule::split: return "split";
    case DomainSchedule::producer: return "producer";
    case DomainSchedule::producer_compact: return "producer-compact";
    case DomainSchedule::producer_geometry: return "producer-geometry";
  }
  throw std::logic_error("invalid domain schedule");
}
inline DomainOverlapMode domain_overlap_mode()
{
  const char* value = std::getenv("DMGMD_DOMAIN_OVERLAP");
  const std::string mode = value ? value : "off";
  if (mode == "off") return DomainOverlapMode::off;
  if (mode == "wait") return DomainOverlapMode::wait;
  if (mode == "poll") return DomainOverlapMode::poll;
  throw std::invalid_argument("DMGMD_DOMAIN_OVERLAP must be off, wait or poll");
}
inline const char* overlap_name(DomainOverlapMode mode)
{ return mode == DomainOverlapMode::off ? "off" : mode == DomainOverlapMode::wait ? "wait" : "poll"; }
inline DomainPartitionMode domain_partition_mode()
{
  const char* value = std::getenv("DMGMD_DOMAIN_PARTITION");
  const std::string mode = value ? value : "off";
  if (mode == "off") return DomainPartitionMode::off;
  if (mode == "serial") return DomainPartitionMode::serial;
  if (mode == "verify") return DomainPartitionMode::verify;
  throw std::invalid_argument("DMGMD_DOMAIN_PARTITION must be off, serial or verify");
}
struct DomainPartitionKey;
struct DomainForceContext {
  DomainPartitionMode mode = DomainPartitionMode::off;
  bool ordinary = false;
  std::uint64_t step = 0, halo_ready_step = 0;
  DomainOverlapMode overlap = DomainOverlapMode::off;
  std::uint64_t mapping_epoch = 0;
  // Owned-ready permits graph-only prepare. Only finish_halo publishes a
  // physical halo-ready proof; begin alone never grants ghost access.
  std::function<std::uint64_t()> begin_halo;
  std::function<bool(std::uint64_t)> finish_halo;
  std::function<void()> clear_outputs;
  std::function<void(const char*, int, const DomainPartitionKey&, std::uint64_t)> observe;
  DomainSchedule schedule = DomainSchedule::split;
  int slab_axis = -1;
  double slab_lo = 0, slab_hi = 0, axis_length = 0;
};

struct DomainPartitionKey {
  std::uint64_t layout = 0, neighbor = 0, storage = 0;
  int owned = 0, dependency = 0, local = 0;
  DomainSchedule rule = DomainSchedule::split;
  int slab_axis = -1;
  double slab_lo = 0, slab_hi = 0, inset = 0;
  // Both box representations matter: the filter uses float MIC.
  std::array<double, 39> box{};
  bool operator==(const DomainPartitionKey& b) const {
    return layout == b.layout && neighbor == b.neighbor && storage == b.storage &&
      owned == b.owned && dependency == b.dependency && local == b.local && box == b.box &&
      rule == b.rule && slab_axis == b.slab_axis && slab_lo == b.slab_lo &&
      slab_hi == b.slab_hi && inset == b.inset;
  }
};

// One compute owns this ticket. Owned-ready and halo-ready are distinct;
// runtime owns the O3 slot, while this ticket freezes the force generations.
struct DomainForceTicket {
  std::uint64_t step;
  int call;
  bool early_done = false, late_done = false;
  DomainPartitionKey key{};
  std::uint64_t halo = 0;
  bool ready = false, finalized = false;
  void validate(std::uint64_t current_step, const DomainPartitionKey& current) const {
    if (finalized || current_step != step || !(key == current))
      throw std::logic_error("stale partition force ticket");
  }
  void posted(std::uint64_t generation) {
    if (!generation || halo || early_done || late_done || finalized)
      throw std::logic_error("invalid partition halo generation");
    halo = generation;
  }
  void halo_ready(std::uint64_t generation) {
    if (!halo || generation != halo || ready || finalized)
      throw std::logic_error("stale/duplicate partition halo-ready token");
    ready = true;
  }
  void early() {
    if (early_done || late_done || finalized) throw std::logic_error("duplicate partition early");
    early_done = true;
  }
  void late() {
    if (!early_done || late_done || finalized || (halo && !ready)) throw std::logic_error("invalid partition late");
    late_done = true;
  }
  void finalize() {
    if (!late_done || finalized) throw std::logic_error("invalid partition finalize");
    finalized = true;
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
