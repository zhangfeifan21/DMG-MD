#!/usr/bin/env python3
"""Give each LAMMPS MPI rank exactly one selected GPU before KOKKOS starts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--input", default="in.lmp")
    args = parser.parse_args()
    world_rank = int(os.environ["OMPI_COMM_WORLD_RANK"])
    local_rank = int(os.environ["OMPI_COMM_WORLD_LOCAL_RANK"])
    devices = os.environ["LAMMPS_RANK_GPU_UUIDS"].split(",")
    if not devices or any(not value.startswith("GPU-") for value in devices):
        raise SystemExit("LAMMPS_RANK_GPU_UUIDS must contain selected full GPU UUIDs")
    if not 0 <= local_rank < len(devices):
        raise SystemExit(f"local rank {local_rank} has no assigned GPU")
    selected = devices[local_rank]
    os.environ["CUDA_VISIBLE_DEVICES"] = selected
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["OMP_NUM_THREADS"] = "1"
    print(f"LAMMPS_GPU_BIND rank={world_rank} local_rank={local_rank} cuda_uuid={selected}",
          flush=True)
    binary = str(args.binary.resolve())
    os.execv(binary, [binary, "-k", "on", "g", "1", "-sf", "kk",
                      "-in", args.input, "-log", "none"])


if __name__ == "__main__":
    main()
