#!/usr/bin/env python3
"""CPU checks for the cache exercise's fixture and decision assertions."""
import tempfile
import unittest
from pathlib import Path
import run_domain_affine_cache as runner


class CacheRunnerTests(unittest.TestCase):
    def test_compression_fixture_has_large_box_and_nonzero_velocity(self):
        model = runner.model_text()
        self.assertIn('128 0 0 0 64 0 0 0 64', model)
        self.assertEqual(int(model.splitlines()[0]), 12)
        self.assertTrue(any(float(line.split()[4]) != 0 for line in model.splitlines()[2:]))
        self.assertIn(' 5100 425 200', runner.input_text(5100))
        self.assertTrue(runner.input_text(5100).endswith('run 20\n'))

    def test_summary_requires_all_ranks_and_consistent_decisions(self):
        def line(rank, reuse):
            return (f'DMGMD_DOMAIN_SUMMARY rank={rank} sequence=0 cache_reuses={reuse} '
                    'cache_rebuilds=2 affine_invalid_rebuilds=0 '
                    'affine_budget_rebuilds=2 box_rebuilds=0\n')
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            log = stage / 'stdout.log'
            log.write_text(line(0, 18) + line(1, 18))
            self.assertEqual(runner.cache_summary(stage, 2)[0]['cache_reuses'], 18)
            log.write_text(line(0, 18))
            with self.assertRaises(AssertionError): runner.cache_summary(stage, 2)
            log.write_text(line(0, 18) + line(1, 17))
            with self.assertRaises(AssertionError): runner.cache_summary(stage, 2)


if __name__ == '__main__':
    unittest.main()
