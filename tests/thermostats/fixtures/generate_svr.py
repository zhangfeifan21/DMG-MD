#!/usr/bin/env python3
"""Build a temporary CPU oracle from locked GPUMD SVR functions and emit fixture."""

import argparse
import hashlib
import json
import pathlib
import shlex
import subprocess
import tempfile


SEEDS = [12345678, 0, 42, 4294967295]
DEGREES = [3, 6, 9, 48]
TAUS = [0.1, 0.01, 1.0, 100.0]


def extract_function(source: str, name: str) -> str:
    marker = f"static double {name}("
    start = source.find(marker)
    if start < 0:
        raise ValueError(f"cannot find {marker}")
    brace = source.find("{", start)
    depth = 0
    for pos in range(brace, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[start : pos + 1]
    raise ValueError(f"unterminated function {name}")


ORACLE_MAIN = r'''
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <random>
#include <cmath>
#include <cstdlib>

static std::uint64_t bits(double x) {
  std::uint64_t value;
  std::memcpy(&value, &x, sizeof(value));
  return value;
}

int main(int argc, char** argv) {
  if (argc != 4) return 2;
  const std::uint32_t seed = static_cast<std::uint32_t>(std::stoull(argv[1]));
  const int ndeg = std::stoi(argv[2]);
  const double taut = std::stod(argv[3]);
  std::mt19937 rng(seed);
  const double kk = 1.234567890123;
  const double sigma = 9.876543210987;
  std::cout << "{\"seed\":" << seed << ",\"ndeg\":" << ndeg
            << ",\"taut\":" << std::setprecision(17) << taut
            << ",\"kk\":" << std::setprecision(17) << kk
            << ",\"sigma\":" << std::setprecision(17) << sigma
            << ",\"gasdev_count\":6,\"sumnoises_nn\":[0,1,2,3,10],\"resamplekin_steps\":8"
            << ",\"gasdev_bits\":[";
  for (int i = 0; i < 6; ++i) {
    if (i) std::cout << ',';
    std::cout << bits(gasdev(rng));
  }
  std::cout << "],\"sumnoises\":[";
  const int nn_values[] = {0, 1, 2, 3, 10};
  for (int i = 0; i < 5; ++i) {
    if (i) std::cout << ',';
    std::cout << "{\"nn\":" << nn_values[i] << ",\"bits\":"
              << bits(resamplekin_sumnoises(nn_values[i], rng)) << '}';
  }
  std::cout << "],\"resamplekin_bits\":[";
  for (int i = 0; i < 8; ++i) {
    if (i) std::cout << ',';
    std::cout << bits(resamplekin(kk, sigma, ndeg, taut, rng));
  }
  std::cout << "]}";
  return std::cout ? 0 : 1;
}
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing fixture: {args.output}")
    ref_file = args.reference_root / "src/integrate/svr_utilities.cuh"
    source = ref_file.read_text()
    functions = "\n\n".join(
        extract_function(source, name)
        for name in ("gasdev", "gamdev", "resamplekin_sumnoises", "resamplekin")
    )
    with tempfile.TemporaryDirectory(prefix="dmgmd-svr-oracle-") as temp:
        temp_path = pathlib.Path(temp)
        cpp = temp_path / "oracle.cpp"
        exe = temp_path / "oracle"
        cpp.write_text("#include <random>\n#include <cmath>\n#include <stdexcept>\n" + functions + "\n" + ORACLE_MAIN)
        compile_command = ["c++", "-std=c++17", "-O2", str(cpp), "-o", str(exe)]
        subprocess.run(compile_command, check=True)
        cases = []
        commands = []
        for seed in SEEDS:
            for ndeg, taut in zip(DEGREES, TAUS):
                command = [str(exe), str(seed), str(ndeg), repr(taut)]
                output = subprocess.run(command, check=True, text=True, capture_output=True).stdout
                cases.append(json.loads(output))
                commands.append(command[1:])
    result = {
        "schema": 1,
        "origin": {
            "commit": "9d23496e41319b9e2af5221a7df6285387401d1e",
            "file": "src/integrate/svr_utilities.cuh",
            "symbols": ["gasdev", "gamdev", "resamplekin_sumnoises", "resamplekin"],
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "oracle_build": shlex.join(compile_command),
            "case_arguments_seed_ndeg_taut": commands,
            "static_cache_isolation": "one fresh oracle process per seed/ndeg/taut case",
        },
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {len(cases)} oracle cases to {args.output}")
    print(f"reference SHA-256: {result['origin']['source_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
