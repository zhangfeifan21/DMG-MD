#!/usr/bin/env python3
"""Prevent missing experiments and corrupted evidence becoming NO_ADOPTION."""
import json
from pathlib import Path
import tempfile
import unittest

from run_overlap_o9 import decision, verify_evidence
from cpu_binding import digest


class EvidenceGate(unittest.TestCase):
    def setUp(self):
        self.denied = dict(status='INCONCLUSIVE', error='ERR_NVGPUCTRPERM',
                           binding=dict(status='PASS'), timeout=False)

    def test_missing_experiment_or_failed_gate_is_partial(self):
        self.assertEqual(decision(None, True, True), 'PARTIAL')
        self.assertEqual(decision(self.denied, False, True), 'PARTIAL')
        self.assertEqual(decision(self.denied, True, False), 'PARTIAL')

    def test_available_metrics_require_a_new_candidate_review(self):
        self.assertEqual(decision(dict(status='PASS'), True, True), 'CANDIDATE_REVIEW_REQUIRED')

    def test_actual_permission_rejection_closes_only_no_candidate_branch(self):
        self.assertEqual(decision(self.denied, True, True), 'NO_ADOPTION')

    def test_unrelated_failure_is_partial(self):
        for error in ('CUDA_ERROR_NO_DEVICE', 'timed out', 'missing kernels', ''):
            with self.subTest(error=error):
                self.assertEqual(decision(dict(self.denied, error=error), True, True), 'PARTIAL')

    def test_permission_marker_does_not_override_invalid_binding_or_timeout(self):
        for change in (dict(binding_error='mapping changed'), dict(binding=dict(status='INVALID')),
                       dict(binding={}), dict(timeout=True), dict(status='FAILED')):
            with self.subTest(change=change):
                self.assertEqual(decision(dict(self.denied, **change), True, True), 'PARTIAL')

    def test_raw_evidence_rejects_missing_and_same_size_changed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'profiler.txt'
            path.write_text('good')
            (root/'raw-evidence-inventory.json').write_text(json.dumps({
                'profiler.txt': dict(bytes=4, sha256=digest(path))}))
            verify_evidence(root)
            path.write_text('evil')
            with self.assertRaisesRegex(ValueError, 'raw evidence differs'):
                verify_evidence(root)
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'raw evidence differs'):
                verify_evidence(root)


class ConfirmationGate(unittest.TestCase):
    def rows(self):
        return [dict(case=c,ranks=p,backend=b,contrast=k,ratio=dict(n=5,ci_family=[1.01,1.03]))
                for c,p in [('carbon_200k',2),('water_400k',8)]
                for b in ['HostStaged','CudaAware'] for k in ['same_backend_net','default_net']]

    def test_new_confirmation_needs_actual_experiments(self):
        from overlap_o9_confirmation import completion
        for flag in range(4):
            args=[True,True,True,self.rows(),True]
            args[[0,1,2,4][flag]]=False
            self.assertEqual(completion(*args),'PARTIAL')
        self.assertEqual(completion(True,True,True,[],True),'PARTIAL')

    def test_new_confirmation_cannot_use_old_permission_result(self):
        from overlap_o9_confirmation import completion
        self.assertEqual(completion(True,False,False,[],False),'PARTIAL')

    def test_confirmed_and_negative_are_distinct(self):
        from overlap_o9_confirmation import completion
        rows=self.rows()
        self.assertEqual(completion(True,True,True,rows,True),'CONFIRMED_SCOPED_CANDIDATE')
        for r in rows: r['ratio']['ci_family']=[.98,1.02]
        self.assertEqual(completion(True,True,True,rows,True),'NO_ADOPTION')
        rows[0]['ratio']['n']=0
        self.assertEqual(completion(True,True,True,rows,True),'PARTIAL')

    def test_duplicate_scopes_cannot_satisfy_confirmation(self):
        from overlap_o9_confirmation import completion
        rows=self.rows()
        rows[-1]=rows[0]
        self.assertEqual(completion(True,True,True,rows,True),'PARTIAL')

    def test_different_rank_windows_are_not_compared_as_same_science(self):
        from overlap_o9_confirmation import science_check
        rows=[dict(case='water_400k',ranks=p,backend='HostStaged',repeat=0,
                   scientific_outputs={'thermo.out':str(p)}) for p in (4,8)]
        science_check(rows)
        rows.append(dict(rows[0],scientific_outputs={'thermo.out':'changed'}))
        with self.assertRaisesRegex(ValueError,'scientific outputs differ'):
            science_check(rows)

    def test_jobs_keep_controls_and_independent_blocks(self):
        from overlap_o9_confirmation import jobs,CORE,EXTRA,DEGRADED
        steps={f'{c}-p{p}':1000 for c,p in CORE+EXTRA+DEGRADED}
        rows=jobs('main',steps,5)
        self.assertEqual(len(rows),300)
        self.assertEqual(len({r['id'] for r in rows}),len(rows))
        self.assertEqual({r['repeat'] for r in rows},set(range(5)))
        self.assertEqual(rows,jobs('main',steps,5))


if __name__ == '__main__':
    unittest.main()
