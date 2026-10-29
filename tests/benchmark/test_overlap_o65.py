#!/usr/bin/env python3
"""O6.5 coverage and demand-thermo-independent activity-window contracts."""
import unittest
from analyze_overlap_o65 import coverage, steady_groups
from run_overlap_o65 import schedule, SCOPES
from run_overlap_o6 import KERNELS


class CoverageTests(unittest.TestCase):
    def test_complement_covers_each_center_once(self):
        mask = [int(i % 3 == 0) for i in range(137)]
        for width in (32, 64):
            a, b = [coverage(mask, len(mask), width, p) for p in (False, True)]
            self.assertEqual(a['active_lanes']+b['active_lanes'], len(mask))
            self.assertEqual(a['groups'], b['groups'])
            self.assertEqual(a['mixed']+a['full']+a['empty'], a['groups'])

    def test_sparse_masks_can_cover_all_warps_and_blocks(self):
        mask = [int(i % 2 == 0) for i in range(128)]
        for width in (32, 64):
            for complement in (False, True):
                row = coverage(mask, 128, width, complement)
                self.assertEqual(row['nonempty'], 128//width)
                self.assertEqual(row['mixed'], row['groups'])
                self.assertEqual(row['active_lanes'], 64)

    def test_tail_padding_is_not_a_full_warp(self):
        row = coverage([1]*65, 65, 32)
        self.assertEqual((row['full'], row['mixed'], row['padded_lanes']), (2, 1, 31))
        self.assertEqual(row['active_lane_histogram'], {1: 1, 32: 2})

    def test_empty_phase_and_invalid_range(self):
        self.assertEqual(coverage([0]*64, 64, 64)['empty'], 1)
        with self.assertRaises(ValueError): coverage([1], 2, 32)


class ActivityTests(unittest.TestCase):
    @staticmethod
    def fixture():
        names = [*KERNELS]  # initial setup is excluded
        traces = []
        for step in range(1, 4):
            names += ['velocity_verlet_range', 'inspect_domain_cache',
                      *(KERNELS if step == 1 else KERNELS*2), 'velocity_verlet_range']
            # O8 computes thermo only once here; segment setup is another
            # full force, between warmup and formal step inspections.
            if step == 1: names += ['normalize_global_thermo', *KERNELS]
            traces.append(dict(step=str(step), sequence='0' if step == 1 else '1'))
        kernels = [dict(name=name, start=i*10, end=i*10+5) for i, name in enumerate(names)]
        return kernels, traces

    def test_setup_and_sparse_thermo(self):
        kernels, traces = self.fixture()
        groups = steady_groups(kernels, traces, 1, 2)
        self.assertEqual([len(g['force']) for g in groups], [10, 10])
        self.assertEqual([g['trace']['step'] for g in groups], ['2', '3'])

    def test_missing_launch_is_rejected(self):
        kernels, traces = self.fixture()
        kernels.pop(next(i for i, k in enumerate(kernels) if k['start'] > 200 and k['name']==KERNELS[-1]))
        with self.assertRaises(ValueError): steady_groups(kernels, traces, 1, 2)

    def test_wrong_step_or_inspection_count_is_rejected(self):
        kernels, traces = self.fixture()
        traces[-1]['step'] = '4'
        with self.assertRaises(ValueError): steady_groups(kernels, traces, 1, 2)
        kernels = [k for k in kernels if k['name'] != 'inspect_domain_cache']
        with self.assertRaises(ValueError): steady_groups(kernels, traces, 1, 2)

    def test_randomization_preserves_independent_blocks(self):
        jobs = schedule(5, 8, {c:100 for c, _ in SCOPES}, 'main')
        self.assertEqual(len(jobs), 30)
        for repeat in range(5):
            block = [j for j in jobs if j['repeat']==repeat]
            self.assertEqual(len(block), 6)
            self.assertEqual({(j['case'],j['mode']) for j in block},
                {(c,m) for c,_ in SCOPES for m in ('full','split-serial','overlap-wait')})


if __name__ == '__main__':
    unittest.main()
