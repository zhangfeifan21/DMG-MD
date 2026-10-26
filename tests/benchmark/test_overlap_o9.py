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


if __name__ == '__main__':
    unittest.main()
