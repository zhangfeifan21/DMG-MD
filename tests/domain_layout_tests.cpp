// CPU unit tests for the M2a local-domain layout logic
// (include/dmgmd/domain_layout.hpp). Everything here is pure host code: no
// MPI, no CUDA. The cases mirror the M2a plan checklist: typewise radii,
// eligibility and fallback reasons, slab boundary rules, ghost classification
// (s=0 / s=1 / exactly at the slab boundary), P=2 same-peer de-duplication,
// empty ranks and N < P, deterministic slot order, malformed plan rejection,
// and one-step multi-slab migration routing.
#include "dmgmd/domain_layout.hpp"
#include "dmgmd/spatial_ownership.hpp"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using namespace dmgmd;

void require(bool condition, const char* message)
{
  if (!condition) throw std::runtime_error(message);
}

DomainAtomRecord make_record(std::uint64_t gid, double x, int type = 0)
{
  DomainAtomRecord record;
  record.global_id = gid;
  record.type = type;
  record.mass = 12.0;
  record.charge = 0.0f;
  record.position = {x, 0.0, 0.0};
  record.velocity = {0.0, 0.0, 0.0};
  record.unwrapped = {x, 0.0, 0.0};
  return record;
}

GhostCandidate make_candidate(std::uint64_t gid, double x, int face, int source_rank)
{
  GhostCandidate candidate;
  candidate.global_id = gid;
  candidate.type = 0;
  candidate.position = {x, 0.0, 0.0};
  candidate.face = face;
  candidate.source_rank = source_rank;
  candidate.stream_index = 0;  // fixed up by the caller
  return candidate;
}

// ---------------------------------------------------------------------------
// Typewise radii.
// ---------------------------------------------------------------------------

void check_radii_single_type()
{
  // nep_C.txt-like single-type potential: rc_radial = 7, rc_angular = 4.
  CutoffSet cutoffs;
  cutoffs.num_types = 1;
  cutoffs.rc_radial = {7.0};
  cutoffs.rc_angular = {4.0};
  const DomainRadii radii = compute_domain_radii(cutoffs);
  require(radii.proven, "single-type radii must be provable");
  // Radial reach 7 dominates: the angular list is a subset of the radial
  // list by the radial-first filter (min(7, 4) = 4 <= 7).
  require(radii.r_force_pair[0] == 7.0, "R_force must equal the typewise radial reach");
  require(radii.r_dep_pair[0] == 7.0, "R_dep must equal the typewise radial reach");
  require(radii.d_dep == 8.0, "d_dep = max R_force + skin");
  require(radii.d_coord == 16.0, "d_coord = max chain + 2*skin");
}

void check_radii_typewise_pairs()
{
  // Two types with different radial cutoffs: reaches are pair averages.
  CutoffSet cutoffs;
  cutoffs.num_types = 2;
  cutoffs.rc_radial = {3.0, 5.0};
  cutoffs.rc_angular = {6.0, 6.0};
  const DomainRadii radii = compute_domain_radii(cutoffs);
  require(radii.r_force_pair[0] == 3.0, "type pair (0,0) reach");
  require(radii.r_force_pair[1] == 4.0, "type pair (0,1) reach is the average");
  require(radii.r_force_pair[3] == 5.0, "type pair (1,1) reach");
  require(radii.r_force_max == 5.0, "max reach");
  require(radii.d_dep == 6.0, "d_dep with mixed types");
  // Chain (1,1)+(1,1) = 5 + 5 = 10 -> d_coord = 12.
  require(radii.d_coord == 12.0, "d_coord takes the max typewise chain");
  require(radii.proven, "typewise radii must be provable");
}

void check_radii_match_float_kernel_rounding()
{
  CutoffSet cutoffs;
  cutoffs.num_types = 2;
  // Mirror the loader: values are parsed into float and then exposed to the
  // CPU radius proof as doubles.
  cutoffs.rc_radial = {static_cast<double>(1.0f), static_cast<double>(1.4f)};
  cutoffs.rc_angular = {2.0, 2.0};
  const DomainRadii radii = compute_domain_radii(cutoffs);
  require(radii.proven, "mixed float cutoffs must remain provable");
  const double exact_average = 0.5 * (cutoffs.rc_radial[0] + cutoffs.rc_radial[1]);
  const float kernel_average =
      (static_cast<float>(cutoffs.rc_radial[0]) +
       static_cast<float>(cutoffs.rc_radial[1])) * 0.5f;
  const double bound = radii.r_force_pair[1];
  require(static_cast<double>(kernel_average) > exact_average,
          "fixture must exercise upward float rounding");
  require(bound > static_cast<double>(kernel_average),
          "halo reach must conservatively exceed the float kernel cutoff");
}

void check_radii_malformed_fails_closed()
{
  CutoffSet broken;
  broken.num_types = 0;
  require(!compute_domain_radii(broken).proven, "zero types must fail closed");
  CutoffSet nan_cutoffs;
  nan_cutoffs.num_types = 1;
  nan_cutoffs.rc_radial = {std::nan("")};
  nan_cutoffs.rc_angular = {4.0};
  require(!compute_domain_radii(nan_cutoffs).proven, "NaN cutoffs must fail closed");
}

// ---------------------------------------------------------------------------
// Eligibility.
// ---------------------------------------------------------------------------

const std::array<double, 9> kBox64x24x24 = {64, 0, 0, 0, 24, 0, 0, 0, 24};

void check_eligibility_reasons()
{
  CutoffSet cutoffs;
  cutoffs.num_types = 1;
  cutoffs.rc_radial = {7.0};
  cutoffs.rc_angular = {4.0};
  const DomainRadii radii = compute_domain_radii(cutoffs);

  // P=1 never enters M2a.
  DomainEligibility single = evaluate_domain_eligibility(1, kBox64x24x24, radii, 7.0);
  require(!single.eligible && single.reason == "p1", "P=1 must stay on the M1 path");

  // 64x24x24 at P=4: axis x, slab width 16 >= d_coord 16 -> eligible.
  DomainEligibility eligible = evaluate_domain_eligibility(4, kBox64x24x24, radii, 7.0);
  require(eligible.eligible, "64x24x24 at P=4 must be eligible");
  require(eligible.axis == 0, "longest edge x is the partition axis");
  require(eligible.slab_width == 16.0, "slab width is thickness / P");
  require(eligible.reason == "eligible", "eligible reason token");

  // Small box (any periodic thickness <= 2.5*(rc+1) = 20): fallback. A 16 A
  // cube is a small box even at P=2 (the NEP small-box image path would be
  // required).
  const std::array<double, 9> small = {16, 0, 0, 0, 16, 0, 0, 0, 16};
  DomainEligibility small_box = evaluate_domain_eligibility(2, small, radii, 7.0);
  require(!small_box.eligible && small_box.reason == "small-box",
          "16 A cube must fall back via the large-box criterion");

  // A 24 A cube IS a large box by the NEP criterion (24 > 20) but its
  // 12 A slabs at P=2 are below d_coord: the slab-width guard catches it.
  const std::array<double, 9> cube24 = {24, 0, 0, 0, 24, 0, 0, 0, 24};
  DomainEligibility cube24_case = evaluate_domain_eligibility(2, cube24, radii, 7.0);
  require(!cube24_case.eligible && cube24_case.reason == "slab-width-below-d-coord",
          "24 A cube at P=2 falls back via the slab-width guard");

  // Large box but slab width below d_coord: 48x24x24 at P=4 -> slab 12 < 16.
  const std::array<double, 9> narrow = {48, 0, 0, 0, 24, 0, 0, 0, 24};
  DomainEligibility narrow_slab = evaluate_domain_eligibility(4, narrow, radii, 7.0);
  require(!narrow_slab.eligible && narrow_slab.reason == "slab-width-below-d-coord",
          "slab width below d_coord must fall back");

  // Cube -> axis y by the reference tie rule.
  const std::array<double, 9> cube = {40, 0, 0, 0, 40, 0, 0, 0, 40};
  DomainEligibility cube_case = evaluate_domain_eligibility(2, cube, radii, 7.0);
  require(cube_case.eligible && cube_case.axis == 1, "cube picks axis y");

  // Unproven radii fail closed to M1 for any box.
  const DomainRadii unproven{};
  DomainEligibility closed = evaluate_domain_eligibility(4, kBox64x24x24, unproven, 7.0);
  require(!closed.eligible && closed.reason == "unproven-radii",
          "unproven radii must fail closed to M1");
}

// ---------------------------------------------------------------------------
// Slab boundaries, ghost classification, deterministic layout.
// ---------------------------------------------------------------------------

LocalLayout build_simple_layout()
{
  // P=4 over a 64 A axis: slabs [0,16), [16,32), [32,48), [48,64); d_dep 8,
  // d_coord 16. Rank 1 owns two atoms (gids 10, 20 inside [16,32)) and
  // receives: a left dependency ghost at 14 (distance 2), a left coordinate
  // ghost at 1 (distance 15 via the direct gap; wrap distance 49), a right
  // dependency ghost at 36 (distance 4), and a right coordinate ghost at
  // 47.5 (distance 15.5).
  std::vector<DomainAtomRecord> owned;
  owned.push_back(make_record(10, 20.0));
  owned.push_back(make_record(20, 30.0));
  std::vector<GhostCandidate> left;
  left.push_back(make_candidate(5, 14.0, kFaceLeft, 0));
  left.push_back(make_candidate(6, 1.0, kFaceLeft, 0));
  std::vector<GhostCandidate> right;
  right.push_back(make_candidate(7, 36.0, kFaceRight, 2));
  right.push_back(make_candidate(8, 47.5, kFaceRight, 2));
  left[0].stream_index = 0;
  left[1].stream_index = 1;
  right[0].stream_index = 0;
  right[1].stream_index = 1;
  return build_local_layout(std::move(owned), left, right, 1, 4, 0, 64.0, 8.0, 16.0);
}

void check_layout_classification_and_order()
{
  const LocalLayout layout = build_simple_layout();
  require(layout.owned_count() == 2, "owned count");
  require(layout.owned[0].global_id == 10 && layout.owned[1].global_id == 20,
          "owned slots stay in their global_id ascending order");
  require(layout.dep_ghost_count() == 2, "two dependency ghosts (14.0 and 36.0)");
  require(layout.coord_ghost_count() == 2, "two coordinate ghosts (1.0 and 47.5)");
  require(layout.local_count() == 6, "local_count = owned + ghosts");
  // Ghost sections are (face, source, gid) ordered: left dep before right dep.
  require(layout.dependency_ghosts[0].global_id == 5 &&
              layout.dependency_ghosts[0].face == kFaceLeft,
          "left dependency ghost first");
  require(layout.dependency_ghosts[1].global_id == 7 &&
              layout.dependency_ghosts[1].face == kFaceRight,
          "right dependency ghost second");
  require(layout.coordinate_ghosts[0].global_id == 6, "left coordinate ghost");
  require(layout.coordinate_ghosts[1].global_id == 8, "right coordinate ghost");
  // Image-shift metadata: interior ranks carry no wrap; the ends would.
  require(layout.dependency_ghosts[0].image_shift == 0,
          "interior faces carry no image shift");
  // Determinism: same inputs rebuild the identical slot sequence.
  const LocalLayout again = build_simple_layout();
  require(again.local_count() == layout.local_count(), "deterministic local_count");
  for (std::size_t slot = 0; slot < again.local_count(); ++slot) {
    const std::uint64_t a = slot < again.owned_count()
                                 ? again.owned[slot].global_id
                                 : (slot < again.dependency_end()
                                        ? again.dependency_ghosts[slot - again.owned_count()].global_id
                                        : again.coordinate_ghosts[slot - again.dependency_end()].global_id);
    const std::uint64_t b = slot < layout.owned_count()
                                ? layout.owned[slot].global_id
                                : (slot < layout.dependency_end()
                                       ? layout.dependency_ghosts[slot - layout.owned_count()].global_id
                                       : layout.coordinate_ghosts[slot - layout.dependency_end()].global_id);
    require(a == b, "slot order is a deterministic function of the inputs");
  }
}

void check_boundary_classification()
{
  // Exactly at d_dep: dependency; exactly at d_coord: coordinate-only;
  // just beyond the band is a protocol error. Rank 0 over [0, 16).
  std::vector<DomainAtomRecord> owned;
  owned.push_back(make_record(1, 8.0));
  auto classify = [&](double x) {
    std::vector<GhostCandidate> right;
    right.push_back(make_candidate(2, x, kFaceRight, 1));
    right[0].stream_index = 0;
    return build_local_layout(owned, {}, right, 0, 4, 0, 64.0, 8.0, 16.0);
  };
  require(classify(16.0 + 8.0).dep_ghost_count() == 1,
          "distance exactly d_dep is a dependency ghost");
  require(classify(16.0 + 8.0 + 1e-9).coord_ghost_count() == 1,
          "just beyond d_dep is coordinate-only");
  require(classify(16.0 + 16.0).coord_ghost_count() == 1,
          "distance exactly d_coord is a coordinate ghost");
  bool rejected = false;
  try {
    static_cast<void>(classify(16.0 + 16.0 + 1e-9));
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "beyond the d_coord band must be rejected");
  // s=0 and s=1 ownership edges (M1 equal-width fractional rules reused).
  require(migration_owner(0.0, 4) == 0, "s=0 belongs to slab 0");
  require(migration_owner(1.0, 4) == 3, "exact s=1 belongs to the last slab");
  require(migration_owner(0.25, 4) == 1, "interior boundary belongs to the right slab");
}

void check_layout_rejects_duplicates_and_disorder()
{
  std::vector<DomainAtomRecord> owned;
  owned.push_back(make_record(20, 20.0));
  owned.push_back(make_record(10, 30.0));
  owned.push_back(make_record(10, 22.0));
  bool rejected = false;
  try {
    LocalLayout bad = build_local_layout(owned, {}, {}, 1, 4, 0, 64.0, 8.0, 16.0);
    static_cast<void>(bad);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "non-ascending owned records must be rejected");

  std::vector<DomainAtomRecord> single;
  single.push_back(make_record(20, 20.0));
  std::vector<GhostCandidate> left;
  left.push_back(make_candidate(20, 14.0, kFaceLeft, 0));
  left[0].stream_index = 0;
  rejected = false;
  try {
    LocalLayout bad = build_local_layout(single, left, {}, 1, 4, 0, 64.0, 8.0, 16.0);
    static_cast<void>(bad);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "a ghost duplicating an owned global ID must be rejected");

  // A ghost appearing on both faces duplicates a slot and must be rejected.
  std::vector<GhostCandidate> both;
  both.push_back(make_candidate(5, 14.0, kFaceLeft, 0));
  both.push_back(make_candidate(5, 34.0, kFaceRight, 2));
  both[0].stream_index = 0;
  both[1].stream_index = 0;
  rejected = false;
  try {
    LocalLayout bad = build_local_layout(single, both, {}, 1, 4, 0, 64.0, 8.0, 16.0);
    static_cast<void>(bad);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "the same global atom on two faces must be rejected");
}

void check_empty_rank_and_n_less_than_p()
{
  // Rank 3 of 4 owns nothing and receives ghosts on both faces.
  std::vector<DomainAtomRecord> owned;  // empty: legal
  std::vector<GhostCandidate> left;
  left.push_back(make_candidate(1, 40.0, kFaceLeft, 2));
  left[0].stream_index = 0;
  std::vector<GhostCandidate> right;
  right.push_back(make_candidate(2, 64.0 + 1.0, kFaceRight, 0));
  right[0].stream_index = 0;
  const LocalLayout layout = build_local_layout(
      std::move(owned), left, right, 3, 4, 0, 64.0, 8.0, 16.0);
  require(layout.owned_count() == 0, "empty rank owns nothing");
  require(layout.local_count() == 2, "empty rank still addresses its ghosts");
  require(layout.dependency_ghosts.size() == 2, "both ghosts are within d_dep");
  // The periodic right face of the last rank carries the +1 image metadata.
  bool saw_wrap = false;
  for (const GhostSlotInfo& ghost : layout.dependency_ghosts) {
    if (ghost.face == kFaceRight && ghost.image_shift == 1) saw_wrap = true;
  }
  require(saw_wrap, "last-rank right-face ghosts carry image_shift +1");

  // N=2 atoms with P=4: ranks 2 and 3 own nothing at all.
  const std::vector<double> fractional = {0.05, 0.30};
  for (int rank = 0; rank < 4; ++rank) {
    const MigrationPlan plan = plan_migration(fractional, rank, 4);
    validate_migration_plan(plan, 2, rank, 4);
  }
  const MigrationPlan rank2 = plan_migration(fractional, 2, 4);
  require(rank2.staying_count == 0, "rank 2 of an N<P system owns nothing");

  // A rank can also have no owned atoms and no ghosts at all.  This is a
  // genuine logical local_count == 0, distinct from the empty-owned case
  // above, and its exchange plan must remain valid.
  const LocalLayout truly_empty = build_local_layout(
      {}, {}, {}, 2, 4, 0, 64.0, 8.0, 16.0);
  require(truly_empty.owned_count() == 0, "truly empty rank owns nothing");
  require(truly_empty.local_count() == 0, "truly empty rank has no addressable ghosts");
  const ExchangePlan empty_plan = build_exchange_plan(truly_empty);
  validate_exchange_plan(empty_plan, 0, 0, 2, 4);
}

// ---------------------------------------------------------------------------
// Exchange plans.
// ---------------------------------------------------------------------------

void check_exchange_plan_and_refresh_cover()
{
  const LocalLayout layout = build_simple_layout();
  const ExchangePlan plan = build_exchange_plan(layout);
  require(plan.face[kFaceLeft].peer == 0 && plan.face[kFaceRight].peer == 2,
          "P=4 peers are the neighboring ranks");
  // Owned atoms at x=20 and x=30, slab [16, 32), d_coord 16:
  // left band x < 32 (= 16 + 16): both; right band x > 0 (= 32 - 32): both.
  require(plan.face[kFaceLeft].send_slots.size() == 2, "send-left list size");
  require(plan.face[kFaceRight].send_slots.size() == 2, "send-right list size");
  require(plan.face[kFaceLeft].send_slots[0] == 0 &&
              plan.face[kFaceLeft].send_slots[1] == 1,
          "send slots ascend with the owned global IDs");
  // Refresh cover: every ghost slot appears exactly once across both faces.
  validate_exchange_plan(plan, layout.owned_count(), layout.local_count(), 1, 4);
  const int left_dep_slot = static_cast<int>(layout.owned_count());
  const int left_coord_slot = static_cast<int>(layout.dependency_end());
  require(plan.face[kFaceLeft].recv_slots.size() == 2, "left recv stream size");
  require(plan.face[kFaceLeft].recv_slots[0] == left_dep_slot &&
              plan.face[kFaceLeft].recv_slots[1] == left_coord_slot,
          "left recv slots follow the sender's global_id order");
  require(plan.face[kFaceRight].recv_slots.size() == 2, "right recv stream size");
  require(plan.face[kFaceRight].recv_slots[0] == left_dep_slot + 1 &&
              plan.face[kFaceRight].recv_slots[1] == left_coord_slot + 1,
          "right recv slots follow the sender's global_id order");
}

void check_p2_same_peer_dedup()
{
  // P=2 over a 32 A axis: slabs [0, 16) and [16, 32); d_coord 16 makes both
  // face bands of rank 1 overlap (x < 32 and x > 0 cover everything), so the
  // same-peer de-duplication must send each atom exactly once.
  std::vector<DomainAtomRecord> owned;
  owned.push_back(make_record(1, 17.0));
  owned.push_back(make_record(2, 25.0));
  const LocalLayout layout = build_local_layout(
      std::move(owned), {}, {}, 1, 2, 0, 32.0, 8.0, 16.0);
  const ExchangePlan plan = build_exchange_plan(layout);
  require(plan.face[kFaceLeft].peer == 0 && plan.face[kFaceRight].peer == 0,
          "P=2 addresses the same peer on both faces");
  require(plan.peers_share_rank(), "peers_share_rank");
  const std::size_t total_sends = plan.face[kFaceLeft].send_slots.size() +
                                  plan.face[kFaceRight].send_slots.size();
  require(total_sends == 2, "de-duplication keeps every atom on exactly one face");
  // The dedup rule keeps the left copy.
  require(plan.face[kFaceLeft].send_slots.size() == 2, "keep-left dedup rule");
  require(plan.face[kFaceRight].send_slots.empty(), "right face drops the duplicates");
  validate_exchange_plan(plan, layout.owned_count(), layout.local_count(), 1, 2);
}

void check_malformed_exchange_plans()
{
  const LocalLayout layout = build_simple_layout();
  ExchangePlan plan = build_exchange_plan(layout);

  ExchangePlan broken = plan;
  broken.face[kFaceLeft].send_slots = {1, 0};
  bool rejected = false;
  try {
    validate_exchange_plan(broken, layout.owned_count(), layout.local_count(), 1, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "descending send slots must be rejected");

  broken = plan;
  broken.face[kFaceLeft].send_slots = {0, 5};  // 5 is outside the owned prefix
  rejected = false;
  try {
    validate_exchange_plan(broken, layout.owned_count(), layout.local_count(), 1, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "send slots outside the owned prefix must be rejected");

  broken = plan;
  broken.face[kFaceLeft].recv_slots.push_back(0);  // owned slot 0 as a recv slot
  rejected = false;
  try {
    validate_exchange_plan(broken, layout.owned_count(), layout.local_count(), 1, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "a recv slot inside the owned prefix must be rejected");

  broken = plan;
  broken.face[kFaceLeft].recv_slots.pop_back();  // misses one ghost slot
  rejected = false;
  try {
    validate_exchange_plan(broken, layout.owned_count(), layout.local_count(), 1, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "a refresh plan missing a ghost slot must be rejected");

  broken = plan;
  broken.face[kFaceLeft].recv_slots.push_back(
      plan.face[kFaceRight].recv_slots.front());  // duplicate refresh
  rejected = false;
  try {
    validate_exchange_plan(broken, layout.owned_count(), layout.local_count(), 1, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "a ghost slot refreshed by both faces must be rejected");

  broken = plan;
  broken.face[kFaceLeft].peer = 1;  // self peer at P=4
  rejected = false;
  try {
    validate_exchange_plan(broken, layout.owned_count(), layout.local_count(), 1, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "a self peer must be rejected at P>2");

  // P=4 with identical peers on both faces is malformed.
  broken = plan;
  broken.face[kFaceRight].peer = 0;
  rejected = false;
  try {
    validate_exchange_plan(broken, layout.owned_count(), layout.local_count(), 1, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "identical peers must be rejected at P>2");
}

// ---------------------------------------------------------------------------
// Migration routing.
// ---------------------------------------------------------------------------

void check_migration_routing()
{
  // Rank 0 of 4 over [0, 16): atoms at fractional 0.1 (stays), 0.99
  // (interior boundary -> rank 3), 1.2 (periodic wrap -> rank 0 after the
  // single +(-1) adjustment: 0.2 -> rank 0... the wrap makes it stay), and a
  // multi-slab jump 0.9 -> 0.35-equivalent (one step crossing two slabs).
  const std::vector<double> fractional = {0.1, 0.99, 1.2, 0.55};
  const MigrationPlan plan = plan_migration(fractional, 0, 4);
  validate_migration_plan(plan, 4, 0, 4);
  // 0.1 stays on rank 0; 0.99 -> rank 3; 1.2 wraps to 0.2 -> stays; 0.55
  // belongs to rank 2 (a two-slab crossing in one step).
  require(plan.staying_count == 2, "staying atoms");
  require(plan.send_counts[3] == 1, "periodic neighbor receives one atom");
  require(plan.send_counts[2] == 1, "multi-slab destination receives one atom");
  require(plan.outgoing_slots[2] == std::vector<int>({3}),
          "outgoing slots ascend");

  // One step crossing more than one full box length is un-routable and the
  // runtime's symmetric gate must reject it before any collective.
  bool thrown = false;
  try {
    static_cast<void>(plan_migration({2.5}, 0, 4));
  } catch (const std::runtime_error&) {
    thrown = true;
  }
  require(thrown, "beyond-one-box displacement must be rejected");
}

void check_malformed_migration_plans()
{
  // Consistent base: two owned atoms, slot 0 stays, slot 1 migrates to rank 1.
  MigrationPlan plan;
  plan.send_counts = {0, 1, 0, 0};
  plan.recv_counts = {0, 0, 0, 0};
  plan.outgoing_slots = {{}, {1}, {}, {}};
  plan.staying_count = 1;
  validate_migration_plan(plan, 2, 0, 4);

  MigrationPlan broken = plan;
  broken.send_counts[2] = 5;  // count for a destination with no slots
  bool rejected = false;
  try {
    validate_migration_plan(broken, 2, 0, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "migration counts that disagree with their slots must be rejected");

  broken = plan;
  broken.outgoing_slots[1] = {1, 1};  // duplicate slot
  broken.send_counts[1] = 2;
  rejected = false;
  try {
    validate_migration_plan(broken, 2, 0, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "an atom routed twice must be rejected");

  broken = plan;
  broken.outgoing_slots[0] = {0};  // routing to the own rank
  broken.send_counts[0] = 1;
  rejected = false;
  try {
    validate_migration_plan(broken, 2, 0, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "routing to the own rank must be rejected");

  broken = plan;
  broken.staying_count = 0;  // no longer accounts for every atom
  rejected = false;
  try {
    validate_migration_plan(broken, 2, 0, 4);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "a plan that loses an atom must be rejected");
}

void check_membership_record_layout()
{
  require(sizeof(GhostMembershipRecord) == 40, "membership record is 40 bytes");
  GhostMembershipRecord record{};
  record.global_id = 42;
  record.type = 3;
  record.position[0] = 1.5;
  record.position[1] = 2.5;
  record.position[2] = 3.5;
  const unsigned char* bytes = reinterpret_cast<const unsigned char*>(&record);
  std::uint64_t gid = 0;
  std::memcpy(&gid, bytes, 8);
  require(gid == 42, "global_id at offset 0");
  std::int32_t type = 0;
  std::memcpy(&type, bytes + 8, 4);
  require(type == 3, "type at offset 8");
  double x = 0.0;
  std::memcpy(&x, bytes + 16, 8);
  require(x == 1.5, "position[0] at offset 16");
}

void run_all()
{
  check_radii_single_type();
  check_radii_typewise_pairs();
  check_radii_match_float_kernel_rounding();
  check_radii_malformed_fails_closed();
  check_eligibility_reasons();
  check_layout_classification_and_order();
  check_boundary_classification();
  check_layout_rejects_duplicates_and_disorder();
  check_empty_rank_and_n_less_than_p();
  check_exchange_plan_and_refresh_cover();
  check_p2_same_peer_dedup();
  check_malformed_exchange_plans();
  check_migration_routing();
  check_malformed_migration_plans();
  check_membership_record_layout();
  std::cout << "domain_layout_tests: all checks passed\n";
}

}  // namespace

int main()
{
  try {
    run_all();
  } catch (const std::exception& error) {
    std::cerr << "domain_layout_tests FAILED: " << error.what() << '\n';
    return 1;
  }
  return 0;
}
