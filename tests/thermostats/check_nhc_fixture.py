#!/usr/bin/env python3
"""Compare the CPU NHC fixture executable with the locked oracle JSON."""

import argparse
import json
import pathlib
import subprocess
import sys


def find_difference(expected, actual, path="$ "):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, (int, float)) and not isinstance(actual, bool) and expected == actual:
            return None
    if type(expected) is not type(actual):
        return f"{path}: type differs: expected {type(expected).__name__}, got {type(actual).__name__}"
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            return f"{path}: keys differ: expected {sorted(expected)}, got {sorted(actual)}"
        for key in expected:
            difference = find_difference(expected[key], actual[key], f"{path}.{key}")
            if difference:
                return difference
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: length differs: expected {len(expected)}, got {len(actual)}"
        for index, (want, got) in enumerate(zip(expected, actual)):
            difference = find_difference(want, got, f"{path}[{index}]")
            if difference:
                return difference
    elif expected != actual:
        return f"{path}: expected {expected!r}, got {actual!r}"
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=pathlib.Path)
    args = parser.parse_args()

    fixture = pathlib.Path(__file__).resolve().parent / "fixtures" / "nhc.json"
    expected = json.loads(fixture.read_text())
    run = subprocess.run([str(args.candidate.resolve())], check=True, text=True, capture_output=True)
    actual = json.loads(run.stdout)
    difference = find_difference(expected, actual)
    if difference:
        print(f"NHC fixture mismatch: {difference}", file=sys.stderr)
        return 1
    print(f"NHC fixture matches {len(expected['cases'])} cases bit-for-bit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
