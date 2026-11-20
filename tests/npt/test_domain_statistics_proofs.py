"""Full synthetic evidence matrix exercises the actual proof gate, without GPUs."""
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import domain_statistics as ds

RANKS = (2, 4)
BACKENDS = ('HostStaged', 'CudaAware')
MODES = ('off', 'rebuild', 'affine')


class ProofMatrix(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.evidence = self.root / 'evidence'; self.evidence.mkdir()
        self.exe = Path('/bin/true').resolve(); self.digest = ds.sha(self.exe)
        self.build_matrix()

    def report(self, name, records, **extra):
        path = self.evidence / name / 'results.json'
        ds.write_json(path, {'status': 'PASS', 'candidate_sha256': self.digest, 'records': records, **extra})

    def stage(self, directory, name, ensemble='npt_ber', **extra):
        stage = self.evidence / directory / name; stage.mkdir(parents=True)
        data = {'model.xyz': 'model', 'nep.txt': 'potential', 'run.in': f'ensemble {ensemble} 300 300 40 0 425 200\n',
                'stdout.log': 'stdout', 'stderr.log': 'stderr', 'thermo.out': 'thermo', 'trajectory.xyz': 'xyz',
                'restart.xyz': 'restart'}
        for key, value in data.items(): (stage / key).write_text(value)
        files = {key: ds.sha(stage / key) for key in data}
        return {'stage': name, 'status': 'PASS', 'command': ['mpiexec', '-n', '2', str(self.exe)],
                'input_sha256': {k: files[k] for k in ('model.xyz', 'nep.txt', 'run.in')},
                'files_sha256': files, 'comparison': {'test': True}, **extra}

    def build_matrix(self):
        self.fixture = self.root / 'pressure-fixture'; self.fixture.mkdir()
        for name in ('model.xyz', 'nep.txt', 'run.in'): (self.fixture / name).write_text(name)
        inventory = {name: ds.sha(self.fixture / name) for name in ('model.xyz', 'nep.txt', 'run.in')}
        records = []
        reference_records = []
        for i in range(15):
            for repeat in (0, 1):
                records.append(self.stage('pressure', f's{i}-p1-HostStaged-off-r{repeat}',
                    ranks=1, backend='HostStaged', mode='off', repeat=repeat, scale=ds.pressure_oracle.JITTER_SCALES[i],
                    metrics={'thermostat_branch_skipped': True, 'thermostat_formula_factor': 1., 'thermostat_coupling': 1.0 / 1e30}))
                reference_records.append(self.stage('pressure-jitter-reference', f's{i}-p1-HostStaged-off-r{repeat}',
                    ranks=1, backend='HostStaged', mode='off', repeat=repeat, scale=ds.pressure_oracle.JITTER_SCALES[i]))
            for p, b, m in itertools.product(RANKS, BACKENDS, ('rebuild', 'affine')):
                records.append(self.stage('pressure', f's{i}-p{p}-{b}-{m}-r0',
                    ranks=p, backend=b, mode=m, repeat=0, scale=ds.pressure_oracle.JITTER_SCALES[i],
                    metrics={'thermostat_branch_skipped': True, 'thermostat_formula_factor': 1., 'thermostat_coupling': 1.0 / 1e30}))
        fd = {'pass': True, 'direction': {'pass': True}, 'derivatives':
              [{'center_log_scale': c, 'h_log_scale': h, 'pass': True} for c, h in itertools.product((-.008, 0., .008), (.001, .0005))]}
        self.report('pressure-jitter-reference', reference_records, schema='dmgmd.domain-pressure-oracle.v1',
                    fixture_sha256=inventory, checks={'finite_difference': fd})
        reference_path = self.evidence/'pressure-jitter-reference'/'results.json'
        reference = json.loads(reference_path.read_text()); reference['status'] = 'REFERENCE_PASS'; ds.write_json(reference_path, reference)
        self.reference_proof = {'path': str(reference_path), 'sha256': ds.sha(reference_path), 'status': 'VERIFIED',
                                'records': 30, 'finite_difference': fd}
        self.report('pressure', records, schema='dmgmd.domain-pressure-oracle.v1', fixture_sha256=inventory,
            reference_proof=self.reference_proof,
            settings={'scales': list(ds.pressure_oracle.JITTER_SCALES), 'dt_fs': 1e-8, 'bulk_modulus_GPa': 2001,
                      'reference_only': False, 'jitter_full': True, 'jitter_reference': False, 'calibration': False,
                      'jitter': {'seed': 20261106, 'distribution': 'independent uniform per atom and axis',
                                 'amplitude_A': .01, 'centers': [-.008, 0., .008], 'h': [.001, .0005]},
                      'temperature_damping_steps': 1e30, 'finite_difference': {'atol_GPa': .01, 'rtol': .002}},
            checks={'reference_repeat': {'status': 'PASS', 'count': 15}, 'finite_difference': fd,
                    'local_finite_difference': [{'ranks': p, 'backend': b, 'mode': m, **fd}
                        for p, b, m in itertools.product(RANKS, BACKENDS, ('rebuild', 'affine'))]})
        for ensemble in ('ber', 'scr'):
            records = []
            for p, b, m in itertools.product(RANKS, BACKENDS, MODES):
                rec = self.stage('differential-'+ensemble, f'p{p}-{b}-{m}', 'npt_'+ensemble)
                rec.update(ranks=p, backend=b, mode=m, output_sha256=rec.pop('files_sha256'))
                records.append(rec)
            self.report('differential-'+ensemble, records)
            records = []
            for fixture, p, b, m in itertools.product(('nep5', 'typewise', 'zbl', 'flexible-zbl', 'typewise-zbl'), RANKS, BACKENDS, MODES):
                records.append(self.stage('variants-'+ensemble, f'{fixture}-p{p}-{b}-{m}', 'npt_'+ensemble))
            self.report('variants-'+ensemble, records)
        records = [self.stage('affine-cache', f'p{p}-{b}-{scenario}-{m}', cache_summary=[{'rank': 0}])
                   for p, b, scenario, m in itertools.product(RANKS, BACKENDS, ('weak', 'expansion', 'compression'), MODES)]
        self.report('affine-cache', records)
        records = []
        for p, b, ensemble, scenario in itertools.product(RANKS, BACKENDS, ('npt_ber', 'npt_scr'), ('single', 'segments', 'late-unwrapped', 'fixed-box')):
            if ensemble == 'npt_ber' and scenario == 'fixed-box': continue
            prefix = f'p{p}-{b}-{ensemble}-{scenario}'
            for m in MODES: records.append(self.stage('contracts', prefix+'-'+m, ensemble, unwrapped_semantics={'coordinates_checked': 1, 'comparison': {'test': True}}))
            if ensemble == 'npt_scr':
                for m in ('rebuild', 'affine'): records.append(self.stage('contracts', prefix+'-'+m+'-repeat', ensemble, unwrapped_semantics={'coordinates_checked': 1, 'comparison': {'test': True}}))
        for b in BACKENDS:
            for source, destination in itertools.product((1, 2, 4), RANKS):
                for m in ('source', *MODES): records.append(self.stage('contracts', f'restart-{b}-from{source}-to{destination}-{m}', 'npt_scr'))
            for source in RANKS:
                for m in ('source', *MODES): records.append(self.stage('contracts', f'restart-{b}-from{source}-to1-{m}', 'npt_scr'))
        self.report('contracts', records)
        negative_names = ('invalid-mode', 'mismatched-mode', 'partition-overlap-invalid', 'partition-mismatch',
                          'partition-npt', 'initial-fallback', 'runtime-geometry', 'multi-box-displacement',
                          'empty-off', 'empty-rebuild', 'empty-affine')
        for b in BACKENDS:
            records = [self.stage('negatives-'+b, name, environment={'DMGMD_COMM_BACKEND': b}) for name in negative_names]
            self.report('negatives-'+b, records)
        for ensemble, p, b, m in itertools.product(('npt_ber', 'npt_scr'), RANKS, BACKENDS, ('rebuild', 'affine')):
            directory = f'pmpi/{ensemble}-P{p}-{b}-{m}'; records = []
            delta = {'thermo': 2, 'reason': 4, 'bcast2': 4 if ensemble == 'npt_scr' else 2, 'preflight': 0,
                     'other_allreduce': 0, 'gather': 0, 'gatherv': 0, 'allgather': 0, 'allgatherv': 0}
            for steps in (4, 6):
                rec = self.stage(directory, f'steps-{steps}', ensemble, steps=steps,
                    environment={'DMGMD_COMM_BACKEND': b, 'DMGMD_DOMAIN_NPT': m})
                rec['command'] = ['mpiexec', '-n', str(p), str(self.exe)]
                if steps == 6: rec['delta_by_rank'] = {str(i): delta for i in range(p)}
                records.append(rec)
            self.report(directory, records)
        directory = self.evidence / 'scalar'; directory.mkdir()
        stream = ''.join(f'{i} 0x1p+0 0x1p+0 0x1p+0\n' for i in range(12))
        records = []
        for p in (1, 2, 4):
            (directory / f'p{p}.stdout').write_text(stream); (directory / f'p{p}.stderr').write_text('')
            records.append({'command': ['mpiexec', '-n', str(p), str(self.exe)], 'status': 'PASS', 'returncode': 0})
        self.report('scalar', records, scalar_sha256=self.digest)

    def generate(self, name='proof'):
        class Args: pass
        args = Args(); args.evidence = self.evidence; args.candidate = self.exe; args.output = self.root / name
        args.pressure_fixture = self.fixture
        with patch.object(ds.pressure_oracle, 'jitter_model', return_value='mock'), \
             patch.object(ds.pressure_oracle, 'jitter_derivative_checks', return_value=self.reference_proof['finite_difference']), \
             patch.object(ds.pressure_oracle, 'validate_jitter_proof', return_value=self.reference_proof):
            ds.generate_gate_proofs(args)
        return args.output

    def test_full_matrix_and_mutations(self):
        proof = self.generate()
        ds.validate_gate_proofs(proof/'rng-proof.json', proof/'deterministic-proof.json', self.exe, deep=False)
        path = self.evidence/'contracts'/'results.json'; original = path.read_text()
        report = json.loads(original); report['records'].pop(); ds.write_json(path, report)
        with self.assertRaisesRegex(ValueError, 'report identity'): ds.validate_gate_proofs(proof/'rng-proof.json', proof/'deterministic-proof.json', self.exe, deep=False)
        with self.assertRaisesRegex(ValueError, 'contracts matrix'): self.generate('missing')
        path.write_text(original)
        stage = self.evidence/'pressure'/'s0-p1-HostStaged-off-r0'/'stdout.log'; stage.write_text('altered')
        with self.assertRaisesRegex(ValueError, 'artifact changed'): self.generate('tamper')
        stage.write_text('stdout')
        path = self.evidence/'pmpi'/'npt_scr-P4-CudaAware-affine'/'results.json'
        report = json.loads(path.read_text()); report['records'][1]['delta_by_rank']['0']['bcast2'] = 999; ds.write_json(path, report)
        with self.assertRaisesRegex(ValueError, 'PMPI counts'): self.generate('bad-combination')

    def test_missing_pmpi_and_wrong_backend_are_rejected(self):
        path = self.evidence/'pmpi'/'npt_scr-P4-CudaAware-affine'/'results.json'
        original = path.read_text(); path.unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete PMPI'): self.generate('missing-pmpi')
        path.write_text(original)
        path = self.evidence/'negatives-CudaAware'/'results.json'
        report = json.loads(path.read_text()); report['records'][0]['environment']['DMGMD_COMM_BACKEND'] = 'HostStaged'
        ds.write_json(path, report)
        with self.assertRaisesRegex(ValueError, 'negative matrix'): self.generate('bad-backend')

    def test_rng_and_unwrapped_evidence_are_required(self):
        path = self.evidence/'scalar'/'p4.stdout'; original = path.read_text()
        path.write_text(original.replace('11 0x1p+0', '11 0x2p+0'))
        with self.assertRaisesRegex(ValueError, 'streams differ'): self.generate('bad-rng')
        path.write_text(original)
        path = self.evidence/'contracts'/'results.json'; report = json.loads(path.read_text())
        record = next(r for r in report['records'] if '-late-unwrapped-' in r['stage'])
        del record['unwrapped_semantics']; ds.write_json(path, report)
        with self.assertRaisesRegex(ValueError, 'late unwrapped'): self.generate('bad-unwrapped')

    def test_pressure_requires_reference_and_all_six_differences(self):
        path = self.evidence/'pressure'/'results.json'; original = path.read_text()
        report = json.loads(original); report['records'].pop(); ds.write_json(path, report)
        with self.assertRaisesRegex(ValueError, 'pressure oracle matrix'): self.generate('pressure-missing')
        path.write_text(original)
        report = json.loads(original); report['checks']['finite_difference']['derivatives'].pop(); ds.write_json(path, report)
        with self.assertRaisesRegex(ValueError, 'pressure oracle checks'): self.generate('pressure-fd-missing')
        path.write_text(original)
        report = json.loads(original); report['reference_proof']['sha256'] = '0' * 64; ds.write_json(path, report)
        with self.assertRaisesRegex(ValueError, 'reference proof mismatch'): self.generate('pressure-wrong-reference')
        path.write_text(original)
        report = json.loads(original); report['checks']['local_finite_difference'][0]['derivatives'][0]['pass'] = False
        ds.write_json(path, report)
        with self.assertRaisesRegex(ValueError, 'local pressure finite difference'): self.generate('pressure-local-fd')


if __name__ == '__main__': unittest.main()
