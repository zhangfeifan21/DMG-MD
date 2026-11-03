#!/usr/bin/env python3
"""Compare the host SVR candidate against the locked, independent oracle fixture."""

import argparse
import json
import pathlib
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=pathlib.Path)
    parser.add_argument(
        "--fixture",
        type=pathlib.Path,
        default=pathlib.Path(__file__).parent / "fixtures" / "svr.json",
    )
    args = parser.parse_args()
    expected = json.loads(args.fixture.read_text())
    result = subprocess.run([str(args.candidate)], check=False, text=True, capture_output=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr, end="")
        raise SystemExit(f"candidate exited {result.returncode}")
    actual = json.loads(result.stdout)
    expected_payload = {"schema": expected["schema"], "cases": expected["cases"]}
    if actual != expected_payload:
        print("SVR fixture mismatch", file=sys.stderr)
        print(json.dumps({"expected": expected_payload, "actual": actual}, indent=2), file=sys.stderr)
        return 1
    print(f"PASS: {len(expected['cases'])} SVR cases match exact double bit patterns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
