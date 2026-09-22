#!/usr/bin/env bash
# Build both executables without modifying the reference checkout or old builds.
set -euo pipefail
md_project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
md_env_file=${MD_ENV_FILE:-"$md_project_root/../env/md-mpi.sh"}
md_arch=${1:-89}
md_jobs=${2:-8}
source "$md_env_file"
md_reference="$md_project_root/../gpumd-reference"
md_pinned=9d23496e41319b9e2af5221a7df6285387401d1e
if [[ $(git -C "$md_reference" rev-parse HEAD) != "$md_pinned" ]]; then
    printf 'GPUMD must be checked out at %s\n' "$md_pinned" >&2
    exit 1
fi
if [[ -n $(git -C "$md_reference" status --porcelain --untracked-files=no) ]]; then
    printf 'GPUMD reference has tracked changes; preserve them and use a clean checkout.\n' >&2
    exit 1
fi
cmake -S "$md_project_root" -B "$md_project_root/build-benchmark" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES="$md_arch"
cmake --build "$md_project_root/build-benchmark" --parallel "$md_jobs"
cmake -S "$md_reference" -B "$md_project_root/build-benchmark-gpumd" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES="$md_arch"
cmake --build "$md_project_root/build-benchmark-gpumd" --target gpumd --parallel "$md_jobs"
printf '\nCandidate: %s\nReference: %s\n' \
    "$md_project_root/build-benchmark/dmg-md" "$md_project_root/build-benchmark-gpumd/gpumd"
