#!/usr/bin/env python3
"""Prepare the registered 5760-atom diagnostic fixture; not a formal freeze."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from generate_inputs import model_text, ber_run, scr_run

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--ensemble', choices=('npt_ber', 'npt_scr'), default='npt_ber')
    p.add_argument('--steps', type=int, default=20)
    p.add_argument('--scenario', choices=('single', 'fixed-box', 'segments'), default='single')
    a = p.parse_args()
    if a.steps < 1: p.error('steps must be positive')
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'model.xyz').write_text(model_text((20, 6, 6), 301))
    shutil.copyfile(ROOT / 'tests/baseline/inputs/potentials/nep_C.txt', out / 'nep.txt')
    if a.ensemble == 'npt_ber':
        run = ber_run(a.steps, trajectory_interval=1, restart_interval=a.steps)
    else:
        run = scr_run(a.steps).replace('dump_thermo 1\n',
            'dump_thermo 1\ndump_xyz 1 trajectory.xyz precision double velocity force potential virial\n')
    if a.scenario == 'fixed-box':
        run = run.replace(' 0 425 200', ' 0 2001 200')
    elif a.scenario == 'segments':
        # Keep controller recreation and transitions explicit at segment boundaries.
        run += ('ensemble nve\nrun 2\nensemble nvt_ber 300 300 40\nrun 2\n'
                f'ensemble {a.ensemble} 300 300 40 0 425 200\nrun 2\n')
    (out / 'run.in').write_text(run)
    print(json.dumps({name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                      for name in ('model.xyz', 'nep.txt', 'run.in')}, indent=2))


if __name__ == '__main__': main()
