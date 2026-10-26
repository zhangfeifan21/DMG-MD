#!/usr/bin/env python3
"""Reject incomplete steady traces and keep setup work out of O6 attribution."""
import unittest
import json
from pathlib import Path
import tempfile

from analyze_overlap_o6 import steady_groups
from run_overlap_o6 import KERNELS, jobs
from profile_overlap_o6_ncu import parse_metrics
from inventory_overlap_o6 import digest, verify


def fixture(split=False):
    kernels = []
    def add(name):
        start = len(kernels)*100
        kernels.append(dict(name=name, start=start, end=start+50))
    for step in range(3):
        if step in (0, 1):
            for name in KERNELS:
                add(name)  # one setup force for each of the two run segments
        add('velocity_verlet_range')
        add('wrap_positions')
        add('inspect_domain_cache')
        for name in KERNELS*(2 if split else 1):
            add(name)
        add('velocity_verlet_range')
        add('normalize_global_thermo')
    traces = [dict(sequence='0' if i==0 else '1', step=str(i+1),
                   begin_ns='900000000000', end_ns='900000000100') for i in range(3)]
    return kernels, traces


class SteadyWindowTests(unittest.TestCase):
    def test_setup_is_excluded_at_second_segment_boundary(self):
        kernels, traces = fixture()
        groups = steady_groups(kernels, traces, 1, 2)
        self.assertEqual(len(groups), 2)
        self.assertEqual([[k['name'] for k in g['force']] for g in groups], [KERNELS, KERNELS])
        self.assertEqual(groups[0]['kernels'][0]['name'], 'velocity_verlet_range')

    def test_two_phases_stay_in_same_step(self):
        kernels, traces = fixture(split=True)
        self.assertEqual([len(g['force']) for g in steady_groups(kernels, traces, 1, 2)], [10,10])

    def test_missing_kernel_is_rejected(self):
        kernels, traces = fixture()
        kernels.pop(-4)
        with self.assertRaisesRegex(ValueError, 'force pipeline'):
            steady_groups(kernels, traces, 1, 2)

    def test_missing_thermo_is_rejected(self):
        kernels, traces = fixture()
        with self.assertRaisesRegex(ValueError, 'thermo launch count'):
            steady_groups(kernels[:-1], traces, 1, 2)

    def test_missing_or_reordered_step_is_rejected(self):
        kernels, traces = fixture()
        with self.assertRaisesRegex(ValueError, 'trace records'):
            steady_groups(kernels, traces[:-1], 1, 2)
        with self.assertRaisesRegex(ValueError, 'step order'):
            steady_groups(kernels, [traces[0], traces[2], traces[1]], 1, 2)

    def test_missing_cache_inspection_is_rejected(self):
        kernels, traces = fixture()
        kernels = [k for k in kernels if k['name']!='inspect_domain_cache']
        with self.assertRaisesRegex(ValueError, 'cache inspection'):
            steady_groups(kernels, traces, 1, 2)

    def test_frozen_order_preserves_independent_blocks(self):
        args = (5, 123, dict(carbon_200k=1500, water_400k=4300),
                dict(carbon_200k=200, water_400k=1600))
        result = jobs(*args)
        self.assertEqual(result, jobs(*args))
        self.assertEqual(len(result), 45)
        self.assertEqual(len({j['id'] for j in result}), 45)
        for repeat in range(5):
            block = [j for j in result if j['repeat']==repeat]
            self.assertEqual(len(block), 9)
            self.assertEqual(len({(j['case'],j['ranks'],j['mode']) for j in block}), 9)

    def test_ncu_raw_csv_keeps_metric_units_and_quoted_values(self):
        text = ('==PROF== report imported\n'
                '"ID","Process ID","Kernel Name","Metric Name","Metric Unit","Metric Value"\n'
                '"0","123","find_descriptor","launch__registers_per_thread","register/thread","165"\n'
                '"0","123","find_descriptor","dram__bytes_read.sum","byte","1,024"\n')
        rows = parse_metrics(text)
        self.assertEqual(rows[0]['Metric Unit'], 'register/thread')
        self.assertEqual(rows[1]['Metric Value'], '1,024')

    def test_ncu_permission_failure_is_not_empty_success(self):
        with self.assertRaisesRegex(ValueError, 'header missing'):
            parse_metrics('==ERROR== ERR_NVGPUCTRPERM\n')
        with self.assertRaisesRegex(ValueError, 'empty or malformed'):
            parse_metrics('"ID","Process ID","Metric Name"\n')

    def test_raw_inventory_rejects_same_size_modified_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root/'raw.txt'
            path.write_text('good')
            (root/'raw-evidence-inventory.json').write_text(json.dumps(dict(
                raw={'raw.txt':dict(bytes=4,sha256=digest(path))},tools={})))
            verify(root)
            path.write_text('evil')
            with self.assertRaisesRegex(ValueError, 'raw evidence differs'):
                verify(root)

    def test_raw_inventory_rejects_missing_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'raw-evidence-inventory.json').write_text(json.dumps(dict(
                raw={'missing.txt':dict(bytes=0,sha256='missing')},tools={})))
            with self.assertRaisesRegex(ValueError, 'raw evidence differs'):
                verify(root)


if __name__ == '__main__':
    unittest.main()
