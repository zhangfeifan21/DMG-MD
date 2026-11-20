#!/usr/bin/env python3
"""Small synthetic orchestration test, never a scientific result."""
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import domain_statistics as ds
import analyze_domain_statistics as analyzer


class SyntheticPipeline(unittest.TestCase):
    def test_freeze_run_analyze_and_reject_tamper(self):
        environment_patch = patch.dict(os.environ)
        environment_patch.start(); self.addCleanup(environment_patch.stop)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inputs = root / 'inputs'; inputs.mkdir()
            claim_patch = patch.object(ds, 'CLAIM_ROOT', root / 'claims')
            claim_patch.start(); self.addCleanup(claim_patch.stop)
            reg = json.loads((ds.HERE / 'domain-statistics-registration.json').read_text())
            reg['status'] = 'APPROVED'; reg['seeds'] = [301, 302]
            reg['independent_scientific_reference']['seeds'] = [401, 402]
            reg['independent_scientific_reference']['steps'] = 64
            for i, profile in enumerate(reg['profiles']):
                profile.update(steps=64 * (i+1), burn_steps=0, block_steps=2*(i+1))
            reg['conditions'] = [c for c in reg['conditions'] if c['mode'] == 'off'] + [c for c in reg['profiles'][1]['conditions'] if c['mode'] == 'affine']
            reg['analysis_details'].update(interval_count=97, bootstrap_replicates=10)
            registration = root/'registration.json'; ds.write_json(registration, reg)
            names = {'nep.txt', 'nvt.in'} | {f'model-{x}.xyz' for x in reg['seeds']}
            names |= {f"scr-{x['name']}.in" for x in reg['profiles']}
            names |= {f'nvt-model-{x}-{v}.xyz' for x in (401,402) for v in ('0.99','1.00','1.01')}
            for name in names:
                text = ('time_step 0.125\nrun 128\n' if name == 'scr-half.in' else 'time_step 0.25\nrun 64\n')
                (inputs/name).write_text(text)
            reg['potential_sha256'] = ds.sha(inputs/'nep.txt'); ds.write_json(registration, reg)
            ds.write_json(inputs/'inventory.json', {'registration_sha256': ds.sha(registration),
                          'files': {name: ds.sha(inputs/name) for name in names}})
            exe = root/'candidate'; exe.write_text('candidate'); exe.chmod(0o755)
            acf = root/'acf'; acf.write_text('#!/bin/sh\necho 1\n'); acf.chmod(0o755)
            mpiexec = root/'fake-mpiexec'
            mpiexec.write_text('''#!/usr/bin/env python3
import os
from pathlib import Path
text=Path('run.in').read_text().splitlines()
dt=float(text[0].split()[1]); steps=int(text[1].split()[1])
mode=os.environ['DMGMD_DOMAIN_NPT']
print('DMGMD_DOMAIN mode='+('m1' if mode=='off' else 'm2a'))
if mode!='off': print('DMGMD_DOMAIN_NPT mode='+mode)
header=('# dump_thermo 1\\n# synthetic\\n# num_atoms 5760\\n'
        +f'# dt_output {dt} fs\\n'
        +'# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz\\n')
row='300 1 1 0 0 0 0 0 0 70 0 0 0 20 0 0 0 20\\n'
Path('thermo.out').write_text(header+row*steps)
'''); mpiexec.chmod(0o755)
            devices = ['0','1','2','3']; gpu = {x:{'uuid':x} for x in devices}
            proof=root/'proof.json'; ds.write_json(proof, {'schema':'dmgmd.npt-b3-environment-proof.v1',
                'status':'PASS', 'candidate_sha256':ds.sha(exe),'gate_ranks':4,
                'supported_runtime_backends':['HostStaged','CudaAware'],'physical_devices':devices,
                'environment_fingerprint':{'mock':True},'gpu_identity':gpu,'mpiexec_path':str(mpiexec)})
            rng=root/'rng.json'; ds.write_json(rng, {'schema':'dmgmd.domain-npt-rng-proof.v1','status':'PASS','candidate_sha256':ds.sha(exe)})
            gate=root/'gate.json'; cases=json.loads((ds.HERE/'domain-manifest.json').read_text())['required_cases']
            ds.write_json(gate, {'schema':'dmgmd.domain-npt-deterministic-proof.v1','status':'PASS',
                                 'candidate_sha256':ds.sha(exe),'passed_cases':cases})
            freeze=root/'freeze.json'
            class Args: pass
            args=Args(); args.registration=registration; args.inputs=inputs; args.candidate=exe
            args.environment_proof=proof; args.rng_proof=rng; args.deterministic_proof=gate
            args.acf_helper=acf; args.output=freeze
            library_patch = patch.object(ds, 'linked_library_hashes', return_value={'/mock/lib': 'mock'})
            library_patch.start(); self.addCleanup(library_patch.stop)
            # This test exercises orchestration only; fabricated proof is not
            # accepted by the actual formal freeze gate.
            proof_patch = patch.object(ds, 'validate_gate_proofs')
            proof_patch.start(); self.addCleanup(proof_patch.stop)
            ds.freeze(args)
            args.output = root/'second-freeze.json'
            with self.assertRaises(FileExistsError): ds.freeze(args)
            jobs=json.loads(freeze.read_text())['jobs']; evidence=root/'evidence'; evidence.mkdir()
            args.freeze=freeze; args.devices=','.join(devices); args.timeout=30
            with patch.object(ds.capture_environment_proof, 'environment_fingerprint', return_value={'mock':True}), \
                 patch.object(ds.capture_environment_proof, 'gpu_inventory', return_value=gpu):
                for key in jobs:
                    args.job=key; args.output=evidence/key; ds.formal_run(args)
                args.job=next(iter(jobs)); args.output=root/'duplicate-job'
                with self.assertRaises(FileExistsError): ds.formal_run(args)
            self.assertEqual(len(analyzer.load_records(freeze, evidence)[1]), len(jobs))
            output=root/'analysis.json'
            cli=['analyze', '--freeze',str(freeze), '--evidence',str(evidence), '--registration',str(registration),
                 '--inputs',str(inputs), '--candidate',str(exe), '--environment-proof',str(proof),
                 '--rng-proof',str(rng),'--deterministic-proof',str(gate),'--acf-helper',str(acf),'--output',str(output)]
            # Exercise real loader/orchestration; statistics are intentionally stubbed here.
            with patch.dict(os.environ), patch.object(sys, 'argv', cli), patch.object(analyzer, 'prepare_response', return_value={'diagnostics': {}, 'temperature_checks': {'0.99': {'status':'PASS'}, '1.00': {'status':'PASS'}, '1.01': {'status':'PASS'}}}), \
                 patch.object(analyzer, 'scientific_reference', return_value={'status':'PASS'}), \
                 patch.object(analyzer, 'compare', return_value={'status':'PASS'}), \
                 patch.object(analyzer, 'absolute_mean', return_value={'status':'PASS'}):
                analyzer.main()
            self.assertEqual(json.loads(output.read_text())['status'], 'PASS')
            with patch.object(sys, 'argv', cli):
                with self.assertRaisesRegex(ValueError, 'already exists'): analyzer.main()
            second_cli = cli[:-1] + [str(root/'second-analysis.json')]
            with patch.object(sys, 'argv', second_cli):
                with self.assertRaises(FileExistsError): analyzer.main()
            copied_evidence=root/'copied-evidence'; shutil.copytree(evidence, copied_evidence)
            copied_cli=cli.copy(); copied_cli[copied_cli.index('--evidence')+1]=str(copied_evidence)
            copied_cli[-1]=str(root/'copied-analysis.json')
            with patch.object(sys, 'argv', copied_cli):
                with self.assertRaisesRegex(ValueError, 'ledger job evidence mismatch'): analyzer.main()
            copied_freeze=root/'copied-freeze.json'; shutil.copyfile(freeze, copied_freeze)
            copied_cli=cli.copy(); copied_cli[copied_cli.index('--freeze')+1]=str(copied_freeze)
            copied_cli[-1]=str(root/'other-analysis.json')
            with patch.object(sys, 'argv', copied_cli):
                with self.assertRaisesRegex(ValueError, 'freeze ledger identity'): analyzer.main()
            stage=evidence/next(iter(jobs)); (stage/'thermo.out').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'thermo changed'): analyzer.load_records(freeze,evidence)
            (stage/'record.json').unlink()
            with self.assertRaisesRegex(ValueError, 'incomplete'): analyzer.load_records(freeze,evidence)


if __name__ == '__main__': unittest.main()
