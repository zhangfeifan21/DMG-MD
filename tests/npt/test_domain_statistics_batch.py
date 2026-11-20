"""Serial batch orchestration with real synthetic child processes, never MD."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import domain_statistics as ds


FAKE_RUNNER = '''import argparse, hashlib, json, shutil, sys
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('action'); p.add_argument('--job'); p.add_argument('--output'); p.add_argument('--inputs'); p.add_argument('--freeze'); a,_=p.parse_known_args()
if a.job == 'bad': sys.exit(7)
out=Path(a.output); out.mkdir(); inputs=Path(a.inputs); frozen=Path(a.freeze)
job=json.loads(frozen.read_text())['jobs'][a.job]
for src,dst in ((job['model'],'model.xyz'),(job['run'],'run.in'),('nep.txt','nep.txt')): shutil.copyfile(inputs/src,out/dst)
for n in ('thermo.out','stdout.log','stderr.log'): (out/n).write_text(n)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
record={'status':'PASS','job':a.job,'metadata':job,'freeze_sha256':sha(frozen),
 'input_sha256':{n:sha(out/n) for n in ('model.xyz','run.in','nep.txt')},
 'thermo_sha256':sha(out/'thermo.out'),'log_sha256':{n:sha(out/n) for n in ('stdout.log','stderr.log')}}
(out/'record.json').write_text(json.dumps(record))
if a.job == 'tamper': (out/'thermo.out').write_text('altered after record')
'''
FAKE_ANALYZER = '''import argparse, json, os
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--output'); p.add_argument('--evidence'); a,_=p.parse_known_args()
fd=os.open(Path(a.evidence)/'.domain-npt-analysis-started',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644); os.close(fd)
Path(a.output).write_text(json.dumps({'status':'PASS'}))
'''


class BatchTests(unittest.TestCase):
    def run_batch(self, root, names, output):
        scripts = root/'scripts'; scripts.mkdir(exist_ok=True)
        (scripts/'domain_statistics.py').write_text(FAKE_RUNNER)
        (scripts/'analyze_domain_statistics.py').write_text(FAKE_ANALYZER)
        inputs = root/'inputs'; inputs.mkdir(exist_ok=True)
        for name in ('model', 'run', 'nep.txt'): (inputs/name).write_text(name)
        ds.write_json(inputs/'inventory.json', {'files': {n: ds.sha(inputs/n) for n in ('model','run','nep.txt')}})
        jobs = {n: {'model':'model','run':'run'} for n in names}
        freeze = root/'freeze.json'; ds.write_json(freeze, {'jobs':jobs})
        class Args: pass
        a=Args(); a.inputs=inputs; a.freeze=freeze; a.output=output
        a.registration=a.candidate=a.environment_proof=a.rng_proof=a.deterministic_proof=a.acf_helper=freeze
        a.devices='0,1,2,3'; a.timeout=30
        with patch.object(ds, 'HERE', scripts), patch.object(ds, 'validate_freeze', return_value={'jobs':jobs}):
            ds.formal_batch(a)

    def test_stops_at_failed_child_and_refuses_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); output=root/'batch'
            with self.assertRaisesRegex(RuntimeError, 'job failed: bad'): self.run_batch(root, ['one','bad','third'], output)
            progress=json.loads((output/'progress.json').read_text())
            self.assertEqual(progress['status'], 'STOPPED')
            self.assertEqual(progress['jobs']['one']['status'], 'PASS')
            self.assertEqual(progress['jobs']['bad']['returncode'], 7)
            self.assertEqual(progress['jobs']['bad']['status'], 'FAIL')
            self.assertNotIn('third', progress['jobs'])
            self.assertIsNone(progress['analysis'])
            before=ds.sha(output/'evidence'/'one'/'record.json')
            with self.assertRaises(FileExistsError): self.run_batch(root, ['one','bad','third'], output)
            self.assertEqual(ds.sha(output/'evidence'/'one'/'record.json'), before)

    def test_complete_batch_analyzes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); output=root/'batch'
            self.run_batch(root, ['one','two'], output)
            progress=json.loads((output/'progress.json').read_text())
            self.assertEqual(progress['status'], 'COMPLETE')
            self.assertEqual(progress['analysis']['status'], 'PASS')
            self.assertTrue((output/'evidence'/'.domain-npt-analysis-started').is_file())
            with self.assertRaises(FileExistsError): self.run_batch(root, ['one','two'], output)
            self.assertEqual(len(list((output/'batch-logs').glob('analysis.stdout'))), 1)

    def test_exit_zero_does_not_hide_changed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); output=root/'batch'
            with self.assertRaisesRegex(ValueError, 'thermo changed'):
                self.run_batch(root, ['one','tamper','third'], output)
            progress=json.loads((output/'progress.json').read_text())
            self.assertEqual(progress['jobs']['tamper']['returncode'], 0)
            self.assertEqual(progress['jobs']['tamper']['status'], 'FAIL')
            self.assertNotIn('third', progress['jobs'])
            self.assertIsNone(progress['analysis'])


if __name__ == '__main__': unittest.main()
