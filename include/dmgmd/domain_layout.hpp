#pragma once

// M2a local-domain layout: pure host-side geometry, classification, counting
// and exchange-plan generation (docs/plans/domain-decomposition.md sections
// 3-8). Nothing here touches MPI or CUDA; the domain runtime in
// src/domain_runtime.cu drives these helpers, and tests/domain_layout_tests.cpp
// exercises them directly.
//
// Contracts implemented here:
//   * typewise two-hop halo radii d_dep / d_coord derived from the kernels'
//     actual consumption paths (radial-first filter, angular list, ZBL
//     consuming the angular list -- semantics preserved, never "corrected");
//   * M2a eligibility: P>1, orthogonal, fully periodic, large-box criterion of
//     the pinned NEP implementation, and slab_width >= d_coord; everything
//     else fails closed to the M1 replicated-full path with a stable reason;
//   * the local slot layout [0, owned) | dependency ghosts | coordinate-only
//     ghosts, ordered by (source face, source rank, global_id, image);
//   * per-face halo exchange plans (P=2 uses one peer for both faces, so the
//     two directions stay separate and a send-list de-duplication keeps every
//     global atom at most once per rank);
//   * direct migration routing to the final owner (one step may cross any
//     number of slabs, including the periodic ends).

#include "dmgmd/spatial_ownership.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace dmgmd {

// Must stay identical to gpumd_compat::Neighbor's fixed Verlet skin.
inline constexpr double kNeighborSkin = 1.0;

// ---------------------------------------------------------------------------
// Typewise consumption radii (plan section 4.3/4.4).
// ---------------------------------------------------------------------------

// Per-type cutoff description taken from the parsed NEP parameters. Kept free
// of gpumd_compat types so this header stays CPU-testable.
struct CutoffSet {
  int num_types = 0;
  std::vector<double> rc_radial;   // per type, Angstrom
  std::vector<double> rc_angular;  // per type, Angstrom
  bool zbl_enabled = false;
  bool zbl_flexible = false;
  double zbl_rc_outer = 0.0;
  bool zbl_typewise = false;
};

struct DomainRadii {
  // R_force(t1,t2): the maximum distance at which an owned center of type t1
  // consumes a neighbor of type t2 in the final-force kernels. Consumers:
  //   * find_force_radial reads the typewise RADIAL list, whose membership
  //     requires d < (rc_radial[t1]+rc_radial[t2])/2;
  //   * gpu_find_force_many_body and find_force_ZBL read the typewise ANGULAR
  //     list, whose membership (radial-first filter, nep.cu find_neighbor_
  //     list_large_box) requires d < rc_radial AND d < rc_angular, i.e.
  //     d < min(Rr, Ra). ZBL's own rc_outer can exceed this but the list caps
  //     the actual consumption -- the pinned semantics are reproduced, not
  //     "fixed".
  // max(Rr, min(Rr, Ra)) == Rr, computed explicitly below so the enumeration
  // stays auditable.
  std::vector<double> r_force_pair;  // [t1 * num_types + t2]
  // R_dep(t2,t3): same enumeration for the descriptor/partial consumers of a
  // dependency center of type t2 (find_descriptor's radial and angular parts,
  // find_partial_force_angular's angular list).
  std::vector<double> r_dep_pair;  // [t2 * num_types + t3]
  double r_force_max = 0.0;
  double r_dep_max = 0.0;
  double d_dep = 0.0;     // max R_force + skin
  double d_coord = 0.0;   // max (R_force + R_dep) over type chains + 2*skin
  bool proven = false;    // false -> the runtime must fail closed to M1
};

[[nodiscard]] inline DomainRadii compute_domain_radii(const CutoffSet& cutoffs)
{
  DomainRadii radii;
  const int num_types = cutoffs.num_types;
  if (num_types <= 0 ||
      cutoffs.rc_radial.size() != static_cast<std::size_t>(num_types) ||
      cutoffs.rc_angular.size() != static_cast<std::size_t>(num_types)) {
    return radii;  // malformed -> proven == false -> fail closed
  }
  // The consumer kernel performs the pair average in float.  Repeating the
  // expression in double can round *below* that value for mixed type pairs
  // (for example float(1.0) and float(1.4)).  Use the larger of the exact
  // average of the loaded float operands and the kernel result; when the
  // kernel rounded upward, one double ULP keeps the host-side halo bound
  // conservatively above it.  Exact uniform pairs stay exact, preserving
  // eligibility at a slab_width == d_coord boundary.
  const auto kernel_pair_upper = [](double first, double second) {
    const float first_f = static_cast<float>(first);
    const float second_f = static_cast<float>(second);
    const float kernel_average = (first_f + second_f) * 0.5f;
    const double exact_average = 0.5 * (first + second);
    double upper = std::max(exact_average, static_cast<double>(kernel_average));
    if (static_cast<double>(kernel_average) > exact_average) {
      upper = std::nextafter(upper, std::numeric_limits<double>::infinity());
    }
    return upper;
  };
  const auto radial_reach = [&](int t1, int t2) {
    return kernel_pair_upper(cutoffs.rc_radial[static_cast<std::size_t>(t1)],
                             cutoffs.rc_radial[static_cast<std::size_t>(t2)]);
  };
  const auto angular_reach = [&](int t1, int t2) {
    return std::min(radial_reach(t1, t2),
                    kernel_pair_upper(
                        cutoffs.rc_angular[static_cast<std::size_t>(t1)],
                        cutoffs.rc_angular[static_cast<std::size_t>(t2)]));
  };
  // Non-finite or non-positive cutoffs must fail closed: std::max would
  // silently drop a NaN and produce a dangerous under-estimate of the halo.
  for (std::size_t index = 0; index < cutoffs.rc_radial.size(); ++index) {
    const double radial = cutoffs.rc_radial[index];
    const double angular = cutoffs.rc_angular[index];
    if (!std::isfinite(radial) || !std::isfinite(angular) || radial <= 0.0 ||
        angular <= 0.0 || !std::isfinite(static_cast<float>(radial)) ||
        !std::isfinite(static_cast<float>(angular))) {
      return radii;  // proven == false -> the runtime fails closed to M1
    }
  }
  radii.r_force_pair.assign(static_cast<std::size_t>(num_types) * num_types, 0.0);
  radii.r_dep_pair.assign(static_cast<std::size_t>(num_types) * num_types, 0.0);
  for (int t1 = 0; t1 < num_types; ++t1) {
    for (int t2 = 0; t2 < num_types; ++t2) {
      // Radial-list reach vs angular-list reach (angular membership is a
      // subset of radial membership by the radial-first filter).
      const double force =
          std::max(radial_reach(t1, t2), angular_reach(t1, t2));
      radii.r_force_pair[static_cast<std::size_t>(t1) * num_types + t2] = force;
      // Descriptor/partial consumers of the center: radial descriptors read
      // the radial list; angular descriptors and the directed partial read
      // the angular list.
      radii.r_dep_pair[static_cast<std::size_t>(t1) * num_types + t2] =
          std::max(radial_reach(t1, t2), angular_reach(t1, t2));
      radii.r_force_max = std::max(radii.r_force_max, force);
      radii.r_dep_max =
          std::max(radii.r_dep_max,
                   radii.r_dep_pair[static_cast<std::size_t>(t1) * num_types + t2]);
    }
  }
  radii.d_dep = radii.r_force_max + kNeighborSkin;
  // Two-hop closure: owned i consumes dependency center j within R_force, and
  // j's descriptor/partial consumes candidate k within R_dep. The +2*skin
  // covers the drift of both edges between halo-membership re-evaluations
  // (which happen only at global neighbor rebuilds).
  double chain_max = 0.0;
  for (int ti = 0; ti < num_types; ++ti) {
    for (int tj = 0; tj < num_types; ++tj) {
      const double hop1 =
          radii.r_force_pair[static_cast<std::size_t>(ti) * num_types + tj];
      for (int tk = 0; tk < num_types; ++tk) {
        const double hop2 =
            radii.r_dep_pair[static_cast<std::size_t>(tj) * num_types + tk];
        chain_max = std::max(chain_max, hop1 + hop2);
      }
    }
  }
  radii.d_coord = chain_max + 2.0 * kNeighborSkin;
  radii.proven = std::isfinite(radii.d_dep) && std::isfinite(radii.d_coord) &&
                 radii.d_dep > 0.0 && radii.d_coord >= radii.d_dep;
  return radii;
}

// ---------------------------------------------------------------------------
// Eligibility (plan section 3.3).
// ---------------------------------------------------------------------------

struct DomainEligibility {
  bool eligible = false;
  int axis = -1;               // partition axis, -1 at P=1 (M1 keeps its own rule)
  double d_dep = 0.0;
  double d_coord = 0.0;
  double axis_thickness = 0.0;
  double slab_width = 0.0;
  std::string reason;          // stable machine-readable token
};

// Face area |b x c| etc. and thickness = volume / area, matching the pinned
// Box::get_area / get_expanded_box formulas exactly (orthogonal or not).
[[nodiscard]] inline std::array<double, 3> box_thickness(
    const std::array<double, 9>& h)
{
  const double a[3] = {h[0], h[3], h[6]};
  const double b[3] = {h[1], h[4], h[7]};
  const double c[3] = {h[2], h[5], h[8]};
  const auto cross_norm = [](const double* u, const double* v) {
    const double s1 = u[1] * v[2] - u[2] * v[1];
    const double s2 = u[2] * v[0] - u[0] * v[2];
    const double s3 = u[0] * v[1] - u[1] * v[0];
    return std::sqrt(s1 * s1 + s2 * s2 + s3 * s3);
  };
  const double volume = std::abs(
      h[0] * (h[4] * h[8] - h[5] * h[7]) + h[1] * (h[5] * h[6] - h[3] * h[8]) +
      h[2] * (h[3] * h[7] - h[4] * h[6]));
  std::array<double, 3> area{cross_norm(b, c), cross_norm(c, a), cross_norm(a, b)};
  std::array<double, 3> thickness{};
  for (int d = 0; d < 3; ++d) {
    if (!(area[static_cast<std::size_t>(d)] > 0.0)) {
      throw std::runtime_error("box face area is degenerate");
    }
    thickness[static_cast<std::size_t>(d)] = volume / area[static_cast<std::size_t>(d)];
  }
  return thickness;
}

// The pinned large-box criterion (get_expanded_box): any periodic direction
// with thickness <= 2.5*(rc_radial_max + 1) selects the small-box image path,
// which M2a does not support.
[[nodiscard]] inline bool box_is_large_box(
    const std::array<double, 3>& thickness, double rc_radial_max)
{
  const double limit = 2.5 * (rc_radial_max + 1.0);
  return thickness[0] > limit && thickness[1] > limit && thickness[2] > limit;
}

// M2a eligibility for an orthogonal, fully periodic box. The caller has
// already rejected triclinic / non-periodic inputs at P>1 with the M1
// unsupported errors (that behavior is unchanged); this function only decides
// between the M2a local-domain path and the M1 replicated-full fallback.
[[nodiscard]] inline DomainEligibility evaluate_domain_eligibility(
    int world_size,
    const std::array<double, 9>& h,
    const DomainRadii& radii,
    double rc_radial_max)
{
  DomainEligibility decision;
  decision.d_dep = radii.d_dep;
  decision.d_coord = radii.d_coord;
  if (world_size <= 1) {
    decision.reason = "p1";
    return decision;
  }
  if (!radii.proven) {
    decision.reason = "unproven-radii";
    return decision;
  }
  const std::array<double, 3> thickness = box_thickness(h);
  if (!box_is_large_box(thickness, rc_radial_max)) {
    decision.reason = "small-box";
    return decision;
  }
  decision.axis = longest_box_axis(h);
  decision.axis_thickness = thickness[static_cast<std::size_t>(decision.axis)];
  decision.slab_width = decision.axis_thickness / world_size;
  if (decision.slab_width < radii.d_coord) {
    decision.reason = "slab-width-below-d-coord";
    return decision;
  }
  decision.eligible = true;
  decision.reason = "eligible";
  return decision;
}

// ---------------------------------------------------------------------------
// Local layout (plan sections 3.2, 7).
// ---------------------------------------------------------------------------

// Full per-atom record for a rank-owned atom. Static fields (type, mass,
// charge, group labels) originate from the immutable host identity model;
// dynamic fields migrate with the atom.
struct DomainAtomRecord {
  std::uint64_t global_id = 0;
  int type = 0;
  double mass = 0.0;
  float charge = 0.0f;
  std::array<double, 3> position{};
  std::array<double, 3> velocity{};
  std::array<double, 3> unwrapped{};
  std::vector<int> group_labels;  // one label per grouping method
};

// One atom received from a face's membership exchange.
struct GhostCandidate {
  std::uint64_t global_id = 0;
  int type = 0;
  std::array<double, 3> position{};
  int face = 0;          // 0 = left face, 1 = right face
  int source_rank = 0;
  int stream_index = 0;  // position within the face's receive stream
};

struct GhostSlotInfo {
  std::uint64_t global_id = 0;
  int type = 0;
  int face = 0;
  int source_rank = 0;
  int image_shift = 0;  // in box lengths along the axis: -1, 0 or +1 (metadata;
                        // stored positions stay wrapped and MIC handles all
                        // distances, so the shift never enters the kernels)
  bool dependency = false;
  std::array<double, 3> position{};
  int stream_index = 0;  // index in the sender's face send list
};

// Face numbering used everywhere: 0 = left (towards rank-1), 1 = right
// (towards rank+1), with periodic wrap at both ends.
inline constexpr int kFaceLeft = 0;
inline constexpr int kFaceRight = 1;

[[nodiscard]] inline int face_peer(int face, int rank, int world_size)
{
  if (face == kFaceLeft) return (rank - 1 + world_size) % world_size;
  return (rank + 1) % world_size;
}

// MIC distance from an axis coordinate to this rank's half-open slab
// [slab_lo, slab_hi); the wrapped candidate is considered for the periodic
// ends, matching how apply_mic would shorten the actual pair distances.
[[nodiscard]] inline double distance_to_slab(
    double x, double slab_lo, double slab_hi, double axis_length)
{
  if (x >= slab_lo && x <= slab_hi) return 0.0;
  if (x < slab_lo) return std::min(slab_lo - x, (x + axis_length) - slab_hi);
  return std::min(x - slab_hi, (slab_lo + axis_length) - x);
}

struct LocalLayout {
  int rank = 0;
  int world_size = 1;
  int axis = 0;
  double axis_length = 0.0;
  double slab_lo = 0.0;
  double slab_hi = 0.0;
  double d_dep = 0.0;
  double d_coord = 0.0;
  std::vector<DomainAtomRecord> owned;          // global_id ascending
  std::vector<GhostSlotInfo> dependency_ghosts; // (face, source, gid) order
  std::vector<GhostSlotInfo> coordinate_ghosts; // same order

  [[nodiscard]] std::size_t owned_count() const noexcept { return owned.size(); }
  [[nodiscard]] std::size_t dep_ghost_count() const noexcept
  {
    return dependency_ghosts.size();
  }
  [[nodiscard]] std::size_t coord_ghost_count() const noexcept
  {
    return coordinate_ghosts.size();
  }
  [[nodiscard]] std::size_t local_count() const noexcept
  {
    return owned.size() + dependency_ghosts.size() + coordinate_ghosts.size();
  }
  [[nodiscard]] std::size_t dependency_end() const noexcept
  {
    return owned_count() + dep_ghost_count();
  }
};

// Builds the rank-local slot layout. `owned` must already be sorted by
// global_id ascending (the caller derives it that way); the ghost sections
// are classified by MIC distance to the slab and ordered by
// (face, source_rank, global_id). Image shifts are face metadata only.
// Throws on structural violations (duplicate global IDs, candidates outside
// the d_coord band, owned entries out of order).
[[nodiscard]] inline LocalLayout build_local_layout(
    std::vector<DomainAtomRecord> owned,
    const std::vector<GhostCandidate>& left_candidates,
    const std::vector<GhostCandidate>& right_candidates,
    int rank,
    int world_size,
    int axis,
    double axis_length,
    double d_dep,
    double d_coord)
{
  if (world_size <= 0 || rank < 0 || rank >= world_size || axis < 0 || axis > 2) {
    throw std::invalid_argument("invalid domain layout geometry");
  }
  LocalLayout layout;
  layout.rank = rank;
  layout.world_size = world_size;
  layout.axis = axis;
  layout.axis_length = axis_length;
  layout.d_dep = d_dep;
  layout.d_coord = d_coord;
  const double slab_lo = (static_cast<double>(rank) / world_size) * axis_length;
  const double slab_hi =
      (static_cast<double>(rank + 1) / world_size) * axis_length;
  layout.slab_lo = slab_lo;
  layout.slab_hi = slab_hi;

  for (std::size_t index = 1; index < owned.size(); ++index) {
    if (owned[index - 1].global_id >= owned[index].global_id) {
      throw std::invalid_argument("owned records must be global_id ascending");
    }
  }
  layout.owned = std::move(owned);

  std::vector<const GhostCandidate*> candidates;
  candidates.reserve(left_candidates.size() + right_candidates.size());
  for (const GhostCandidate& candidate : left_candidates) {
    if (candidate.face != kFaceLeft) {
      throw std::invalid_argument("left stream carries a right-face candidate");
    }
    candidates.push_back(&candidate);
  }
  for (const GhostCandidate& candidate : right_candidates) {
    if (candidate.face != kFaceRight) {
      throw std::invalid_argument("right stream carries a left-face candidate");
    }
    candidates.push_back(&candidate);
  }
  // Stable order (face, source_rank, global_id); stream indices stay unique
  // per face because the sender's send list is global_id ordered.
  std::stable_sort(
      candidates.begin(), candidates.end(),
      [](const GhostCandidate* left, const GhostCandidate* right) {
        if (left->face != right->face) return left->face < right->face;
        if (left->source_rank != right->source_rank)
          return left->source_rank < right->source_rank;
        return left->global_id < right->global_id;
      });

  std::vector<std::uint64_t> seen_ids;
  seen_ids.reserve(layout.owned.size() + candidates.size());
  for (const DomainAtomRecord& record : layout.owned) {
    seen_ids.push_back(record.global_id);
  }
  // The owned prefix is global_id-sorted (binary search); appended ghost IDs
  // are checked linearly. Each global atom may occupy at most one local slot:
  // the send-list de-duplication at P=2 and the exclusive slab ownership make
  // this an invariant, so a violation is a protocol bug, not a geometry.
  const auto note_id = [&](std::uint64_t global_id) {
    if (std::binary_search(seen_ids.begin(),
                           seen_ids.begin() + static_cast<std::ptrdiff_t>(layout.owned.size()),
                           global_id) ||
        std::find(seen_ids.begin() + static_cast<std::ptrdiff_t>(layout.owned.size()),
                  seen_ids.end(), global_id) != seen_ids.end()) {
      throw std::invalid_argument(
          "a global atom appears more than once in the local layout");
    }
    seen_ids.push_back(global_id);
  };

  for (const GhostCandidate* candidate : candidates) {
    const double distance = distance_to_slab(
        candidate->position[static_cast<std::size_t>(axis)], slab_lo, slab_hi,
        axis_length);
    if (distance > d_coord) {
      // The sender bands every atom it sends within d_coord of the shared
      // boundary and the MIC distance can only shrink, so this is a
      // protocol bug rather than a legitimate geometry.
      throw std::invalid_argument(
          "ghost candidate lies outside the coordinate halo band");
    }
    GhostSlotInfo ghost;
    ghost.global_id = candidate->global_id;
    ghost.type = candidate->type;
    ghost.face = candidate->face;
    ghost.source_rank = candidate->source_rank;
    ghost.position = candidate->position;
    ghost.stream_index = candidate->stream_index;
    ghost.dependency = distance <= d_dep;
    ghost.image_shift = 0;
    if (candidate->face == kFaceLeft && rank == 0) ghost.image_shift = -1;
    if (candidate->face == kFaceRight && rank == world_size - 1) {
      ghost.image_shift = +1;
    }
    note_id(ghost.global_id);
    if (ghost.dependency) {
      layout.dependency_ghosts.push_back(ghost);
    } else {
      layout.coordinate_ghosts.push_back(ghost);
    }
  }
  return layout;
}

// ---------------------------------------------------------------------------
// Halo exchange plans (plan section 6.1 / 7).
// ---------------------------------------------------------------------------

// Membership record carried by the rebuild-time face exchange: 40 bytes per
// atom (global id, type, wrapped position). Per-step refresh exchanges only
// the 24-byte position.
#pragma pack(push, 8)
struct GhostMembershipRecord {
  std::uint64_t global_id;
  std::int32_t type;
  std::int32_t reserved;
  double position[3];
};
#pragma pack(pop)
static_assert(sizeof(GhostMembershipRecord) == 40, "membership record layout");

struct FaceExchangePlan {
  int peer = -1;
  std::vector<int> send_slots;  // owned slots in the face's send list, ascending
  std::vector<int> recv_slots;  // ghost slot per received stream index
};

struct ExchangePlan {
  FaceExchangePlan face[2];  // [kFaceLeft], [kFaceRight]
  bool peers_share_rank() const { return face[0].peer == face[1].peer; }
};

// Send lists: owned atoms within d_coord of a peer's slab, in ascending
// slot (= global_id) order. The left list carries atoms the left peer needs
// as its right-face ghosts and the right list atoms the right peer needs as
// its left-face ghosts. The band test is the MIC distance to the peer's slab
// (distance_to_slab), NOT a raw coordinate band: the wrap kernel only applies
// a single <0 / >1 adjustment, so an atom displaced more than one box length
// in a step legally keeps an out-of-box coordinate, and a raw band test would
// silently drop it from both faces while a neighbor still consumes it. At
// P=2 both faces reach the same peer; an atom needed by both (possible when
// the two bands overlap) is sent only on the left face, so the peer imports
// every atom exactly once -- with wrapped storage and minimum-image distances
// a single slot serves both image roles.
[[nodiscard]] inline ExchangePlan build_exchange_plan(const LocalLayout& layout)
{
  ExchangePlan plan;
  for (int face = 0; face < 2; ++face) {
    plan.face[face].peer = face_peer(face, layout.rank, layout.world_size);
  }
  const double slab_width = layout.axis_length / layout.world_size;
  const double left_lo = (static_cast<double>(plan.face[kFaceLeft].peer) /
                         layout.world_size) * layout.axis_length;
  const double left_hi = left_lo + slab_width;
  const double right_lo = (static_cast<double>(plan.face[kFaceRight].peer) /
                          layout.world_size) * layout.axis_length;
  const double right_hi = right_lo + slab_width;
  std::vector<char> in_left(layout.owned_count(), 0);
  for (std::size_t slot = 0; slot < layout.owned_count(); ++slot) {
    const double x = layout.owned[slot].position[static_cast<std::size_t>(layout.axis)];
    if (distance_to_slab(x, left_lo, left_hi, layout.axis_length) < layout.d_coord) {
      in_left[slot] = 1;
    }
  }
  for (std::size_t slot = 0; slot < layout.owned_count(); ++slot) {
    const double x = layout.owned[slot].position[static_cast<std::size_t>(layout.axis)];
    const bool near_right =
        distance_to_slab(x, right_lo, right_hi, layout.axis_length) < layout.d_coord;
    if (near_right && !(plan.peers_share_rank() && in_left[slot])) {
      plan.face[kFaceRight].send_slots.push_back(static_cast<int>(slot));
    }
  }
  for (std::size_t slot = 0; slot < layout.owned_count(); ++slot) {
    if (in_left[slot]) {
      plan.face[kFaceLeft].send_slots.push_back(static_cast<int>(slot));
    }
  }
  // Receive maps: local ghost slot addressed by (face, stream index). Every
  // ghost slot must appear exactly once, or the per-step refresh would miss
  // (or double-write) a position.
  std::array<std::vector<int>, 2> recv;
  const auto map_ghost = [&](const GhostSlotInfo& ghost, std::size_t slot) {
    std::vector<int>& stream = recv[static_cast<std::size_t>(ghost.face)];
    if (static_cast<std::size_t>(ghost.stream_index) >= stream.size()) {
      stream.resize(static_cast<std::size_t>(ghost.stream_index) + 1, -1);
    }
    if (stream[static_cast<std::size_t>(ghost.stream_index)] != -1) {
      throw std::invalid_argument("duplicate (face, stream index) in the layout");
    }
    stream[static_cast<std::size_t>(ghost.stream_index)] = static_cast<int>(slot);
  };
  for (std::size_t index = 0; index < layout.dependency_ghosts.size(); ++index) {
    map_ghost(layout.dependency_ghosts[index], layout.owned_count() + index);
  }
  for (std::size_t index = 0; index < layout.coordinate_ghosts.size(); ++index) {
    map_ghost(layout.coordinate_ghosts[index], layout.dependency_end() + index);
  }
  for (int face = 0; face < 2; ++face) {
    for (int slot : recv[static_cast<std::size_t>(face)]) {
      if (slot < 0) {
        throw std::invalid_argument("a face receive stream has holes");
      }
      plan.face[face].recv_slots.push_back(slot);
    }
  }
  return plan;
}

// Structural validation of an exchange plan (CPU-testable malformed-plan
// rejection). `ghost_count` is local_count - owned_count.
inline void validate_exchange_plan(
    const ExchangePlan& plan,
    std::size_t owned_count,
    std::size_t local_count,
    int rank,
    int world_size)
{
  if (world_size <= 1) {
    throw std::invalid_argument("exchange plans require world_size > 1");
  }
  if (rank < 0 || rank >= world_size) {
    throw std::invalid_argument("exchange plan rank is outside the world");
  }
  if (local_count < owned_count) {
    throw std::invalid_argument("local_count is below owned_count");
  }
  for (int face = 0; face < 2; ++face) {
    const FaceExchangePlan& side = plan.face[face];
    if (side.peer < 0 || side.peer >= world_size || side.peer == rank) {
      throw std::invalid_argument("exchange plan peer is invalid");
    }
    int previous = -1;
    for (int slot : side.send_slots) {
      if (slot <= previous) {
        throw std::invalid_argument("send slots must be strictly ascending");
      }
      if (slot < 0 || static_cast<std::size_t>(slot) >= owned_count) {
        throw std::invalid_argument("send slot is outside the owned prefix");
      }
      previous = slot;
    }
    for (int slot : side.recv_slots) {
      if (slot < 0 || static_cast<std::size_t>(slot) >= local_count ||
          static_cast<std::size_t>(slot) < owned_count) {
        throw std::invalid_argument("recv slot is outside the ghost section");
      }
    }
  }
  if (world_size == 2 && !plan.peers_share_rank()) {
    throw std::invalid_argument("P=2 must address the same peer on both faces");
  }
  if (world_size > 2 && plan.peers_share_rank()) {
    throw std::invalid_argument("distinct faces must address distinct peers");
  }
  // Every ghost slot must be refreshed exactly once per step.
  std::vector<int> seen;
  for (int face = 0; face < 2; ++face) {
    for (int slot : plan.face[face].recv_slots) seen.push_back(slot);
  }
  std::sort(seen.begin(), seen.end());
  if (std::adjacent_find(seen.begin(), seen.end()) != seen.end()) {
    throw std::invalid_argument("a ghost slot is refreshed by both faces");
  }
  if (seen.size() != local_count - owned_count) {
    throw std::invalid_argument("refresh plan does not cover every ghost slot");
  }
}

// ---------------------------------------------------------------------------
// Migration routing (plan section 8).
// ---------------------------------------------------------------------------

// Final owner of a wrapped (or wrap-compatible) fractional coordinate along
// the partition axis. One step may cross any number of slabs, including the
// periodic ends; the single <0 / >1 adjustment mirrors wrap_positions.
[[nodiscard]] inline int migration_owner(double fractional, int world_size)
{
  return slab_owner_of_fractional(fractional, world_size);
}

struct MigrationPlan {
  std::vector<int> send_counts;   // atoms per destination rank
  std::vector<int> recv_counts;   // atoms per source rank (filled by the caller
                                  // from the Alltoall handshake)
  std::vector<std::vector<int>> outgoing_slots;  // per destination, ascending
  int staying_count = 0;
};

// Routes every owned atom to its final owner from the wrapped positions.
// Only atoms whose owner differs from this rank are routed; the rest stay.
[[nodiscard]] inline MigrationPlan plan_migration(
    const std::vector<double>& owned_axis_fractional,
    int rank,
    int world_size)
{
  if (world_size <= 0 || rank < 0 || rank >= world_size) {
    throw std::invalid_argument("invalid migration rank or world size");
  }
  MigrationPlan plan;
  plan.send_counts.assign(static_cast<std::size_t>(world_size), 0);
  plan.recv_counts.assign(static_cast<std::size_t>(world_size), 0);
  plan.outgoing_slots.assign(static_cast<std::size_t>(world_size), {});
  for (std::size_t slot = 0; slot < owned_axis_fractional.size(); ++slot) {
    const int owner =
        migration_owner(owned_axis_fractional[slot], world_size);
    if (owner == rank) {
      ++plan.staying_count;
    } else {
      plan.send_counts[static_cast<std::size_t>(owner)] += 1;
      plan.outgoing_slots[static_cast<std::size_t>(owner)]
          .push_back(static_cast<int>(slot));
    }
  }
  return plan;
}

inline void validate_migration_plan(
    const MigrationPlan& plan,
    std::size_t owned_count,
    int rank,
    int world_size)
{
  if (plan.send_counts.size() != static_cast<std::size_t>(world_size) ||
      plan.outgoing_slots.size() != static_cast<std::size_t>(world_size)) {
    throw std::invalid_argument("migration plan is not world-sized");
  }
  std::vector<char> seen(owned_count, 0);
  std::size_t total_out = 0;
  for (int destination = 0; destination < world_size; ++destination) {
    const std::vector<int>& slots =
        plan.outgoing_slots[static_cast<std::size_t>(destination)];
    if (static_cast<std::size_t>(plan.send_counts[static_cast<std::size_t>(
            destination)]) != slots.size()) {
      throw std::invalid_argument("migration count disagrees with its slots");
    }
    if (destination == rank && !slots.empty()) {
      throw std::invalid_argument("migration routes atoms to their own rank");
    }
    int previous = -1;
    for (int slot : slots) {
      if (slot <= previous) {
        throw std::invalid_argument("migration slots must be ascending");
      }
      if (slot < 0 || static_cast<std::size_t>(slot) >= owned_count) {
        throw std::invalid_argument("migration slot is outside the owned set");
      }
      if (seen[static_cast<std::size_t>(slot)]) {
        throw std::invalid_argument("an owned atom is routed twice");
      }
      seen[static_cast<std::size_t>(slot)] = 1;
      previous = slot;
    }
    total_out += slots.size();
  }
  if (total_out + static_cast<std::size_t>(plan.staying_count) != owned_count) {
    throw std::invalid_argument("migration does not account for every atom");
  }
}

}  // namespace dmgmd
