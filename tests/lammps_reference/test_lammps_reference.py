#!/usr/bin/env python3
"""CPU checks for cross-engine input equivalence and physical comparison."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tests/long_nve"))
import compare_static  # noqa: E402
import lammps_inputs  # noqa: E402
import long_nve_common as fixtures  # noqa: E402


class LammpsReferenceTests(unittest.TestCase):
    def test_equal_typewise_cutoff_normalizes_to_same_water_model(self):
        cases = fixtures.load_manifest()["cases"]
        source = fixtures.generate_potential(cases["dense_water_typewise_cutoff"])
        target, record = lammps_inputs.normalize_potential(source)
        base = fixtures.generate_potential(cases["dense_water"])
        self.assertEqual(target, base)
        self.assertEqual(record["transform"]["kind"], "equal_typewise_to_uniform")
        self.assertNotEqual(record["source_sha256"], record["lammps_sha256"])
        with self.assertRaisesRegex(ValueError, "nonuniform typewise cutoff"):
            lammps_inputs.normalize_potential(source.replace(
                "cutoff 6.0 4.0 6.0 4.0", "cutoff 6.0 4.0 5.0 4.0"))

    def test_data_uses_potential_order_and_metal_velocity_units(self):
        model = (ROOT / "tests/baseline/inputs/multi_nvt_restart/model.xyz").read_text()
        potential = (ROOT / "tests/baseline/inputs/potentials/nep_water.txt").read_text()
        data = lammps_inputs.data_file(model, potential)
        masses = data.split("Masses\n\n", 1)[1].split("\n\nAtoms", 1)[0].splitlines()
        self.assertEqual([float(line.split()[1]) for line in masses], [15.999, 1.008])
        atoms = data.split("Atoms # atomic\n\n", 1)[1].split("\n\nVelocities", 1)[0].splitlines()
        velocities = data.split("Velocities\n\n", 1)[1].splitlines()
        self.assertEqual(atoms[0].split()[:2], ["1", "1"])
        self.assertEqual(velocities[0].split()[:4], ["1", "2", "-1", "1.5"])

    def test_active_species_keep_the_potential_order(self):
        potential = "nep4 3 Ag Cu Ni\n"
        self.assertEqual(lammps_inputs.lammps_elements(potential, ["Ni", "Cu"]), ["Cu", "Ni"])
        script = lammps_inputs.input_file(potential, 0, 0, active_species=["Ni", "Cu"])
        self.assertIn("pair_coeff * * Cu Ni\n", script)

    def test_upper_face_images_follow_gpumd_wrapping_and_keep_source_ids(self):
        model = ('2\npbc="T T T" Lattice="163.24 0 0 0 446.6 0 0 0 163.24" '
                 'Properties=species:S:1:pos:R:3:vel:R:3\nC 0 1 2 0 0 1\nC 163.24 2 3 0 0 1\n')
        data, faces = lammps_inputs.model_data(model, "nep4 1 C", gpumd_wraps=2)
        self.assertEqual(faces, ([2], [], [], [], [], []))
        self.assertEqual(data, lammps_inputs.data_file(model, "nep4 1 C"))
        script = lammps_inputs.input_file("nep4 1 C", 0, 0, geometry="gpumd_5_9_1", initial_faces=faces)
        self.assertIn("group _dmgmd_nep_high_x id 2\n", script)
        self.assertIn("initial_face_images _dmgmd_nep_high_x NULL NULL NULL NULL NULL\n", script)
        model = model.replace("163.24 0 0 0 446.6 0 0 0 163.24",
                              "128.51999999999998 0 0 0 349.85999999999996 0 0 0 128.51999999999998")
        model = model.replace("C 163.24 2 3", "C 1 349.85999999999996 3")
        self.assertEqual(lammps_inputs.model_data(model, "nep4 1 C", gpumd_wraps=2)[1], ([], [], [], [], [], []))
        # The near-upper y value reaches L on the first GPUMD force entry and wraps on the second.
        near = model.replace("C 1 349.85999999999996 3", "C 1 349.8599999999999 3")
        self.assertEqual(lammps_inputs.model_data(near, "nep4 1 C", gpumd_wraps=1)[1], ([], [], [], [], [], []))
        self.assertEqual(lammps_inputs.model_data(near, "nep4 1 C", gpumd_wraps=2)[1], ([], [], [], [], [2], []))
        # A tiny negative input can select the upper y face at setup under the same cell.
        tiny = model.replace("C 1 349.85999999999996 3", "C 1 -1e-15 3")
        self.assertEqual(lammps_inputs.model_data(tiny, "nep4 1 C", gpumd_wraps=1)[1], ([], [2], [], [], [], []))

    def test_periodic_geometry_requires_confirmation_from_the_pair_style(self):
        potential = "nep4 1 C\n"
        native = lammps_inputs.input_file(potential, 0, 0)
        mode = "gpumd_5_9_1"
        selected = lammps_inputs.input_file(potential, 0, 0, geometry=mode)
        self.assertIn("pair_style matpl/nep/kk nep.txt\n", native)
        self.assertIn(f"pair_style matpl/nep/kk nep.txt geometry {mode}\n", selected)
        with self.assertRaises(ValueError):
            lammps_inputs.verify_geometry(selected, mode)
        marker = f"MatPL NEP geometry: {mode}\n"
        lammps_inputs.verify_geometry(selected + marker, mode)
        lammps_inputs.verify_geometry(native, "native")
        with self.assertRaises(ValueError):
            lammps_inputs.verify_geometry(marker, "native")

    def test_physics_gate_rejects_force_change(self):
        atom = {"species": "C", "pos": (1., 2., 3.), "vel": (0., 0., 0.),
                "force": (1., 2., 3.), "energy": -1., "virial": (0.,) * 6}
        reference = {"atoms": [atom], "energy": -1.}
        modified = {"atoms": [dict(atom, force=(1., 2., 3.01))], "energy": -1.}
        tolerance = __import__("json").loads((HERE / "manifest.json").read_text())["tolerances"]
        self.assertFalse(compare_static.compare(reference, modified, tolerance)["pass"])


if __name__ == "__main__":
    unittest.main()
