#!/usr/bin/env bash

set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
export DMGMD_LONG_NVE_ENTRYPOINT="$script_dir/run_long_nve_release.sh"
exec "$script_dir/run_long_nve_profile.sh" --profile release "$@"
