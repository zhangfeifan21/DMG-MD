#!/usr/bin/env python3
"""Frozen O6.5 diagnostic binary gates, capture, standalone replay and NCU probe."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import sys

import run_benchmark as b
import run_overlap_o5 as o5
import run_overlap_o65 as base
import o5_rank_output
from cpu_binding import digest
from analyze_overlap_o65 import snapshot_report

execute_local = b.execute
SOURCES = ['CMakeLists.txt', 'tests/CMakeLists.txt', 'src/gpumd_compat/nep.cu',
    'src/gpumd_compat/nep.cuh', 'src/gpumd_compat/nep_o65.inc', 'tests/benchmark/o65_replay.cu',
    'tests/benchmark/run_overlap_o65.py', 'tests/benchmark/run_overlap_o65_replays.py',
    'tests/benchmark/analyze_overlap_o65.py', 'tests/benchmark/test_overlap_o65.py']


def freeze(root):
    if (root/'diagnostic-plan.json').exists():
        checked(root)
        print('FROZEN O65 diagnostic binary/source retained', flush=True)
        return
    for source, target in [('build-o65/dmg-md', 'dmg-md-diagnostic'),
                           ('build-o65/dmgmd-o65-replay', 'dmgmd-o65-replay')]:
        if not (root/target).exists():
            shutil.copy2(b.ROOT/source, root/target)
        if digest(b.ROOT/source) != digest(root/target):
            raise ValueError('diagnostic executable changed; use an independent root')
    o5.freeze(root/'diagnostic-plan.json', dict(stage='O6.5',
        binaries={name:digest(root/name) for name in ('dmg-md-diagnostic','dmgmd-o65-replay')},
        sources={name:digest(b.ROOT/name) for name in SOURCES},
        scope='standalone radial carbon/P2, angular partial water/P8; no production adoption',
        gates='environment P8; four committed goldens; MPI P2/P4 off,verify-wait both backends; long_nve nightly with original manifest; CPU CTest',
        snapshot_format='native little-endian uint64 length followed by typed raw data; Box ABI pinned by binary/toolchain; meta.txt carries O/D/L, generations, step/call, capacities/dimensions',
        compact='ascending original slots only; CPU prototype download/enumerate/allocate/upload cost measured separately; DomainPartitionKey generation/shape/box invalidation checked; no physical compaction',
        candidate_screen='C/D paired 95% lower bound >1 supports an independent whole-step compact experiment; maintenance is reported separately, never subtracted from GPU event time to predict net wall benefit; O8.5 remains required regardless of this screen',
        ncu='one valid probe from new carbon/full ordinary window; permission failure limits metrics only'))
    patch=b.command_output(['git','diff','--binary'])
    for name in SOURCES:
        if subprocess.run(['git','ls-files','--error-unmatch',name],cwd=b.ROOT,capture_output=True).returncode:
            patch+=subprocess.run(['git','diff','--no-index','--binary','/dev/null',name],
                cwd=b.ROOT,capture_output=True,text=True).stdout
    (root/'implementation.patch').write_text(patch)
    for name in SOURCES:
        target=root/'source'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists(): shutil.copy2(b.ROOT/name,target)
    print('FROZEN O65 diagnostic binary/source', flush=True)


def checked(root):
    plan=base.load(root/'diagnostic-plan.json')
    amendments={}
    if (root/'collector-amendment.json').exists():
        amendment=base.load(root/'collector-amendment.json')
        allowed={'tests/benchmark/run_overlap_o65.py',
                 'tests/benchmark/analyze_overlap_o65.py',
                 'tests/benchmark/run_overlap_o65_replays.py'}
        if amendment['diagnostic_plan_sha256']!=digest(root/'diagnostic-plan.json'):
            raise ValueError('collector amendment original plan changed')
        amendments=amendment['sources']
        if not set(amendments)<=allowed:
            raise ValueError('collector amendment cannot change numerical sources')
        for name,change in amendments.items():
            if change['original_sha256']!=plan['sources'][name] or digest(root/'source'/name)!=change['original_sha256']:
                raise ValueError('collector amendment original source changed')
    for name, expected in plan['binaries'].items():
        if digest(root/name)!=expected: raise ValueError('diagnostic binary changed')
    for name, expected in plan['sources'].items():
        if name in amendments:
            expected=amendments[name]['current_sha256']
        if digest(b.ROOT/name)!=expected: raise ValueError('diagnostic source changed: '+name)
    return base.checked(root)[0]


def gates(root):
    current=checked(root)
    binary=str(root/'dmg-md-diagnostic')
    commands=[
        ('environment-p8',[sys.executable,str(b.ROOT/'tests/mpi/check_environment.py'),
            '--candidate',binary,'--devices',','.join(current.devices),'--ranks','8'],'PASS environment:'),
        ('golden',[sys.executable,str(b.ROOT/'tests/baseline/run_baselines.py'),
            '--candidate',binary,'--device',current.devices[0]],'PASS: all 4 baseline cases match'),
        ('ctest-gpu',['ctest','--test-dir',str(b.ROOT/'build-o65'),'--output-on-failure'],
            '100% tests passed'),
        ('mpi-domain',[sys.executable,str(b.ROOT/'tests/mpi/run_mpi_domain.py'),
            '--candidate',binary,'--devices',','.join(current.devices[:4]),'--ranks','2,4',
            '--partition-modes','off,verify-wait','--work-root',str(root/'mpi-work')],
            'PASS: M2a domain matrix'),
        ('long-nve',[sys.executable,str(b.ROOT/'tests/long_nve/run_long_nve.py'),
            '--candidate',binary,'--devices',','.join(current.devices[:4]),'--profile','nightly',
            '--report',str(root/'long-nve.json'),'--keep-work'], 'PASS long-NVE profile=nightly')]
    environment=o5.controlled_environment()
    for name,command,marker in commands:
        gate_environment=dict(environment)
        if name=='long-nve':
            temporary=root/'long-nve-work'
            temporary.mkdir(exist_ok=True)
            gate_environment['TMPDIR']=str(temporary)
        if not (root/f'{name}.log').exists():
            b.save(root/f'{name}-command.json',dict(command=command,environment=gate_environment))
            with (root/f'{name}.log').open('x') as out:
                subprocess.run(command,env=gate_environment,cwd=b.ROOT,stdout=out,stderr=subprocess.STDOUT,check=True)
        if marker not in (root/f'{name}.log').read_text():
            raise ValueError('incomplete diagnostic gate: '+name)
        print('PASS O65 diagnostic gate',name,flush=True)
    long=base.load(root/'long-nve.json')
    if long['candidate_sha256']!=digest(root/'dmg-md-diagnostic') or long['completed_configs']!=long['total_configs']:
        raise ValueError('long NVE identity/count differs')
    o5.freeze(root/'diagnostic-gates.json',dict(status='PASS',
        binary_sha256=digest(root/'dmg-md-diagnostic'),
        logs={name:digest(root/f'{name}.log') for name,_,_ in commands}))


def capture(root):
    current=checked(root)
    if base.load(root/'diagnostic-gates.json')['status']!='PASS': raise ValueError('gates required')
    original=b.execute
    for item in base.load(root/'capture-plan.json')['captures']:
        job=dict(item['job'],version='diagnostic',id=item['job']['case'])
        destination=root/'snapshots'/job['case']
        destination.mkdir(parents=True,exist_ok=True)
        def execute(command,directory,environment,timeout,interval,cpu_session=None,binding_audit=None):
            environment.update(DMGMD_O65_CAPTURE_CALL=str(item['call']),DMGMD_O65_CAPTURE_ROOT=str(destination))
            b.save(directory/'snapshot-launch.json',dict(command=command,environment=environment))
            return original(command,directory,environment,timeout,interval,cpu_session,binding_audit)
        b.execute=execute
        try: result=current.trial('capture',job)
        finally: b.execute=original
        if result['status']!='PASS': raise ValueError('snapshot capture failed: '+str(result.get('error')))
        reference=base.load(root/'profiles'/item['job']['id']/'result.json')
        if result['scientific_outputs']!=reference['scientific_outputs']:
            raise ValueError('snapshot run scientific outputs differ from baseline')
        for rank,row in enumerate(item['rank_records']):
            report=snapshot_report(destination/f'rank-{rank}')
            expected={'step':int(row['step']),'call':int(row['call']),'layout':int(row['epoch']),
                'neighbor_generation':int(row['neighbor_generation']),
                'workspace_generation':int(row['storage_generation']),
                'O':int(row['owned']),'D':int(row['dependency']),'L':int(row['local'])}
            if any(report[key]!=value for key,value in expected.items()):
                raise ValueError('captured generations differ from frozen activity trace')
    print('PASS O65 captures: all ten rank snapshots',flush=True)


def replays(root):
    current=checked(root)
    settings=base.load(root/'frozen-design.json')['replay']
    if not (root/'replay-plan.json').exists():
        reports=[snapshot_report(root/'snapshots'/case/f'rank-{rank}')
                 for case,ranks in base.SCOPES for rank in range(ranks)]
        rng=random.Random(settings['seed'])
        jobs=[]
        for repeat in range(settings['repeats']):
            configs=[(case,ranks,rank) for case,ranks in base.SCOPES for rank in range(ranks)]
            rng.shuffle(configs)
            for case,ranks,rank in configs:
                jobs.append(dict(id=f'{len(jobs):03d}-b{repeat}-{case}-rank{rank}',case=case,ranks=ranks,
                    rank=rank,repeat=repeat,seed=rng.randrange(2**31),
                    target='radial' if ranks==2 else 'partial',gpu=current.devices[rank],
                    cpus=current.bindings[ranks]['mapping'][rank]['os_cpus']))
        o5.freeze(root/'replay-plan.json',dict(**settings,jobs=jobs,snapshots=reports,
            binary_sha256=digest(root/'dmgmd-o65-replay'),
            context='one GPU process at a time; same frozen snapshot per rank in five blocks; rank means form block samples'))
    plan=base.load(root/'replay-plan.json')
    if plan['binary_sha256']!=digest(root/'dmgmd-o65-replay'): raise ValueError('replay binary changed')
    for report in plan['snapshots']:
        directory=Path(report['snapshot'])
        for name,metadata in report['arrays'].items():
            if digest(directory/name)!=metadata['sha256']: raise ValueError('frozen snapshot changed')
    for job in plan['jobs']:
        directory=root/'replays'/job['id']
        if (directory/'result.json').exists():
            if base.load(directory/'result.json')['status']!='PASS': raise ValueError('previous replay failed')
            continue
        directory.mkdir(parents=True,exist_ok=False)
        env=o5.controlled_environment()
        env.update(CUDA_DEVICE_ORDER='PCI_BUS_ID',CUDA_VISIBLE_DEVICES=job['gpu'],OMP_NUM_THREADS='1')
        command=['taskset','--cpu-list',','.join(map(str,job['cpus'])),str(root/'dmgmd-o65-replay'),
            str(root/'inputs'/job['case']/'nep.txt'),str(root/'snapshots'/job['case']/f'rank-{job["rank"]}'),
            str(directory/'output'),job['target'],str(job['seed']),str(plan['warmup']),str(plan['iterations'])]
        b.save(directory/'launch.json',dict(job=job,command=command,environment=env))
        code,wall,timeout=execute_local(command,directory,env,120,2)
        result=dict(job=job,returncode=code,wall_seconds=wall,timeout=timeout,status='FAILED')
        text=(directory/'stdout.txt').read_text()
        if code==0 and not timeout and 'PASS O65 replay:' in text:
            actual=next(b.fields(line) for line in text.splitlines() if line.startswith('O65_REPLAY_GPU '))
            normal=lambda s:s.lower().replace('gpu-','').replace('-','')
            if normal(actual['cuda_uuid'])!=normal(job['gpu']) or sorted(map(int,actual['cpus'].strip(',').split(',')))!=job['cpus']:
                raise ValueError('replay actual GPU/CPU binding differs')
            result['status']='PASS'
        b.save(directory/'result.json',result)
        print('REPLAY',job['id'],result['status'],flush=True)
        if result['status']!='PASS': raise ValueError('failed standalone replay; preserve attempt')


def ncu(root):
    current=checked(root)
    from profile_overlap_o6_ncu import attempt
    window=next(w for w in base.load(root/'capture-plan.json')['ncu_windows'] if
                w['case']=='carbon_200k' and w['mode']=='full')
    if not window['ordinary']: raise ValueError('NCU requires new ordinary activity window')
    job=dict(window,id='carbon-p2-full-new-steady-probe',cache_control='none',
        sections=['LaunchStats','Occupancy','SchedulerStats','WarpStateStats','MemoryWorkloadAnalysis'])
    o5.freeze(root/'ncu-plan.json',dict(job=job,rule='one probe; permission failure limits hardware metrics only; continue all controlled replays'))
    result=attempt(current,job)
    b.save(root/'ncu-analysis.json',dict(result=result,
        hardware_metrics='UNAVAILABLE' if 'ERR_NVGPUCTRPERM' in result.get('error','') else result['status'],
        exact_wall_decomposition='UNKNOWN'))


def activity(root):
    """Independent short CUPTI check of the actual compact launch mapping."""
    checked(root)
    plan=base.load(root/'replay-plan.json')
    reports=[]
    for case,ranks in base.SCOPES:
        job=next(j for j in plan['jobs'] if j['case']==case and j['rank']==0 and j['repeat']==0)
        directory=root/'replay-activity'/case
        if not (directory/'launch.json').exists():
            directory.mkdir(parents=True,exist_ok=False)
            env=o5.controlled_environment()
            env.update(CUDA_DEVICE_ORDER='PCI_BUS_ID',CUDA_VISIBLE_DEVICES=job['gpu'],OMP_NUM_THREADS='1')
            command=['nsys','profile','--trace=cuda','--sample=none','--cpuctxsw=none',
                '--cuda-event-trace=false','--output',str(directory/'trace'),
                'taskset','--cpu-list',','.join(map(str,job['cpus'])),str(root/'dmgmd-o65-replay'),
                str(root/'inputs'/case/'nep.txt'),str(root/'snapshots'/case/'rank-0'),
                str(directory/'output'),job['target'],str(job['seed']),'1','1']
            o5.freeze(directory/'launch.json',dict(command=command,environment=env,
                purpose='short actual-launch validation only; not performance samples'))
            code,wall,timeout=execute_local(command,directory,env,120,2)
            if code or timeout: raise ValueError('replay activity failed')
        if not (directory/'trace.sqlite').exists():
            with (directory/'export.log').open('x') as out:
                subprocess.run(['nsys','export','--type=sqlite','--output',str(directory/'trace.sqlite'),
                    str(directory/'trace.nsys-rep')],stdout=out,stderr=subprocess.STDOUT,check=True)
        db=sqlite3.connect(f'file:{directory}/trace.sqlite?mode=ro',uri=True)
        db.row_factory=sqlite3.Row
        try:
            names=dict(db.execute('select id,value from StringIds'))
            prefix='find_force_radial' if job['target']=='radial' else 'find_partial_force_angular'
            kernels=[dict(row,name=names[row['shortName']],
                          demangled_name=names[row['demangledName']],
                          mangled_name=names[row['mangledName']]) for row in db.execute(
                'select * from CUPTI_ACTIVITY_KIND_KERNEL order by start') if prefix in names[row['shortName']]]
        finally: db.close()
        if len(kernels)!=18 or any(k['blockX']!=64 for k in kernels):
            raise ValueError('unexpected replay target launch count/block size')
        result=base.load(directory/'output/result.json')
        expected_grid=(result['center_count']+63)//64
        compact_grids=sorted([(result['early_count']+63)//64]*3+[(result['late_count']+63)//64]*3)
        # shortName drops template arguments. Nsight 2025 spells bools as
        # (bool)0/1 in demangledName; the CUDA ABI retains ILb0E/ILb1E.
        compact=[k for k in kernels if 'ILb1E' in k['mangled_name']]
        original=[k for k in kernels if 'ILb0E' in k['mangled_name']]
        if len(compact)!=6 or len(original)!=12 or sorted(k['gridX'] for k in compact)!=compact_grids or any(
                k['gridX']!=expected_grid for k in original):
            raise ValueError('actual compact/original grids differ from frozen slot lists')
        reports.append(dict(case=case,target=job['target'],kernels=kernels,
            meaning='actual CUPTI grid/block/register evidence; no achieved occupancy or cache metric'))
    b.save(root/'replay-activity.json',dict(status='PASS',reports=reports))
    print('PASS O65 actual compact activity',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['freeze','gates','capture','replays','ncu','activity','all'])
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    root=args.root.resolve()
    with (root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        def captured():
            from cpu_binding import Audit
            original_wrapper,original_execute=Audit.wrapper,b.execute
            o5_rank_output.install()
            try: capture(root)
            finally: Audit.wrapper,b.execute=original_wrapper,original_execute
        if args.phase=='all':
            freeze(root); gates(root)
            from analyze_overlap_o65 import profile_analysis, replay_analysis
            profile_analysis(root); captured(); replays(root); activity(root); ncu(root); replay_analysis(root)
        elif args.phase=='capture': captured()
        else: globals()[args.phase](root)


if __name__=='__main__': main()
