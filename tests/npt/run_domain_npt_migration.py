#!/usr/bin/env python3
"""Validate NPT ownership at each output and compare with the same-input P1 run."""
import argparse
import itertools
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_domain_npt_contracts as contracts
import check_environment

CASES = HERE / 'domain-npt-migration-cases.json'
PROBE_SOURCE = HERE / 'domain_npt_migration_pmpi.cpp'


def model_text(spec, case):
    box = spec['box_A']
    lines = [str(len(case['atoms'])),
             f'pbc="T T T" Lattice="{box[0]} 0 0 0 {box[1]} 0 0 0 {box[2]}" Properties=species:S:1:pos:R:3:vel:R:3']
    lines.extend('C ' + ' '.join(format(v, '.17g') for v in atom) for atom in case['atoms'])
    return '\n'.join(lines) + '\n'


def run_text(spec, ensemble):
    return (f"potential nep.txt\ntime_step {spec['dt_fs']}\n"
            f"ensemble {ensemble} {spec['temperature_K']} {spec['temperature_K']} "
            f"{spec['temperature_damping_steps']} {spec['target_pressure_GPa']} "
            f"{spec['bulk_modulus_GPa']} {spec['pressure_damping_steps']}\n"
            'dump_thermo 1\ndump_xyz 1 trajectory.xyz precision double velocity force potential virial\n'
            f"dump_restart 1\nrun {spec['steps']}\n")


def owner(x, length, ranks):
    if not math.isfinite(x) or not math.isfinite(length) or length <= 0:
        raise ValueError('non-finite or non-positive box/position')
    fractional = (x / length) % 1.0
    return min(int(fractional * ranks), ranks - 1)


def frame_state(frame, ranks):
    h = [float(x) for x in frame['fields']['Lattice'].split()]
    if len(h) != 9 or any(h[i] != 0 for i in (1, 2, 3, 5, 6, 7)):
        raise ValueError('expected diagonal orthogonal box')
    pos = next((sum(w for _, _, w in frame['properties'][:i])
                for i, (name, _, _) in enumerate(frame['properties']) if name == 'pos'), None)
    if pos is None: raise ValueError('position property missing')
    xyz = [float(row[pos]) for row in frame['rows']]
    return h[0], xyz, [owner(x, h[0], ranks) for x in xyz]


def parse_events(log, ranks):
    layouts, migrations = {}, {}
    for line in log.splitlines():
        if not line.startswith(('DMGMD_DOMAIN_LAYOUT ', 'DMGMD_DOMAIN_MIGRATION ')): continue
        fields = dict(re.findall(r'(\w+)=("[^"]*"|[^\s]+)', line))
        rank, step = int(fields['rank']), int(fields['step'])
        if rank not in range(ranks): raise ValueError('unexpected event rank')
        dest = layouts if line.startswith('DMGMD_DOMAIN_LAYOUT ') else migrations
        key = (step, rank)
        if key in dest: raise ValueError('duplicate event')
        dest[key] = fields
    if {(0, r) for r in range(ranks)} - set(layouts): raise ValueError('missing initial layout')
    return layouts, migrations


def verify_replay(initial, frames, log, ranks, name, mode):
    layouts, migrations = parse_events(log, ranks)
    if not frames: raise ValueError('missing trajectory frames')
    length0, x0, manager = frame_state(initial, ranks)
    states = [frame_state(frame, ranks) for frame in frames]
    if not any(length != length0 for length, _, _ in states): raise ValueError('no actual box change')
    for rank in range(ranks):
        if int(layouts[(0, rank)]['owned']) != manager.count(rank): raise ValueError('initial owned count mismatch')
    previous_owners = manager.copy()
    epochs = [int(layouts[(0, r)]['epoch']) for r in range(ranks)]
    if len(set(epochs)) != 1: raise ValueError('initial epoch mismatch')
    changes, managers = [], []
    no_migration_rebuilds, geometric_crossings, multi_slab = 0, 0, False
    periodic_crossings = set()
    previous_x, previous_length = x0, length0
    for step, (length, xs, geometric) in enumerate(states, 1):
        event_ranks = [r for r in range(ranks) if (step, r) in layouts]
        if event_ranks and len(event_ranks) != ranks: raise ValueError('incomplete collective layout')
        transitions = {}
        event_migrants = [r for r in range(ranks) if (step, r) in migrations]
        if event_migrants and len(event_migrants) != ranks: raise ValueError('incomplete migration records')
        for rank in event_migrants:
            item = migrations[(step, rank)]
            if item.get('truncated') != 'false': raise ValueError('truncated migration witness')
            entries = item['transitions'].strip('"').split(',') if item['transitions'].strip('"') else []
            if len(entries) != int(item['migrated_in']): raise ValueError('migration count mismatch')
            for entry in entries:
                match = re.fullmatch(r'(\d+):(\d+)->(\d+)', entry)
                if not match: raise ValueError('invalid migration entry')
                gid, old, new = map(int, match.groups())
                if gid in transitions or gid not in range(len(manager)) or new != rank: raise ValueError('duplicate or invalid migrated gid')
                transitions[gid] = (old, new)
        geometric_crossings += sum(old != new for old, new in zip(previous_owners, geometric))
        if event_ranks:
            next_epochs = [int(layouts[(step, r)]['epoch']) for r in range(ranks)]
            if len(set(next_epochs)) != 1 or next_epochs[0] != epochs[0] + 1:
                raise ValueError('unexpected layout epoch')
            expected = {i: (old, new) for i, (old, new) in enumerate(zip(manager, geometric)) if old != new}
            if transitions != expected: raise ValueError(f'step {step}: owner transitions differ: {transitions} vs {expected}')
            if not transitions: no_migration_rebuilds += 1
            for gid, (old, new) in transitions.items():
                if abs(old-new) > 1 and {old,new} != {0,ranks-1}: multi_slab = True
                manager[gid] = new
                changes.append((step, gid, old, new))
            epochs = next_epochs
            if manager != geometric: raise ValueError('rebuild manager does not match geometry')
            for rank in range(ranks):
                if int(layouts[(step, rank)]['owned']) != manager.count(rank):
                    raise ValueError('layout owned counts disagree with replayed unique gids')
        elif transitions:
            raise ValueError('migration without layout epoch')
        # A wrapped coordinate reversing relative to its known velocity means crossing x=0.
        velocities = initial['rows']
        for gid, (prev_x, x) in enumerate(zip(previous_x, xs)):
            v = float(velocities[gid][4])
            raw = x * previous_length / length - prev_x
            # Require a jump > half the box, excluding force-driven tiny sign reversals.
            if abs(raw) > 0.5 * previous_length and ((v > 0 and raw < 0) or (v < 0 and raw > 0)):
                periodic_crossings.add((step, gid))
        managers.append(manager.copy())
        previous_owners, previous_x, previous_length = geometric, xs, length
    if any(step > len(frames) for step, _ in layouts | migrations): raise ValueError('event after final frame')
    if name == 'micro' and mode == 'affine':
        if changes or len(layouts) != ranks or not geometric_crossings:
            raise AssertionError('affine micro-crossing failed to preserve manager and epoch')
    if name == 'quiet' and mode == 'rebuild' and not no_migration_rebuilds:
        raise AssertionError('expected at least one rebuild without migration')
    if name == 'crossings':
        if not periodic_crossings: raise AssertionError('missing periodic coordinate crossing')
        if not any((step, gid) in periodic_crossings and {old, new} == {0, ranks - 1}
                   for step, gid, old, new in changes):
            raise AssertionError('missing periodic-end owner migration')
        if not any(sum(change[1] == gid for change in changes) >= 2 for gid in range(len(manager))):
            raise AssertionError('missing repeated migration of a gid')
        if ranks == 4 and not multi_slab: raise AssertionError('missing non-neighbor multi-slab migration')
    return {'geometric_crossings': geometric_crossings, 'transitions': changes,
            'expected_managers': managers,
            'no_migration_rebuilds': no_migration_rebuilds, 'periodic_crossings': sorted(periodic_crossings),
            'non_neighbor_migration': multi_slab, 'initial_length_A': length0,
            'final_length_A': states[-1][0]}


def verify_owned_snapshots(log, ranks, managers):
    observed = {}
    for line in log.splitlines():
        if not line.startswith('DMGMD_TEST_OWNED '):
            continue
        fields = re.fullmatch(r'DMGMD_TEST_OWNED snapshot=(\d+) rank=(\d+) root=(\d+) '
                              r'count=(\d+) gids="([\d,]*)"', line)
        if fields is None:
            raise ValueError(f'malformed owned GID witness: {line}')
        step, rank, root, count = map(int, fields.groups()[:4])
        gids = [int(gid) for gid in fields[5].split(',')] if fields[5] else []
        if ((step, rank) in observed or step not in range(1, len(managers) + 1) or
                rank not in range(ranks) or root != 0 or len(gids) != count):
            raise ValueError(f'invalid owned GID witness: {line}')
        observed[step, rank] = gids
    snapshots = []
    for step, manager in enumerate(managers, 1):
        by_rank = []
        for rank in range(ranks):
            expected = [gid for gid, owner_rank in enumerate(manager) if owner_rank == rank]
            actual = observed.get((step, rank))
            if actual != expected:
                raise ValueError(f'step {step} rank {rank}: owned GIDs {actual}, expected {expected}')
            by_rank.append(actual)
        snapshots.append(by_rank)
    return snapshots


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', required=True, type=Path)
    p.add_argument('--work-root', required=True, type=Path)
    p.add_argument('--devices', required=True)
    p.add_argument('--ranks', default='2,4')
    p.add_argument('--backends', default='HostStaged,CudaAware')
    p.add_argument('--timeout', type=int, default=900)
    a = p.parse_args()
    spec = json.loads(CASES.read_text())
    if spec.get('schema') != 'dmgmd.domain-npt.migration-cases.v1' or spec['steps'] != 4:
        raise ValueError('unknown case manifest')
    candidate, root = a.candidate.resolve(), a.work_root.resolve()
    ranks, backends, devices = list(map(int, a.ranks.split(','))), a.backends.split(','), a.devices.split(',')
    if not ranks or any(x not in (2, 4) for x in ranks) or len(devices) < max(ranks) or len(set(devices)) != len(devices):
        p.error('invalid ranks or insufficient unique devices')
    if not backends or any(x not in ('HostStaged', 'CudaAware') for x in backends): p.error('invalid backend')
    gpu_uuids = contracts.physical_gpu_uuids(devices[:max(ranks)])
    mpiexec = check_environment.validate_environment(candidate, 'mpiexec', gpu_uuids, max(ranks), a.timeout)
    root.mkdir(parents=True, exist_ok=False)
    probe = root / 'domain_npt_migration_pmpi.so'
    probe_command = ['mpicxx', '-std=c++17', '-shared', '-fPIC', str(PROBE_SOURCE), '-o', str(probe)]
    subprocess.run(probe_command, check=True, env={**os.environ, 'TMPDIR': str(root)})
    # Each MPI job starts one directory below the probe. LD_PRELOAD splits
    # on whitespace and colons, both of which may occur in the work root.
    preload = "../" + probe.name
    if os.environ.get('LD_PRELOAD'):
        preload += ':' + os.environ['LD_PRELOAD']
    fixture_root = root / 'fixtures'; fixture_root.mkdir()
    potential = contracts.ROOT / 'tests/baseline/inputs/potentials/nep_C.txt'
    tolerances = json.loads((contracts.ROOT / 'tests/baseline/manifest.json').read_text())['tolerances']
    fixture_inventory = {}
    for name, case in spec['cases'].items():
        model = fixture_root / f'{name}.xyz'; model.write_text(model_text(spec, case))
        fixture_inventory[name] = contracts.sha(model)
    records = []
    result = {'schema': 'dmgmd.domain-npt.migration-result.v1', 'status': 'RUNNING',
              'candidate_sha256': contracts.sha(candidate), 'case_manifest_sha256': contracts.sha(CASES),
              'runner_sha256': contracts.sha(Path(__file__)), 'potential_sha256': contracts.sha(potential),
              'models_sha256': fixture_inventory, 'records': records,
              'physical_gpu_uuids': dict(zip(devices, gpu_uuids)), 'tolerances': tolerances,
              'owned_probe': {'build_command': probe_command, 'source_sha256': contracts.sha(PROBE_SOURCE),
                              'sha256': contracts.sha(probe)}}
    def save(): (root / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    def launch(stage, rank, backend, mode, model, run):
        extra_env = {'LD_PRELOAD': preload} if mode != 'off' else None
        contracts.execute(stage, candidate, mpiexec, rank, backend, mode, gpu_uuids,
                          a.timeout, model, potential, run, records, extra_env=extra_env)
        log = (stage / 'stdout.log').read_text(errors='replace')
        records[-1]['rank_bindings'] = contracts.verify_binding(log, gpu_uuids[:rank], backend)
        route = next(line for line in log.splitlines() if line.startswith('DMGMD_DOMAIN '))
        fields = dict(item.split('=', 1) for item in route.split()[1:])
        if fields.get('ranks') != str(rank):
            raise ValueError(f'wrong rank count in route: {route}')
        if mode == 'off' and fields.get('mode') != 'm1':
            raise ValueError(f'expected replicated NPT reference: {route}')
        if mode != 'off' and fields.get('axis') != 'x':
            raise ValueError(f'expected the frozen case to partition along x: {route}')
        records[-1]['route'] = fields
        return log

    try:
        for name, ensemble in itertools.product(spec['cases'], ('npt_ber', 'npt_scr')):
            model = fixture_root / f'{name}.xyz'
            run = run_text(spec, ensemble)
            p1 = root / f'{name}-{ensemble}-p1-HostStaged-off'
            launch(p1, 1, 'HostStaged', 'off', model, run)
            save()
            for rank, backend in itertools.product(ranks, backends):
                ref = root / f'{name}-{ensemble}-p{rank}-{backend}-off'
                launch(ref, rank, backend, 'off', model, run)
                initial = contracts.baseline.parse_xyz(ref / 'model.xyz')[0]
                initial_length = frame_state(initial, rank)[0]
                off_frames = contracts.baseline.parse_xyz(ref / 'trajectory.xyz')
                if not any(frame_state(frame, rank)[0] != initial_length for frame in off_frames):
                    raise AssertionError('off reference has no actual box change')
                save()
                for mode in ('rebuild', 'affine'):
                    stage = root / f'{name}-{ensemble}-p{rank}-{backend}-{mode}'
                    log = launch(stage, rank, backend, mode, model, run)
                    records[-1]['comparison'] = contracts.compare(ref, stage, tolerances)
                    records[-1]['p1_reference'] = p1.name
                    records[-1]['p1_comparison'] = contracts.compare(p1, stage, tolerances)
                    frames = contracts.baseline.parse_xyz(stage / 'trajectory.xyz')
                    replay = verify_replay(initial, frames, log, rank, name, mode)
                    managers = replay.pop('expected_managers')
                    records[-1]['manager_replay'] = replay
                    records[-1]['owned_gids_by_frame_and_rank'] = verify_owned_snapshots(log, rank, managers)
                    save()
        result['status'] = 'PASS'
    except Exception as error:
        if records:
            records[-1].update(status='FAIL', error=str(error))
        result.update(status='FAIL', error=str(error))
        raise
    finally:
        save()


if __name__ == '__main__': main()
