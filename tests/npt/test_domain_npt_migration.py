#!/usr/bin/env python3
"""CPU tests of the dynamic-box manager replay and frozen input contracts."""
import json
import unittest
from unittest import mock
import run_domain_npt_contracts as contracts
import run_domain_npt_migration as migration


def frame(length, xs):
    return {'fields': {'Lattice': f'{length} 0 0 0 24 0 0 0 24'},
            'properties': [('species', 'S', 1), ('pos', 'R', 3), ('vel', 'R', 3)],
            'rows': [['C', str(x), '1', '1', '0.1', '0', '0'] for x in xs]}


def layout(step, epoch, rank, count):
    return f'DMGMD_DOMAIN_LAYOUT rank={rank} step={step} epoch={epoch} owned={count}'


def migration_log(step, rank, transitions):
    entries = ','.join(f'{gid}:{old}->{new}' for gid, old, new in transitions)
    return f'DMGMD_DOMAIN_MIGRATION rank={rank} step={step} epoch=1 migrated_in={len(transitions)} transitions="{entries}" truncated=false'


class MigrationOracleTests(unittest.TestCase):
    def test_owner_uses_current_box(self):
        self.assertEqual(migration.owner(40.1, 80, 2), 1)
        self.assertEqual(migration.owner(40.1, 81, 2), 0)
        self.assertEqual(migration.owner(-0.1, 80, 4), 3)

    def test_micro_crossing_keeps_manager_and_epoch(self):
        initial = frame(80, [39.9, 10])
        frames = [frame(80 + step * .01, [40.1, 10]) for step in range(1, 5)]
        log = '\n'.join([layout(0, 1, 0, 2), layout(0, 1, 1, 0)])
        result = migration.verify_replay(initial, frames, log, 2, 'micro', 'affine')
        self.assertGreater(result['geometric_crossings'], 0)
        self.assertEqual(result['transitions'], [])

    def test_rebuild_requires_precise_gid_transition_and_counts(self):
        initial = frame(80, [39.9, 10])
        frames = [frame(80 + step * .01, [40.1, 10]) for step in range(1, 5)]
        initial_log = [layout(0, 1, 0, 2), layout(0, 1, 1, 0)]
        events = [migration_log(1, 0, []), migration_log(1, 1, [(0, 0, 1)]),
                  layout(1, 2, 0, 1), layout(1, 2, 1, 1)]
        self.assertEqual(migration.verify_replay(initial, frames, '\n'.join(initial_log + events),
                                                2, 'synthetic', 'rebuild')['transitions'], [(1, 0, 0, 1)])
        with self.assertRaisesRegex(ValueError, 'transitions differ'):
            migration.verify_replay(initial, frames, '\n'.join(initial_log + [migration_log(1,0,[]),
                migration_log(1,1,[]), layout(1,2,0,1), layout(1,2,1,1)]), 2, 'synthetic', 'rebuild')
        with self.assertRaisesRegex(ValueError, 'owned counts'):
            migration.verify_replay(initial, frames, '\n'.join(initial_log + events[:-2] +
                [layout(1,2,0,2), layout(1,2,1,0)]), 2, 'synthetic', 'rebuild')

    def test_periodic_multislab_repeated_transitions(self):
        initial = frame(80, [75, 70])
        initial['rows'][0][4] = '20'
        initial['rows'][1][4] = '-45'
        normalized = [[15,25], [35,60], [55,15], [75,50]]
        frames = [frame(80 + step * .01, [x * (80 + step * .01) / 80 for x in positions])
                  for step, positions in enumerate(normalized, 1)]
        logs = [layout(0,1,r,2 if r == 3 else 0) for r in range(4)]
        manager = [3,3]
        for step, item in enumerate(frames, 1):
            _, _, geom = migration.frame_state(item, 4)
            changes = [(gid, old, new) for gid,(old,new) in enumerate(zip(manager,geom)) if old != new]
            logs += [migration_log(step, r, [x for x in changes if x[2] == r]) for r in range(4)]
            logs += [layout(step,step+1,r,geom.count(r)) for r in range(4)]
            manager = geom
        result = migration.verify_replay(initial, frames, '\n'.join(logs), 4, 'crossings', 'rebuild')
        self.assertTrue(result['non_neighbor_migration'])
        self.assertEqual(result['periodic_crossings'], [(1, 0)])
        self.assertGreaterEqual(sum(t[1] == 0 for t in result['transitions']), 2)

    def test_quiet_rebuild_without_migration(self):
        initial = frame(80, [10,50])
        frames = [frame(80 + step * .01, [10,50]) for step in range(1,5)]
        logs = [layout(0,1,0,1), layout(0,1,1,1), layout(2,2,0,1), layout(2,2,1,1)]
        result = migration.verify_replay(initial, frames, '\n'.join(logs), 2, 'quiet', 'rebuild')
        self.assertEqual(result['no_migration_rebuilds'], 1)

    def test_snapshot_witness_checks_gid_membership_and_root(self):
        logs = [
            'DMGMD_TEST_OWNED snapshot=1 rank=0 root=0 count=1 gids="1"',
            'DMGMD_TEST_OWNED snapshot=1 rank=1 root=0 count=2 gids="0,2"',
        ]
        self.assertEqual(migration.verify_owned_snapshots('\n'.join(logs), 2, [[1, 0, 1]]),
                         [[[1], [0, 2]]])
        with self.assertRaisesRegex(ValueError, 'owned GIDs'):
            duplicate_gid = logs[1].replace('0,2', '0,1')
            migration.verify_owned_snapshots('\n'.join([logs[0], duplicate_gid]), 2, [[1, 0, 1]])
        with self.assertRaisesRegex(ValueError, 'owned GIDs'):
            swapped = [logs[0].replace('gids="1"', 'gids="0"'),
                       logs[1].replace('gids="0,2"', 'gids="1,2"')]
            migration.verify_owned_snapshots('\n'.join(swapped), 2, [[1, 0, 1]])
        with self.assertRaisesRegex(ValueError, 'owned GIDs'):
            migration.verify_owned_snapshots(logs[0], 2, [[1, 0, 1]])
        with self.assertRaisesRegex(ValueError, 'invalid owned GID witness'):
            migration.verify_owned_snapshots('\n'.join(logs).replace('root=0', 'root=1'), 2, [[1, 0, 1]])
        with self.assertRaisesRegex(ValueError, 'invalid owned GID witness'):
            migration.verify_owned_snapshots('\n'.join(logs + logs[:1]), 2, [[1, 0, 1]])

    def test_binding_checks_expected_physical_gpu_and_backend(self):
        uuids = ['GPU-00000000-0000-0000-0000-000000000001', 'GPU-00000000-0000-0000-0000-000000000002']
        logs = [
            f'DMGMD_MPI rank={rank} world_size=2 local_rank={rank} cuda_device={rank} '
            f'cuda_uuid={uuid[4:].replace("-", "")} cuda_name="NVIDIA L20" backend=CudaAware '
            'cuda_aware_self_test=passed cuda_aware_p2p_self_test=passed'
            for rank, uuid in enumerate(uuids)
        ]
        records = contracts.verify_binding('\n'.join(reversed(logs)), uuids, 'CudaAware')
        self.assertEqual([record['rank'] for record in records], ['0', '1'])
        with self.assertRaisesRegex(ValueError, 'wrong GPU binding'):
            contracts.verify_binding('\n'.join(logs), list(reversed(uuids)), 'CudaAware')
        with self.assertRaisesRegex(ValueError, 'wrong communication backend'):
            contracts.verify_binding('\n'.join(logs).replace('backend=CudaAware', 'backend=HostStaged'),
                                     uuids, 'CudaAware')
        swapped_local = [line.replace(f'local_rank={rank}', f'local_rank={1 - rank}')
                         for rank, line in enumerate(logs)]
        with self.assertRaisesRegex(ValueError, 'wrong GPU binding'):
            contracts.verify_binding('\n'.join(swapped_local), uuids, 'CudaAware')
        with self.assertRaisesRegex(ValueError, 'duplicate or unexpected rank'):
            contracts.verify_binding('\n'.join(logs + logs[:1]), uuids, 'CudaAware')
        with self.assertRaisesRegex(ValueError, 'missing MPI rank'):
            contracts.verify_binding(logs[0], uuids, 'CudaAware')
        with self.assertRaisesRegex(ValueError, 'missing CudaAware self-test'):
            contracts.verify_binding('\n'.join(logs).replace('cuda_aware_p2p_self_test=passed',
                                                               'cuda_aware_p2p_self_test=failed'),
                                     uuids, 'CudaAware')

    def test_device_resolution_preserves_requested_order_and_rejects_aliases(self):
        first = 'GPU-00000000-0000-0000-0000-000000000001'
        second = 'GPU-00000000-0000-0000-0000-000000000002'
        inventory = f'0, {first}\n2, {second}\n'
        with mock.patch.object(contracts.subprocess, 'run', return_value=mock.Mock(stdout=inventory)):
            self.assertEqual(contracts.physical_gpu_uuids([second, '0']), [second, first])
            with self.assertRaisesRegex(ValueError, 'same physical GPU'):
                contracts.physical_gpu_uuids(['0', first])

    def test_frozen_parameters_and_force_noop_thermostat(self):
        spec = json.loads(migration.CASES.read_text())
        self.assertEqual(spec['box_A'], [80, 24, 24])
        self.assertEqual(spec['steps'], 4)
        self.assertLess(spec['dt_fs'] * max(row[3] for row in spec['cases']['micro']['atoms']) * spec['steps'], .5)
        for name in ('npt_ber', 'npt_scr'):
            run = migration.run_text(spec, name)
            self.assertIn('1e+30', run)
            self.assertIn('dump_xyz 1 trajectory.xyz precision double velocity force potential virial', run)


if __name__ == '__main__': unittest.main()
