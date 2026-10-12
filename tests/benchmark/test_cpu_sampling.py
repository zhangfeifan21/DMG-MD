#!/usr/bin/env python3
"""CPU-only contracts and real subprocess lifecycle tests; no exact CPU% gate."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import analyze_cpu as analysis
import cpu_sampling as sampler
import run_benchmark as bench


def counter(i, seconds, u, s=0, **extra):
    return dict(schema_version=1, type='proc_sample', rank=0, pid=100, tid=None,
                start_ticks=42, clk_tck=100, clock_domain_id='test-clock', sample_id=i,
                t_ns=int(seconds*1e9), t_begin_ns=int(seconds*1e9)-10,
                t_end_ns=int(seconds*1e9)+10, utime_ticks=u, stime_ticks=s,
                num_threads=1, voluntary_ctxt_switches=i*3, nonvoluntary_ctxt_switches=i,
                status='ok', **extra)


def cap(cpus=(0,1,2,3), quota=2, domain='shared', complete=True):
    return dict(status='ok', hierarchy_complete=complete, capacity_id=domain,
                allowed_cpus=list(cpus), quota_cores=quota, ancestors=[
                    {'path':'/'+domain, 'cpu.max':f'{int(quota*100000)} 100000',
                     'cpuset.cpus.effective':','.join(map(str,cpus))}])


class Contracts(unittest.TestCase):
    def test_ticks_actual_windows_and_no_child_guest_addition(self):
        a=counter(0,1,10,3, cutime=900, guest_time=50)
        b=counter(1,3,110,23, cutime=1000, guest_time=150)
        v=analysis.interval(a,b)
        self.assertEqual(v['cpu_seconds'],1.2)
        self.assertEqual(v['core_equivalent'],.6)
        b['t_ns']=int(2e9)
        self.assertEqual(analysis.interval(a,b)['core_equivalent'],1.2)
        self.assertEqual(v['voluntary_ctxt_switches'],3)

    def test_identity_gaps_missing_and_counter_reset(self):
        a,b=counter(0,1,10),counter(1,2,30)
        for change in ({'start_ticks':99},{'pid':101},{'clk_tck':250},
                       {'clock_domain_id':'other'}, {'sample_id':2},
                       {'status':'unavailable'}, {'utime_ticks':9}, {'t_ns':a['t_ns']}):
            self.assertIsNone(analysis.interval(a,dict(b,**change)),change)
        a.update(tid=7,process_start_ticks=42)
        b.update(tid=7,process_start_ticks=43)
        self.assertIsNone(analysis.interval(a,b))

    def test_comm_with_parentheses_and_live_raw(self):
        raw=Path('/proc/self/stat').read_text()
        original=sampler.parse_stat(raw)
        raw=raw[:raw.index('(')]+'(a (b) c)'+raw[raw.rindex(')')+1:]
        parsed=sampler.parse_stat(raw)
        self.assertEqual(parsed['comm'],'a (b) c')
        self.assertEqual(parsed['start_ticks'],original['start_ticks'])
        observed=sampler.sample(os.getpid())
        self.assertEqual(observed['status'],'ok')
        self.assertLessEqual(observed['t_begin_ns'],observed['t_ns'])
        self.assertEqual(sampler.parse_stat(observed['raw_stat'])['utime_ticks'],observed['utime_ticks'])

    def test_shared_quota_and_disjoint_domains(self):
        self.assertEqual(analysis.shared_capacity([cap((0,1)),cap((2,3))]),(2,None))
        self.assertIsNone(analysis.shared_capacity([cap(complete=False)])[0])
        self.assertEqual(analysis.shared_capacity([cap((0,1),domain='a'),cap((2,3),domain='b')]),(4,None))
        self.assertIsNone(analysis.shared_capacity([cap(domain='a'),cap(domain='b')])[0])
        a,b=cap((0,1),domain='a'),cap((2,3),domain='b')
        for v in (a,b):
            v['ancestors'].append({'path':'/parent','cpu.max':'200000 100000'})
        self.assertIsNone(analysis.shared_capacity([a,b])[0])

    def fixture(self, root, records, ranks=1):
        sampler.save(root/'config.json',dict(trial_id='test',ranks=ranks))
        (root/'samples.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))

    def test_process_not_added_to_threads_and_shared_denominator(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            records=[]
            for rank in range(2):
                for i in range(3):
                    c=counter(i,i+1,i*100)
                    c.update(rank=rank,pid=100+rank)
                    records.extend([c,dict(c,type='task_sample',tid=100+rank,process_start_ticks=42),
                                    dict(cap(),type='capacity',schema_version=1,rank=rank,sample_id=i)])
            self.fixture(root,records,ranks=2)
            result=analysis.analyze(root)
            self.assertEqual(result['job']['cpu_seconds'],4)
            self.assertEqual(result['job']['capacity_percent'],100)
            procs=[r for r in result['rows'] if r['scope']=='process_observed']
            self.assertEqual([r['capacity_percent'] for r in procs],[50,50])
            self.assertTrue(all(r['context_switch_scope']=='main_task' for r in procs))
            # Different window timestamps cannot become a synchronized job peak.
            for record in records:
                if record['type']=='proc_sample' and record['rank']==1:
                    record['t_ns']+=100
            self.fixture(root,records,ranks=2)
            result=analysis.analyze(root)
            self.assertIsNone(result['job']['peak_core_equivalent'])

    def test_corrupt_line_gaps_capacity_change_and_tid_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            records=[counter(0,1,0),counter(1,2,50),counter(3,4,100)]
            for i in (0,1,3):
                records.append(dict(cap(quota=1 if i==0 else 2),type='capacity',schema_version=1,rank=0,sample_id=i))
            records += [dict(counter(0,1,0),type='task_sample',tid=22),
                        dict(counter(1,2,20),type='task_sample',tid=22),
                        dict(counter(2,3,0),type='task_sample',tid=22,start_ticks=90)]
            self.fixture(root,records)
            with (root/'samples.jsonl').open('a') as f: f.write('{broken')
            result=analysis.analyze(root)
            proc=next(r for r in result['rows'] if r['scope']=='process_observed')
            self.assertEqual(proc['valid_intervals'],1)
            self.assertEqual(proc['capacity_reason'],'capacity_changed')
            self.assertEqual(len([r for r in result['rows'] if r['tid']==22]),2)
            self.assertTrue(result['parse_errors'])
            self.assertFalse(result['collector_complete'])

    def test_markers_use_full_read_brackets(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.fixture(root,[counter(i,i+1,i*10) for i in range(4)])
            marker=dict(schema_version=1,rank=0,pid=100,process_start_ticks=42,
                        clock_domain_id='test-clock',clock_id='CLOCK_MONOTONIC',sequence=0,phase='run')
            markers=root/'markers.jsonl'
            markers.write_text('\n'.join(json.dumps(dict(marker,edge=edge,t_ns=t))
                                         for edge,t in [('begin',int(1e9)),('end',int(4e9))]))
            result=analysis.analyze(root,markers_path=markers)
            self.assertEqual(result['phases'][0]['valid_intervals'],1)
            self.assertIsNone(result['rows'][0]['run_cpu_utilization'])
            self.assertTrue(analysis.marker_windows([dict(marker,edge='begin',t_ns=1)])[1])

    def test_gpu_mapping_no_guess_and_conflicts(self):
        reg=dict(type='rank_map',rank=0,local_rank=0,local_size=1,world_size=1,hostname='host')
        verified=dict(type='exec_verified',rank=0)
        config=dict(ranks=1,metadata=dict(visible_devices=['GPU-'+'ab'*16]))
        log='DMGMD_MPI rank=0 local_rank=0 local_size=1 world_size=1 hostname=host cuda_uuid='+'ab'*16
        self.assertEqual(analysis.mapping(config,[reg,verified],log)[0]['status'],'verified')
        self.assertEqual(analysis.mapping(config,[reg],log)[0]['status'],'unavailable')
        self.assertEqual(analysis.mapping(config,[reg,verified],log.replace('hostname=host','hostname=other'))[0]['status'],'conflict')
        self.assertIsNone(analysis.mapping(config,[reg,verified],'')[0]['gpu_uuid'])

    def test_namespace_boundary_and_capacity_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            config=Path(temp)/'config.json'
            sampler.save(config,dict(host={'hostname':'different'}))
            with self.assertRaisesRegex(RuntimeError,'unsupported_multi_node_or_namespace'):
                sampler.wrap(config, ['/bin/true'])
        with patch.object(Path,'read_text',return_value='1:cpu:/legacy\n'):
            result=sampler.capacity(os.getpid(),[])
        self.assertEqual(result['status'],'unavailable')
        self.assertFalse(result['hierarchy_complete'])

    def test_cli_dry_run_off_and_on_no_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'must-not-exist'
            base=[sys.executable,str(Path(bench.__file__)), '--profile','smoke','--engines','dmgmd',
                  '--devices','0','--ranks','1','--output',str(output),'--dry-run']
            for extra,mode in [([], 'off'),(['--cpu-sampling','proc','--cpu-diagnostics','perf'],'proc')]:
                result=subprocess.run(base+extra,capture_output=True,text=True,check=True)
                data=json.loads(result.stdout)
                self.assertEqual(data['cpu_sampling']['mode'],mode)
                self.assertFalse(output.exists())
            result=subprocess.run(base+['--cpu-sample-interval','nan'],capture_output=True)
            self.assertNotEqual(result.returncode,0)


class RealProcesses(unittest.TestCase):
    def run_candidate(self, root, code, timeout=5, diagnostics='none', interrupt=False):
        root.mkdir()
        command=[str(Path(sys.executable).resolve()),'-c',code]
        session=sampler.Session(root/'cpu','cpu-test',1,command[0],.05,diagnostics)
        environment=dict(os.environ,OMPI_COMM_WORLD_RANK='0',OMPI_COMM_WORLD_LOCAL_RANK='0',
                         OMPI_COMM_WORLD_SIZE='1',OMPI_COMM_WORLD_LOCAL_SIZE='1')
        session.start()
        try:
            with patch.object(bench,'snapshot',side_effect=KeyboardInterrupt if interrupt else None,
                              return_value={'gpus':[]}):
                if interrupt:
                    with self.assertRaises(KeyboardInterrupt):
                        bench.execute(session.wrapper(command),root,environment,timeout,.05,session)
                    result=None
                else:
                    result=bench.execute(session.wrapper(command),root,environment,timeout,.05,session)
        finally:
            session.stop('test_finished')
        self.assertIsNotNone(session.process.poll())
        records,errors=analysis.read_jsonl(root/'cpu/samples.jsonl')
        self.assertFalse(errors)
        summary=analysis.analyze(root)
        return result,records,summary

    def test_load_sleep_exit_and_thread_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            _,_,sleep=self.run_candidate(root/'sleep','import time; time.sleep(1.3)')
            _,records,load=self.run_candidate(root/'load',
                'import time,threading\n'
                't=threading.Thread(target=lambda: time.sleep(.35)); t.start()\n'
                'end=time.monotonic()+1.3\n'
                'while time.monotonic()<end: pass\n'
                't.join()\n')
            def cpu(result):
                return next(r['cpu_seconds'] for r in result['rows'] if r['scope']=='process_observed')
            self.assertGreater(cpu(load),cpu(sleep))
            self.assertTrue(any(r['type']=='thread_lost' for r in records))
            self.assertTrue(any(r['type']=='exec_verified' for r in records))
            self.assertTrue(any(r['type']=='observer_sample' for r in records))
            result,_,summary=self.run_candidate(root/'exit','import time; time.sleep(.25); raise SystemExit(42)')
            self.assertEqual(result[0],42)
            self.assertTrue(summary['collector_complete'])
            self.assertEqual(summary['mappings'][0]['status'],'unavailable')

    def test_timeout_interrupt_and_unrelated_process_survives(self):
        other=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
        try:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                result,_,_=self.run_candidate(root/'timeout','import time; time.sleep(30)',timeout=.4)
                self.assertTrue(result[2])
                self.run_candidate(root/'interrupt','import time; time.sleep(30)',interrupt=True)
                self.assertIsNone(other.poll())
        finally:
            other.terminate()
            other.wait()

    def test_denied_diagnostic_keeps_cpu_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            tool=root/'perf'
            tool.write_text('#!/bin/sh\necho permission-denied >&2\nexit 3\n')
            tool.chmod(0o700)
            with patch.dict(os.environ,PATH=str(root)):
                result,records,_=self.run_candidate(root/'denied','import time; time.sleep(.3)',diagnostics='perf')
            self.assertEqual(result[0],0)
            self.assertTrue(any(r['type']=='proc_sample' for r in records))
            self.assertTrue(any(r['type']=='diagnostics_exit' and r['status']=='unavailable' for r in records))
            self.assertIn('permission-denied',(root/'denied/cpu/perf-rank-0.txt').read_text())

    def test_missing_optional_tool_does_not_abort(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,PATH='/nonexistent'):
            result,records,_=self.run_candidate(Path(temp)/'missing','import time; time.sleep(.3)',diagnostics='pidstat')
            self.assertEqual(result[0],0)
            self.assertTrue(any(r.get('reason')=='tool_missing' for r in records))


if __name__=='__main__':
    unittest.main()
