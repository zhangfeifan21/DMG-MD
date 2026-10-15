#!/usr/bin/env python3
"""O1 diagnostic runner for the existing M2a crossings/rebuild fixture."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import sys

import run_benchmark as bench
import run_mpi_domain as domain


ROOT = bench.ROOT
HERE = bench.HERE
POTENTIAL = ROOT / 'tests/baseline/inputs/potentials/nep_C.txt'


def validate_trace(text, backend, expected_lengths=(4, 4)):
    mode = bench.actual_dmgmd_profiling(text, 'trace')
    path = [bench.fields(line) for line in text.splitlines()
            if line.startswith('DMGMD_DOMAIN mode=')]
    runs = [bench.fields(line) for line in text.splitlines()
            if line.startswith('DMGMD_TIMING phase=run ')]
    steps = [bench.fields(line) for line in text.splitlines()
             if line.startswith('DMGMD_DOMAIN_TRACE rank=')]
    if len(path) != 1 or path[0].get('mode') != 'm2a':
        raise ValueError('crossings fixture did not enter M2a')
    if (len(runs) != 2 or any(row.get('backend') != backend for row in runs) or
            [int(row['steps']) for row in runs] != list(expected_lengths)):
        raise ValueError('crossings fixture backend or segment timer differs')
    classes = {kind: sum(row.get('class') == kind for row in steps)
               for kind in ('ordinary', 'rebuild')}
    if classes['rebuild'] == 0 or len(steps) != 2 * sum(expected_lengths):
        raise ValueError('fixture must expose real rebuild steps on both ranks')
    return dict(mode=mode, classes=classes,
                run_seconds_max=[float(row['seconds_max']) for row in runs])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=ROOT / 'build/dmg-md')
    parser.add_argument('--devices', default='0,1', help='two physical nvidia-smi indices or UUIDs')
    parser.add_argument('--backends', default='HostStaged,CudaAware')
    parser.add_argument('--fixture', choices=('crossings', 'carbon-drift'), default='crossings',
                        help='small M2a oracle fixture or carbon_200k with uniform +2 Å/fs y drift')
    parser.add_argument('--warmup', type=int, default=10, help='carbon-drift warmup steps')
    parser.add_argument('--steps', type=int, default=100, help='carbon-drift measured steps')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--mpiexec', default='mpiexec')
    parser.add_argument('--mpiexec-arg', action='append', default=[])
    parser.add_argument('--expected-cores-per-rank', type=int, required=True)
    parser.add_argument('--expected-numa-node', type=int, required=True)
    parser.add_argument('--cpu-sample-interval', type=float, default=.001)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--output', type=Path, default=ROOT / 'dmgmd-overlap-rebuild')
    parser.add_argument('--dry-run', action='store_true', help='print resolved trials without GPU access or writes')
    args = parser.parse_args()
    backends = args.backends.split(',')
    devices = args.devices.split(',')
    if (set(backends) != {'HostStaged', 'CudaAware'} or len(backends) != 2 or
            len(devices) != 2 or len(set(devices)) != 2 or args.repeats <= 0 or
            args.expected_cores_per_rank <= 0 or args.expected_numa_node < 0 or
            args.cpu_sample_interval <= 0 or args.timeout <= 0 or
            args.warmup <= 0 or args.steps <= 0):
        parser.error('need two unique backends/devices, positive repeats/cores/interval/timeout and NUMA >= 0')
    manifest = json.loads((HERE / 'manifest.json').read_text())
    expected_lengths = (4, 4) if args.fixture == 'crossings' else (args.warmup, args.steps)
    order = []
    rng = random.Random(manifest['order_seed'])
    for repeat in range(args.repeats):
        batch = [dict(backend=backend, repeat=repeat) for backend in backends]
        rng.shuffle(batch)
        order.extend(batch)
    for index, trial in enumerate(order):
        trial['id'] = (f'{index:04d}-{args.fixture}-{trial["backend"]}'
                       f'-p2-r{trial["repeat"]}')
    diagnostics = dict(DMGMD_DOMAIN_TIMING='1', DMGMD_DOMAIN_TRACE='1',
                       DMGMD_DOMAIN_DIAGNOSTICS='0', DMGMD_COMM_LOG_INTERVAL='1')
    if args.dry_run:
        print(json.dumps(dict(trials=order, fixture=args.fixture,
                              segment_lengths=expected_lengths, requested_devices=devices,
                              diagnostic_environment=diagnostics,
                              mpiexec_args=args.mpiexec_arg,
                              expected_cores_per_rank=args.expected_cores_per_rank,
                              expected_numa_node=args.expected_numa_node), indent=2))
        return 0
    candidate = args.candidate.resolve()
    output = bench.create_output_directory(args.output)
    snap = bench.snapshot()
    selected = bench.resolve_devices(devices, snap)
    bench.save(output / 'initial-gpus.json', snap)
    with (output / 'preflight.txt').open('w') as stream:
        import contextlib
        with contextlib.redirect_stdout(stream):
            bench.validate_environment(candidate, args.mpiexec, selected, 2,
                                       min(300, int(args.timeout)), args.mpiexec_arg)
    metadata = dict(schema_version=1, fixture=(
                        'tests/mpi/run_mpi_domain.py:CROSSINGS_MODEL/CROSSINGS_RUN'
                        if args.fixture == 'crossings' else
                        'tests/benchmark/manifest.json:carbon_200k, uniform +2 Angstrom/fs y drift'),
                    segment_lengths=expected_lengths,
                    order_seed=manifest['order_seed'], trials=order, selected_devices=selected,
                    candidate=str(candidate), candidate_sha256=bench.sha(candidate),
                    sources={str(path.relative_to(ROOT)): bench.sha(path) for path in
                             (Path(__file__), HERE / 'run_benchmark.py',
                              ROOT / 'tests/mpi/run_mpi_domain.py', POTENTIAL,
                              HERE / 'manifest.json',
                              ROOT / 'tests/long_nve/long_nve_common.py')},
                    mpiexec_args=args.mpiexec_arg, expected_cores_per_rank=args.expected_cores_per_rank,
                    expected_numa_node=args.expected_numa_node,
                    dmgmd_revision=bench.command_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).strip(),
                    dmgmd_status=bench.command_output(['git', '-C', str(ROOT), 'status', '--porcelain']))
    (output / 'source-dirty.patch').write_text(
        bench.command_output(['git', '-C', str(ROOT), 'diff', 'HEAD', '--binary']))
    metadata['dirty_patch_sha256'] = bench.sha(output / 'source-dirty.patch')
    bench.save(output / 'metadata.json', metadata)
    results = []
    for trial in order:
        directory = output / trial['id']
        directory.mkdir()
        if args.fixture == 'crossings':
            (directory / 'model.xyz').write_text(domain.CROSSINGS_MODEL)
            (directory / 'run.in').write_text(domain.CROSSINGS_RUN)
            shutil.copyfile(POTENTIAL, directory / 'nep.txt')
        else:
            case, _ = bench.geometry(manifest['cases']['carbon_200k'], 2)
            lines = bench.fixtures.generate_model(case, manifest['seed']).splitlines()
            for index in range(2, len(lines)):
                atom = lines[index].split()
                atom[6] = f'{float(atom[6]) + 2.0:.12e}'
                lines[index] = ' '.join(atom)
            (directory / 'model.xyz').write_text('\n'.join(lines) + '\n')
            (directory / 'run.in').write_text(
                bench.run_input('dmgmd', 2, case, args.warmup, args.steps))
            (directory / 'nep.txt').write_text(bench.fixtures.generate_potential(case))
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith('DMGMD_')}
        environment.update(CUDA_DEVICE_ORDER='PCI_BUS_ID',
                           CUDA_VISIBLE_DEVICES=','.join(selected), OMP_NUM_THREADS='1',
                           DMGMD_COMM_BACKEND=trial['backend'], **diagnostics)
        cpu = bench.CpuSession(directory / 'cpu', trial['id'], 2, candidate,
                               args.cpu_sample_interval, 'none', False,
                               metadata=dict(trial_directory=str(directory),
                                             benchmark_metadata=str(output / 'metadata.json'),
                                             visible_devices=selected, job=trial))
        command = [args.mpiexec, *args.mpiexec_arg, '-n', '2', *cpu.wrapper([str(candidate)])]
        result = dict(trial, status='failed', command=command,
                      launch_environment={key: environment[key] for key in environment
                                          if key.startswith(('CUDA_', 'DMGMD_', 'OMP_'))},
                      input_sha256={name: bench.sha(directory / name)
                                    for name in ('model.xyz', 'run.in', 'nep.txt')})
        try:
            cpu.start()
            code, wall, timeout = bench.execute(command, directory, environment,
                                                args.timeout, 1., cpu)
            result.update(returncode=code, process_wall_seconds=wall)
            if timeout or code:
                raise RuntimeError(f'crossings trial exit={code}, timeout={timeout}')
            stdout = (directory / 'stdout.txt').read_text()
            result['actual_trace'] = validate_trace(
                stdout, trial['backend'], expected_lengths)
            markers = bench.write_trace_markers(stdout, cpu.directory)
            result['cpu_markers'] = str(markers)
            result['status'] = 'ok'
        except (OSError, ValueError, RuntimeError) as error:
            result['error'] = str(error)
        finally:
            cpu.stop(result.get('error', result['status']))
            try:
                summary = bench.analyze_cpu(cpu.directory,
                                            markers_path=result.get('cpu_markers'))
                result['cpu_status'] = ('ok' if summary['collector_complete'] and
                                        all(row['status'] == 'verified' for row in summary['mappings'])
                                        else 'partial')
                result['cpu_binding'] = bench.verify_cpu_binding(
                    cpu.directory, args.expected_cores_per_rank, args.expected_numa_node)
            except (OSError, ValueError, KeyError) as error:
                result.update(status='invalid_cpu_binding', cpu_status='partial', cpu_error=str(error))
            results.append(result)
            bench.save(directory / 'result.json', result)
            bench.save(output / 'results.json', results)
            print(f'OVERLAP_REBUILD {trial["id"]} status={result["status"]}', flush=True)
    print(f'Output: {output}')
    return 0 if all(row['status'] == 'ok' and row['cpu_status'] == 'ok' for row in results) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f'OVERLAP_REBUILD ERROR: {error}', file=sys.stderr)
        sys.exit(1)
