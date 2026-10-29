#!/usr/bin/env python3
"""O8.6 design and evidence gates reject incomplete/misidentified execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from run_overlap_o86 import schedule, VARIANTS, SCHEDULES, experiment, validate_trial, main_jobs
from analyze_overlap_o86 import steady_groups, pipeline, KERNELS, PRODUCER_KERNELS
from cpu_binding import digest

class CompactExperimentTests(unittest.TestCase):
    def test_complete_paired_design_and_independent_controls(self):
        jobs=schedule(5,2026102882,{'carbon_200k':1600,'water_400k':4500},'main')
        self.assertEqual(len(jobs),240)
        self.assertEqual(len({j['id'] for j in jobs}),240)
        for repeat in range(5):
            for case in ('carbon_200k','water_400k'):
                for backend in ('HostStaged','CudaAware'):
                    block=[j for j in jobs if (j['repeat'],j['case'],j['backend'])==(repeat,case,backend)]
                    self.assertEqual({(j['version'],j['mode']) for j in block},set(VARIANTS))
                    self.assertTrue(all(j['schedule']==SCHEDULES[j['mode']] for j in block))
        self.assertEqual(jobs,schedule(5,2026102882,{'carbon_200k':1600,'water_400k':4500},'main'))

    def test_baseline_default_identity_retained(self):
        with patch('run_overlap_o86.o5.Experiment',return_value=SimpleNamespace(binaries={'o4':'retained'})):
            current=experiment(Path('/frozen'))
        self.assertEqual(current.binaries['o4'],'retained')
        self.assertEqual(current.binaries['candidate'],Path('/frozen/dmg-md-candidate'))

    def test_consumer_order_and_rebuild(self):
        for mode in SCHEDULES:
            self.assertEqual(pipeline(mode,False),KERNELS)
        for mode in ('compact-wait','geometry-wait','producer-serial'):
            self.assertEqual(pipeline(mode),PRODUCER_KERNELS)
        self.assertEqual(pipeline('split-serial'),KERNELS*2)
        rows=[dict(name=n,start=i,end=i+1) for i,n in enumerate(['inspect_domain_cache',*PRODUCER_KERNELS,'velocity_verlet_range'])]
        self.assertEqual(len(steady_groups(rows,[dict(step='1',sequence='1')],0,1)[0]['force']),7)
        rows[5]['name']='find_descriptor'
        with self.assertRaises(ValueError): steady_groups(rows,[dict(step='1',sequence='1')],0,1)

    def test_missing_step_delimiter(self):
        with self.assertRaises(ValueError): steady_groups([], [dict(step='1',sequence='1')],0,1)
        with self.assertRaises(ValueError): steady_groups([dict(name='inspect_domain_cache',start=0)], [dict(step='1',sequence='1')],0,1)

    def test_fallback_is_not_mapping_evidence(self):
        job=dict(id='test',mode='compact-wait',ranks=2,version='candidate',schedule='producer-compact')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);directory=root/'main/test';directory.mkdir(parents=True)
            (directory/'result.json').write_text(json.dumps(dict(job,status='PASS',actual_partition=dict(split_calls=0))))
            (directory/'stdout.txt').write_text('')
            with patch('run_overlap_o86.o5.validate_schedule',return_value={'calls':0}):
                with self.assertRaisesRegex(ValueError,'all-fallback'): validate_trial(root,job,'main')

    def test_schedule_must_match_actual_compact_rule(self):
        job=dict(id='test',mode='geometry-wait',ranks=2,version='candidate',schedule='producer-geometry')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);directory=root/'pilot/test';directory.mkdir(parents=True)
            (directory/'result.json').write_text(json.dumps(dict(job,status='PASS',actual_partition=dict(split_calls=1))))
            (directory/'stdout.txt').write_text('DMGMD_SCHEDULE_CONFIG schedule=producer stream=default\n')
            with patch('run_overlap_o86.o5.validate_schedule',return_value={'calls':1}):
                with self.assertRaisesRegex(ValueError,'configuration differs'): validate_trial(root,job,'pilot')

    def test_incomplete_block_recovery_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);jobs=[dict(id='a',repeat=0),dict(id='b',repeat=0)]
            (root/'main-plan.json').write_text(json.dumps(dict(jobs=jobs)))
            (root/'main-recovery-plan.json').write_text(json.dumps(dict(original_sha256=digest(root/'main-plan.json'),replaced_blocks=[0],jobs=[dict(id='new-a',repeat=0),jobs[1]])))
            with self.assertRaises(ValueError): main_jobs(root)

if __name__=='__main__': unittest.main()
