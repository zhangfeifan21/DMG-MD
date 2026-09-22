#!/usr/bin/env bash

set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
invocation_dir=$(pwd -P)
entrypoint=${DMGMD_LONG_NVE_ENTRYPOINT:-$script_dir/run_long_nve_profile.sh}

usage() {
  cat <<EOF
Run or resume a DMG-MD long-NVE test profile.

Usage:
  $entrypoint [RESULT_ROOT]
  $entrypoint [options]

With no RESULT_ROOT, a new dmgmd-${profile}-YYYYMMDD-HHMMSS directory is created
under the repository root. When RESULT_ROOT contains exactly one long-NVE run
checkpoint, that work directory is resumed automatically.

Options:
  --profile NAME        smoke, nightly, or release (selected: ${profile})
  --result-root PATH    Result directory (same as the optional positional path)
  --work-root PATH      Explicit retained work directory to resume
  --candidate PATH      Candidate executable (default: ./build/dmg-md)
  --devices LIST        CUDA IDs/UUIDs (defaults: smoke=0, nightly=0..3, release=0..7)
  --cases LIST          Comma-separated manifest cases
  --ranks LIST          Comma-separated MPI rank counts
  --backends LIST       Comma-separated HostStaged,CudaAware
  --sections LIST       Comma-separated short,long,replay,restart,nvt
  --performance         Disable detailed domain logs for total-time measurement
  --domain-timing       Enable diagnostic M2a ordinary/rebuild phase timing
  --retries N           Retries per failed stage (default: 1)
  --ui MODE             auto, dashboard, or plain (default: auto)
  --adopt-existing      Forward legacy-stage adoption to the Python runner
  --dry-run             Print the resolved command without running it
  -h, --help            Show this help
EOF
}

fail() {
  echo "$(basename -- "$entrypoint"): $*" >&2
  exit 2
}

profile="release"
profile_was_set=0
result_root=""
work_root=""
candidate="$repo_root/build/dmg-md"
devices=""
cases=""
ranks=""
backends=""
sections=""
performance=0
domain_timing=0
retries=1
ui_mode="auto"
adopt_existing=0
dry_run=0

while (($# > 0)); do
  case "$1" in
    --profile)
      (($# >= 2)) || fail "--profile requires smoke, nightly, or release"
      ((profile_was_set == 0)) || fail "profile was specified more than once"
      profile=$2
      profile_was_set=1
      shift 2
      ;;
    --result-root)
      (($# >= 2)) || fail "--result-root requires a path"
      [[ -z "$result_root" ]] || fail "result root was specified more than once"
      result_root=$2
      shift 2
      ;;
    --work-root)
      (($# >= 2)) || fail "--work-root requires a path"
      work_root=$2
      shift 2
      ;;
    --candidate)
      (($# >= 2)) || fail "--candidate requires a path"
      candidate=$2
      shift 2
      ;;
    --devices)
      (($# >= 2)) || fail "--devices requires a comma-separated list"
      devices=$2
      shift 2
      ;;
    --cases)
      (($# >= 2)) || fail "--cases requires a comma-separated list"
      cases=$2
      shift 2
      ;;
    --ranks)
      (($# >= 2)) || fail "--ranks requires a comma-separated list"
      ranks=$2
      shift 2
      ;;
    --backends)
      (($# >= 2)) || fail "--backends requires a comma-separated list"
      backends=$2
      shift 2
      ;;
    --sections)
      (($# >= 2)) || fail "--sections requires a comma-separated list"
      sections=$2
      shift 2
      ;;
    --domain-timing)
      domain_timing=1
      shift
      ;;
    --performance)
      performance=1
      shift
      ;;
    --retries)
      (($# >= 2)) || fail "--retries requires a non-negative integer"
      retries=$2
      shift 2
      ;;
    --ui)
      (($# >= 2)) || fail "--ui requires auto, dashboard, or plain"
      ui_mode=$2
      shift 2
      ;;
    --adopt-existing)
      adopt_existing=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      fail "unknown option: $1"
      ;;
    *)
      [[ -z "$result_root" ]] || fail "unexpected positional argument: $1"
      result_root=$1
      shift
      ;;
  esac
done

case "$profile" in
  smoke)
    default_devices="0"
    ;;
  nightly)
    default_devices="0,1,2,3"
    ;;
  release)
    default_devices="0,1,2,3,4,5,6,7"
    ;;
  *)
    fail "--profile must be smoke, nightly, or release"
    ;;
esac
devices=${devices:-$default_devices}

[[ "$retries" =~ ^[0-9]+$ ]] || fail "--retries must be a non-negative integer"
[[ -n "$devices" ]] || fail "--devices must not be empty"
case "$ui_mode" in
  auto|dashboard|plain) ;;
  *) fail "--ui must be auto, dashboard, or plain" ;;
esac
((performance == 0 || domain_timing == 0)) ||
  fail "--performance and --domain-timing are mutually exclusive"

if [[ -z "$result_root" ]]; then
  result_root="$repo_root/dmgmd-$profile-$(date +%Y%m%d-%H%M%S)"
elif [[ "$result_root" != /* ]]; then
  result_root="$invocation_dir/$result_root"
fi
result_root=$(realpath -m -- "$result_root")

if [[ "$candidate" != /* ]]; then
  candidate="$invocation_dir/$candidate"
fi
candidate=$(realpath -m -- "$candidate")

if [[ -n "$work_root" ]]; then
  if [[ "$work_root" != /* ]]; then
    work_root="$invocation_dir/$work_root"
  fi
  work_root=$(realpath -m -- "$work_root")
fi

work_parent="$result_root/work"
mkdir -p -- "$work_parent"

command -v flock >/dev/null 2>&1 || fail "flock is required to prevent duplicate runs"
exec 9>"$result_root/.run-long-nve.lock"
flock -n 9 || fail "another $profile runner already holds the lock for $result_root"

if [[ -n "$work_root" ]]; then
  [[ -d "$work_root" ]] || fail "work root does not exist: $work_root"
  [[ -f "$work_root/.dmgmd-run-checkpoint.json" || $adopt_existing -eq 1 ]] ||
    fail "work root has no run checkpoint; use --adopt-existing only for a legacy run"
else
  mapfile -t discovered_work_roots < <(
    find "$work_parent" -mindepth 2 -maxdepth 2 -type f \
      -name .dmgmd-run-checkpoint.json -printf '%h\n' | sort -u
  )
  case ${#discovered_work_roots[@]} in
    0)
      if [[ -s "$result_root/run.log" || -e "$result_root/report.json" ]]; then
        fail "result directory has logs/report but no resumable checkpoint: $result_root"
      fi
      ;;
    1)
      work_root=${discovered_work_roots[0]}
      ;;
    *)
      printf 'Multiple work roots found under %s:\n' "$work_parent" >&2
      printf '  %s\n' "${discovered_work_roots[@]}" >&2
      fail "select one with --work-root"
      ;;
  esac
fi

[[ -x "$candidate" ]] || fail "candidate is not executable: $candidate"

log_path="$result_root/run.log"
runner=(
  python3 "$repo_root/tests/long_nve/run_long_nve.py"
  --candidate "$candidate"
  --devices "$devices"
  --profile "$profile"
  --retries "$retries"
  --report "$result_root/report.json"
  --ui "$ui_mode"
  --ui-log "$log_path"
  --keep-work
)
[[ -z "$cases" ]] || runner+=(--cases "$cases")
[[ -z "$ranks" ]] || runner+=(--ranks "$ranks")
[[ -z "$backends" ]] || runner+=(--backends "$backends")
[[ -z "$sections" ]] || runner+=(--sections "$sections")
((performance == 0)) || runner+=(--performance)
((domain_timing == 0)) || runner+=(--domain-timing)
if [[ -n "$work_root" ]]; then
  runner+=(--resume-work "$work_root")
fi
if ((adopt_existing)); then
  [[ -n "$work_root" ]] || fail "--adopt-existing requires a work root"
  runner+=(--adopt-existing)
fi

print_command() {
  printf 'cd %q && TMPDIR=%q' "$repo_root" "$work_parent"
  printf ' %q' "${runner[@]}"
  printf '\n'
}

echo "PROFILE=$profile"
echo "RESULT_ROOT=$result_root"
if [[ -n "$work_root" ]]; then
  echo "WORK_ROOT=$work_root"
  echo "MODE=resume"
else
  echo "MODE=start"
fi
echo "To resume later: $entrypoint --result-root $result_root"

if ((dry_run)); then
  print_command
  exit 0
fi

provenance_dir="$result_root/provenance"
if [[ ! -e "$provenance_dir" ]]; then
  mkdir -- "$provenance_dir"
  git -C "$repo_root" rev-parse HEAD >"$provenance_dir/source-revision.txt"
  git -C "$repo_root" status --short --branch >"$provenance_dir/git-status.txt"
  git -C "$repo_root" diff --binary HEAD >"$provenance_dir/dirty.patch"
  git -C "$repo_root" ls-files --modified --others --exclude-standard -z |
    while IFS= read -r -d '' path; do
      [[ -f "$repo_root/$path" ]] || continue
      sha256sum "$repo_root/$path"
    done | LC_ALL=C sort >"$provenance_dir/dirty-files.sha256"
  sha256sum "$candidate" >"$provenance_dir/candidate.sha256"
  candidate_build_dir=$(dirname -- "$candidate")
  if [[ -f "$candidate_build_dir/CMakeCache.txt" ]]; then
    cp -- "$candidate_build_dir/CMakeCache.txt" "$provenance_dir/CMakeCache.txt"
    sha256sum "$candidate_build_dir/CMakeCache.txt" >"$provenance_dir/CMakeCache.sha256"
  fi
fi

# This is the only supported MPI/CUDA environment entry point. Sourcing it in
# the wrapper makes a new tmux shell independent of the caller's shell state.
source "$repo_root/../env/md-mpi.sh"

dashboard_active=0
if [[ "$ui_mode" == "dashboard" ]]; then
  [[ ${TERM:-dumb} != "dumb" ]] || fail "dashboard mode requires a capable terminal"
  if { exec 3>/dev/tty; } 2>/dev/null; then
    dashboard_active=1
  else
    fail "dashboard mode cannot open /dev/tty"
  fi
elif [[ "$ui_mode" == "auto" && -t 1 && ${TERM:-dumb} != "dumb" ]]; then
  if { exec 3>/dev/tty; } 2>/dev/null; then
    dashboard_active=1
  fi
fi
{
  printf '\nLONG_NVE_WRAPPER started_utc=%s profile=%s mode=%s result_root=%s' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$profile" \
    "$([[ -n "$work_root" ]] && echo resume || echo start)" "$result_root"
  printf ' candidate_sha256=%s retries=%s devices=%s\n' \
    "$(sha256sum "$candidate" | awk '{print $1}')" "$retries" "$devices"
  print_command
} | tee -a "$log_path"

if ((dashboard_active)); then
  runner+=(--ui-fd 3)
fi

set +e
if ((dashboard_active)); then
  restore_terminal() {
    printf '\033[?25h\033[?1049l' >&3 2>/dev/null || true
  }
  trap restore_terminal EXIT INT TERM HUP
  (
    cd "$repo_root"
    TMPDIR="$work_parent" "${runner[@]}"
  ) 2>&1 | tee -a "$log_path" >/dev/null
  runner_status=${PIPESTATUS[0]}
else
  (
    cd "$repo_root"
    TMPDIR="$work_parent" "${runner[@]}"
  ) 2>&1 | tee -a "$log_path"
  runner_status=${PIPESTATUS[0]}
fi
if ((dashboard_active)); then
  restore_terminal
  dashboard_active=0
  trap - EXIT INT TERM HUP
fi
set -e

if ((runner_status == 0)); then
  echo "LONG_NVE_WRAPPER status=passed profile=$profile result_root=$result_root" |
    tee -a "$log_path"
else
  echo "LONG_NVE_WRAPPER status=failed profile=$profile exit_code=$runner_status result_root=$result_root" |
    tee -a "$log_path" >&2
  echo "Last log lines ($log_path):" >&2
  tail -n 12 -- "$log_path" >&2
  echo "Resume with: $entrypoint --result-root $result_root" >&2
fi
exit "$runner_status"
