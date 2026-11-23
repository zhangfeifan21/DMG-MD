#!/usr/bin/env python3
"""DN4 short operational comparisons; output is evidence, not statistical acceptance.

Run after the MPI/CUDA environment gate. The fixture must be the registered large
box. Each comparison is paired with off using identical inputs and tolerances.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'tests/baseline'), str(ROOT / 'tests/mpi')]
import run_baselines as baseline
import run_domain_npt as domain
import check_environment
import run_mpi_migration as migration


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_text(ensemble, scenario):
    bulk = 2001 if scenario == 'fixed-box' else 425
    control = f'ensemble {ensemble} 300 300 40 0 {bulk} 200\n'
    output = ('dump_thermo 1\n'
              'dump_xyz 1 trajectory.xyz precision double velocity force potential virial\n'
              'dump_restart 1\n')
    header = 'potential nep.txt\ntime_step 0.25\n' + control
    if scenario == 'late-unwrapped':
        later = output.replace('virial\n', 'virial unwrapped_position\n')
        return (header + output + 'run 1\n' + control + later + 'run 2\n'
                + 'ensemble nve\n' + later + 'run 2\n')
    if scenario == 'segments':
        return (header + output + 'run 1\nensemble nve\n' + output + 'run 2\n'
                + 'ensemble nvt_ber 300 300 40\n' + output + 'run 2\n'
                + control + output + 'run 2\n')
    return header + output + 'run 4\n'


def validate_outputs(stage, expected_steps, time_step_fs=0.25):
    thermo = baseline.parse_thermo(stage / 'thermo.out')
    frames = baseline.parse_xyz(stage / 'trajectory.xyz')
    restart = baseline.parse_xyz(stage / 'restart.xyz')
    rows = sum(len(segment['rows']) for segment in thermo)
    if rows != expected_steps or len(frames) != expected_steps or len(restart) != 1:
        raise ValueError(f'expected {expected_steps} thermo rows/frames and one restart, '
                         f'got {rows}/{len(frames)}/{len(restart)}')
    model = baseline.parse_xyz(stage / 'model.xyz')
    n = model[0]['natoms']
    if any(frame['natoms'] != n for frame in frames + restart):
        raise ValueError('output atom count differs from input')
    required = {'pos', 'vel', 'forces', 'energy_atom', 'virial'}
    for index, frame in enumerate(frames):
        if not required.issubset({name for name, _, _ in frame['properties']}):
            raise ValueError('trajectory lacks requested position, velocity, force, energy or virial')
        if abs(float(frame['fields']['Time']) - time_step_fs * (index + 1)) > 1e-7:
            raise ValueError(f'wrong trajectory time at frame {index}')
    final, last_restart = frames[-1], restart[0]
    # restart.xyz contains no time; verify that it represents the snapshot at
    # the expected final simulation time via the file's %.17g serialization (compared at .6g).
    if abs(float(final['fields']['Time']) - time_step_fs * expected_steps) > 1e-7:
        raise ValueError('last trajectory time is not the end of the run')
    if [format(float(x), '.6g') for x in final['fields']['Lattice'].split()] != [format(float(x), '.6g') for x in last_restart['fields']['Lattice'].split()]:
        raise ValueError('restart lattice differs from final snapshot')
    for prop in ('pos', 'vel'):
        def offset(frame):
            pos = next(i for i, (name, _, _) in enumerate(frame['properties']) if name == prop)
            return sum(width for _, _, width in frame['properties'][:pos]), frame['properties'][pos][2]
        left, width = offset(final)
        right, restart_width = offset(last_restart)
        if width != restart_width: raise ValueError('restart property width mismatch')
        for a, b in zip(final['rows'], last_restart['rows']):
            if a[0] != b[0] or [format(float(x), '.6g') for x in a[left:left+width]] != [format(float(x), '.6g') for x in b[right:right+width]]:
                raise ValueError(f'restart {prop} differs from final snapshot')
    return {'thermo_rows': rows, 'trajectory_frames': len(frames),
            'last_time_fs': float(final['fields']['Time']), 'restart_matches_final': True}


def compare(left, right, tolerances):
    metrics = baseline.DiffCollector(tolerances, enforce=True)
    baseline.compare_thermo(left / 'thermo.out', right / 'thermo.out', metrics, 'thermo')
    frames = baseline.parse_xyz(left / 'trajectory.xyz')
    if any(name == 'unwrapped_position' for frame in frames for name, _, _ in frame['properties']):
        migration.compare_xyz_files(left / 'trajectory.xyz', right / 'trajectory.xyz', metrics, 'trajectory.xyz')
    else:
        baseline.compare_xyz(left / 'trajectory.xyz', right / 'trajectory.xyz', {'kind': 'xyz'}, metrics, 'trajectory.xyz')
    baseline.compare_xyz(left / 'restart.xyz', right / 'restart.xyz', {'kind': 'restart'}, metrics, 'restart.xyz')
    return metrics.stats


def validate_late_unwrapped(stage, tolerances):
    frames = baseline.parse_xyz(stage / 'trajectory.xyz')
    collector = baseline.DiffCollector(tolerances, enforce=True)
    def offset(frame, wanted):
        off = 0
        for name, _, width in frame['properties']:
            if name == wanted:
                return off
            off += width
        return None
    checked = 0
    for previous, current in zip(frames, frames[1:]):
        unwrapped = offset(current, 'unwrapped_position')
        if unwrapped is None:
            continue
        previous_unwrapped = offset(previous, 'unwrapped_position')
        prev_pos, pos = offset(previous, 'pos'), offset(current, 'pos')
        old_lattice = [float(x) for x in previous['fields']['Lattice'].split()]
        new_lattice = [float(x) for x in current['fields']['Lattice'].split()]
        for atom, (old, new) in enumerate(zip(previous['rows'], current['rows'])):
            for d in range(3):
                old_length, new_length = old_lattice[4 * d], new_lattice[4 * d]
                old_x = float(old[prev_pos + d])
                raw_x = float(new[pos + d]) * old_length / new_length
                delta = raw_x - old_x
                if delta < -old_length * 0.5: delta += old_length
                elif delta > old_length * 0.5: delta -= old_length
                base = float(old[previous_unwrapped + d]) if previous_unwrapped is not None else old_x
                collector.observe('position', base + delta, float(new[unwrapped + d]),
                                  f'late-unwrapped atom={atom} axis={d}')
                checked += 1
    if not checked:
        raise AssertionError('no late unwrapped coordinates were checked')
    return {'coordinates_checked': checked, 'comparison': collector.stats}


def execute(stage, executable, mpiexec, rank, backend, mode, devices, timeout, model, potential, run, records):
    stage.mkdir()
    shutil.copyfile(model, stage / 'model.xyz')
    shutil.copyfile(potential, stage / 'nep.txt')
    (stage / 'run.in').write_text(run)
    env = os.environ.copy()
    env.update(CUDA_VISIBLE_DEVICES=','.join(devices[:rank]), CUDA_DEVICE_ORDER='PCI_BUS_ID',
               DMGMD_COMM_BACKEND=backend, DMGMD_DOMAIN_NPT=mode,
               DMGMD_DOMAIN_PARTITION='off', DMGMD_DOMAIN_OVERLAP='off',
               DMGMD_DOMAIN_DIAGNOSTICS='1', DMGMD_ENSEMBLE_SEED='12345678', OMP_NUM_THREADS='1')
    command = [str(mpiexec), '-n', str(rank), str(executable)]
    rec = {'stage': stage.name, 'command': command,
           'environment': {k: env[k] for k in ('CUDA_VISIBLE_DEVICES', 'CUDA_DEVICE_ORDER',
             'DMGMD_COMM_BACKEND', 'DMGMD_DOMAIN_NPT', 'DMGMD_DOMAIN_PARTITION',
             'DMGMD_DOMAIN_OVERLAP', 'DMGMD_DOMAIN_DIAGNOSTICS', 'DMGMD_ENSEMBLE_SEED', 'OMP_NUM_THREADS')},
           'input_sha256': {x: sha(stage / x) for x in ('model.xyz', 'nep.txt', 'run.in')}, 'status': 'RUNNING'}
    records.append(rec)
    try:
        with (stage / 'stdout.log').open('w') as out, (stage / 'stderr.log').open('w') as err:
            proc = subprocess.Popen(command, cwd=stage, env=env, stdout=out, stderr=err, start_new_session=True)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise
        rec['returncode'] = proc.returncode
        log = (stage / 'stdout.log').read_text(errors='replace') + '\n' + (stage / 'stderr.log').read_text(errors='replace')
        if proc.returncode:
            raise RuntimeError(f'exit {proc.returncode}')
        domain.check_route(log, rank, mode)
        for name in ('thermo.out', 'trajectory.xyz', 'restart.xyz'):
            if not (stage / name).is_file():
                raise RuntimeError(f'missing {name}')
        expected_steps = sum(int(line.split()[1]) for line in run.splitlines() if line.startswith('run '))
        time_step_fs = float(next(line.split()[1] for line in run.splitlines() if line.startswith('time_step ')))
        rec['outputs'] = validate_outputs(stage, expected_steps, time_step_fs)
        if 'unwrapped_position' in run:
            rec['unwrapped_semantics'] = validate_late_unwrapped(stage, json.loads(
                (ROOT / 'tests/baseline/manifest.json').read_text())['tolerances'])
        rec['status'] = 'PASS'
    except Exception as error:
        rec.update(status='FAIL', error=str(error))
        raise
    finally:
        rec['files_sha256'] = {x.name: sha(x) for x in stage.iterdir() if x.is_file()}
    return stage


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--devices', required=True)
    p.add_argument('--ranks', default='2,4')
    p.add_argument('--backends', default='HostStaged,CudaAware')
    p.add_argument('--timeout', type=int, default=900)
    a = p.parse_args()
    candidate, fixture, root = a.candidate.resolve(), a.fixture.resolve(), a.work_root.resolve()
    ranks, backends, devices = list(map(int, a.ranks.split(','))), a.backends.split(','), a.devices.split(',')
    if not ranks or any(x not in (2, 4) for x in ranks) or len(devices) < 4 or len(set(devices)) != len(devices):
        p.error('ranks must be 2,4 with four unique devices for P4 restart sources')
    if not backends or any(x not in ('HostStaged', 'CudaAware') for x in backends):
        p.error('invalid backend')
    mpiexec = check_environment.validate_environment(candidate, 'mpiexec', devices, 4, a.timeout)
    root.mkdir(parents=True, exist_ok=False)
    tolerances = json.loads((ROOT / 'tests/baseline/manifest.json').read_text())['tolerances']
    records = []
    result = {'scope': 'operational_subset', 'candidate_sha256': sha(candidate),
              'fixture_sha256': {x: sha(fixture / x) for x in ('model.xyz', 'nep.txt', 'run.in')},
              'records': records, 'status': 'RUNNING'}
    def save():
        (root / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    def launch(name, rank, backend, mode, model, run):
        try:
            return execute(root / name, candidate, mpiexec, rank, backend, mode, devices,
                           a.timeout, model, fixture / 'nep.txt', run, records)
        finally:
            save()
    def compare_stage(reference, stage):
        try:
            records[-1]['comparison'] = compare(reference, stage, tolerances)
        except Exception as error:
            records[-1].update(status='FAIL', error=str(error))
            raise
        finally:
            save()
    try:
        for rank in ranks:
            for backend in backends:
                for ensemble in ('npt_ber', 'npt_scr'):
                    for scenario in ('single', 'segments', 'late-unwrapped', 'fixed-box'):
                        if scenario == 'fixed-box' and ensemble != 'npt_scr':
                            continue
                        run = run_text(ensemble, scenario)
                        prefix = f'p{rank}-{backend}-{ensemble}-{scenario}'
                        ref = launch(prefix + '-off', rank, backend, 'off', fixture / 'model.xyz', run)
                        for mode in ('rebuild', 'affine'):
                            stage = launch(prefix + '-' + mode, rank, backend, mode, fixture / 'model.xyz', run)
                            compare_stage(ref, stage)
                            if ensemble == 'npt_scr':
                                repeat = launch(prefix + '-' + mode + '-repeat', rank, backend, mode, fixture / 'model.xyz', run)
                                for name in ('thermo.out', 'trajectory.xyz', 'restart.xyz'):
                                    if sha(stage / name) != sha(repeat / name):
                                        records[-1].update(status='FAIL', error=f'same seed repeat differs: {name}')
                                        raise AssertionError(f'same seed repeat differs: {name}')
                # New jobs consume the same restart snapshot with reset controller state.
                # Pair each destination against its own off reference; no trajectory continuity claim.
                for source_rank in (1, 2, 4):
                    source_mode = 'off' if source_rank == 1 else 'rebuild'
                    source = launch(f'restart-{backend}-from{source_rank}-to{rank}-source', source_rank,
                                    backend, source_mode, fixture / 'model.xyz', run_text('npt_scr', 'single'))
                    run = run_text('npt_scr', 'single')
                    ref = launch(f'restart-{backend}-from{source_rank}-to{rank}-off', rank, backend,
                                 'off', source / 'restart.xyz', run)
                    for mode in ('rebuild', 'affine'):
                        stage = launch(f'restart-{backend}-from{source_rank}-to{rank}-{mode}', rank, backend,
                                       mode, source / 'restart.xyz', run)
                        compare_stage(ref, stage)
        # Also restore local-domain checkpoints into fresh P1 jobs. P1 has no
        # local marker, so compare its rebuilt and off configurations directly.
        for backend in backends:
            for source_rank in (2, 4):
                source = launch(f'restart-{backend}-from{source_rank}-to1-source', source_rank,
                                backend, 'rebuild', fixture / 'model.xyz', run_text('npt_scr', 'single'))
                run = run_text('npt_scr', 'single')
                ref = launch(f'restart-{backend}-from{source_rank}-to1-off', 1, backend,
                             'off', source / 'restart.xyz', run)
                for mode in ('rebuild', 'affine'):
                    stage = launch(f'restart-{backend}-from{source_rank}-to1-{mode}', 1, backend,
                                   mode, source / 'restart.xyz', run)
                    compare_stage(ref, stage)
        result['status'] = 'PASS'
    except Exception as error:
        result.update(status='FAIL', error=str(error))
        raise
    finally:
        save()
    return 0


if __name__ == '__main__':
    sys.exit(main())
