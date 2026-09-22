#!/usr/bin/env python3

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import long_nve_common as common
import run_long_nve as runner
import long_nve_ui as terminal_ui


class LongNveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = common.load_manifest()

    def test_all_generated_models_match_locked_hashes_and_counts(self) -> None:
        for case in self.manifest["cases"].values():
            for seed in range(10):
                text = common.generate_model(case, seed)
                common.validate_generated_model(case, seed, text)
                self.assertEqual(int(text.splitlines()[0]), case["atoms"])

    def test_profile_geometries_match_locked_hashes_counts_and_base_inheritance(self) -> None:
        expected = {
            "nightly": {
                "carbon_crystal": ([8, 48, 8], 24576),
                "dense_water": ([8, 128, 8], 24576),
                "batio3_zbl": ([10, 40, 10], 20000),
            },
            "release": {
                "carbon_crystal": ([8, 96, 8], 49152),
                "dense_water": ([8, 256, 8], 49152),
                "batio3_zbl": ([10, 80, 10], 40000),
            },
        }
        for profile_name, cases in expected.items():
            for case_name, (cells, atoms) in cases.items():
                case = common.profile_case(self.manifest, profile_name, case_name)
                self.assertEqual(case["cells"], cells)
                self.assertEqual(case["atoms"], atoms)
                for seed in self.manifest["profiles"][profile_name]["seeds"]:
                    model = common.generate_model(case, seed)
                    common.validate_generated_model(case, seed, model)
                    self.assertEqual(int(model.splitlines()[0]), atoms)

        derived = common.profile_case(
            self.manifest, "release", "dense_water_typewise_cutoff"
        )
        release_water = common.profile_case(self.manifest, "release", "dense_water")
        self.assertEqual(derived["cells"], release_water["cells"])
        self.assertEqual(derived["atoms"], release_water["atoms"])
        self.assertEqual(derived["model_sha256"], release_water["model_sha256"])

    def test_retry_count_must_be_nonnegative(self) -> None:
        self.assertEqual(runner.nonnegative_integer("0"), 0)
        self.assertEqual(runner.nonnegative_integer("2"), 2)
        with self.assertRaises(argparse.ArgumentTypeError):
            runner.nonnegative_integer("-1")

    def test_dashboard_tracks_resumed_configs_stages_and_restarts(self) -> None:
        configs = [
            ("carbon_crystal", 0, "HostStaged", 1),
            ("carbon_crystal", 0, "HostStaged", 2),
            ("carbon_crystal", 0, "CudaAware", 1),
            ("carbon_crystal", 0, "CudaAware", 2),
        ]
        restarts = [("carbon_crystal", "HostStaged", 1, 2)]
        stream = io.StringIO()
        dashboard = terminal_ui.LongNveDashboard(
            "nightly",
            ["carbon_crystal"],
            [0],
            [1, 2],
            ["HostStaged", "CudaAware"],
            configs,
            restarts,
            stream,
            None,
            alternate_screen=False,
            color=False,
            start_thread=False,
        )
        work_root = Path("/tmp/long-nve-ui-test")
        dashboard.set_plan(work_root, configs[:2])
        dashboard.config_started(configs[2], revalidating=False)
        dashboard.stage_event(
            "running",
            {
                "path": work_root / "carbon_crystal/seed-0/CudaAware-r1/long",
                "attempt": 1,
                "max_attempts": 2,
            },
        )
        rendered = "\n".join(dashboard.render_lines(120, 40))
        self.assertIn("Overall", rendered)
        self.assertIn("2/5", rendered)
        self.assertIn("carbon_crystal / seed-0 / CudaAware / r1", rendered)
        self.assertIn("carbon_crystal/seed-0/CudaAware-r1/long", rendered)
        self.assertIn("H1  H2  C1  C2", rendered)
        self.assertIn("✓   ✓   ▶", rendered)

        dashboard.config_passed(configs[2])
        dashboard.restart_started(restarts[0])
        dashboard.restart_passed(restarts[0])
        dashboard.finish()
        rendered = "\n".join(dashboard.render_lines(120, 40))
        self.assertIn("suite=PASSED", rendered)
        self.assertIn("Restarts 1/1", rendered)
        dashboard.close()

    def test_stage_event_observer_is_best_effort_and_receives_reuse(self) -> None:
        events = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "fake-md.py"
            executable.write_text(
                f"#!{sys.executable}\nfrom pathlib import Path\n"
                "Path('result.out').write_text('complete\\n')\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            stage = root / "stage"
            common.set_stage_event_sink(
                lambda status, fields: events.append((status, dict(fields)))
            )
            try:
                arguments = (
                    executable,
                    (),
                    "model\n",
                    "run\n",
                    "potential\n",
                    stage,
                    os.environ,
                    10,
                    ("result.out",),
                )
                common.execute_md(*arguments, resume=True)
                common.execute_md(*arguments, resume=True)
            finally:
                common.set_stage_event_sink(None)
        self.assertEqual([status for status, _ in events], ["running", "passed", "reused"])
        self.assertEqual(events[-1][1]["path"], stage)

    def test_dashboard_uses_and_restores_the_alternate_screen(self) -> None:
        stream = io.StringIO()
        dashboard = terminal_ui.LongNveDashboard(
            "smoke",
            ["carbon_crystal"],
            [0],
            [1],
            ["HostStaged"],
            [("carbon_crystal", 0, "HostStaged", 1)],
            [],
            stream,
            None,
            alternate_screen=True,
            color=False,
            start_thread=False,
        )
        dashboard.close()
        output = stream.getvalue()
        self.assertTrue(output.startswith("\x1b[?1049h\x1b[?25l"))
        self.assertIn("\x1b[2J\x1b[H", output)
        self.assertTrue(output.endswith("\x1b[?25h\x1b[?1049l"))

    def test_all_generated_potentials_match_locked_hashes(self) -> None:
        for case in self.manifest["cases"].values():
            text = common.generate_potential(case)
            common.validate_generated_potential(case, text)
        self.assertTrue(
            common.generate_potential(self.manifest["cases"]["carbon_nep5"])
            .splitlines()[0]
            .startswith("nep5 ")
        )
        typewise_cutoff = next(
            line
            for line in common.generate_potential(
                self.manifest["cases"]["dense_water_typewise_cutoff"]
            ).splitlines()
            if line.startswith("cutoff ")
        )
        self.assertEqual(len(typewise_cutoff.split()), 7)
        flexible = common.generate_potential(self.manifest["cases"]["batio3_flexible_zbl"])
        self.assertIn("\nzbl 0 0\n", flexible)
        typewise_zbl = next(
            line
            for line in common.generate_potential(
                self.manifest["cases"]["batio3_typewise_zbl_cutoff"]
            ).splitlines()
            if line.startswith("zbl ")
        )
        self.assertEqual(len(typewise_zbl.split()), 4)

    def test_release_matrix_covers_backends_variants_and_nvt_statistics(self) -> None:
        release = self.manifest["profiles"]["release"]
        self.assertEqual(release["case_geometry"], "release")
        self.assertEqual(release["backends"], ["HostStaged", "CudaAware"])
        self.assertEqual(release["ranks"], [1, 2, 4, 8])
        self.assertEqual(release["seeds"], list(range(5)))
        self.assertIn("nvt", release["sections"])
        for case in (
            "carbon_nep5",
            "dense_water_typewise_cutoff",
            "batio3_flexible_zbl",
            "batio3_typewise_zbl_cutoff",
        ):
            self.assertIn(case, release["cases"])
        self.assertEqual(
            set(self.manifest["statistical_acceptance"]["metrics"]),
            {
                "temperature_mean_K",
                "temperature_std_K",
                "temperature_rmse_K",
                "msd_mean_A2",
                "msd_final_A2",
                "msd_slope_A2_per_fs",
            },
        )

    def test_manifest_separates_m1_and_m2a_configurations(self) -> None:
        smoke_carbon = common.profile_case(self.manifest, "smoke", "carbon_crystal")
        smoke_water = common.profile_case(self.manifest, "smoke", "dense_water")
        nightly_carbon = common.profile_case(self.manifest, "nightly", "carbon_crystal")
        nightly_water = common.profile_case(self.manifest, "nightly", "dense_water")
        nightly_water_typewise = common.profile_case(
            self.manifest, "nightly", "dense_water_typewise_cutoff"
        )
        release_water = common.profile_case(self.manifest, "release", "dense_water")
        self.assertEqual(runner.expected_domain_mode(smoke_carbon, 2), "m1-fallback")
        self.assertEqual(runner.expected_domain_mode(smoke_water, 2), "m2a")
        self.assertEqual(runner.expected_domain_mode(smoke_water, 4), "m1-fallback")
        self.assertEqual(runner.expected_domain_mode(nightly_carbon, 2), "m2a")
        self.assertEqual(runner.expected_domain_mode(nightly_water, 1), "m1-fallback")
        self.assertEqual(runner.expected_domain_mode(nightly_water, 2), "m2a")
        self.assertEqual(runner.expected_domain_mode(nightly_water, 4), "m2a")
        self.assertEqual(runner.expected_domain_mode(nightly_water_typewise, 4), "m2a")
        self.assertEqual(runner.expected_domain_mode(release_water, 8), "m2a")
        self.assertEqual(
            runner.ordered_configurations(
                nightly_water, [1, 2, 4], ["HostStaged", "CudaAware"]
            ),
            [
                ("HostStaged", 1, "m1-fallback"),
                ("HostStaged", 2, "m2a"),
                ("HostStaged", 4, "m2a"),
                ("CudaAware", 1, "m1-fallback"),
                ("CudaAware", 2, "m2a"),
                ("CudaAware", 4, "m2a"),
            ],
        )
        with self.assertRaises(common.baseline.BaselineError):
            runner.expected_domain_mode(nightly_water, 3)

    def test_candidate_validation_uses_distinct_m1_and_m2a_contracts(self) -> None:
        def write_stage(root: Path, mode: str) -> Path:
            stage = root / mode
            stage.mkdir()
            center = (
                "replicated-full false"
                if mode == "m1-fallback"
                else "local-domain-force-centers true"
            ).split()
            lines = [
                "DMGMD_MPI implementation=OpenMPI",
                "DMGMD_MPI rank=0 world_size=2 local_rank=0 local_size=2 "
                "hostname=node cuda_device=0 cuda_uuid=uuid0 "
                "cuda_aware_capability=supported cuda_aware_self_test=not-run "
                "backend=HostStaged",
                "DMGMD_MPI rank=1 world_size=2 local_rank=1 local_size=2 "
                "hostname=node cuda_device=1 cuda_uuid=uuid1 "
                "cuda_aware_capability=supported cuda_aware_self_test=not-run "
                "backend=HostStaged",
                f"DMGMD_DOMAIN mode={mode}",
                "DMGMD_CENTER_PARTITION global_count=4 missing=0 overlapping=0 "
                "owned_output_coverage=complete "
                f"nep_kernel_centers={center[0]} "
                f"nep_N1_N2_shard_complete={center[1]}",
                "DMGMD_CENTER_OWNERSHIP rank=0 owned_count=2",
                "DMGMD_CENTER_OWNERSHIP rank=1 owned_count=2",
            ]
            if mode == "m2a":
                lines.extend(
                    (
                        "DMGMD_DOMAIN_LAYOUT rank=0 step=0 owned=2 dep_left=0 "
                        "dep_right=1 coord_left=0 coord_right=0 local_count=3",
                        "DMGMD_DOMAIN_LAYOUT rank=1 step=0 owned=2 dep_left=1 "
                        "dep_right=0 coord_left=0 coord_right=0 local_count=3",
                    )
                )
            lines.extend(
                (
                    "DMGMD_COMM accounting=collective-buffer-bytes log_interval=1",
                    "DMGMD_COMM step=1 backend=HostStaged collective_calls=2 "
                    "mpi_input_bytes_global=1 mpi_output_bytes_global=1 "
                    "device_to_host_bytes_global=1 host_to_device_bytes_global=1 "
                    "output_download_bytes=0",
                    "DMGMD_TIMING phase=total sequence=0 steps=1 atoms=4 ranks=2 "
                    "backend=HostStaged seconds_min=1 seconds_mean=1 seconds_max=1 "
                    "global_atom_steps_per_second=4",
                )
            )
            (stage / "execution.stdout").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
            (stage / "run.in").write_text("run 1\n", encoding="utf-8")
            return stage

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            m1 = write_stage(root, "m1-fallback")
            m2a = write_stage(root, "m2a")
            runner.validate_candidate_stage(m1, 2, "HostStaged", "m1-fallback")
            runner.validate_candidate_stage(m2a, 2, "HostStaged", "m2a")
            performance = root / "m2a-performance"
            performance.mkdir()
            performance_stdout = "\n".join(
                line
                for line in (m2a / "execution.stdout").read_text(
                    encoding="utf-8"
                ).splitlines()
                if not line.startswith("DMGMD_DOMAIN_LAYOUT " )
            ) + "\n"
            (performance / "execution.stdout").write_text(
                performance_stdout, encoding="utf-8"
            )
            (performance / "run.in").write_text("run 1\n", encoding="utf-8")
            runner.validate_candidate_stage(
                performance, 2, "HostStaged", "m2a",
                require_domain_layouts=False,
            )
            with self.assertRaises(common.baseline.BaselineError):
                runner.validate_candidate_stage(
                    performance, 2, "HostStaged", "m2a"
                )
            with self.assertRaises(common.baseline.BaselineError):
                runner.validate_candidate_stage(
                    m2a, 2, "HostStaged", "m2a",
                    require_domain_layouts=False,
                )
            with self.assertRaises(common.baseline.BaselineError):
                runner.validate_candidate_stage(m1, 2, "HostStaged", "m2a")
            with self.assertRaises(common.baseline.BaselineError):
                runner.validate_candidate_stage(m2a, 2, "HostStaged", "m1-fallback")

    def test_candidate_environments_enable_parsed_diagnostics(self) -> None:
        # The m2a stage validation parses per-rank DMGMD_DOMAIN_LAYOUT
        # records, so candidate stages must explicitly enable domain
        # diagnostics (the runtime default is quiet); the reference never
        # receives candidate-only variables.
        reference_env = dict(os.environ)
        reference_env["DMGMD_COMM_LOG_INTERVAL"] = "7"
        reference_env["DMGMD_DOMAIN_DIAGNOSTICS"] = "1"
        reference_env["DMGMD_DOMAIN_TIMING"] = "1"
        with unittest.mock.patch.dict(os.environ, reference_env):
            cleaned = runner.reference_environment("3")
        self.assertNotIn("DMGMD_COMM_LOG_INTERVAL", cleaned)
        self.assertNotIn("DMGMD_DOMAIN_DIAGNOSTICS", cleaned)
        self.assertNotIn("DMGMD_DOMAIN_TIMING", cleaned)
        candidate_env = dict(os.environ)
        candidate_env["DMGMD_COMM_LOG_INTERVAL"] = "7"
        candidate_env["DMGMD_DOMAIN_DIAGNOSTICS"] = "0"
        candidate_env["DMGMD_DOMAIN_TIMING"] = "1"
        with unittest.mock.patch.dict(os.environ, candidate_env):
            staged = runner.candidate_environment(["0", "1"], 2, "HostStaged", 10)
        self.assertEqual(staged["DMGMD_COMM_LOG_INTERVAL"], "10")
        self.assertEqual(staged["DMGMD_DOMAIN_DIAGNOSTICS"], "1")
        self.assertNotIn("DMGMD_DOMAIN_TIMING", staged)
        with unittest.mock.patch.dict(os.environ, candidate_env):
            timed = runner.candidate_environment(
                ["0", "1"], 2, "HostStaged", 10, True
            )
        self.assertEqual(timed["DMGMD_DOMAIN_TIMING"], "1")
        with unittest.mock.patch.dict(os.environ, candidate_env):
            performance = runner.candidate_environment(
                ["0", "1"], 2, "HostStaged", 10, False, True
            )
        self.assertEqual(performance["DMGMD_DOMAIN_DIAGNOSTICS"], "0")
        self.assertNotIn("DMGMD_DOMAIN_TIMING", performance)

    def test_generated_models_have_zero_mass_weighted_momentum(self) -> None:
        case = self.manifest["cases"]["dense_water"]
        text = common.generate_model(case, 3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.xyz"
            path.write_text(text, encoding="utf-8")
            frame = common.baseline.parse_xyz(path)[0]
        masses = [item[0] for item in common.frame_vectors(frame, "mass")]
        velocities = common.frame_vectors(frame, "vel")
        for axis in range(3):
            momentum = sum(mass * velocity[axis] for mass, velocity in zip(masses, velocities))
            self.assertLess(abs(momentum), 1.0e-9)

    def test_water_occupies_each_three_dimensional_grid_cell_once(self) -> None:
        case = self.manifest["cases"]["dense_water"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "water.xyz"
            path.write_text(common.generate_model(case, 0), encoding="utf-8")
            frame = common.baseline.parse_xyz(path)[0]
        species = common.frame_species(frame)
        positions = common.frame_vectors(frame, "pos")
        spacing = float(case["spacing_A"])
        oxygen_cells = {
            tuple(int(position[axis] / spacing) for axis in range(3))
            for atom, position in enumerate(positions)
            if species[atom] == "O"
        }
        self.assertEqual(len(oxygen_cells), 16 ** 3)

    def test_zbl_fixture_contains_the_locked_close_pair(self) -> None:
        case = self.manifest["cases"]["batio3_zbl"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batio3.xyz"
            path.write_text(common.generate_model(case, 0), encoding="utf-8")
            frame = common.baseline.parse_xyz(path)[0]
        positions = common.frame_vectors(frame, "pos")
        central_cell = ((5 * 10 + 5) * 10 + 5) * 5
        displacement = [
            positions[central_cell + 1][axis] - positions[central_cell][axis]
            for axis in range(3)
        ]
        distance = sum(value * value for value in displacement) ** 0.5
        self.assertGreater(distance, 0.98)
        self.assertLess(distance, 1.02)

    def test_frame_to_model_preserves_replay_fields(self) -> None:
        case = self.manifest["cases"]["carbon_crystal"]
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.xyz"
            second = Path(directory) / "second.xyz"
            first.write_text(common.generate_model(case, 0), encoding="utf-8")
            frame = common.baseline.parse_xyz(first)[0]
            second.write_text(common.frame_to_model(frame), encoding="utf-8")
            replay = common.baseline.parse_xyz(second)[0]
        self.assertEqual(frame["natoms"], replay["natoms"])
        self.assertEqual(common.frame_species(frame), common.frame_species(replay))
        self.assertEqual(common.frame_vectors(frame, "pos"), common.frame_vectors(replay, "pos"))
        self.assertEqual(common.frame_vectors(frame, "vel"), common.frame_vectors(replay, "vel"))

    def test_nve_metrics_include_true_initial_energy(self) -> None:
        header_zero = """# dump_thermo 1
# format_version 1
# num_atoms 2
# dt_output 0.0000000000e+00 fs
# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz
0 2 8 0 0 0 0 0 0 1 0 0 0 1 0 0 0 1
"""
        header_long = """# dump_thermo 10
# format_version 1
# num_atoms 2
# dt_output 1.0000000000e+00 fs
# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz
0 2 10 0 0 0 0 0 0 1 0 0 0 1 0 0 0 1
0 2 12 0 0 0 0 0 0 1 0 0 0 1 0 0 0 1
"""
        with tempfile.TemporaryDirectory() as directory:
            initial = Path(directory) / "initial.out"
            long = Path(directory) / "long.out"
            initial.write_text(header_zero, encoding="utf-8")
            long.write_text(header_long, encoding="utf-8")
            metrics = common.nve_metrics(initial, long, 2)
        self.assertEqual(metrics["samples"], 3.0)
        self.assertAlmostEqual(metrics["duration_fs"], 2.0)
        self.assertAlmostEqual(metrics["initial_energy_per_atom_eV"], 5.0)
        self.assertAlmostEqual(metrics["max_excursion_per_atom_eV"], 2.0)
        self.assertAlmostEqual(metrics["drift_slope_eV_per_atom_fs"], 1.0)

    def test_noninferiority_uses_median_and_q95(self) -> None:
        reference = [{"metric": value} for value in (1.0, 1.1, 0.9)]
        actual = [{"metric": value} for value in (1.05, 1.0, 1.1)]
        report = common.enforce_noninferiority(
            reference, actual, ("metric",), 0.25, {"metric": 0.01}, "unit"
        )
        self.assertTrue(report["metric"]["passed"])
        with self.assertRaises(common.baseline.BaselineError):
            common.enforce_noninferiority(
                reference,
                [{"metric": value} for value in (2.0, 2.1, 2.2)],
                ("metric",),
                0.25,
                {"metric": 0.01},
                "unit",
            )

    def test_nvt_temperature_msd_and_time_averaged_rdf_statistics(self) -> None:
        thermo = """# dump_thermo 1
# format_version 1
# num_atoms 4
# dt_output 1.0000000000e+00 fs
# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz
290 0 0 0 0 0 0 0 0 10 0 0 0 10 0 0 0 10
300 0 0 0 0 0 0 0 0 10 0 0 0 10 0 0 0 10
310 0 0 0 0 0 0 0 0 10 0 0 0 10 0 0 0 10
"""
        frames = []
        atoms = (("A", 1.0, 1.0, 1.0), ("A", 4.0, 1.0, 1.0),
                 ("B", 1.0, 4.0, 1.0), ("B", 4.0, 4.0, 1.0))
        for time, shift in ((0.0, 0.0), (1.0, 1.0), (2.0, 2.0)):
            frames.extend(
                (
                    "4",
                    f'Time={time} pbc="T T T" Lattice="10 0 0 0 10 0 0 0 10" '
                    'Properties=species:S:1:pos:R:3:unwrapped_position:R:3',
                    *(
                        f"{name} {x} {y} {z} {x + shift} {y} {z}"
                        for name, x, y, z in atoms
                    ),
                )
            )
        with tempfile.TemporaryDirectory() as directory:
            thermo_path = Path(directory) / "thermo.out"
            trajectory_path = Path(directory) / "nvt.xyz"
            thermo_path.write_text(thermo, encoding="utf-8")
            trajectory_path.write_text("\n".join(frames) + "\n", encoding="utf-8")
            temperatures = common.temperature_statistics(thermo_path, 300.0)
            msd = common.msd_statistics(trajectory_path)
            rdf = common.time_averaged_rdf(trajectory_path, 4.5, 9)
        self.assertAlmostEqual(temperatures["temperature_mean_K"], 300.0)
        self.assertAlmostEqual(temperatures["temperature_rmse_K"], (200.0 / 3.0) ** 0.5)
        self.assertAlmostEqual(msd["msd_mean_A2"], 5.0 / 3.0)
        self.assertAlmostEqual(msd["msd_final_A2"], 4.0)
        self.assertAlmostEqual(msd["msd_slope_A2_per_fs"], 2.0)
        self.assertEqual(rdf["frames"], 3)
        self.assertEqual(common.rdf_l1(rdf, rdf), 0.0)

    def test_statistical_equivalence_is_two_sided(self) -> None:
        reference = [{"metric": value} for value in (10.0, 11.0, 12.0)]
        actual = [{"metric": value} for value in (10.5, 11.5, 12.5)]
        report = common.enforce_distribution_equivalence(
            reference,
            actual,
            {"metric": {"relative": 0.1, "absolute": 0.1}},
            "unit",
        )
        self.assertTrue(report["metric"]["passed"])
        with self.assertRaises(common.baseline.BaselineError):
            common.enforce_distribution_equivalence(
                reference,
                [{"metric": value} for value in (5.0, 6.0, 7.0)],
                {"metric": {"relative": 0.1, "absolute": 0.1}},
                "unit",
            )

    def test_execute_md_reuses_only_completed_matching_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "fake-md.py"
            executable.write_text(
                "\n".join(
                    (
                        f"#!{sys.executable}",
                        "from pathlib import Path",
                        "counter = Path('runs.txt')",
                        "count = int(counter.read_text()) + 1 if counter.exists() else 1",
                        "counter.write_text(str(count))",
                        "Path('result.out').write_text('complete\\n')",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            executable.chmod(0o755)
            stage = root / "stage"
            arguments = (
                executable,
                (),
                "model\n",
                "run\n",
                "potential\n",
                stage,
                os.environ,
                10,
                ("result.out",),
            )
            common.execute_md(*arguments, resume=True)
            common.execute_md(*arguments, resume=True)
            self.assertEqual((stage / "runs.txt").read_text(encoding="utf-8"), "1")
            self.assertEqual(common.stage_checkpoint_provenance(stage), "executed")
            (stage / "result.out").write_text("corrupt\n", encoding="utf-8")
            common.execute_md(*arguments, resume=True)
            self.assertEqual((stage / "result.out").read_text(encoding="utf-8"), "complete\n")
            self.assertEqual(len(list(root.glob("stage.failed-*"))), 1)

    def test_execute_md_can_explicitly_adopt_legacy_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "unused-md.py"
            executable.write_text(f"#!{sys.executable}\n", encoding="utf-8")
            executable.chmod(0o755)
            stage = root / "stage"
            stage.mkdir()
            for name, text in (
                ("model.xyz", "model\n"),
                ("run.in", "run\n"),
                ("nep.txt", "potential\n"),
                ("result.out", "complete\n"),
                ("execution.stdout", "legacy output\n"),
                ("execution.stderr", ""),
            ):
                (stage / name).write_text(text, encoding="utf-8")
            common.execute_md(
                executable,
                (),
                "model\n",
                "run\n",
                "potential\n",
                stage,
                os.environ,
                10,
                ("result.out",),
                resume=True,
                adopt_existing=True,
            )
            self.assertEqual(
                common.stage_checkpoint_provenance(stage),
                "adopted-existing-unverified-executable",
            )

    def test_execute_md_retries_once_from_a_clean_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            counter = root / "attempts.txt"
            executable = root / "fake-transient.py"
            executable.write_text(
                "\n".join(
                    (
                        f"#!{sys.executable}",
                        "import sys",
                        "from pathlib import Path",
                        f"counter = Path({str(counter)!r})",
                        "attempt = int(counter.read_text()) + 1 if counter.exists() else 1",
                        "counter.write_text(str(attempt))",
                        "if attempt == 1:",
                        "    Path('partial.out').write_text('must not survive retry\\n')",
                        "    print('transient failure', file=sys.stderr)",
                        "    sys.exit(1)",
                        "assert not Path('partial.out').exists()",
                        "Path('result.out').write_text('complete\\n')",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            executable.chmod(0o755)
            stage = root / "stage"
            common.execute_md(
                executable,
                (),
                "model\n",
                "run\n",
                "potential\n",
                stage,
                os.environ,
                10,
                ("result.out",),
            )
            self.assertEqual(counter.read_text(encoding="utf-8"), "2")
            self.assertFalse((stage / "partial.out").exists())
            checkpoint = json.loads(
                (stage / common.STAGE_COMPLETE_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint["attempt"], 2)
            self.assertEqual(checkpoint["max_attempts"], 2)
            self.assertEqual(len(checkpoint["retry_failures"]), 1)
            archived = list(root.glob("stage.failed-attempt-1-*"))
            self.assertEqual(len(archived), 1)
            self.assertIn(
                "transient failure",
                (archived[0] / "execution.stderr").read_text(encoding="utf-8"),
            )

    def test_execute_md_records_cuda_oom_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            counter = root / "attempts.txt"
            executable = root / "fake-oom.py"
            executable.write_text(
                f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                f"counter = Path({str(counter)!r})\n"
                "attempt = int(counter.read_text()) + 1 if counter.exists() else 1\n"
                "counter.write_text(str(attempt))\n"
                "print('CUDA Error: out of memory', file=sys.stderr)\nsys.exit(1)\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            stage = root / "stage"
            with self.assertRaises(common.baseline.BaselineError):
                common.execute_md(
                    executable,
                    (),
                    "model\n",
                    "run\n",
                    "potential\n",
                    stage,
                    os.environ,
                    10,
                    ("result.out",),
                    resume=True,
                )
            failure = json.loads(
                (stage / common.STAGE_FAILURE_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(failure["failure"], "cuda-out-of-memory")
            self.assertEqual(failure["attempt"], 2)
            self.assertEqual(failure["max_attempts"], 2)
            self.assertEqual(counter.read_text(encoding="utf-8"), "2")


if __name__ == "__main__":
    unittest.main()
