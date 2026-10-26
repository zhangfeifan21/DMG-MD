#!/usr/bin/env python3
"""O7 frozen resource identity must distinguish observations from constraints."""
import copy
import unittest

from run_overlap_o7 import binding_identity


class ResourceIdentity(unittest.TestCase):
    def setUp(self):
        self.plan = {2: dict(mapping=[dict(rank=0, os_cpus=[0, 96])],
            capacity=dict(t_begin_ns=1, t_end_ns=2, allowed_cpus=[0, 96],
                          ancestors=[{'path': '/cg', 'cpu.max': 'max 100000',
                                      'cpuset.cpus.effective': '0,96', 'cpu.stat': 'usage_usec 1'}]))}

    def test_observation_changes_and_json_round_trip(self):
        current = copy.deepcopy(self.plan)
        current[2]['capacity'].update(t_begin_ns=10, t_end_ns=20)
        current[2]['capacity']['ancestors'][0]['cpu.stat'] = 'usage_usec 999'
        self.assertEqual(binding_identity(self.plan), binding_identity(current))
        self.assertEqual(binding_identity(self.plan), binding_identity({'2': current[2]}))
        self.assertEqual(self.plan[2]['capacity']['ancestors'][0]['cpu.stat'], 'usage_usec 1')

    def test_mapping_changes_are_rejected(self):
        current = copy.deepcopy(self.plan)
        current[2]['mapping'][0]['os_cpus'] = [1, 97]
        self.assertNotEqual(binding_identity(self.plan), binding_identity(current))

    def test_resource_constraint_changes_are_rejected(self):
        for field, value in [('cpu.max', '100000 100000'), ('cpuset.cpus.effective', '0')]:
            with self.subTest(field=field):
                current = copy.deepcopy(self.plan)
                current[2]['capacity']['ancestors'][0][field] = value
                self.assertNotEqual(binding_identity(self.plan), binding_identity(current))


if __name__ == '__main__':
    unittest.main()
