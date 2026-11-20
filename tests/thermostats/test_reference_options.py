#!/usr/bin/env python3
"""CPU-only tests for explicit/legacy thermostat reference identity selection."""
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'long_nve'))
import run_thermostats as thermostats
import check_variants as variants
import run_long_nve


class ReferenceOptions(unittest.TestCase):
    def test_legacy_default_still_checks_locked_reference(self):
        args = SimpleNamespace(reference=Path('/tmp/reference'))
        manifest = {'reference': {'commit': 'expected'}}
        with mock.patch.object(thermostats.baseline, 'load_manifest', return_value=manifest), \
             mock.patch.object(thermostats.baseline, 'validate_reference', return_value='commit') as check, \
             mock.patch.object(thermostats.baseline, 'sha256', return_value='binary-sha'):
            result = thermostats.reference_identity(args)
        check.assert_called_once_with(Path('/tmp/reference'), manifest)
        self.assertEqual(result['kind'], 'locked-reference')
        self.assertEqual(result['binary_sha256'], 'binary-sha')

    def test_explicit_branch_calls_existing_strict_gate(self):
        args = SimpleNamespace(reference=Path('/tmp/reference'), reference_provenance=Path('/tmp/receipt'),
                               reference_provenance_sha256='digest')
        with mock.patch.object(run_long_nve, 'validate_reference_provenance', return_value={'binary_sha256': 'locked'}) as check:
            result = thermostats.reference_identity(args)
        check.assert_called_once_with(Path('/tmp/reference'), Path('/tmp/receipt'), 'digest')
        self.assertEqual(result['kind'], 'isolated-provenance')
        self.assertEqual(result['binary_sha256'], 'locked')

    def test_legacy_namespace_for_non_reference_runners(self):
        thermostats.validate_reference_options(SimpleNamespace())
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(work_root=Path(temp) / 'never', reference_provenance=Path('/tmp/proof'))
            with self.assertRaisesRegex(ValueError, 'must be supplied together'):
                thermostats.Runner(args, {})
            self.assertFalse(args.work_root.exists())

    def test_cli_pair_rejected_before_directory_or_gpu(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ('run_thermostats.py', 'check_variants.py'):
                for option in (['--reference-provenance', '/tmp/missing'],
                               ['--reference-provenance-sha256', 'bad']):
                    out = Path(temp) / (name + option[0])
                    cmd = [sys.executable, str(HERE / name), '--candidate', '/tmp/missing',
                           '--work-root', str(out), *option]
                    if name == 'run_thermostats.py': cmd += ['--ensembles', 'nvt_nhc']
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn('must be supplied together', proc.stderr)
                    self.assertFalse(out.exists())

    def test_changed_oracle_rejected_before_stage_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = thermostats.Runner.__new__(thermostats.Runner)
            runner.root = Path(temp)
            runner.args = SimpleNamespace(reference=Path('/tmp/ref'), reference_identity={
                'path': '/tmp/ref', 'binary_sha256': 'expected'})
            with mock.patch.object(thermostats.baseline, 'sha256', return_value='changed'):
                with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                    runner.execute('oracle', 'nvt_nhc', 'fallback', 1, 'HostStaged', {}, reference=True)
            self.assertFalse((Path(temp) / 'oracle').exists())

    def test_variants_forward_identity_and_missing_fields_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(candidate=Path(temp) / 'candidate', devices=['0', '1', '2', '3'],
                                   work_root=Path(temp) / 'work')
            forwarded = variants.make_runner_args(args)
            self.assertEqual(forwarded.reference, variants.ROOT.parent / 'gpumd-reference/src/gpumd')
            self.assertIsNone(forwarded.reference_provenance)
            args.reference = Path('/tmp/ref')
            args.reference_provenance = Path('/tmp/receipt')
            args.reference_provenance_sha256 = 'hash'
            args.reference_identity = {'binary_sha256': 'binary'}
            forwarded = variants.make_runner_args(args)
            self.assertEqual(forwarded.reference, Path('/tmp/ref'))
            self.assertEqual(forwarded.reference_identity, args.reference_identity)


if __name__ == '__main__': unittest.main()
