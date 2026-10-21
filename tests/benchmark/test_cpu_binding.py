#!/usr/bin/env python3
"""Resource-policy rejection tests; no GPU or MPI required."""
import sys
from pathlib import Path
import unittest
import tempfile
import hashlib
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from cpu_binding import allocate, parse_rankfile, reject_conflicts
from cpu_sampling import capacity
import cpu_binding

CORES=[dict(logical_core=i, os_cpus=[2*i,2*i+16],socket=i//4,
            core_id=i%4,numa=[i//4]) for i in range(8)]
ALLOWED={p for c in CORES for p in c['os_cpus']}

class BindingTests(unittest.TestCase):
    def test_physical_not_smt(self):
        r=allocate(CORES,ALLOWED,2,2)
        self.assertEqual(r[0]['os_cpus'],[0,2,16,18])
        self.assertTrue(set(r[0]['os_cpus']).isdisjoint(r[1]['os_cpus']))
    def test_cpuset_crops_whole_core(self):
        r=allocate(CORES,ALLOWED-{16},1,1)
        self.assertEqual(r[0]['logical_cores'],[1])
    def test_capacity(self):
        for k in (0,9):
            with self.assertRaises(ValueError): allocate(CORES,ALLOWED,1,k)
        with self.assertRaises(ValueError): allocate(CORES,ALLOWED,4,2,quota=7.9)
    def test_numa(self):
        r=allocate(CORES,ALLOWED,2,3,'numa',nodes=[1,0])
        self.assertEqual([x['logical_cores'] for x in r],[[4,5,6],[0,1,2]])
        with self.assertRaises(ValueError): allocate(CORES,ALLOWED,2,3,'numa',nodes=[1])
    def test_rankfile_indices_not_os_ids(self):
        explicit=parse_rankfile('rank 0=+n0 slot=4-5\n',1)
        self.assertEqual(allocate(CORES,ALLOWED,1,2,'rankfile',explicit=explicit)[0]['os_cpus'],[8,10,24,26])
    def test_rankfile_reject(self):
        for txt in ('rank 0=+n0 slot=0\nrank 0=+n0 slot=1', 'rank 1=+n0 slot=0',
                    'rank 0=remote slot=0', 'rank 0=+n0 slot=0,0'):
            with self.assertRaises(ValueError): parse_rankfile(txt,1)
        for ids in ([[0],[0]], [[0],[8]]):
            with self.assertRaises(ValueError): allocate(CORES,ALLOWED,2,1,'rankfile',explicit=ids)
    def test_conflicts(self):
        for arg in ('--map-by=core','--bind-to','--rankfile=x','--oversubscribe','--cpu-list','rmaps_default_mapping_policy','--rank-by','--host','--cpus-per-rank','--OVERSUBSCRIBE','map_by','bind_to'):
            with self.assertRaises(ValueError): reject_conflicts([arg],{'PATH':''})
        with self.assertRaises(ValueError): reject_conflicts([],{'OMPI_MCA_hwloc_base_binding_policy':'none'})
        with self.assertRaises(ValueError): reject_conflicts([],{'PRTE_MCA_map_by':'slot'})
        reject_conflicts(['--report-bindings'],{'OMPI_MCA_pml':'ucx'})
    def test_nondelegated_cgroup_inherits_quota_and_cpuset(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'leaf/sub').mkdir(parents=True)
            (base/'cpu.max').write_text('200000 100000')
            (base/'cpuset.cpus.effective').write_text('0-3')
            original=Path.read_text
            def read(path,*a,**kw):
                if str(path)=='/proc/123/cgroup':return '0::/leaf/sub\n'
                if str(path)=='/proc/123/mountinfo':return f'1 2 0:1 / {base} rw - cgroup2 cgroup rw\n'
                return original(path,*a,**kw)
            with patch.object(Path,'read_text',read):
                c=capacity(123,[dict(status='ok',affinity=[0,1,2,3])])
            self.assertEqual(c['status'],'ok');self.assertEqual(c['quota_cores'],2)
            self.assertEqual(c['allowed_cpus'],[0,1,2,3])
            self.assertFalse(c['hierarchy_complete'])
    def test_runtime_quota_change_is_not_silently_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);limits=base/'limits';limits.mkdir()
            (limits/'cpu.max').write_text('400000 100000')
            (limits/'cpuset.cpus.effective').write_text('0-3')
            cap=dict(status='ok',ancestors=[{'path':str(limits),'cpu.max':'400000 100000','cpuset.cpus.effective':'0-3'}])
            content='rank 0=localhost slot=0\n'
            plan=dict(capacity=cap,ranks=1,rankfile_content=content,rankfile_sha256=hashlib.sha256(content.encode()).hexdigest())
            with patch.object(cpu_binding,'capacity',return_value=cap):audit=cpu_binding.Audit(base/'audit',plan,'/bin/sleep')
            audit.poll();self.assertEqual(audit.errors,[])
            (limits/'cpu.max').write_text('200000 100000');audit.next_resource_check=0;audit.poll()
            self.assertIn('visible cgroup CPU limits changed during trial',audit.errors)
            audit.close()

if __name__=='__main__': unittest.main()
