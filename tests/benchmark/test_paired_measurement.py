"""CPU subprocess tests for paired measurement and invalid records."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
from tempfile import TemporaryDirectory
from threading import Timer
import time
import unittest

from paired_measurement import LAMMPS_RANK_LAUNCHER, RECORD, STDERR, STDOUT, lammps_input_name
from paired_measurement import measure_job, sha


STEPS = 1200
STRIDE = 100
ATOMS = 2
XYZ = ('2\npbc="T T T" Lattice="2 0 0 0 2 0 0 0 2" '
       'Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3\n'
       'C 0 0 0 12 0 0 0\nC 1 1 1 12 0 0 0\n')
LAMMPS_FRAME = ('ITEM: TIMESTEP\n1200\nITEM: NUMBER OF ATOMS\n2\n'
                'ITEM: BOX BOUNDS pp pp pp\n0 2\n0 2\n0 2\n'
                'ITEM: ATOMS id type mass x y z vx vy vz\n'
                '1 1 12 0 0 0 0 0 0\n2 1 12 1 1 1 0 0 0\n')
FAKE_ENGINE = r"""
from pathlib import Path
import sys
import time

import socket

engine, scenario = sys.argv[1:3]
started = time.monotonic()
if engine == "lammps":
    print("KOKKOS mode with Kokkos version 4.6.2 is enabled", flush=True)
    print("pair matpl/nep/kk, kokkos_device, NEP Kokkos potential nep.txt loaded successfully", flush=True)
    print("  read_data CPU = 0.010 seconds", flush=True)
    print("   Step  Atoms  Temp  Press", flush=True)
    for step in range(0, 1201, 100):
        print(step, 2, 300, 0, flush=(scenario != "unflushed"))
        time.sleep(.02)
    print(f"Loop time of {time.monotonic() - started:.6f} on 1 procs for 1200 steps with 2 atoms", flush=True)
    print("Output  | 0.001 | 0.001 | 0.001 | 0.0 | 1.0", flush=True)
else:
    if engine == "dmgmd":
        backend = "CudaAware" if scenario == "wrong-backend" else "HostStaged"
        if scenario != "missing-gpu-evidence":
            print(f"DMGMD_MPI rank=0 world_size=1 local_size=1 local_rank=0 hostname={socket.gethostname()} cuda_uuid=cpucontrol backend={backend}", flush=True)
    else:
        print("Started initializing positions and related parameters.", flush=True)
        time.sleep(.025)
        print("Finished initializing positions and related parameters.", flush=True)
    with Path("thermo.out").open("w") as thermo:
        thermo.write("# dump_thermo 100\n# format_version 1\n# num_atoms 2\n"
                     "# dt_output 1.0000000000e+02 fs\n# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz\n")
        for step in range(100, 1201, 100):
            thermo.write(" ".join(["0"] * 18) + "\n")
            if scenario not in ("unflushed",):
                thermo.flush()
            time.sleep(.02)
    if engine == "dmgmd":
        mode = "invalid" if scenario == "wrong-mode" else "m1"
        print(f"DMGMD_DOMAIN mode={mode} reason=npt-m1-only ranks=1")
        for phase in ("run", "total"):
            print(f"DMGMD_TIMING phase={phase} sequence=0 steps=1200 atoms=2 ranks=1 "
                  f"backend={backend} seconds_max={time.monotonic() - started:.6f}")
        if scenario == "rank0-timing":
            for label, rank0 in (("ordinary", .006), ("rebuild", .004)):
                print(f"DMGMD_DOMAIN_TIMING sequence=0 class={label} count=1 "
                      "scientific_output_host_seconds_rank_min_total=0.001 "
                      "scientific_output_host_seconds_rank_max_total=0.050 "
                      f"scientific_output_host_seconds_rank0_total={rank0}")
        Path("neighbor.out").write_text("neighbor statistics\n")
    else:
        print("                  * version 5.9.1 *")
        print("number of GPUs = 1")
        print("Number of atoms is 2.")
        if engine == "wpe" and scenario != "wrong-mode":
            print("Potential engine: Wisevolve Potential Engine 1.0.0")
        print("Run 1200 steps.")
        print(f"Time used for this run = {time.monotonic() - started:.6f} second.")
        print(f"Time used = {time.monotonic() - started:.6f} s.")
if scenario != "missing-frame":
    payload = Path("frame.content").read_text()
    if scenario == "truncated-frame":
        payload = "\n".join(payload.splitlines()[:-1]) + "\n"
    Path("final.lammpstrj" if engine == "lammps" else "restart.xyz").write_text(payload)
if scenario == "extra-file":
    Path("extra.out").write_text("unfrozen output\n")
time.sleep(.05)
if scenario == "nonzero":
    print("test engine deliberately failed", file=sys.stderr)
    sys.exit(7)
"""


def prepare_control(workdir, engine="dmgmd", scenario="normal"):
    files = {"nep.txt": "frozen potential identity\n", "control.py": FAKE_ENGINE,
             "frame.content": LAMMPS_FRAME if engine == "lammps" else XYZ}
    command = [str(Path(sys.executable).absolute()), "control.py", engine, scenario]
    spec = {"engine": engine, "command": command, "atoms": ATOMS, "ranks": 1,
            "steps": STEPS, "thermo_interval": STRIDE, "timeout_s": 10}
    if engine == "lammps":
        files["in.lmp"] = ("read_data data.lmp\npair_style matpl/nep/kk nep.txt\n"
                           "thermo_style custom step atoms temp press\nthermo 100\n"
                           "thermo_modify flush yes\nrun 1200\n"
                           "write_dump all custom final.lammpstrj id type mass x y z vx vy vz "
                           "modify sort id\n")
        files["data.lmp"] = "frozen converted data\n"
        command += ["-in", "in.lmp"]
        spec["final_frame"] = {"path": "final.lammpstrj", "format": "lammps-dump"}
    else:
        files["run.in"] = ("potential nep.txt\ntime_step 1\nensemble nve\n"
                           "dump_thermo 100\ndump_restart 1200\nrun 1200\n")
        files["model.xyz"] = XYZ
        if engine == "dmgmd":
            spec["extra_outputs"] = {"neighbor.out": "replicated force diagnostics"}
            spec["expected_domain_mode"] = "m1"
            spec["expected_backend"] = "HostStaged"
            spec["required_log_patterns"] = {"domain": r"^DMGMD_DOMAIN mode=m1\b",
                                             "backend": r"^DMGMD_TIMING phase=run .*backend=HostStaged\b"}
    for name, content in files.items():
        (workdir / name).write_text(content)
    spec["inputs"] = {name: sha(workdir / name) for name in files}
    spec["binaries"] = {command[0]: sha(command[0])}
    return spec


class MeasureJobTest(unittest.TestCase):
    def setUp(self):
        evidence = os.environ.get("DMGMD_PAIRED_MEASUREMENT_EVIDENCE")
        if evidence:
            self.workdir = Path(evidence) / self._testMethodName
            self.workdir.mkdir()
        else:
            temporary = TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            self.workdir = Path(temporary.name)

    def execute(self, engine="dmgmd", scenario="normal", observe_process=None):
        spec = prepare_control(self.workdir, engine, scenario)
        result = measure_job(spec, self.workdir, observe_process=observe_process)
        self.assertEqual(json.loads((self.workdir / RECORD).read_text()), result)
        return result

    def test_short_measured_dmgmd_and_resource_observer(self):
        def sample_process(pid):
            time.sleep(.45)
            return {"pid": pid, "marker": "CPU test observer"}

        began = time.monotonic()
        result = self.execute(observe_process=sample_process)
        actual = time.monotonic() - began
        self.assertTrue(result["valid"], result["invalid_reasons"])
        self.assertGreaterEqual(actual - result["wall_s"], .09)
        self.assertEqual(result["process_observations"][0]["sample"]["pid"], result["pid"])
        self.assertEqual(result["thermo_steps"], list(range(STRIDE, STEPS + 1, STRIDE)))
        self.assertGreater(result["tail_s"], .03)
        self.assertLess(result["tail_s"], .10)
        self.assertGreater(result["steady_ms_step"], .08)
        self.assertLess(result["steady_ms_step"], .35)
        self.assertEqual(result["outputs"]["neighbor.out"]["declared"], "replicated force diagnostics")
        self.assertIsNone(result["rank0_output"]["seconds"])
        self.assertIn("did not report rank 0", result["rank0_output"]["unavailable_reason"])
        self.assertGreater(result["startup_read"]["model_read_upper_s"], 0)
        self.assertIsNone(result["read_input_s"])

    def test_rank0_cost_uses_own_duration_in_both_step_classes(self):
        result = self.execute(scenario="rank0-timing")
        self.assertTrue(result["valid"], result["invalid_reasons"])
        timing = result["rank0_output"]
        self.assertEqual(timing["by_step_class_s"], {"ordinary": .006, "rebuild": .004})
        self.assertAlmostEqual(timing["seconds"], .010)
        self.assertIsNone(timing["unavailable_reason"])

    def test_short_measured_gpumd(self):
        result = self.execute("gpumd")
        self.assertTrue(result["valid"], result["invalid_reasons"])
        self.assertEqual(result["reported_versions"], {"GPUMD": "5.9.1"})
        self.assertGreater(result["startup_read"]["positions_init_s"], .01)
        self.assertLess(result["startup_read"]["positions_init_s"], .04)
        self.assertIsNone(result["read_input_s"])

    def test_short_measured_wpe(self):
        result = self.execute("wpe")
        self.assertTrue(result["valid"], result["invalid_reasons"])
        self.assertEqual(result["reported_versions"], {"GPUMD": "5.9.1", "WPE": "1.0.0"})

    def test_short_measured_lammps(self):
        result = self.execute("lammps")
        self.assertTrue(result["valid"], result["invalid_reasons"])
        self.assertEqual(result["thermo_steps"], list(range(0, STEPS + 1, STRIDE)))
        self.assertEqual(result["thermo_source"], STDOUT)
        self.assertEqual(result["read_input_s"], .01)
        self.assertEqual(result["startup_read"]["engine_read_data_cpu_s"], .01)
        self.assertIn("Output ", result["engine_subsegments"][0])

    def test_existing_lammps_rank_launcher_resolves_frozen_input(self):
        launcher = "/opt/mpi/bin/mpirun"
        lmp = "/opt/lammps/bin/lmp"
        frozen = {launcher, LAMMPS_RANK_LAUNCHER, lmp}
        command = [launcher, "-n", "1", "/usr/bin/python3", LAMMPS_RANK_LAUNCHER,
                   "--binary", lmp]
        self.assertEqual(lammps_input_name(command, frozen, lmp), "in.lmp")
        self.assertEqual(lammps_input_name(command + ["--input", "run.in"], frozen, lmp), "run.in")
        with self.assertRaisesRegex(ValueError, "rank launcher has no frozen identity"):
            lammps_input_name(command, {launcher, lmp}, lmp)

    def test_nonzero_exit_retains_wall_and_raw_stderr(self):
        result = self.execute(scenario="nonzero")
        self.assertFalse(result["valid"])
        self.assertEqual(result["returncode"], 7)
        self.assertGreater(result["wall_s"], 0)
        self.assertIn("nonzero exit: 7", result["invalid_reasons"])
        self.assertIn("deliberately failed", (self.workdir / STDERR).read_text())

    def test_missing_final_frame(self):
        result = self.execute(scenario="missing-frame")
        self.assertFalse(result["valid"])
        self.assertGreater(result["wall_s"], 0)
        self.assertTrue(any("final frame validation" in message for message in result["invalid_reasons"]))

    def test_truncated_final_frame(self):
        result = self.execute(scenario="truncated-frame")
        self.assertFalse(result["valid"])
        self.assertIn("final frame validation: truncated extxyz atom record", result["invalid_reasons"])

    def test_wrong_runtime_identity_retains_wall_and_hashes(self):
        result = self.execute(scenario="wrong-mode")
        self.assertFalse(result["valid"])
        self.assertGreater(result["wall_s"], 0)
        self.assertIn("runtime identity mismatch: domain", result["invalid_reasons"])
        self.assertIn("DMG-MD runtime domain absent or differs from frozen identity",
                      result["invalid_reasons"])
        self.assertEqual(result["inputs"], result["identity_after"]["inputs"])
        self.assertEqual(result["binaries"], result["identity_after"]["binaries"])

    def test_wrong_runtime_backend_without_additional_regexes(self):
        spec = prepare_control(self.workdir, scenario="wrong-backend")
        spec.pop("required_log_patterns")
        result = measure_job(spec, self.workdir)
        self.assertFalse(result["valid"])
        self.assertGreater(result["wall_s"], 0)
        self.assertIn("DMG-MD runtime backend absent or differs from frozen identity",
                      result["invalid_reasons"])

    def test_expired_deadline_records_exit(self):
        spec = prepare_control(self.workdir)
        spec["timeout_s"] = .1
        result = measure_job(spec, self.workdir)
        self.assertFalse(result["valid"])
        self.assertIn("job exceeded timeout", result["invalid_reasons"])
        self.assertLess(result["wall_s"], 1.)

    def test_interrupt_preserves_raw_and_stops_caller(self):
        spec = prepare_control(self.workdir)
        interrupt = Timer(.1, os.kill, args=(os.getpid(), signal.SIGINT))
        interrupt.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                measure_job(spec, self.workdir)
        finally:
            interrupt.cancel()
            interrupt.join()
        record = json.loads((self.workdir / RECORD).read_text())
        self.assertFalse(record["valid"])
        self.assertTrue(record["interrupted"])
        self.assertIn("job interrupted", record["invalid_reasons"])

    def test_wrong_binary_hash_invalidates_before_launch(self):
        spec = prepare_control(self.workdir)
        executable = spec["command"][0]
        spec["binaries"][executable] = "a" * 64
        result = measure_job(spec, self.workdir)
        self.assertFalse(result["valid"])
        self.assertIsNone(result["wall_s"])
        self.assertIn(f"binary identity mismatch: {executable}", result["invalid_reasons"])
        self.assertIn("binary identity mismatch", (self.workdir / STDERR).read_text())

    def test_buffered_thermo_does_not_produce_point_estimates(self):
        result = self.execute(scenario="unflushed")
        self.assertFalse(result["valid"])
        self.assertGreater(result["wall_s"], 0)
        self.assertIsNone(result["tail_s"])
        self.assertIsNone(result["steady_ms_step"])
        self.assertIn("final thermo was coalesced with earlier rows", result["invalid_reasons"])

    def test_additional_output_requires_frozen_explanation(self):
        result = self.execute(scenario="extra-file")
        self.assertFalse(result["valid"])
        self.assertIn("unexpected output: extra.out", result["invalid_reasons"])
        self.assertGreater(result["outputs"]["extra.out"]["bytes"], 0)

    def test_observer_failure_retains_launched_measurement(self):
        def sample_process(pid):
            raise RuntimeError(f"CPU observer failed for {pid}")

        result = self.execute(observe_process=sample_process)
        self.assertFalse(result["valid"])
        self.assertGreater(result["wall_s"], 0)
        self.assertTrue(any("process observer: RuntimeError" in message
                            for message in result["invalid_reasons"]))


if __name__ == "__main__":
    unittest.main()
