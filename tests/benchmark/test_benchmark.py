#!/usr/bin/env python3
"""CPU-only tests for benchmark measurement and validity decisions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_benchmark as bench


class BenchmarkTests(unittest.TestCase):
    def test_thermo_headers_and_nonfinite_rejection(self):
        text = '# dump_thermo 5\n# columns T KE PE ...\n' + ' '.join(['1'] * 18) + '\n'
        self.assertEqual(bench.parse_thermo(text), [1.0] * 18)
        for bad in (text.replace('1 ', 'nan ', 1), text + ' '.join(['1'] * 18), '1 2 3'):
            with self.assertRaises(ValueError):
                bench.parse_thermo(bad)

    def test_all_profiles_have_valid_geometry(self):
        manifest = json.loads((Path(__file__).parent / 'manifest.json').read_text())
        for spec in manifest['cases'].values():
            base, _ = bench.geometry(spec, 1)
            for p in manifest['ranks']:
                case, lengths = bench.geometry(spec, p)
                self.assertGreater(lengths[1], max(lengths[0], lengths[2]))
                self.assertEqual(case['atoms'], base['atoms'] * (p if spec['scaling'] == 'weak' else 1))

    def test_gpumd_excludes_warmup_and_checks_multigpu(self):
        log = ('Try to use 4 GPUs for the NEP part.\n'
               'Time used for this run = 3 second.\nTime used for this run = 1.2e1 second.\n')
        self.assertEqual(bench.parse_timing(log, 'gpumd', 4, 1000, 20, 100, 'none'), 12)
        for bad in (log.replace('4 GPUs', '2 GPUs'), log + 'Time used for this run = 9 second.\n'):
            with self.assertRaises(ValueError):
                bench.parse_timing(bad, 'gpumd', 4, 1000, 20, 100, 'none')

    def test_dmgmd_rejects_fallback_wrong_steps_and_shared_device(self):
        log = ('DMGMD_DOMAIN mode=m2a axis=y\n'
               'DMGMD_MPI rank=0 cuda_uuid=A\nDMGMD_MPI rank=1 cuda_uuid=B\n'
               'DMGMD_TIMING phase=run sequence=0 steps=20 atoms=1000 ranks=2 backend=CudaAware seconds_max=1\n'
               'DMGMD_TIMING phase=run sequence=1 steps=100 atoms=1000 ranks=2 backend=CudaAware seconds_max=8\n')
        self.assertEqual(bench.parse_timing(log, 'dmgmd', 2, 1000, 20, 100, 'CudaAware'), 8)
        for bad in (log.replace('mode=m2a', 'mode=m1-fallback'),
                    log.replace('steps=100 ', 'steps=90 '), log.replace('cuda_uuid=B', 'cuda_uuid=A'),
                    log.replace('backend=CudaAware', 'backend=HostStaged')):
            with self.assertRaises(ValueError):
                bench.parse_timing(bad, 'dmgmd', 2, 1000, 20, 100, 'CudaAware')

    def test_summary_uses_matching_baselines_and_excludes_contamination(self):
        def row(p, seconds, scaling='strong', status='ok', engine='dmgmd'):
            return dict(case=scaling, scaling=scaling, engine=engine, backend='none' if engine == 'gpumd' else 'HostStaged',
                        ranks=p, atoms=1000 * (p if scaling == 'weak' else 1), steps=100,
                        seconds=seconds, status=status)
        data = [row(1, 8), row(2, 5), row(2, 0.1, status='contaminated'), row(2, 10, engine='gpumd'),
                row(1, 4, 'weak'), row(2, 5, 'weak')]
        rows = bench.summary(data, 1)
        strong = next(r for r in rows if r['scaling'] == 'strong' and r['ranks'] == 2 and r['engine'] == 'dmgmd')
        self.assertAlmostEqual(strong['speedup'], 1.6)
        self.assertAlmostEqual(strong['efficiency'], 0.8)
        self.assertEqual(strong['dmgmd_over_gpumd'], 2)
        weak = next(r for r in rows if r['scaling'] == 'weak' and r['ranks'] == 2)
        self.assertIsNone(weak['speedup'])
        self.assertAlmostEqual(weak['efficiency'], 0.8)
        self.assertTrue(all(r['speedup'] is None and r['efficiency'] is None for r in bench.summary(data, 3)))

    def test_idle_guard_and_uuid_mapping(self):
        snap = {'gpus': [dict(index='2', uuid='GPU-A', **{'memory.used': '12', 'utilization.gpu': '0'})],
                'apps': []}
        self.assertEqual(bench.resolve_devices(['2'], snap), ['GPU-A'])
        bench.ensure_idle(snap, ['GPU-A'], 256, 10)
        snap['apps'] = [dict(uuid='GPU-A', pid='42')]
        with self.assertRaises(RuntimeError):
            bench.ensure_idle(snap, ['GPU-A'], 256, 10)
        with patch.object(bench, 'process_record', side_effect=lambda pid: {'parent': 99 if pid == 42 else 1}):
            self.assertEqual(bench.foreign_apps(snap, ['GPU-A'], 99), [])
            self.assertEqual(len(bench.foreign_apps(snap, ['GPU-A'], 100)), 1)

    def test_timeout_cleans_rank_in_separate_session(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            program = ("import subprocess,sys,time,pathlib; "
                       "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
                       "pathlib.Path('child.pid').write_text(str(p.pid)); time.sleep(60)")
            with patch.object(bench, 'snapshot', return_value={'apps': [], 'gpus': []}):
                code, _, contaminated, timeout = bench.execute(
                    [sys.executable, '-c', program], directory, os.environ.copy(), [], 0.5, 0.05)
            self.assertTrue(timeout)
            self.assertNotEqual(code, 0)
            self.assertFalse(contaminated)
            child = int((directory / 'child.pid').read_text())
            stat = Path(f'/proc/{child}/stat')
            if stat.exists():
                self.assertEqual(stat.read_text().rsplit(')', 1)[1].split()[0], 'Z')

    def test_identical_physics_only_reference_partition_argument_differs(self):
        case, _ = bench.geometry({'fixture': 'carbon_crystal', 'scaling': 'strong', 'cells': [8, 96, 8]}, 8)
        dmg = bench.run_input('dmgmd', 8, case, 20, 100)
        ref = bench.run_input('gpumd', 8, case, 20, 100)
        self.assertEqual(dmg, ref.replace('nep.txt y', 'nep.txt'))
        self.assertNotIn('velocity ', dmg)
        self.assertNotIn('dump_xyz', dmg)


if __name__ == '__main__':
    unittest.main()
