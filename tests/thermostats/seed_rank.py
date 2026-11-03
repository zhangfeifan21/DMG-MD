#!/usr/bin/env python3
"""Set deliberately rank-dependent ensemble seed text, then exec DMG-MD."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def seed_for_rank(rank: int, rank_zero_seed: str, other_seed: str) -> str:
    return rank_zero_seed if rank == 0 else other_seed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--rank-zero-seed", required=True)
    parser.add_argument("--other-seed", required=True)
    args = parser.parse_args()
    try:
        rank = int(os.environ["OMPI_COMM_WORLD_RANK"])
    except (KeyError, ValueError) as error:
        parser.error(f"OMPI_COMM_WORLD_RANK is missing or invalid: {error}")
    os.environ["DMGMD_ENSEMBLE_SEED"] = seed_for_rank(
        rank, args.rank_zero_seed, args.other_seed
    )
    candidate = str(args.candidate.resolve())
    os.execv(candidate, [candidate])
    raise RuntimeError("os.execv unexpectedly returned")


if __name__ == "__main__":
    raise SystemExit(main())
