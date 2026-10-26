#!/usr/bin/env python3
"""O7 differential boundary tests and independent PMPI collective witnesses.

The probe is diagnostic only, used for BOTH binaries. Formal timing never
preloads it. Run after the canonical environment and committed golden gates.
"""
import argparse
import json
import os
from pathlib import Path
import shutil

import run_mpi_domain as d


def cases():
    header = d.MICRO_CROSSINGS_MODEL.splitlines()[1]
    run = d.MICRO_CROSSINGS_RUN
    result = []
    for name, speed, rebuilds in [('below', .125 - 1e-8, 0),
                                  ('equal', .125, 0), ('above', .125 + 1e-8, 1)]:
        # A non-root manager is the only contributor. Other ranks are empty.
        model = f'2\n{header}\nC 1 3 3 0 0 0\nC 40 12 12 {speed:.17g} 0 0\n'
        result.append((name, model, run, rebuilds, False))
    result.extend([
        ('periodic', d.MICRO_CROSSINGS_MODEL, run, 0, False),
        ('multi-run', d.CHAIN_MODEL, d.CONTINUOUS_RUN, 0, False),
        ('empty-local', d.EMPTY_LOCAL_MODEL, d.EMPTY_LOCAL_RUN, 0, False),
        ('multi-box', d.UNROUTABLE_MODEL, d.UNROUTABLE_RUN, None, True),
        ('nonfinite-square', f'2\n{header}\nC 1 3 3 0 0 0\nC 40 12 12 1e200 0 0\n', run, None, True),
        ('mixed-invalid-limit', f'2\n{header}\nC 1 3 3 0.6 0 0\nC 40 12 12 1e200 0 0\n',
         run, None, True),
    ])
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before', type=Path, required=True)
    p.add_argument('--after', type=Path, required=True)
    p.add_argument('--probe', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--devices', default='0,1,2,3')
    p.add_argument('--ranks', default='2,4')
    args = p.parse_args()
    root = args.work_root.resolve()
    root.mkdir(exist_ok=False, parents=True)
    devices = args.devices.split(',')
    requested_ranks = [int(value) for value in args.ranks.split(',')]
    if (not requested_ranks or set(requested_ranks) - {2, 4, 8} or
            len(devices) < max(requested_ranks)):
        raise ValueError('ranks must be 2,4,8 with enough visible devices')
    launcher = Path(shutil.which('mpiexec')).resolve()
    potential = d.BASELINE_DIR / 'inputs/potentials/nep_C.txt'
    records = []
    previous_preload = os.environ.get('LD_PRELOAD')
    os.environ['LD_PRELOAD'] = str(args.probe.resolve())
    try:
        for ranks in requested_ranks:
            for backend in ('HostStaged', 'CudaAware'):
                for name, model, run, rebuilds, error in cases():
                    if ranks == 8:
                        # Keep slab_width >= d_coord. Translation preserves
                        # the original 16 A face crossings, including PBC.
                        lines = model.splitlines()
                        lines[1] = lines[1].replace('Lattice="64 ', 'Lattice="128 ')
                        for i in range(2, len(lines)):
                            atom = lines[i].split()
                            atom[1] = str(float(atom[1]) + 64)
                            if name == 'multi-box':
                                atom[4] = str(float(atom[4]) * 2)
                            lines[i] = ' '.join(atom)
                        model = '\n'.join(lines) + '\n'
                    pair = []
                    for version in ('before', 'after'):
                        binary = getattr(args, version).resolve()
                        work = root / f'p{ranks}-{backend}-{name}-{version}'
                        case = d.DomainCase(name, model, run, [], [], int(model.splitlines()[0]))
                        d.stage_case(case, work, potential)
                        launch = dict(command=[str(launcher), '-n', str(ranks), str(binary)],
                                      probe=str(args.probe.resolve()), diagnostic_only=True)
                        (work/'command.json').write_text(json.dumps(launch, indent=2)+'\n')
                        output = d.execute(binary, launcher, work, ranks, backend, devices, 90)
                        text = output['stdout'] + output['stderr']
                        witness = [d.key_values(line) for line in output['stdout'].splitlines()
                                   if line.startswith('O7_MPI ')]
                        if error:
                            assert output['returncode'] != 0, (work, 'expected failure')
                            assert 'an owned atom has a non-finite or unsupported multi-box displacement' in text, work
                            assert witness and all(int(r['invalid_or']) == 1 for r in witness), work
                            if name == 'mixed-invalid-limit':
                                assert all(int(r['displacement_or']) == 1 for r in witness), work
                        else:
                            assert output['returncode'] == 0, (work, text[-2000:])
                            assert {int(r['rank']) for r in witness} == set(range(ranks)), work
                            assert all(int(r['reason_or']) == case.steps for r in witness), work
                            summaries = [d.key_values(line) for line in text.splitlines()
                                         if line.startswith('DMGMD_DOMAIN_SUMMARY ')]
                            for rank in range(ranks):
                                assert sum(int(r['rebuild_steps']) for r in summaries
                                           if int(r['rank']) == rank) == rebuilds, work
                            assert all(int(r['displacement_or']) == rebuilds for r in witness), work
                            assert all(int(r['invalid_or']) == 0 for r in witness), work
                        record = dict(name=name, ranks=ranks, backend=backend, version=version,
                                      returncode=output['returncode'], witness=witness, status='PASS')
                        pair.append((work, record))
                        records.append(record)
                    if not error:
                        before, after = pair
                        old = {int(r['rank']): r for r in before[1]['witness']}
                        for row in after[1]['witness']:
                            assert int(old[int(row['rank'])]['scalar_max']) - int(row['scalar_max']) == case.steps, name
                        for filename in ('thermo.out', 'trajectory.xyz', 'first.xyz', 'second.xyz', 'neighbor.out'):
                            if (before[0]/filename).exists():
                                assert (before[0]/filename).read_bytes() == (after[0]/filename).read_bytes(), (name, filename)
                    (root/'results.json').write_text(json.dumps(records, indent=2)+'\n')
                    print('PASS', ranks, backend, name, flush=True)
        # M1/P1 remains on the original protocol and produces identical bytes.
        for backend in ('HostStaged', 'CudaAware'):
            pair = []
            for version in ('before', 'after'):
                work = root / f'p1-{backend}-{version}'
                case = d.DomainCase('m1', d.CHAIN_MODEL, d.CHAIN_RUN, [], [], 3)
                d.stage_case(case, work, potential)
                output = d.execute(getattr(args, version).resolve(), launcher, work, 1, backend, devices, 90)
                assert output['returncode'] == 0, work
                pair.append(work)
            for filename in ('thermo.out', 'trajectory.xyz', 'neighbor.out'):
                assert (pair[0]/filename).read_bytes() == (pair[1]/filename).read_bytes(), filename
            print('PASS M1', backend, flush=True)
    finally:
        if previous_preload is None:
            os.environ.pop('LD_PRELOAD', None)
        else:
            os.environ['LD_PRELOAD'] = previous_preload
    print('PASS O7 boundary/count differential', len(records), 'MPI jobs + 4 M1 jobs', flush=True)


if __name__ == '__main__':
    main()
