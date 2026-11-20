import json
import tempfile
import unittest
from pathlib import Path
import domain_statistics as ds
import analyze_domain_statistics as analysis
import random
from unittest.mock import patch
import math


class StatisticsContracts(unittest.TestCase):
    def test_registered_jobs_unique_and_complete(self):
        registration = json.loads((ds.HERE / 'domain-statistics-registration.json').read_text())
        jobs = ds.formal_jobs(registration)
        self.assertEqual(len(jobs), 152)
        self.assertEqual(sum(job['kind'] == 'nvt' for job in jobs.values()), 24)
        self.assertTrue(all(job['mode'] == 'off' for job in jobs.values() if job['ranks'] == 1))

    def test_inventory_refuses_changed_input_or_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); reg = root / 'reg.json'; reg.write_text('{}')
            artifact = root / 'x'; artifact.write_text('a')
            ds.write_json(root / 'inventory.json', {'registration_sha256': ds.sha(reg),
                                                    'files': {'x': ds.sha(artifact)}})
            ds.validate_inventory(root, reg)
            artifact.write_text('b')
            with self.assertRaisesRegex(ValueError, 'input changed'): ds.validate_inventory(root, reg)
            artifact.write_text('a'); reg.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, 'registration changed'): ds.validate_inventory(root, reg)

    def test_draft_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); reg = root / 'reg.json'; reg.write_text('{"status":"DRAFT"}')
            class Args: pass
            args = Args(); args.registration=reg; args.inputs=root; args.candidate=Path('/bin/true')
            with self.assertRaisesRegex(ValueError, 'APPROVED'): ds.freeze(args)

    def test_self_reported_proofs_do_not_unlock_freeze(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); rng = root / 'rng.json'; gate = root / 'gate.json'
            ds.write_json(rng, {'schema': 'dmgmd.domain-npt-rng-proof.v1', 'status': 'PASS'})
            ds.write_json(gate, {'schema': 'dmgmd.domain-npt-deterministic-proof.v1', 'status': 'PASS'})
            with self.assertRaisesRegex(ValueError, 'RNG proof identity'):
                ds.validate_gate_proofs(rng, gate, Path('/bin/true'))

    def test_runner_artifact_validation_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); stage = root / 'case'; stage.mkdir()
            files = ('model.xyz', 'nep.txt', 'run.in', 'stdout.log', 'stderr.log')
            for name in files: (stage / name).write_text(name)
            hashes = {name: ds.sha(stage / name) for name in files}
            report = root / 'results.json'
            ds.write_json(report, {'status': 'PASS', 'candidate_sha256': ds.sha(Path('/bin/true')),
                          'records': [{'status': 'PASS', 'stage': 'case', 'command': ['/bin/true'], 'files_sha256': hashes,
                                       'input_sha256': {n: hashes[n] for n in files[:3]}}]})
            descriptor = {'path': str(report), 'sha256': ds.sha(report)}
            ds.validate_report_artifacts(descriptor, Path('/bin/true'))
            (stage / 'stdout.log').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'artifact changed'):
                ds.validate_report_artifacts(descriptor, Path('/bin/true'))

    def test_scalar_proof_checks_rank_stream_and_log_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); binary = Path('/bin/true'); records = []
            stream = ''.join(f'{i} 0x1p+0 0x1p+0 0x1p+0\n' for i in range(12))
            files = {}
            for rank in (1, 2, 4):
                for suffix, content in (('stdout', stream), ('stderr', '')):
                    path = root / f'p{rank}.{suffix}'; path.write_text(content); files[path.name] = ds.sha(path)
                records.append({'command': ['mpiexec', '-n', str(rank), str(binary)], 'status': 'PASS', 'returncode': 0})
            report = root / 'results.json'
            ds.write_json(report, {'status': 'PASS', 'scalar_sha256': ds.sha(binary), 'records': records})
            proof = {'schema': 'dmgmd.domain-npt-rng-proof.v2', 'candidate_sha256': ds.sha(binary),
                     'scalar': {'path': str(report), 'sha256': ds.sha(report), 'files_sha256': files}}
            ds.validate_scalar_proof(proof, binary)
            (root / 'p4.stdout').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'scalar logs'):
                ds.validate_scalar_proof(proof, binary)

    def test_response_fit_rejects_wrong_sign_and_extrapolation(self):
        zero, kappa = analysis.response_fit([99, 100, 101], [1, 0, -1])
        self.assertAlmostEqual(zero, 100)
        self.assertAlmostEqual(kappa, .01)
        with self.assertRaisesRegex(ValueError, 'nonnegative'):
            analysis.response_fit([99, 100, 101], [-1, 0, 1])
        with self.assertRaisesRegex(ValueError, 'outside'):
            analysis.response_fit([99, 100, 101], [4, 3, 2])

    def test_paired_nvt_bootstrap_keeps_volume_alignment(self):
        blocks = [{s: [(1, float(s+i), 0.0) for _ in range(32)] for s in (1, 2)}
                  for i in range(3)]
        values = analysis.paired_response_bootstrap(blocks, [1, 2], random.Random(8))
        self.assertAlmostEqual(values[1]-values[0], 1)
        self.assertAlmostEqual(values[2]-values[1], 1)

    def test_loader_checks_full_artifact_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); inputs = root / 'inputs'; inputs.mkdir()
            inventory = {}
            for name in ('model.xyz', 'nep.txt', 'run.in'):
                (inputs / name).write_text(name); inventory[name] = ds.sha(inputs / name)
            ds.write_json(inputs / 'inventory.json', {'files': inventory})
            job = {'model': 'model.xyz', 'run': 'run.in'}
            freeze = root / 'freeze.json'; ds.write_json(freeze, {'jobs': {'one': job}, 'inputs': str(inputs)})
            evidence = root / 'evidence'; stage = evidence / 'one'; stage.mkdir(parents=True)
            for name in ('model.xyz', 'nep.txt', 'run.in'):
                (stage / name).write_text(name)
            (stage / 'thermo.out').write_text('thermo')
            (stage / 'stdout.log').write_text('out'); (stage / 'stderr.log').write_text('err')
            record = {'job': 'one', 'metadata': job, 'status': 'PASS', 'freeze_sha256': ds.sha(freeze),
                      'input_sha256': inventory, 'thermo_sha256': ds.sha(stage / 'thermo.out'),
                      'log_sha256': {name: ds.sha(stage / name) for name in ('stdout.log', 'stderr.log')}}
            ds.write_json(stage / 'record.json', record)
            self.assertEqual(len(analysis.load_records(freeze, evidence)[1]), 1)
            (stage / 'thermo.out').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'thermo changed'):
                analysis.load_records(freeze, evidence)

    def test_complete_block_rule(self):
        with self.assertRaisesRegex(ValueError, 'complete block'):
            analysis.summaries([1.0] * 65, 2)

    def test_scientific_response_and_fluctuation_math(self):
        reg = json.loads((ds.HERE / 'domain-statistics-registration.json').read_text())
        reg['analysis_details']['bootstrap_replicates'] = 100
        reg['precision_gates']['pooled_ess_per_condition_per_metric'] = 1
        reg['precision_gates']['pooled_squared_deviation_ess_per_condition'] = 1
        seedset = [401, 402]
        response = {'volumes': [99., 100., 101.], 'seeds': seedset, 'enough': True, 'reg': reg,
                    'pressure_blocks': [{s: [(2, 2*p, 2*p*p)]*32 for s in seedset}
                                        for p in (1., 0., -1.)]}
        predicted = 8.617343e-5 * 300 * 160.21766208 * .01 * 100
        amp = math.sqrt(predicted)
        values = [100 + amp*math.sqrt(1.6), 100 - amp*math.sqrt(1.6),
                  100 + amp*math.sqrt(.4), 100 - amp*math.sqrt(.4)] * 32
        candidate = {'volume_A3': {301: values, 302: values}}
        with patch.object(analysis.stats._statistics, '_integrated_autocorrelation_time', return_value=1.):
            result = analysis.scientific_reference(response, candidate, 4, 2.0)
        self.assertEqual(result['status'], 'PASS')
        self.assertLess(abs(result['relative_volume_difference_ci'][0]), 1e-10)
        self.assertTrue(.99 < result['variance_to_response_prediction_ci'][0] < 1.03)

    def test_half_grid_uses_same_physical_times(self):
        burn_time = 100.0
        half = [burn_time + (i+1)*0.125 for i in range(8)]
        base = [burn_time + (i+1)*0.25 for i in range(4)]
        self.assertEqual(analysis.align_half_grid(half), base)
        with self.assertRaises(ValueError): analysis.align_half_grid(half[:-1])

    def test_numerical_response_pipeline_rejects_biased_variance(self):
        # Exercise the actual thermo parser, pressure fit, ACF, and bootstrap.
        # Each two-row block has the same exact moments, making the expected
        # fluctuation variance deterministic while seed/block resampling is real.
        reg = json.loads((ds.HERE / 'domain-statistics-registration.json').read_text())
        nvt = reg['independent_scientific_reference']
        nvt.update(seeds=list(range(401, 409)), steps=128, burn_steps=0, block_steps=4)
        reg['analysis_details']['bootstrap_replicates'] = 400
        reg['precision_gates'].update(pooled_ess_per_condition_per_metric=1,
                                      pooled_squared_deviation_ess_per_condition=1)
        predicted = 8.617343e-5 * 300 * 160.21766208 * .01 * 100
        header = ('# dump_thermo 1\n# synthetic\n# num_atoms 5760\n# dt_output 0.25 fs\n'
                  '# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz\n')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); found = {}
            for label, volume, pressure in (('0.99', 99., 1.), ('1.00', 100., 0.), ('1.01', 101., -1.)):
                for seed in nvt['seeds']:
                    key = f'nvt-vol{label}-seed{seed}'
                    stage = root / key; stage.mkdir(); found[key] = stage
                    length = volume ** (1/3)
                    offsets = (.00002, -.00002, .00001, -.00001)
                    rows = [f'{300 + offsets[i%4]} 1 1 {pressure+offsets[i%4]} {pressure+offsets[i%4]} {pressure+offsets[i%4]} 0 0 0 {length:.15g} 0 0 0 {length:.15g} 0 0 0 {length:.15g}\n' for i in range(128)]
                    (stage / 'thermo.out').write_text(header + ''.join(rows))
            response = analysis.prepare_response(found, reg, 2.0)
            self.assertEqual(response['diagnostics']['pressure_precision_status'], 'PASS')
            self.assertAlmostEqual(response['diagnostics']['ols_fit']['zero_pressure_volume_A3'], 100.)
            self.assertAlmostEqual(response['diagnostics']['ols_fit']['compressibility_GPa_inverse'], .01)
            self.assertEqual(response['diagnostics']['points']['0.99']['complete_blocks_per_seed']['401'], 32)
            amplitude = math.sqrt(predicted * (1024-1)/1024)
            def candidate(scale):
                return {'volume_A3': {seed: [100 + scale*amplitude*math.sqrt(1.6), 100 - scale*amplitude*math.sqrt(1.6),
                                             100 + scale*amplitude*math.sqrt(.4), 100 - scale*amplitude*math.sqrt(.4)] * 32
                                      for seed in range(301, 309)}}
            good = analysis.scientific_reference(response, candidate(1), 4, 2.0)
            bad = analysis.scientific_reference(response, candidate(1.4), 4, 2.0)
            self.assertEqual(good['status'], 'PASS')
            self.assertEqual(bad['status'], 'FAIL')
            self.assertGreater(bad['variance_to_response_prediction_ci'][0], 1.15)
            for stage in found.values():
                path = stage / 'thermo.out'
                lines = path.read_text().splitlines()
                path.write_text('\n'.join(line if line.startswith('#') else ' '.join([str(float(line.split()[0])+15), *line.split()[1:]]) for line in lines) + '\n')
            shifted = analysis.prepare_response(found, reg, 2.0)
            self.assertTrue(all(x['status'] == 'FAIL' for x in shifted['temperature_checks'].values()))
            for stage in found.values():
                path = stage / 'thermo.out'
                lines = path.read_text().splitlines(); rows = []
                for i, line in enumerate(lines):
                    if line.startswith('#'): rows.append(line); continue
                    fields = line.split(); value = str((.00002, -.00002, .00001, -.00001)[(i-5)%4])
                    fields[3:6] = [value]*3; rows.append(' '.join(fields))
                path.write_text('\n'.join(rows)+'\n')
            flat = analysis.prepare_response(found, reg, 2.0)
            self.assertEqual(flat['diagnostics']['ols_fit']['status'], 'INVALID')
            self.assertEqual(len(flat['diagnostics']['points']), 3)
            self.assertEqual(analysis.scientific_reference(flat, candidate(1), 4, 2.)['status'], 'INCONCLUSIVE')

    def test_common_temperature_and_pressure_bias_fails_absolute_gates(self):
        reg = json.loads((ds.HERE / 'domain-statistics-registration.json').read_text())
        reg['precision_gates'].update(pooled_ess_per_condition_per_metric=1,
                                      pooled_squared_deviation_ess_per_condition=1)
        seeds = range(301, 309)
        candidate = {'temperature_K': {s: [314., 316., 314.5, 315.5] * 32 for s in seeds},
                     'pressure_GPa': {s: [.29, .31, .295, .305] * 32 for s in seeds},
                     'volume_A3': {s: [99., 101., 99.5, 100.5] * 32 for s in seeds}}
        with patch.object(analysis.stats, 'BOOTSTRAP_REPLICATES', 100):
            self.assertEqual(analysis.compare(candidate, candidate, 4, 2., .25, reg)['status'], 'PASS')
            reg['precision_gates']['pooled_ess_per_condition_per_metric'] = 10**9
            with patch.object(analysis.stats, '_ecdf_distance', return_value=.2):
                self.assertEqual(analysis.compare(candidate, candidate, 4, 2., .25, reg)['status'], 'FAIL')
        reg['precision_gates']['pooled_ess_per_condition_per_metric'] = 1
        self.assertEqual(analysis.absolute_mean(candidate['temperature_K'], 4, 2., 300., 6., reg)['status'], 'FAIL')
        self.assertEqual(analysis.absolute_mean(candidate['pressure_GPa'], 4, 2., 0., .1, reg)['status'], 'FAIL')

    def test_constant_chain_is_not_evidence_of_precision(self):
        reg = json.loads((ds.HERE / 'domain-statistics-registration.json').read_text())
        chain = {s: [300.] * 1024 for s in range(8)}
        result = analysis.absolute_mean(chain, 32, 2., 300., 6., reg)
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertEqual(result['precision']['effective_samples_mean'], 0)
        self.assertEqual(len(result['precision']['unidentifiable_autocorrelation']), 8)
        alternating = {s: [299., 301.] * 512 for s in range(8)}
        result = analysis.absolute_mean(alternating, 32, 2., 300., 6., reg)
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertIn('squared deviation', result['precision']['unidentifiable_autocorrelation'][0]['reason'])
        identical = {'temperature_K': chain, 'pressure_GPa': {s: [0.]*1024 for s in range(8)},
                     'volume_A3': {s: [100.]*1024 for s in range(8)}}
        comparison = analysis.compare(identical, identical, 32, 2., .25, reg)
        self.assertEqual(comparison['status'], 'INCONCLUSIVE')
        self.assertFalse(comparison['metrics']['temperature_K']['pass'])

    def test_interval_and_aggregate_result_rules(self):
        self.assertEqual(analysis.interval_status([-.5, .5], -1, 1), 'PASS')
        self.assertEqual(analysis.interval_status([.5, 1.5], -1, 1), 'INCONCLUSIVE')
        self.assertEqual(analysis.interval_status([1.1, 2], -1, 1), 'FAIL')
        self.assertEqual(analysis.interval_status([1.1, 2], -1, 1, False), 'INCONCLUSIVE')
        self.assertEqual(analysis.aggregate(['PASS', 'INCONCLUSIVE', 'FAIL']), 'FAIL')

    def test_incomplete_records_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); freeze = root / 'freeze.json'
            ds.write_json(freeze, {'jobs': {'missing': {}}})
            with self.assertRaisesRegex(ValueError, 'incomplete job matrix'):
                analysis.load_records(freeze, root)


if __name__ == '__main__': unittest.main()
