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
import run_overlap_rebuild as rebuild


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

    def test_lammps_timing_requires_nep_device_style_and_unique_rank_gpus(self):
        log = ('LAMMPS_GPU_BIND rank=0 local_rank=0 cuda_uuid=GPU-A\n'
               'LAMMPS_GPU_BIND rank=1 local_rank=1 cuda_uuid=GPU-B\n'
               'KOKKOS mode with Kokkos version 4.6.2 is enabled\n'
               '  will use up to 1 GPU(s) per node\n'
               'NEP Kokkos potential nep.txt loaded successfully\n'
               '(1) pair matpl/nep/kk, perpetual\n'
               'attributes: full, newton on, kokkos_device\n'
               'Loop time of 3 on 2 procs for 20 steps with 1000 atoms\n'
               'Loop time of 12 on 2 procs for 100 steps with 1000 atoms\n')
        self.assertEqual(bench.parse_timing(log, 'lammps', 2, 1000, 20, 100,
                                            'none', ['GPU-A', 'GPU-B']), 12)
        for bad in (log.replace('cuda_uuid=GPU-B', 'cuda_uuid=GPU-A'),
                    log.replace('kokkos_device', 'host'),
                    log.replace('for 100 steps', 'for 90 steps')):
            with self.assertRaises(ValueError):
                bench.parse_timing(bad, 'lammps', 2, 1000, 20, 100,
                                   'none', ['GPU-A', 'GPU-B'])

    def test_lammps_thermo_uses_final_measured_step(self):
        segment = ('Step Atoms Temp PotEng KinEng TotEng Press\n'
                   '0 100 1 -10 1 -9 0\n1 100 2 -11 1 -10 0\n'
                   'Loop time of 1 on 1 procs for 1 steps with 100 atoms\n')
        text = segment + ('Step Atoms Temp PotEng KinEng TotEng Press\n'
                          '1 100 2 -11 1 -10 0\n3 100 3 -12 1 -11 0\n'
                          'Loop time of 2 on 1 procs for 2 steps with 100 atoms\n')
        self.assertEqual(bench.parse_lammps_thermo(text, 3, 100)[0], 3)
        with self.assertRaises(ValueError):
            bench.parse_lammps_thermo(text, 4, 100)

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

    def test_profiling_modes_remain_separate_and_require_runtime_evidence(self):
        off = ('DMGMD_DOMAIN_DIAGNOSTICS detailed=off summary=run-segment '
               'timing=off trace=off\n')
        trace = ('DMGMD_DOMAIN_DIAGNOSTICS detailed=off summary=run-segment '
                 'timing=on trace=on\n' +
                 'DMGMD_DOMAIN_TRACE rank=0 pid=123 sequence=1 step=1 class=ordinary '
                 'clock=CLOCK_MONOTONIC begin_ns=100 end_ns=200\n' +
                 ''.join(f'DMGMD_DOMAIN_TIMING sequence={s} class={c}\n'
                         for s in (0, 1) for c in ('ordinary', 'rebuild')))
        self.assertEqual(bench.actual_dmgmd_profiling(off, 'off')['trace'], 'off')
        self.assertEqual(bench.actual_dmgmd_profiling(trace, 'trace')['step_trace_rows'], 1)
        with self.assertRaises(ValueError):
            bench.actual_dmgmd_profiling(off, 'trace')
        with self.assertRaises(ValueError):
            bench.actual_dmgmd_profiling(trace, 'off')
        trials = [dict(case='x', scaling='strong', engine='dmgmd', backend='HostStaged',
                       ranks=2, atoms=100, steps=10, seconds=1, status='ok', profiling=mode)
                  for mode in ('off', 'trace')]
        self.assertEqual({r['profiling'] for r in bench.summary(trials, 1)}, {'off', 'trace'})

    def test_trace_markers_use_c1_identity_and_reject_wrong_pid(self):
        with tempfile.TemporaryDirectory() as temp:
            cpu = Path(temp)
            (cpu / 'config.json').write_text(json.dumps({'ranks': 1}))
            (cpu / 'rank-0.json').write_text(json.dumps({
                'pid': 123, 'process_start_ticks': 456,
                'clock_domain_id': 'boot:time:[1]'}))
            trace = ('DMGMD_DOMAIN_TRACE rank=0 pid=123 sequence=1 step=3 '
                     'class=rebuild clock=CLOCK_MONOTONIC begin_ns=100 end_ns=200\n'
                     'DMGMD_DOMAIN_TRACE_SEGMENT rank=0 pid=123 sequence=1 '
                     'clock=CLOCK_MONOTONIC begin_ns=50 end_ns=250\n')
            markers = [json.loads(line) for line in
                       bench.write_trace_markers(trace, cpu).read_text().splitlines()]
            self.assertEqual(len(markers), 4)
            self.assertEqual({m['phase'] for m in markers}, {'run', 'step_rebuild'})
            self.assertEqual({m['process_start_ticks'] for m in markers}, {456})
            with self.assertRaises(ValueError):
                bench.write_trace_markers(trace.replace('pid=123', 'pid=124'), cpu)

    def test_cpu_binding_counts_physical_cores_and_rejects_numa_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            cpu = Path(temp)
            topology = {}
            for logical, core in ((0, 0), (1, 1), (4, 0), (5, 1)):
                base = f'/sys/devices/system/cpu/cpu{logical}/topology/'
                topology[base + 'core_id'] = str(core)
                topology[base + 'physical_package_id'] = '0'
            (cpu / 'config.json').write_text(json.dumps({
                'ranks': 1, 'topology': topology,
                'numa': {'/sys/devices/system/node/node0/cpulist': '0-1,4-5'}}))
            rows = [dict(type=kind, rank=0, status='ok', affinity=[0, 1, 4, 5])
                    for kind in ('proc_sample', 'task_sample')]
            (cpu / 'samples.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
            self.assertEqual(bench.verify_cpu_binding(cpu, 2, 0)[0]['physical_cores'], 2)
            with self.assertRaises(ValueError):
                bench.verify_cpu_binding(cpu, 4, 0)
            with self.assertRaises(ValueError):
                bench.verify_cpu_binding(cpu, 2, 1)

    def test_rebuild_diagnostic_requires_real_rebuild_steps(self):
        log = ('DMGMD_DOMAIN_DIAGNOSTICS detailed=off summary=run-segment timing=on trace=on\n'
               'DMGMD_DOMAIN mode=m2a axis=x\n' +
               ''.join(f'DMGMD_TIMING phase=run sequence={s} steps=4 backend=HostStaged seconds_max=1\n'
                       for s in (0, 1)) +
               ''.join(f'DMGMD_DOMAIN_TIMING sequence={s} class={c}\n'
                       for s in (0, 1) for c in ('ordinary', 'rebuild')) +
               ''.join(f'DMGMD_DOMAIN_TRACE rank={r} class=rebuild\n'
                       for r in (0, 1) for _ in range(8)))
        self.assertEqual(rebuild.validate_trace(log, 'HostStaged')['classes'],
                         {'ordinary': 0, 'rebuild': 16})
        with self.assertRaises(ValueError):
            rebuild.validate_trace(log.replace('class=rebuild', 'class=ordinary'), 'HostStaged')

    def test_uuid_mapping(self):
        snap = {'gpus': [dict(index='2', uuid='GPU-A', **{'memory.used': '12', 'utilization.gpu': '0'})],
                'apps': []}
        self.assertEqual(bench.resolve_devices(['2'], snap), ['GPU-A'])

    def test_snapshot_records_busy_gpu_without_querying_processes(self):
        values = ['0', 'GPU-A', 'Tesla T4', '0000:00:06.0', '570.124.04',
                  '15360', '12000', '100', '34', '34', '70', '1590', '5000']
        with patch.object(bench, 'command_output', return_value=','.join(values)) as query:
            snap = bench.snapshot()
        query.assert_called_once_with([
            'nvidia-smi', f'--query-gpu={bench.GPU_FIELDS}', '--format=csv,noheader,nounits'])
        self.assertEqual(snap['gpus'][0]['utilization.gpu'], '100')
        self.assertNotIn('apps', snap)

    def test_timeout_cleans_rank_in_separate_session(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            program = ("import subprocess,sys,time,pathlib; "
                       "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
                       "pathlib.Path('child.pid').write_text(str(p.pid)); time.sleep(60)")
            with patch.object(bench, 'snapshot', return_value={'apps': [], 'gpus': []}):
                code, _, timeout = bench.execute(
                    [sys.executable, '-c', program], directory, os.environ.copy(), 0.5, 0.05)
            self.assertTrue(timeout)
            self.assertNotEqual(code, 0)
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
