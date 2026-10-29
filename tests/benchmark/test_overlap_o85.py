#!/usr/bin/env python3
"""Reject incomplete producer experiments and incorrect launch order."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from analyze_overlap_o85 import KERNELS, PRODUCER_KERNELS, steady_groups, pipeline
from run_overlap_o85 import schedule, VARIANTS, main_jobs, validate_trial, experiment
from cpu_binding import digest


class ProducerExperimentTests(unittest.TestCase):
    def test_six_controls_each_independent_block(self):
        # The inherited trial() calls identity() with its default o4 key.
        with patch('run_overlap_o85.o5.Experiment', return_value=SimpleNamespace(binaries={'o4': 'retained'})):
            current = experiment(Path('/frozen'))
        self.assertEqual(current.binaries['o4'], 'retained')
        self.assertEqual(current.binaries['candidate'], Path('/frozen/dmg-md-candidate'))
        jobs = schedule(5, 2026102782, {'carbon_200k':1600, 'water_400k':4500}, 'main')
        self.assertEqual(len(jobs), 120)
        self.assertEqual(len({j['id'] for j in jobs}), 120)
        for block in range(5):
            for case in ('carbon_200k', 'water_400k'):
                for backend in ('HostStaged', 'CudaAware'):
                    self.assertEqual({(j['version'], j['mode']) for j in jobs if
                        (j['repeat'], j['case'], j['backend']) == (block, case, backend)}, set(VARIANTS))

    def test_thermo_free_step_delimiters_and_producer_order(self):
        names = ['inspect_domain_cache', *PRODUCER_KERNELS, 'velocity_verlet_range']
        rows = [dict(name=n, start=i, end=i+1) for i, n in enumerate(names)]
        traces = [dict(step='1', sequence='1')]
        self.assertEqual(len(steady_groups(rows, traces, 0, 1)[0]['force']), 7)
        rows[5]['name'] = 'find_descriptor'  # Force consumer was not launched.
        with self.assertRaises(ValueError): steady_groups(rows, traces, 0, 1)

    def test_rebuild_is_complete_full_pipeline(self):
        self.assertEqual(pipeline('producer-wait', False), KERNELS)
        self.assertEqual(pipeline('producer-serial'), PRODUCER_KERNELS)
        self.assertEqual(pipeline('split-serial'), KERNELS*2)

    def test_missing_step_or_vv2_rejected(self):
        with self.assertRaises(ValueError): steady_groups([], [dict(step='1', sequence='1')], 0, 1)
        with self.assertRaises(ValueError):
            steady_groups([dict(name='inspect_domain_cache', start=0)], [dict(step='1', sequence='1')], 0, 1)

    def test_fallback_is_not_candidate_evidence(self):
        job = dict(id='test', mode='producer-wait', ranks=2, version='candidate', schedule='producer')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); directory = root/'main/test'; directory.mkdir(parents=True)
            (directory/'result.json').write_text(json.dumps(dict(job, status='PASS', actual_partition=dict(split_calls=0))))
            (directory/'stdout.txt').write_text('')
            with patch('run_overlap_o85.o5.validate_schedule', return_value={'calls': 0}):
                with self.assertRaisesRegex(ValueError, 'all-fallback'):
                    validate_trial(root, job, 'main')

    def test_incomplete_block_recovery_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); jobs = [dict(id='a', repeat=0), dict(id='b', repeat=0)]
            (root/'main-plan.json').write_text(json.dumps(dict(jobs=jobs)))
            (root/'main-recovery-plan.json').write_text(json.dumps(dict(
                original_sha256=digest(root/'main-plan.json'), replaced_blocks=[0],
                jobs=[dict(id='new-a', repeat=0), jobs[1]])))
            with self.assertRaises(ValueError): main_jobs(root)


if __name__ == '__main__': unittest.main()
