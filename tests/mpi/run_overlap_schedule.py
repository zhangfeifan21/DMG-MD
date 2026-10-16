#!/usr/bin/env python3
"""O4 test-only delayed transport, bounded progress and single-rank error exit.

Run after the environment/golden/CUDA/lifecycle gates. Saves exact launch and
raw output. A controlled delayed-post witness proves pending-request state;
it is not evidence of NIC activity or normal-path performance.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import run_mpi_domain as domain
import check_environment


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, default=domain.PROJECT_ROOT / 'build/dmg-md')
    p.add_argument('--test-executable', type=Path, default=domain.PROJECT_ROOT / 'build/tests/dmgmd_overlap_schedule_tests')
    p.add_argument('--devices', default='0,1,2,3')
    p.add_argument('--work-root', type=Path, required=True)
    args = p.parse_args()
    devices = args.devices.split(',')
    launcher = check_environment.validate_environment(args.candidate.resolve(), 'mpiexec', devices, 4, 60)
    root = args.work_root.resolve(); root.mkdir(parents=True, exist_ok=False)
    atoms = [f'C {x} {y} 12 0 0 0' for y, xs in ((12, (17,23,29,35)), (2, (49,55,61,3))) for x in xs]
    model = '8\n' + domain.CHAIN_MODEL.splitlines()[1] + '\n' + '\n'.join(atoms) + '\n'
    case = domain.DomainCase('aux', model, domain.VARIANT_RUN, ['static.xyz'], ['thermo.out','static.xyz','neighbor.out'], 8)
    potential = domain.BASELINE_DIR / 'inputs/potentials/nep_C.txt'
    records = []
    def run(name, ranks, backend, mode, binary, extra=None, expected=None, mixed=None):
        work = root / name; domain.stage_case(case, work, potential)
        env = {k:v for k,v in os.environ.items() if not k.startswith(('DMGMD_', 'O4_TEST_'))}
        env.update(CUDA_DEVICE_ORDER='PCI_BUS_ID', CUDA_VISIBLE_DEVICES=','.join(devices[:ranks]),
                   DMGMD_COMM_BACKEND=backend, DMGMD_DOMAIN_PARTITION='serial',
                   DMGMD_DOMAIN_OVERLAP=mode, DMGMD_OVERLAP_TRACE='1' if mode != 'off' else '0', DMGMD_DOMAIN_DIAGNOSTICS='1')
        env.update(extra or {})
        command = [str(launcher), '-n', str(ranks), str(binary.resolve())]
        if mixed:
            command = [str(launcher), '-n', str(ranks), sys.executable, str(Path(__file__).with_name('overlap_config_rank.py')), str(binary.resolve()), *mixed]
        record = dict(name=name, command=command, launch_environment={k:v for k,v in env.items() if k.startswith(('DMGMD_', 'O4_TEST_', 'CUDA_', 'OMPI_', 'UCX_', 'PMIX_'))})
        (work/'launch.json').write_text(json.dumps(record,indent=2)+'\n')
        result = subprocess.run(command, cwd=work, env=env, text=True, capture_output=True, timeout=60)
        (work/'stdout.txt').write_text(result.stdout); (work/'stderr.txt').write_text(result.stderr)
        record['returncode'] = result.returncode
        (work/'result.json').write_text(json.dumps(record,indent=2)+'\n')
        if expected:
            assert result.returncode != 0 and expected in result.stdout+result.stderr, name
        else:
            assert result.returncode == 0, name + result.stderr[-2000:]
            assert f'DMGMD_OVERLAP_CONFIG mode={mode}' in result.stdout, name
            if binary == args.test_executable:
                witnesses = [domain.key_values(line) for line in result.stdout.splitlines() if line.startswith('O4_DELAY_WITNESS ')]
                assert witnesses and all(float(r['early_cuda_ms']) > 0 and r['receive_pending'] == '1' for r in witnesses), name
                assert f'backend={backend}' in result.stdout, name
                if mode == 'poll':
                    rows = [domain.key_values(line) for line in result.stdout.splitlines() if line.startswith('DMGMD_OVERLAP_SUMMARY ')]
                    assert any(int(r['cumulative_polls']) >= 8 for r in rows), name
        record.update(status='PASS', returncode=result.returncode)
        records.append(record); (root/'results.json').write_text(json.dumps(records,indent=2)+'\n')
        print('PASS',name,flush=True)
        return work
    for ranks in (2,4):
        for backend in ('HostStaged','CudaAware'):
            oracle = run(f'{backend}-p{ranks}-oracle',ranks,backend,'off',args.candidate)
            for mode in ('wait','poll'):
                work = run(f'{backend}-p{ranks}-{mode}',ranks,backend,mode,args.test_executable)
                for output in case.outputs:
                    assert (oracle/output).read_bytes() == (work/output).read_bytes(), (work,output)
                run(f'{backend}-p{ranks}-{mode}-fault',ranks,backend,mode,args.test_executable,
                    {'O4_TEST_FAIL':'1'}, 'finish halo MPI_Waitall')
    for name,extra,expected,mixed in (
        ('illegal-mode',{'DMGMD_DOMAIN_OVERLAP':'bad'},'must be off, wait or poll',None),
        ('incompatible',{'DMGMD_DOMAIN_PARTITION':'off'},'requires DMGMD_DOMAIN_PARTITION',None),
        ('illegal-trace',{'DMGMD_OVERLAP_TRACE':'bad'},'must be 0 or 1',None),
        ('rank-mismatch',{},'domain overlap mode',['DMGMD_DOMAIN_OVERLAP=poll']),
        ('trace-mismatch',{},'overlap trace mode',['DMGMD_OVERLAP_TRACE=0']),
    ):
        run(name,2,'HostStaged','wait',args.candidate,extra,expected,mixed)
    print(f'PASS O4 schedule: {len(records)} jobs',flush=True)


if __name__ == '__main__': main()
