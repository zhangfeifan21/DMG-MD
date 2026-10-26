#!/usr/bin/env python3
"""Consumer schedule and exact collective-accounting regression cases."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'mpi'))
from run_mpi_domain import thermo_consuming_steps


class ThermoConsumers(unittest.TestCase):
    def test_union_includes_xyz_headers_and_excludes_restart(self):
        run = ('ensemble nve\ndump_thermo 3\ndump_xyz 4 xyz.xyz\n'
               'dump_xyz 5 other.xyz\ndump_restart 2\nrun 13\n')
        self.assertEqual(thermo_consuming_steps(run), {3, 4, 5, 6, 8, 9, 10, 12})

    def test_run_boundaries_reset_measurements_and_phase(self):
        run = ('ensemble nve\ndump_thermo 3\nrun 5\nrun 4\n'
               'dump_xyz 2 xyz.xyz\nrun 5\nensemble nvt_ber 300 350 1000\nrun 4\n'
               'ensemble nve\ndump_thermo 2\nrun 5\n')
        self.assertEqual(thermo_consuming_steps(run), {3, 11, 13, 15, 16, 17, 18, 20, 22})

    def test_no_consumers_and_output_beyond_segment(self):
        self.assertEqual(thermo_consuming_steps('ensemble nve\nrun 9\n'), set())
        self.assertEqual(thermo_consuming_steps(
            'ensemble nve\ndump_thermo 10\ndump_xyz 12 xyz.xyz\nrun 9\n'), set())

    def test_nvt_feedback_survives_empty_measurements_and_following_run(self):
        self.assertEqual(thermo_consuming_steps(
            'ensemble nvt_ber 300 350 1000\nrun 3\nrun 2\n'), {1, 2, 3, 4, 5})


if __name__ == '__main__':
    unittest.main()
