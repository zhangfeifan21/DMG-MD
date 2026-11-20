#!/usr/bin/env python3
"""Compare fixed SCR scalar stream at P1/P2/P4 without CUDA or MD trajectories."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scalar', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--mpiexec', default='mpiexec')
    p.add_argument('--timeout', type=int, default=120)
    a = p.parse_args()
    scalar, root = a.scalar.resolve(), a.work_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    records = []
    result = {'scope': 'CPU fixed scalar inputs; not CUDA environment validation',
              'scalar_sha256': hashlib.sha256(scalar.read_bytes()).hexdigest(),
              'records': records, 'status': 'RUNNING'}
    def save(): (root / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    try:
        streams = []
        for ranks in (1, 2, 4):
            command = [a.mpiexec, '-n', str(ranks), str(scalar)]
            record = {'command': command, 'status': 'RUNNING',
                      'environment': {k: v for k, v in os.environ.items() if k.startswith(('OMPI_', 'PMIX_', 'UCX_'))}}
            records.append(record); save()
            with (root / f'p{ranks}.stdout').open('w') as out, (root / f'p{ranks}.stderr').open('w') as err:
                proc = subprocess.run(command, stdout=out, stderr=err, timeout=a.timeout)
            record['returncode'] = proc.returncode
            if proc.returncode: raise RuntimeError(f'P{ranks}: exit {proc.returncode}')
            lines = [x for x in (root / f'p{ranks}.stdout').read_text().splitlines()
                     if re.fullmatch(r'\d+ (?:-?0x[0-9a-f.]+p[+-]\d+ ){2}-?0x[0-9a-f.]+p[+-]\d+', x)]
            if len(lines) != 12 or [int(x.split()[0]) for x in lines] != list(range(12)):
                raise ValueError(f'P{ranks}: missing or reordered scalar output')
            streams.append(lines)
            record['status'] = 'PASS'; save()
        if any(x != streams[0] for x in streams[1:]): raise AssertionError('different fixed scalar RNG stream across rank counts')
        result['status'] = 'PASS'
    except Exception as e:
        result.update(status='FAIL', error=str(e)); raise
    finally: save()


if __name__ == '__main__': main()
