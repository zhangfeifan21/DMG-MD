"""O11 must retain controls and cannot promote absent or invalid experiments."""
import unittest
import tempfile
from pathlib import Path
import run_overlap_o11 as m

class O11Contract(unittest.TestCase):
    def test_shared_input_hash_still_detects_changed_content(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input'; alias=Path(d)/'alias'
            path.write_text('before');alias.symlink_to(path)
            hashed=m.content_digester()
            old=hashed(path)
            self.assertEqual(hashed(alias),old)
            path.write_text('after mutation')
            self.assertNotEqual(hashed(alias),old)

    def test_frozen_matrix_and_poll_control(self):
        steps={f'{c}-p{p}':1000 for c,p in m.CORE+m.EXTRA+m.DEGRADED}
        rows=m.jobs('main',steps,5)
        self.assertEqual(len(rows),400)
        self.assertEqual(len({r['id'] for r in rows}),400)
        for c,p in m.CORE+m.EXTRA+m.DEGRADED:
            for b in m.o86.BACKENDS:
                group=[r for r in rows if (r['case'],r['ranks'],r['backend'])==(c,p,b)]
                for v,mode in [('candidate','full'),('o8','full'),('candidate','compact-serial'),('candidate','compact-wait'),('candidate','compact-poll')]:
                    self.assertEqual({r['repeat'] for r in group if (r['version'],r['mode'])==(v,mode)},set(range(5)))
        self.assertEqual(len(m.jobs('cpu',steps)),48)
        self.assertEqual(m.o5.MODES['compact-poll'],('serial','poll'))
        self.assertEqual(m.o86.SCHEDULES['compact-poll'],'producer-compact')

    def test_missing_and_duplicate_comparisons_fail_closed(self):
        rows=[dict(case=c,ranks=p,backend=b,contrast=k,ratio=dict(n=5,ci_family=[1.01,1.03])) for c,p in m.CORE for b in m.o86.BACKENDS for k in ('same_backend_net','default_net')]
        for flag in (0,1,2,4):
            args=[True,True,True,rows,True];args[flag]=False
            self.assertEqual(m.completion(*args),'PARTIAL')
        rows[-1]=rows[0]
        self.assertEqual(m.completion(True,True,True,rows,True),'PARTIAL')

if __name__=='__main__':unittest.main()
