#!/usr/bin/env bash
# Build the DMG-MD timing candidate. External engines use installed executables.
set -euo pipefail
md_project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
md_env_file=${MD_ENV_FILE:-"$md_project_root/../env/md-mpi.sh"}
md_arch=${1:-89}
md_jobs=${2:-8}
source "$md_env_file"
md_build_dir=${MD_BENCHMARK_BUILD_DIR:-"$md_project_root/build-benchmark"}
cmake -S "$md_project_root" -B "$md_build_dir" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES="$md_arch"
cmake --build "$md_build_dir" --parallel "$md_jobs"
printf '\nCandidate: %s\n' "$md_build_dir/dmg-md"
