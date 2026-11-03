#!/usr/bin/env python3
"""Build an isolated CPU oracle from GPUMD nhc() and write its JSON output."""

import argparse
import hashlib
import pathlib
import re
import subprocess
import tempfile


CASES = [
    (1, 300.0, 1000.0, 0.001, 0.012, [300.0, 300.0, 300.0, 300.0]),
    (17, 725.0, 800.0, 0.0005, 0.19, [725.0, 700.0, 675.0, 650.0]),
    (103, 1200.0, 400.0, 0.002, 1.7, [1200.0, 1225.0, 1250.0, 1275.0]),
]

HARNESS = r'''
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
static void emit_bits(double x) {
  std::uint64_t bits; std::memcpy(&bits, &x, sizeof(bits));
  std::cout << "\"" << std::hex << std::setw(16) << std::setfill('0') << bits << std::dec << "\"";
}
static void emit_array(const char* name, const double* x) {
  std::cout << "          \"" << name << "\": [";
  for (int i = 0; i < 4; ++i) { if (i) std::cout << ", "; emit_bits(x[i]); }
  std::cout << "]";
}
'''


def render_main(source_sha1):
    lines = [
        "int main() {",
        '  std::cout << "{\\n  \\\"source_sha1\\\": \\\"' + source_sha1 + '\\\",\\n  \\\"cases\\\": [\\n";',
    ]
    for case_index, (n, temperature, damping, dt, initial_ek2, ramp) in enumerate(CASES):
        comma = "" if case_index == 0 else ","
        ramp_values = ", ".join(repr(value) for value in ramp)
        lines.extend(
            [
                f"  std::cout << \"{comma}\\n    {{\\n      \\\"name\\\": \\\"case{case_index}\\\", \\\"N\\\": {n}, \\\"T0\\\": {temperature!r}, \\\"Tdamp\\\": {damping!r}, \\\"dt\\\": {dt!r}, \\\"initial_Ek2\\\": {initial_ek2!r},\\n\";",
                "  {",
                "    double posbuf[5] = {0.25, 0, 0, 0, 0};",
                "    double velbuf[5] = {0.75, 1, -1, 1, -1};",
                "    double masbuf[5] = {2.5, 1, 1, 1, 1};",
                "    // Pad index -1: upstream m=0 reads it before overwriting G.",
                "    double* pos = posbuf + 1; double* vel = velbuf + 1; double* mas = masbuf + 1;",
                f"    double tau = {dt!r} * {damping!r}; double kT0 = 8.617343e-5 * {temperature!r};",
                "    for (int i = 0; i < 4; ++i) mas[i] = kT0 * tau * tau;",
                f"    mas[0] *= 3.0 * {n};",
                f"    double Ek2 = {initial_ek2!r};",
                '    emit_array("initial_position", pos); std::cout << ",\\n";',
                '    emit_array("initial_momentum", vel); std::cout << ",\\n";',
                '    emit_array("initial_mass", mas); std::cout << ",\\n      \\\"steps\\\": [\\n";',
                f"    const double rampT[4] = {{{ramp_values}}};",
                "    for (int step = 0; step < 4; ++step) {",
                '      if (step) std::cout << ",\\n";',
                "      double factor = nhc(4, pos, vel, mas, Ek2, 8.617343e-5 * rampT[step], 3.0 * "
                + f"{n}, 0.5 * {dt!r});",
                "      Ek2 *= factor * factor;",
                '      std::cout << "        {\\n          \\\"target_temperature\\\": " << rampT[step] << ",\\n          \\\"factor\\\": "; emit_bits(factor);',
                '      std::cout << ",\\n          \\\"Ek2_after\\\": "; emit_bits(Ek2); std::cout << ",\\n";',
                '      emit_array("position", pos); std::cout << ",\\n";',
                '      emit_array("momentum", vel); std::cout << ",\\n";',
                '      emit_array("mass", mas); std::cout << "\\n        }";',
                "    }",
                '    std::cout << "\\n      ]\\n    }";',
                "  }",
            ]
        )
    lines += ['  std::cout << "\\n  ]\\n}\\n";', "}"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()

    reference_file = args.reference_root / "src/integrate/ensemble_nhc.cu"
    reference_bytes = reference_file.read_bytes()
    reference_text = reference_bytes.decode("utf-8")
    match = re.search(
        r"static double nhc\([\s\S]*?(?=\nvoid Ensemble_NHC::integrate_nvt_nhc_1)",
        reference_text,
    )
    if match is None:
        raise SystemExit(f"could not isolate nhc() from {reference_file}")
    source_sha1 = hashlib.sha1(reference_bytes).hexdigest()
    generated = "#include <cmath>\n" + HARNESS + "\n" + match.group(0) + "\n" + render_main(source_sha1)

    with tempfile.TemporaryDirectory(prefix="nhc-oracle-") as temp_name:
        temp = pathlib.Path(temp_name)
        source = temp / "nhc_oracle.cpp"
        executable = temp / "nhc_oracle"
        source.write_text(generated)
        subprocess.run(["c++", "-std=c++17", "-O0", str(source), "-o", str(executable)], check=True)
        result = subprocess.run([str(executable)], check=True, text=True, capture_output=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.stdout)
    print(f"wrote {args.output}; source SHA1 {source_sha1}")


if __name__ == "__main__":
    main()
