#!/usr/bin/env python3
"""Run an isolated CPU harmonic harness using thermostat routines extracted from locked GPUMD.

This does not invoke the GPUMD executable.  It compiles the unchanged GPUMD `nhc`,
`gasdev`, `gamdev`, `resamplekin_sumnoises`, and `resamplekin` function bodies into
a small test-only velocity-Verlet driver.  The driver supplies the same independent
harmonic force model and canonical C++ initial state as harmonic_sampler.cpp.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests" / "thermostats"
REFERENCE = ROOT.parent / "gpumd-reference"
MANIFEST = TESTS / "manifest.json"
CONFIRMATION = TESTS / "harmonic-confirmation.json"
LOCKED_REFERENCE_COMMIT = "9d23496e41319b9e2af5221a7df6285387401d1e"


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_locked_reference() -> str:
    commit = subprocess.check_output(
        ["git", "-C", str(REFERENCE), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != LOCKED_REFERENCE_COMMIT:
        raise RuntimeError(
            f"GPUMD reference HEAD {commit} does not match locked commit "
            f"{LOCKED_REFERENCE_COMMIT}"
        )
    return commit


def extract(text: str, pattern: str, name: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"could not extract locked GPUMD function {name}")
    return match.group(0)


def cxx_source() -> tuple[str, dict[str, str]]:
    nhc_path = REFERENCE / "src/integrate/ensemble_nhc.cu"
    svr_path = REFERENCE / "src/integrate/svr_utilities.cuh"
    nhc_text = nhc_path.read_text()
    svr_text = svr_path.read_text()
    nhc = extract(
        nhc_text,
        r"static double nhc\([\s\S]*?(?=\nvoid Ensemble_NHC::integrate_nvt_nhc_1)",
        "nhc",
    )
    svr = "\n\n".join(
        extract(
            svr_text,
            rf"static double {name}\([\s\S]*?(?=\nstatic double |\Z)",
            name,
        )
        for name in ("gasdev", "gamdev", "resamplekin_sumnoises", "resamplekin")
    )
    source_hashes = {
        "ensemble_nhc.cu": sha256(nhc_path),
        "svr_utilities.cuh": sha256(svr_path),
    }
    driver = r'''
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>
#define K_B 8.617343e-5
'''
    main = r'''
struct Mode { double x, v, w; };
static double kinetic(const std::vector<Mode>& modes) {
  double k = 0.0;
  for (const auto& m : modes) k += 0.5 * m.v * m.v;
  return k;
}
static void vv(std::vector<Mode>& modes, double dt) {
  for (auto& m : modes) {
    m.v += -0.5 * dt * m.w * m.w * m.x;
    m.x += dt * m.v;
    m.v += -0.5 * dt * m.w * m.w * m.x;
  }
}
int main(int argc, char** argv) {
  if (argc != 7) return 2;
  const std::string ensemble = argv[1], output_path = argv[2];
  const uint64_t seed = std::stoull(argv[3]);
  const int steps = std::stoi(argv[4]);
  const double dt = std::stod(argv[5]), damping = std::stod(argv[6]);
  constexpr int natoms = 8, dof = 3 * natoms, chain = 4;
  constexpr double temp = 300.0, kT = K_B * temp;
  std::mt19937_64 init_rng(seed);
  std::normal_distribution<double> normal(0.0, 1.0);
  std::vector<Mode> modes;
  for (int i = 0; i < dof; ++i) {
    const double w = 0.15 + 0.01 * i;
    modes.push_back({std::sqrt(kT) / w * normal(init_rng), std::sqrt(kT) * normal(init_rng), w});
  }
  double pos_store[chain + 1] = {}, vel_store[chain + 1] = {}, mass_store[chain + 1] = {};
  double* pos = pos_store + 1; double* vel = vel_store + 1; double* mass = mass_store + 1;
  vel[0] = vel[2] = 1.0; vel[1] = vel[3] = -1.0;
  const double tau = dt * damping;
  for (int i = 0; i < chain; ++i) mass[i] = kT * tau * tau;
  mass[0] *= dof;
  std::mt19937 bdp_rng(static_cast<uint32_t>(seed));
  std::ofstream out(output_path, std::ios::out | std::ios::trunc);
  if (!out) return 3;
  out << "# ensemble=" << ensemble << "\n# seed=" << seed
      << "\n# natoms=" << natoms << "\n# modes=" << dof << "\n# steps=" << steps
      << "\n# temperature=" << std::setprecision(17) << temp
      << "\n# damping=" << damping << "\n# dt=" << dt
      << "\n# sample_count=" << steps << "\n# kB=" << K_B << "\n";
  out << "step,kinetic_energy,mean_mv2_x,mean_mv2_y,mean_mv2_z\n" << std::setprecision(17);
  for (int step = 0; step < steps; ++step) {
    if (ensemble == "nvt_nhc") {
      double ek2 = 2.0 * kinetic(modes) / (dof * K_B);
      ek2 *= dof * K_B;
      const double factor = nhc(chain, pos, vel, mass, ek2, kT,
                                static_cast<double>(dof), 0.5 * dt);
      for (auto& m : modes) m.v *= factor;
      vv(modes, dt);
      ek2 = 2.0 * kinetic(modes) / (dof * K_B);
      ek2 *= dof * K_B;
      const double factor_after = nhc(chain, pos, vel, mass, ek2, kT,
                                      static_cast<double>(dof), 0.5 * dt);
      for (auto& m : modes) m.v *= factor_after;
    } else if (ensemble == "nvt_bdp") {
      vv(modes, dt);
      const double thermo_temperature = 2.0 * kinetic(modes) / (dof * K_B);
      double ek = thermo_temperature;
      ek *= dof * K_B * 0.5;
      const double sampled = resamplekin(ek, dof * kT * 0.5, dof, damping, bdp_rng);
      const double factor = std::sqrt(sampled / ek);
      for (auto& m : modes) m.v *= factor;
    } else return 4;
    double axes[3] = {};
    for (int i = 0; i < dof; ++i) axes[i % 3] += modes[i].v * modes[i].v / natoms;
    out << step << ',' << kinetic(modes) << ',' << axes[0] << ',' << axes[1] << ',' << axes[2] << '\n';
    if (!out) return 5;
  }
}
'''
    return driver + "\n" + nhc + "\n" + svr + "\n" + main, source_hashes


def write_native_fixture(args: argparse.Namespace) -> int:
    """Write one GPUMD-native FCP input directory for a preregistered seed."""
    reference_commit = assert_locked_reference()
    directory = args.outputdir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    fcp = directory / "fcp"
    fcp.mkdir()
    natoms, dof = 8, 24
    kt = 8.617343e-5 * args.temperature
    # Reuse libstdc++'s exact mt19937_64/normal_distribution initialization order.
    cpp = directory / "initial_state.cpp"
    cpp.write_text(r'''#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <random>
int main(int argc, char** argv) {
  const uint64_t seed = std::stoull(argv[1]);
  const double kT = std::stod(argv[2]);
  std::mt19937_64 gen(seed); std::normal_distribution<double> normal(0.0, 1.0);
  std::cout << std::setprecision(17);
  for (int m = 0; m < 24; ++m) {
    const double w = 0.15 + 0.01 * m;
    const double q = std::sqrt(kT) / w * normal(gen);
    const double v = std::sqrt(kT) * normal(gen);
    std::cout << m << ' ' << w << ' ' << q << ' ' << v << '\n';
  }
}
''')
    exe = directory / "initial_state"
    compile_command = [args.cxx, "-std=c++17", "-O3", "-DNDEBUG", str(cpp), "-o", str(exe)]
    built = subprocess.run(compile_command, check=True, text=True, capture_output=True)
    (directory / "initial_state.compile.stdout.txt").write_text(built.stdout)
    (directory / "initial_state.compile.stderr.txt").write_text(built.stderr)
    state = subprocess.check_output([str(exe), str(args.seed), format(kt, ".17g")], text=True)
    modes = [tuple(map(float, line.split())) for line in state.splitlines()]
    if len(modes) != dof:
        raise RuntimeError("initial state helper did not emit 24 modes")

    factor = 10.18051
    # The FCP has only onsite terms, so coincident zero anchors avoid an
    # irrelevant large-coordinate subtraction before its float displacement buffer.
    anchors = [(0.0, 0.0, 0.0) for _ in range(natoms)]
    model = [str(natoms), 'pbc="F F F" lattice="100 0 0 0 100 0 0 0 100" properties=species:S:1:pos:R:3:mass:R:1:vel:R:3']
    r0_lines, cluster_lines, fcs_lines = [], [], []
    max_phi_error = 0.0
    max_initial_displacement_rounding = 0.0
    for atom in range(natoms):
        anchor = anchors[atom]
        xyz, vel = [], []
        phi = [0.0] * 9
        for axis in range(3):
            mode_index = 3 * atom + axis
            _, omega, q, v = modes[mode_index]
            xyz.append(anchor[axis] + q)
            q_as_float = float(__import__("struct").unpack("f", __import__("struct").pack("f", q))[0])
            max_initial_displacement_rounding = max(
                max_initial_displacement_rounding, abs(q_as_float - q))
            vel.append(v / factor)
            exact = omega * omega
            rounded = float(__import__("struct").unpack("f", __import__("struct").pack("f", exact))[0])
            phi[axis * 3 + axis] = rounded
            max_phi_error = max(max_phi_error, abs(rounded - exact) / exact)
        model.append("H " + " ".join(format(x, ".17g") for x in xyz)
                     + " 1 " + " ".join(format(x, ".17g") for x in vel))
        r0_lines.append(" ".join(format(x, ".17g") for x in anchor))
        cluster_lines.append(f"{atom} {atom} {atom}")
        fcs_lines.extend(f"{a} {b} {phi[a * 3 + b]:.9g}" for a in range(3) for b in range(3))

    (directory / "model.xyz").write_text("\n".join(model) + "\n")
    (fcp / "r0.in").write_text("\n".join(r0_lines) + "\n")
    (fcp / "clusters_order2.in").write_text(str(natoms) + "\n" + "\n".join(cluster_lines) + "\n")
    (fcp / "fcs_order2.in").write_text(str(natoms) + "\n" + "\n".join(fcs_lines) + "\n")
    (directory / "potential.in").write_text("fcp 1 H\n2 2\nfcp\n")
    (directory / "run.in").write_text(
        f"potential potential.in\ntime_step {args.dt_internal * factor:.17g}\n"
        f"ensemble {args.ensemble} {args.temperature:.17g} {args.temperature:.17g} {args.damping_steps}\n"
        "dump_xyz 1 samples.xyz precision double velocity\n"
        f"run {args.steps}\n")
    report = {
        "seed": args.seed, "steps": args.steps, "ensemble": args.ensemble,
        "temperature_K": args.temperature, "dt_internal": args.dt_internal,
        "dt_input_fs": args.dt_internal * factor, "damping_steps": args.damping_steps,
        "natoms": natoms, "mode_count": dof, "mass_amu": 1.0,
        "velocity_input_conversion": "model.xyz A/fs = harmonic_sampler internal velocity / 10.18051; GPUMD read_xyz multiplies back by TIME_UNIT_CONVERSION",
        "frequency_conversion": "run.in time_step in fs is divided by TIME_UNIT_CONVERSION=10.18051; FCP phi2 is omega^2 in GPUMD natural units",
        "fcp_phi2_type": "float",
        "max_relative_float_rounding_error_in_omega2": max_phi_error,
        "fcp_float_path": "FCP stores r0, displacement u, phi2, pfv, and force/potential accumulation in float; the logged omega^2 rounding is only the input coefficient error. GPU force results also include float displacement/accumulation rounding.",
        "max_absolute_initial_displacement_float_rounding_A": max_initial_displacement_rounding,
        "initial_state_sha256": hashlib.sha256(state.encode()).hexdigest(),
        "potential_model_cluster_contract": "8 onsite fc2 clusters (i,i,index=i); each index matrix is diagonal with atom-axis omega^2; no periodicity and no cross-atom terms",
        "stock_gpumd_caveat": "A stock GPUMD FCP run is not an equivalent native control: Force::compute applies a system-mean-force correction for FCP, coupling the onsite modes. An out-of-tree test adapter must bypass that correction before this fixture can be treated as a native independent-mode run.",
        "output": "samples.xyz, one double-precision velocity frame per step",
        "reference_commit": reference_commit,
        "reference_source_sha256": {
            "src/force/fcp.cu": sha256(REFERENCE / "src/force/fcp.cu"),
            "src/model/read_xyz.cu": sha256(REFERENCE / "src/model/read_xyz.cu"),
            "src/main_gpumd/run.cu": sha256(REFERENCE / "src/main_gpumd/run.cu"),
        },
        "files_sha256": {
            str(p.relative_to(directory)): sha256(p)
            for p in directory.rglob("*")
            if p.is_file() and p != directory / "fixture.json"
        },
    }
    (directory / "fixture.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"native FCP fixture written: {directory}")
    print(f"max float omega^2 relative error: {max_phi_error:.9e}")
    return 0


def run(args: argparse.Namespace) -> int:
    reference_commit = assert_locked_reference()
    tool_hash_at_start = sha256(pathlib.Path(__file__).resolve())
    base = json.loads(MANIFEST.read_text())
    harmonic = base["harmonic"]
    if args.stage == "confirm":
        registered = json.loads(CONFIRMATION.read_text())["harmonic"]
    else:
        registered = harmonic
    if args.stage == "pilot":
        confidence = harmonic["acceptance"]["confidence_z"]
    else:
        confidence = registered["acceptance"]["confidence_z"]

    evidence = args.work_root.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    # Preserve the exact code and registrations used for later independent review.
    analysis_sources = {
        "run_harmonic.py": TESTS / "run_harmonic.py",
        "sample_statistics.py": TESTS / "sample_statistics.py",
        "manifest.json": MANIFEST,
        "harmonic-confirmation.json": CONFIRMATION,
    }
    for name, path in analysis_sources.items():
        shutil.copyfile(path, evidence / name)
    source, source_hashes = cxx_source()
    cxx = evidence / "gpumd_harmonic_source_harness.cpp"
    exe = evidence / "gpumd_harmonic_source_harness"
    cxx.write_text(source)
    compile_command = [args.cxx, "-std=c++17", "-O3", "-DNDEBUG", str(cxx), "-o", str(exe)]
    build = subprocess.run(compile_command, text=True, capture_output=True)
    (evidence / "compile.stdout.txt").write_text(build.stdout)
    (evidence / "compile.stderr.txt").write_text(build.stderr)
    if build.returncode:
        raise RuntimeError(f"reference harness compilation failed; see {evidence}")

    records = []
    start_all = time.monotonic()
    tasks = [(ensemble, seed) for ensemble in args.ensembles for seed in registered["seeds"]]

    def sample_one(task: tuple[str, int]) -> dict:
        ensemble, seed = task
        stem = f"{args.stage}-{ensemble}-seed{seed}"
        output = evidence / f"{stem}.csv"
        command = [str(exe), ensemble, str(output), str(seed), str(registered["steps"]),
                   str(registered["dt_internal"]), str(registered["damping_steps"])]
        started = time.monotonic()
        result = subprocess.run(command, text=True, capture_output=True)
        elapsed = time.monotonic() - started
        (evidence / f"{stem}.stdout.txt").write_text(result.stdout)
        (evidence / f"{stem}.stderr.txt").write_text(result.stderr)
        record = {
            "stage": args.stage, "ensemble": ensemble, "seed": seed,
            "command": command, "returncode": result.returncode,
            "elapsed_seconds": elapsed,
            "output": str(output),
            "output_sha256": sha256(output) if output.exists() else None,
        }
        print(f"{stem}: rc={result.returncode}, elapsed={elapsed:.1f}s", flush=True)
        return record

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        records = list(executor.map(sample_one, tasks))
    failed = [record for record in records if record["returncode"] != 0]
    if failed:
        raise RuntimeError(f"harness failed for {failed[0]['ensemble']} seed {failed[0]['seed']}; retain evidence at {evidence}")

    manifest = json.loads((CONFIRMATION if args.stage == "confirm" else MANIFEST).read_text())
    harmonic = manifest["harmonic"]
    statistics_spec = importlib.util.spec_from_file_location(
        "reference_gpumd_harmonic_registered_stats", TESTS / "run_harmonic.py")
    statistics_module = importlib.util.module_from_spec(statistics_spec)
    assert statistics_spec.loader is not None
    statistics_spec.loader.exec_module(statistics_module)
    configured_helper = args.acf_helper or (
        pathlib.Path(os.environ["DMGMD_ACF_HELPER"])
        if os.environ.get("DMGMD_ACF_HELPER") else None
    )
    if configured_helper is not None:
        helper = configured_helper.resolve(strict=True)
        if not helper.is_file() or not helper.stat().st_mode & 0o111:
            raise RuntimeError(f"ACF helper is not executable: {helper}")
        os.environ["DMGMD_ACF_HELPER"] = str(helper)
    parameters = {key: harmonic[key] for key in (
        "natoms", "steps", "discard_steps", "dt_internal", "damping_steps",
        "temperature_K", "acceptance")}
    ensemble_statistics = {}
    for ensemble in args.ensembles:
        chains = {name: [] for name in (
            "kinetic_energy", "mean_mv2_x", "mean_mv2_y", "mean_mv2_z")}
        ensemble_records = []
        for record in records:
            if record["ensemble"] != ensemble:
                continue
            columns = statistics_module.parse_csv(
                pathlib.Path(record["output"]), harmonic["steps"], ensemble)
            for name in chains:
                chains[name].append(columns[name][harmonic["discard_steps"]:])
            ensemble_records.append({"status": "PASS"})
        analysis = statistics_module.analyze(ensemble_records, chains, manifest, parameters)
        ensemble_statistics[ensemble] = analysis
        (evidence / f"statistics-{ensemble}.json").write_text(
            json.dumps(analysis, indent=2, allow_nan=False) + "\n")

    report = {
        "reference_commit": reference_commit,
        "locked_reference_commit": LOCKED_REFERENCE_COMMIT,
        "source_hashes_sha256": source_hashes,
        "extracted_harness_sha256": sha256(cxx),
        "extracted_harness_snapshot": str(cxx),
        "executable_sha256": sha256(exe),
        "acf_helper": str(configured_helper.resolve()) if configured_helper else None,
        "acf_helper_sha256": sha256(configured_helper.resolve()) if configured_helper else None,
        "analysis_source_sha256": {
            name: sha256(path) for name, path in analysis_sources.items()
        },
        "analysis_source_snapshots": {
            name: str(evidence / name) for name in analysis_sources
        },
        "compile_command": compile_command,
        "compile_returncode": build.returncode,
        "registered_config": registered,
        "confidence_z": confidence,
        "pilot_manifest_sha256": sha256(MANIFEST),
        "confirmation_manifest_sha256": sha256(CONFIRMATION),
        "records": records,
        "statistics": ensemble_statistics,
        "overall_status": ("PASS" if all(value["status"] == "PASS" for value in ensemble_statistics.values())
                           else "INCONCLUSIVE" if all(value["status"] != "FAIL" for value in ensemble_statistics.values())
                           else "FAIL"),
        "elapsed_seconds": time.monotonic() - start_all,
        "coverage": "Unmodified GPUMD thermostat function bodies in a separate CPU harness; independent harmonic VV driver and canonical fixture. Not a native GPUMD executable run.",
        "tool_sha256": tool_hash_at_start,
    }
    (evidence / "run.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"evidence: {evidence / 'run.json'}")
    return 0 if report["overall_status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-fixture", action="store_true",
                        help="write a native GPUMD FCP model, potential, and run.in")
    parser.add_argument("--stage", choices=("pilot", "confirm"))
    parser.add_argument("--work-root", type=pathlib.Path)
    parser.add_argument("--outputdir", type=pathlib.Path)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--steps", type=int, default=200000)
    parser.add_argument("--dt-internal", type=float, default=0.01)
    parser.add_argument("--damping-steps", type=float, default=100)
    parser.add_argument("--temperature", type=float, default=300)
    parser.add_argument("--native-ensemble", choices=("nvt_nhc", "nvt_bdp"), default="nvt_nhc")
    parser.add_argument("--ensemble", dest="ensembles", action="append",
                        choices=("nvt_nhc", "nvt_bdp"))
    parser.add_argument("--cxx", default="c++")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--acf-helper", type=pathlib.Path,
                        default=None,
                        help="optional executable ACF helper; defaults to DMGMD_ACF_HELPER or Python estimator")
    args = parser.parse_args()
    if args.native_fixture:
        if args.outputdir is None:
            parser.error("--native-fixture requires --outputdir")
        args.ensemble = args.native_ensemble
        return write_native_fixture(args)
    if args.stage is None or args.work_root is None:
        parser.error("harmonic source run requires --stage and --work-root")
    args.ensembles = args.ensembles or ["nvt_nhc", "nvt_bdp"]
    if args.workers < 1:
        parser.error("--workers must be positive")
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"reference_gpumd_harmonic: {error}", file=sys.stderr)
        raise SystemExit(2)
