#!/usr/bin/env python3
"""Separate Nsight O5 evidence; never use profiler walls as main performance."""
from pathlib import Path
from collections import Counter
import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import run_benchmark as b
from run_overlap_o5 import Experiment,freeze,controlled_environment
from cpu_binding import Audit,digest


def union(intervals):
    out=[]
    for a,z in sorted(intervals):
        if z<=a:continue
        if out and a<=out[-1][1]:out[-1][1]=max(out[-1][1],z)
        else:out.append([a,z])
    return out

def intersect(a,z):return union([(max(x,v),min(y,w)) for x,y in a for v,w in z if min(y,w)>max(x,v)])
def duration(a):return sum(z-x for x,z in union(a))


def halo_post_groups(posts):
    """Four directional halo posts per callback, independent of pack launches.

    Distinct neighbors require two pack kernels; the same neighbor at P2 can
    use a single combined pack. Kernel count is therefore not callback count.
    """
    ordered=sorted(posts,key=lambda p:p['start'])
    if len(ordered)%4:
        raise ValueError('halo post count is not a multiple of four')
    groups=[ordered[i:i+4] for i in range(0,len(ordered),4)]
    if any(Counter(p['tag'] for p in group)!={1101:2,1102:2} for group in groups):
        raise ValueError('halo group does not contain two posts per direction tag')
    return groups


def collect(root):
    exp=Experiment(root);frozen=json.loads((root/'frozen-plan.json').read_text())
    if exp.identity(('o1','o2','o3','o4'))!=frozen['identity']:raise RuntimeError('frozen identity differs')
    specs=[('carbon_200k',2),('carbon_1m',4),('water_400k',8),('carbon_thin_empty',4),('carbon_200k_drift',2)]
    jobs=[dict(case=c,ranks=p,backend=back,mode=mode,id=f'{c}-p{p}-{back}-{mode}')
          for c,p in specs for back in ('HostStaged','CudaAware') for mode in ('wait','poll')]
    freeze(root/'profile-plan.json',dict(jobs=jobs,source_sha256=digest(__file__),binary_sha256=digest(exp.binaries['o4']),
                  clock_error='UNKNOWN',cuda_event_trace=False,performance_sample=False))
    (root/'nsys-version.txt').write_text(subprocess.check_output(['nsys','--version'],text=True))
    for job in jobs:
        directory=root/'profiles'/job['id']
        if (directory/'result.json').exists():continue
        if directory.exists():raise RuntimeError('incomplete profile preserved; choose new directory')
        directory.mkdir(parents=True)
        source,case=exp.inputs[job['case']]
        for f in ('model.xyz','nep.txt'):(directory/f).symlink_to(source/f)
        (directory/'run.in').write_text(f'potential nep.txt\ntime_step {case["time_step_fs"]}\nensemble nve\nrun 3\n')
        audit=Audit(directory/'binding',exp.bindings[job['ranks']],exp.binaries['o4'])
        command=['nsys','profile','--trace=cuda,mpi','--mpi-impl=openmpi','--sample=none','--cpuctxsw=none',
                 '--cuda-event-trace=false','--output',str(directory/'trace'),
                 'mpiexec',*audit.mpi_args(),'-n',str(job['ranks']),*audit.wrapper([str(exp.binaries['o4'])])]
        env=controlled_environment()
        env.update(CUDA_DEVICE_ORDER='PCI_BUS_ID',CUDA_VISIBLE_DEVICES=','.join(exp.devices[:job['ranks']]),OMP_NUM_THREADS='1',
                   DMGMD_COMM_BACKEND=job['backend'],DMGMD_DOMAIN_PARTITION='serial',DMGMD_DOMAIN_OVERLAP=job['mode'],
                   DMGMD_OVERLAP_TRACE='1',DMGMD_DOMAIN_DIAGNOSTICS='1',DMGMD_DOMAIN_TIMING='0',DMGMD_DOMAIN_TRACE='0',DMGMD_COMM_LOG_INTERVAL='1')
        result=dict(job,status='FAILED',command=command,launch_environment=env,
                    input_sha256={f:digest(directory/f) for f in ('model.xyz','nep.txt','run.in')},
                    binary_sha256=digest(exp.binaries['o4']),tool_sha256=digest(__file__))
        b.save(directory/'launch.json',result)
        try:
            code,wall,timeout=b.execute(command,directory,env,600,2,binding_audit=audit)
            text=(directory/'stdout.txt').read_text();result['binding']=audit.finish(text)
            if code or timeout:raise RuntimeError(f'profile exit={code}, timeout={timeout}')
            command=['nsys','export','--type=sqlite','--output',str(directory/'trace.sqlite'),str(directory/'trace.nsys-rep')]
            with (directory/'export.log').open('x') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
            result.update(status='PASS',process_wall_seconds=wall,export_command=command)
        except (ValueError,RuntimeError,subprocess.SubprocessError) as e:result['error']=str(e)
        finally:
            if not audit.file.closed:audit.close()
            b.save(directory/'result.json',result)
        print(job['id'],result['status'],result.get('error',''),flush=True)


def analyze_profiles(root):
    root=Path(root);reports={}
    for file in sorted((root/'profiles').glob('*/result.json')):
        meta=json.loads(file.read_text());directory=file.parent
        if meta['status']!='PASS':reports[directory.name]=meta;continue
        db=sqlite3.connect(directory/'trace.sqlite');db.row_factory=sqlite3.Row
        ranks={r['globalTid']:r['rank'] for r in db.execute('select * from MPI_RANKS')}
        names=dict(db.execute('select id,value from StringIds'))
        kernels=[dict(r) for r in db.execute('select * from CUPTI_ACTIVITY_KIND_KERNEL')]
        for k in kernels:k['name']=names[k['shortName']]
        posts=[dict(r) for r in db.execute('select * from MPI_P2P_EVENTS where tag in (1101,1102) order by start')]
        waits=[dict(r) for r in db.execute('select * from MPI_START_WAIT_EVENTS order by start')]
        for w in waits:w['name']=names[w['textId']]
        raw=(directory/'stdout.txt').read_text()
        partition=[b.fields(s) for s in raw.splitlines() if s.startswith('DMGMD_PARTITION rank=')]
        warnings=[s for s in ((directory/'stdout.txt').read_text()+(directory/'stderr.txt').read_text()).splitlines()
                  if any(word in s.lower() for word in ('warning','error','dropped'))]
        rows=[];issues=[];kernel_counts={}
        for tid,rank in ranks.items():
            ks=sorted([k for k in kernels if k['globalPid']==(tid//(1<<24))*(1<<24)],key=lambda k:k['start'])
            kernel_counts[rank]=dict(Counter(k['name'] for k in ks))
            packs=[k for k in ks if k['name']=='pack_indexed_soa']
            ps=[r for r in posts if r['globalTid']==tid];ws=[w for w in waits if w['globalTid']==tid]
            ordinary=[r for r in partition if int(r['rank'])==rank and r.get('reason') not in ('setup','rebuild')]
            try:post_groups=halo_post_groups(ps)
            except ValueError as error:issues.append(f'rank {rank}: {error}');continue
            indexed_groups=[]
            for index,group in enumerate(post_groups):
                previous_end=max(p['end'] for p in post_groups[index-1]) if index else 0
                pack_group=[k for k in packs if previous_end<k['start'] and k['end']<group[0]['start']]
                # Setup/rebuild also uses these tags, with different pack kernels.
                if pack_group:indexed_groups.append((group,pack_group))
            if sum(len(p) for _,p in indexed_groups)!=len(packs):issues.append(f'rank {rank}: unmapped indexed pack kernel')
            if len(indexed_groups)!=len(ordinary):issues.append(f'rank {rank}: indexed halo/ordinary mapping {len(indexed_groups)}/{len(ordinary)}')
            for index,(group,pack_group) in enumerate(indexed_groups):
                post_end=max(r['end'] for r in group)
                unpack=next((k for k in ks if k['name']=='unpack_indexed_soa' and k['start']>post_end),None)
                if not unpack:issues.append('unpack missing');continue
                early=[k for k in ks if post_end<=k['start'] and k['end']<=unpack['start'] and k['name'] in
                       ('find_neighbor_list_large_box','find_descriptor','find_force_radial','find_partial_force_angular','gpu_find_force_many_body_domain')]
                part=ordinary[index] if index<len(ordinary) else None
                intervals=[];requests=[];missing_observations=[]
                for p in group:
                    if not p['size']:continue
                    first=next((w for w in ws if p['end']<=w['start']<unpack['start'] and w['requestHandle']==p['requestHandle']),None)
                    if not first:
                        missing_observations.append(p)
                        if not part or part['mode']=='split':issues.append('request completion observation missing')
                        continue
                    intervals.append([p['end'],first['end']]);requests.append(dict(post=p,first_observation=first))
                I=union([[k['start'],k['end']] for k in early]);C=union(intervals);O=intersect(I,C)
                api=union([[w['start'],w['end']] for w in ws if post_end<=w['start']<unpack['start']])
                if part and part['mode']=='split' and len(early)!=5:issues.append('split does not expose five actual early kernels')
                rows.append(dict(rank=rank,step=int(part['step']) if part else None,force_call=int(part['call']) if part else None,
                    behavior=part,pack_kernels_before_posts=len(pack_group),early_kernel_union_ns=I,request_state_union_ns=C,intersection_ns=O,
                    early_gpu_ns=duration(I),conservative_request_state_overlap_ns=duration(O),
                    gpu_during_mpi_api_ns=duration(intersect(I,api)),
                    shrink_10us_overlap_ns=duration(intersect(I,union([[a+10000,z-10000] for a,z in C]))),
                    early_kernels=[{k:v for k,v in x.items() if k in ('name','start','end','correlationId','streamId')} for x in early],
                    requests=requests,missing_completion_observations=missing_observations,
                    request_boundary_status='NOT_OBSERVED' if missing_observations else 'OBSERVED',
                    ratio=None,ratio_reason='clock/completion error bound UNKNOWN; empty denominator N/A'))
        witnesses=sum(r['conservative_request_state_overlap_ns']>0 for r in rows)
        eligible=sum(bool(r['behavior'] and r['behavior']['mode']=='split') for r in rows)
        reports[directory.name]=dict(status='PARTIAL' if issues else 'PASS',issues=issues,metadata=meta,
                    actual_cuda_kernel_launch_counts=kernel_counts,
                    warnings_or_dropped_reports=warnings,rows=rows,
                    witnesses=witnesses,eligible_rank_steps=eligible,
                    observation='OBSERVED' if witnesses else 'NOT_OBSERVED' if eligible else 'NOT_APPLICABLE_FULL_FALLBACK',
                    api_witnesses=sum(r['gpu_during_mpi_api_ns']>0 for r in rows))
        db.close()
    result=dict(clock='Nsight/CUPTI report timebase; no CLOCK_MONOTONIC splice',clock_max_error='UNKNOWN',
                scope='request state / actual GPU execution; not physical transport or critical-path saving',reports=reports)
    b.save(root/'profile-analysis.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--analyze-only',action='store_true');a=p.parse_args()
    if not a.analyze_only:
        with (a.root/'scheduler.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);collect(a.root.resolve())
    result=analyze_profiles(a.root.resolve())
    for name,r in result['reports'].items():print(name,r['status'],r.get('witnesses'),r.get('eligible_rank_steps'))
