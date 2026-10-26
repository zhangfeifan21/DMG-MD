#!/usr/bin/env python3
"""O8 output-step, segment, restart and independent actual-collective witnesses."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

import run_mpi_domain as domain


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cases():
    prefix = 'potential nep.txt\ntime_step 0.001\nensemble nve\n'
    xyz = ' precision double velocity force potential virial unwrapped_position\n'
    return [
        ('silent', domain.lattice_model(), prefix+'run 7\n', [0]),
        ('thermo', domain.lattice_model(), prefix+'dump_thermo 3\nrun 8\n', [2]),
        ('xyz', domain.lattice_model(), prefix+'dump_xyz 2 xyz.xyz'+xyz+'run 7\n', [3]),
        ('union', domain.lattice_model(), prefix+'dump_thermo 3\ndump_xyz 4 xyz.xyz'+xyz+
         'dump_xyz 5 other.xyz'+xyz+'run 13\n', [8]),
        ('late', domain.lattice_model(), prefix+'dump_thermo 11\ndump_xyz 12 xyz.xyz'+xyz+'run 7\n', [0]),
        ('restart', domain.lattice_model(), prefix+'dump_restart 2\nrun 5\n', [0]),
        ('multi-run', domain.lattice_model(), prefix+'dump_thermo 3\nrun 5\nrun 4\n'+
         'dump_xyz 2 xyz.xyz'+xyz+'run 5\nensemble nvt_ber 300 350 1000\nrun 4\n'+
         'ensemble nve\ndump_thermo 2\nrun 5\n', [1, 0, 2, 4, 2]),
        ('nvt', domain.lattice_model(), prefix+'ensemble nvt_ber 300 350 1000\n'+
         'dump_thermo 3\ndump_xyz 4 xyz.xyz'+xyz+'run 7\n', [7]),
        ('nvt-weak', domain.lattice_model(), prefix+'ensemble nvt_ber 300 350 1000000\nrun 5\n', [5]),
        ('adaptive-correction', domain.lattice_model(), prefix+'time_step 1.0 0.0001\n'+
         'correct_velocity 10\ndump_thermo 3\ndump_xyz 4 xyz.xyz'+xyz+'run 13\n', [6]),
        ('migration', '2\n'+domain.EMPTY_LOCAL_MODEL.splitlines()[1]+'\n'+
         'C 31.8 12 12 0.2 0 0\nC 63.8 12 12 0.2 0 0\n',
         'potential nep.txt\ntime_step 1.0\nensemble nve\n'+
         'run 4\ndump_thermo 2\ndump_xyz 3 xyz.xyz'+xyz+'run 7\n', [0, 4]),
        ('empty-local', domain.EMPTY_LOCAL_MODEL, domain.EMPTY_LOCAL_RUN, [0]),
    ]


def science_files(work):
    return {path.name: digest(path) for path in work.iterdir()
            if path.name in ('thermo.out', 'neighbor.out', 'restart.xyz') or
            path.suffix == '.xyz' and path.name != 'model.xyz'}


def run_pair(args, root, name, model, run, counts, ranks, backend, mode='off', timing=False):
    launcher = Path(shutil.which('mpiexec')).resolve()
    potential = domain.BASELINE_DIR/'inputs/potentials/nep_C.txt'
    records = []
    expected_steps = domain.expected_steps(run)
    for version in ('before', 'after'):
        binary = getattr(args, version).resolve()
        work = root/f'p{ranks}-{backend}-{name}-{mode}-timing{int(timing)}-{version}'
        case = domain.DomainCase(name, model, run, [], [], int(model.splitlines()[0]))
        domain.stage_case(case, work, potential)
        (work/'command.json').write_text(json.dumps(dict(
            command=[str(launcher), '-n', str(ranks), str(binary)], mode=mode,
            probe=str(args.probe.resolve()), diagnostic_only=True,
            domain_timing=timing), indent=2)+'\n')
        output = domain.execute(binary, launcher, work, ranks, backend, args.devices.split(','),
                                180, domain_timing=timing, partition_mode=mode)
        if output['returncode']:
            raise ValueError(str(work)+'\n'+output['stderr'][-3000:])
        witnesses = [domain.key_values(line) for line in output['stdout'].splitlines()
                     if line.startswith('O8_MPI ')]
        if {int(row['rank']) for row in witnesses} != set(range(ranks)):
            raise ValueError('missing independent PMPI witnesses: '+str(work))
        local_domain = 'DMGMD_DOMAIN mode=m2a ' in output['stdout']
        expected = sum(counts) if version == 'after' and local_domain else expected_steps
        if any(int(row['thermo_sums']) != expected for row in witnesses):
            raise ValueError('thermo sum count differs: '+str(work)+' '+str(witnesses))
        if local_domain and any(int(row['reason_ors']) != expected_steps for row in witnesses):
            raise ValueError('cache decision count differs')
        if timing:
            domain.verify_timing_records(output['stdout'], run, str(work))
        summaries = [domain.key_values(line) for line in output['stdout'].splitlines()
                     if line.startswith('DMGMD_DOMAIN_SUMMARY ')]
        if version == 'after' and local_domain:
            for row in summaries:
                sequence = int(row['sequence'])
                if (int(row['thermo_steps']) != counts[sequence] or
                        int(row['thermo_steps'])+int(row['thermo_skipped_steps']) != int(row['steps'])):
                    raise ValueError('segment thermo count differs')
        records.append(dict(version=version, name=name, ranks=ranks, backend=backend,
                            mode=mode, timing=timing, expected_thermo_sums=expected,
                            witnesses=witnesses, science=science_files(work), directory=str(work),
                            status='PASS'))
    if records[0]['science'] != records[1]['science']:
        raise ValueError('scientific bytes differ: '+str(records))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--devices', default='0,1,2,3,4,5,6,7')
    args = parser.parse_args()
    root = args.work_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    records = []
    previous_preload = os.environ.get('LD_PRELOAD')
    os.environ['LD_PRELOAD'] = str(args.probe.resolve())
    try:
        for ranks in (2, 4):
            for backend in ('HostStaged', 'CudaAware'):
                for name, model, run, counts in cases():
                    records += run_pair(args, root, name, model, run, counts, ranks, backend)
                    (root/'results.json').write_text(json.dumps(records, indent=2)+'\n')
                    print('PASS', ranks, backend, name, flush=True)
                    if name == 'restart':
                        resumed = [Path(row['directory'])/'restart.xyz' for row in records[-2:]]
                        if not all(path.exists() for path in resumed):
                            raise ValueError('missing restart model')
                        resume_run = ('potential nep.txt\ntime_step 0.001\nensemble nve\n'+
                                      'dump_thermo 2\ndump_xyz 3 resumed.xyz precision double velocity force potential virial\nrun 3\n')
                        records += run_pair(args, root, 'resumed', resumed[0].read_text(),
                                            resume_run, [2], 4 if ranks == 2 else 2, backend)
                selected = {entry[0]: entry for entry in cases()}
                for name in ('silent', 'union', 'empty-local', 'migration', 'nvt'):
                    for mode in ('serial', 'overlap-wait', 'overlap-poll'):
                        records += run_pair(args, root, *selected[name], ranks, backend, mode)
                for name in ('silent', 'union', 'empty-local', 'migration', 'nvt'):
                    records += run_pair(args, root, *selected[name], ranks, backend, timing=True)
        for backend in ('HostStaged', 'CudaAware'):
            for name, model, run, counts in cases()[:1]:
                records += run_pair(args, root, 'm1-p1', model, run, counts, 1, backend)
                small_model = domain.EMPTY_LOCAL_MODEL.replace('Lattice="64 ', 'Lattice="24 ')
                records += run_pair(args, root, 'm1-fallback', small_model, run, counts, 2, backend)
            for name, model, run, counts in cases():
                if name not in ('silent', 'union', 'multi-run', 'empty-local', 'nvt'):
                    continue
                large_model = model.replace('Lattice="64 ', 'Lattice="128 ')
                records += run_pair(args, root, name, large_model, run, counts, 8, backend)
    finally:
        if previous_preload is None:
            os.environ.pop('LD_PRELOAD', None)
        else:
            os.environ['LD_PRELOAD'] = previous_preload
    (root/'results.json').write_text(json.dumps(records, indent=2)+'\n')
    (root/'summary.json').write_text(json.dumps(dict(status='PASS', jobs=len(records),
        candidate_sha256=digest(args.after.resolve()), baseline_sha256=digest(args.before.resolve()),
        results_sha256=digest(root/'results.json'), all_scientific_files_identical=True), indent=2)+'\n')
    print('PASS O8 thermo demand differential', len(records), 'jobs', flush=True)


if __name__ == '__main__':
    main()
