#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import long_nve_common as common


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
        self.assertEqual(release["backends"], ["HostStaged", "CudaAware"])
        self.assertEqual(release["ranks"], [1, 2, 4, 8])
        self.assertEqual(release["seeds"], list(range(10)))
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


if __name__ == "__main__":
    unittest.main()
