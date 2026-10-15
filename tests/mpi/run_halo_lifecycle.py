#!/usr/bin/env python3
"""O3 GPU lifecycle/error acceptance; run after sourcing ../env/md-mpi.sh.

Uses a separate test executable with linker wrappers, never production fault
switches. All failures are rank-0-only and must terminate the whole job before
the timeout. Retains exact commands, environment and unfiltered output.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import check_environment

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=ROOT / 'build/dmg-md')
    parser.add_argument('--test-executable', type=Path, default=ROOT / 'build/tests/dmgmd_mpi_halo_lifecycle_tests')
    parser.add_argument('--devices', default='0,1,2,3')
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=45)
    args = parser.parse_args()
    devices = args.devices.split(',')
    if len(devices) < 4:
        parser.error('four distinct GPUs are required for P=1/2/4 coverage')
    launcher = check_environment.validate_environment(args.candidate.resolve(), 'mpiexec', devices, 4, args.timeout)
    root = args.work_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    records = []
    good = ['normal', 'progress-only', 'receive-first', 'send-first']
    bad = ['invalid-count', 'partial-post', 'wait-error', 'test-error',
           'unpack-error', 'double-begin', 'host-active', 'stale-token', 'abandon', 'selftest-post']
    expected = {'invalid-count': 'invalid halo components/counts',
                'partial-post': 'post halo right send', 'wait-error': 'finish halo MPI_Waitall',
                'test-error': 'test halo sends', 'unpack-error': 'finish halo unpack',
                'double-begin': 'halo slot is not reusable',
                'host-active': 'host face exchange during active halo',
                'stale-token': 'stale or invalid halo token',
                'abandon': 'destroying runtime with unfinished halo',
                'selftest-post': 'self-test right send',
                'd2h-error': 'wait for halo send source', 'h2d-error': 'stage halo H2D'}
    for backend in ['HostStaged', 'CudaAware']:
        for ranks in [1, 2, 4]:
            modes = good + (bad if ranks > 1 else [])
            if ranks > 1 and backend == 'HostStaged':
                modes += ['d2h-error', 'h2d-error']
            for mode in modes:
                name = f'{backend}-p{ranks}-{mode}'
                work = root / name
                work.mkdir()
                env = {k: v for k, v in os.environ.items() if not k.startswith('DMGMD_')}
                env.update(CUDA_DEVICE_ORDER='PCI_BUS_ID', CUDA_VISIBLE_DEVICES=','.join(devices[:ranks]),
                           DMGMD_COMM_BACKEND=backend)
                if mode == 'selftest-post':
                    env['DMGMD_CUDA_AWARE_PROBE'] = '1'
                command = [str(launcher), '-n', str(ranks), str(args.test_executable.resolve()), mode]
                record = dict(name=name, command=command, launch_environment={k:v for k,v in env.items()
                              if k.startswith(('DMGMD_', 'CUDA_', 'OMPI_', 'UCX_', 'PMIX_'))})
                (work / 'launch.json').write_text(json.dumps(record, indent=2) + '\n')
                try:
                    result = subprocess.run(command, cwd=work, env=env, text=True,
                                            capture_output=True, timeout=args.timeout)
                except subprocess.TimeoutExpired as error:
                    (work / 'stdout.txt').write_bytes(error.stdout or b'')
                    (work / 'stderr.txt').write_bytes(error.stderr or b'')
                    raise RuntimeError(f'{name}: MPI job hung past {args.timeout}s') from error
                (work / 'stdout.txt').write_text(result.stdout)
                (work / 'stderr.txt').write_text(result.stderr)
                combined = result.stdout + result.stderr
                if mode in good:
                    assert result.returncode == 0 and 'HALO_LIFECYCLE PASS' in combined, name
                    assert f'backend={backend}' in result.stdout, f'{name}: unexpected backend fallback'
                    if backend == 'CudaAware' and ranks > 1:
                        assert 'cuda_aware_p2p_self_test=passed' in result.stdout, name
                else:
                    assert result.returncode != 0 and 'category=halo' in combined and expected[mode] in combined, name
                record.update(returncode=result.returncode, status='passed')
                records.append(record)
                (root / 'results.json').write_text(json.dumps(records, indent=2) + '\n')
                print(f'PASS {name}', flush=True)
    print(f'PASS halo lifecycle/error matrix: {len(records)} jobs', flush=True)


if __name__ == '__main__':
    main()
