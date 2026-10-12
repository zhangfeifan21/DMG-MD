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

    def test_physics_gate_rejects_force_change(self):
        atom = {"species": "C", "pos": (1., 2., 3.), "vel": (0., 0., 0.),
                "force": (1., 2., 3.), "energy": -1., "virial": (0.,) * 6}
        reference = {"atoms": [atom], "energy": -1.}
        modified = {"atoms": [dict(atom, force=(1., 2., 3.01))], "energy": -1.}
        tolerance = __import__("json").loads((HERE / "manifest.json").read_text())["tolerances"]
        self.assertFalse(compare_static.compare(reference, modified, tolerance)["pass"])


if __name__ == "__main__":
    unittest.main()
