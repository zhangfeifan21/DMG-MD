#!/usr/bin/env python3
"""Recompute O5 tables, paired intervals, CPU/diagnostic attribution and figures."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import statistics as st
import sys


def save(path,data):path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
def fields(line):return dict(t.split('=',1) for t in line.split()[1:] if '=' in t)
def percentile(x,p):
    x=sorted(x);at=(len(x)-1)*p;i=int(at);return x[i]+(x[min(i+1,len(x)-1)]-x[i])*(at-i)


def distribution(x):
    if not x:return dict(n=0,median=None,min=None,max=None,sd=None,mad=None,relative_range=None)
    m=st.median(x)
    return dict(n=len(x),median=m,min=min(x),max=max(x),sd=st.stdev(x) if len(x)>1 else None,
                mad=st.median(abs(v-m) for v in x),relative_range=(max(x)-min(x))/m if m else None)


def paired_ratio(a,b,seed=2026101606,resamples=20000):
    blocks=sorted(set(a)&set(b));aa=[a[k] for k in blocks];bb=[b[k] for k in blocks]
    if not blocks:return dict(n=0,estimate=None,ci95=None,blocks=[])
    estimate=st.median(aa)/st.median(bb);ci=None
    if len(blocks)>=5:
        rng=random.Random(seed);ratios=[]
        for _ in range(resamples):
            ids=rng.choices(range(len(blocks)),k=len(blocks))
            ratios.append(st.median(aa[i] for i in ids)/st.median(bb[i] for i in ids))
        ci=[percentile(ratios,.025),percentile(ratios,.975)]
    return dict(n=len(blocks),estimate=estimate,ci95=ci,blocks=blocks,
                estimator='ratio of medians',interval='pointwise paired-block percentile bootstrap',
                seed=seed,resamples=resamples,paired_numerator=aa,paired_denominator=bb)


def write_csv(path,rows):
    if not rows:return
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w') as f:
        w=csv.DictWriter(f,keys);w.writeheader()
        for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})


def trial_locations(root,plan,jobs):
    """Resolve explicitly frozen continuation attempts without replacing raw trials."""
    overrides={};path=root/'continuation-plan.json'
    if path.exists():
        resumed=json.loads(path.read_text())
        frozen_hash=hashlib.sha256((root/'frozen-plan.json').read_bytes()).hexdigest()
        if resumed['frozen_plan_sha256']!=frozen_hash or resumed['identity']!=plan['identity']:
            raise ValueError('continuation frozen identity differs')
        planned={j['id']:j for j in plan['jobs']}
        for row in resumed['jobs']:
            logical=row['logical_id'];actual=row['job']
            if logical in overrides or logical not in planned:
                raise ValueError('duplicate/unknown continuation logical trial')
            if any(actual.get(k)!=v for k,v in planned[logical].items() if k!='id'):
                raise ValueError('continuation configuration differs from frozen trial')
            if Path(row['group']).name!=row['group'] or Path(actual['id']).name!=actual['id']:
                raise ValueError('continuation directory must be local trial component')
            overrides[logical]=(row['group'],actual['id'])
    for j in jobs:
        group,actual=overrides.get(j['id'],('extension' if j.get('extension') else 'main',j['id']))
        yield j,group,root/group/actual/'result.json'


def telemetry_analysis(root,results):
    """Descriptive whole-process observations; no inferred formal GPU window."""
    rows=[]
    metrics=('temperature.gpu','power.draw','power.limit','clocks.sm','clocks.mem','memory.used','utilization.gpu')
    for r in results:
        path=Path(r['directory'])/'telemetry.jsonl'
        if not path.exists():
            rows.append(dict(id=r['id'],status='NOT_OBSERVED',directory=r['directory']));continue
        samples=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        for rank,uuid in enumerate(r['devices']):
            gpu=[g for sample in samples for g in sample['gpus'] if g['uuid']==uuid]
            row=dict(id=r['id'],case=r['case'],ranks=r['ranks'],backend=r['backend'],version=r['version'],mode=r['mode'],
                repeat=r['repeat'],rank=rank,gpu_uuid=uuid,status='PASS' if gpu else 'NOT_OBSERVED',
                scope='whole observed process including startup/warmup; not formal segment',directory=r['directory'])
            for key in metrics:
                vals=[]
                for g in gpu:
                    try:vals.append(float(g[key]))
                    except (ValueError,TypeError,KeyError):pass
                row[key]=dict(n=len(vals),min=min(vals) if vals else None,max=max(vals) if vals else None,
                              median=st.median(vals) if vals else None)
            rows.append(row)
    save(root/'telemetry-analysis.json',rows);write_csv(root/'telemetry-analysis.csv',rows)


def adoption_validation(root):
    path=root/'adoption-validation-plan.json'
    if not path.exists():return dict(status='NOT_PLANNED',trials=0)
    plan=json.loads(path.read_text());raw=[];bad=[];missing=[]
    for job in plan['jobs']:
        result=root/'adoption-validation'/job['id']/'result.json'
        if not result.exists():missing.append(job['id']);continue
        r=json.loads(result.read_text())
        oracle=json.loads((root/'science-main'/f'{job["case"]}-p{job["ranks"]}-outputs.json').read_text())['hashes']
        valid=r['status']=='PASS' and all(r.get(k)==v for k,v in job.items()) and r.get('scientific_outputs')==oracle
        valid=valid and r.get('binary_sha256')==plan['identity']['binaries']['o4']['sha256']
        if valid:raw.append(r)
        else:bad.append(job['id'])
    contrasts=[]
    for p in (4,8):
        values={mode:{r['repeat']:r['seconds'] for r in raw if r['ranks']==p and r['mode']==mode}
                for mode in ('full','split-serial','overlap-wait','overlap-poll')}
        for index,mode in enumerate(('overlap-wait','overlap-poll')):
            seed=plan['bootstrap_seed']+p*10+index;ratio=paired_ratio(values['full'],values[mode],seed)
            simultaneous=None
            if ratio['n']>=5:
                rng=random.Random(seed);boot=[];blocks=ratio['blocks']
                for _ in range(20000):
                    ids=rng.choices(blocks,k=len(blocks));boot.append(st.median(values['full'][i] for i in ids)/st.median(values[mode][i] for i in ids))
                simultaneous=[percentile(boot,.00625),percentile(boot,.99375)]
            contrasts.append(dict(case='water_400k',ranks=p,backend='CudaAware',mode=mode,**ratio,
                simultaneous_ci98_75=simultaneous,family='4 prespecified net contrasts; nominal Bonferroni familywise95%',
                criterion='median ratio >=1.05 and family-adjusted interval lower >1',
                confirmed=bool(simultaneous and ratio['estimate']>=1.05 and simultaneous[0]>1)))
    result=dict(status='PASS' if not missing and not bad and len(raw)==40 else 'PARTIAL',trials=len(raw),
        missing=missing,invalid=bad,contrasts=contrasts,primary_pooling=False,
        scope='held-out independent process trials; exact fixture/box/N/potential/backend/resource only; no generalization',
        recommendation='keep default off; conditional manual wait trial only for confirmed exact configurations; no automatic production policy')
    save(root/'adoption-validation-analysis.json',result)
    write_csv(root/'adoption-validation-ratios.csv',contrasts)
    write_csv(root/'adoption-validation-trials.csv',[{k:r.get(k) for k in
        ('id','case','ranks','backend','mode','repeat','warmup','steps','seconds','ms_per_step','process_wall_seconds','binary_sha256','scientific_outputs','directory')} for r in raw])
    return result


def counters(result):
    lines=(Path(result['directory'])/'stdout.txt').read_text().splitlines()
    rows=[fields(s) for s in lines if s.startswith(('DMGMD_DOMAIN_SUMMARY ','DMGMD_PARTITION_SUMMARY ','DMGMD_OVERLAP_SUMMARY '))]
    output=[]
    for rank in range(result['ranks']):
        row=dict(rank=rank,split=0,full=None,classifications=0,overlap=0,progress=0,
                 finish_wall_ns=None,finish_thread_cpu_ns=None,finish_callbacks=0,rebuild=None,migration=None)
        for name,prefix in [('split','cumulative_split_calls'),('full','cumulative_full_calls'),
                            ('classifications','cumulative_classifications'),('overlap','cumulative_calls'),
                            ('progress','cumulative_polls'),('finish_wall_ns','cumulative_finish_ns'),
                            ('finish_thread_cpu_ns','cumulative_finish_cpu_ns')]:
            matches={int(r['sequence']):r for r in rows if int(r['rank'])==rank and prefix in r}
            if 0 in matches and 1 in matches:row[name]=int(matches[1][prefix])-int(matches[0][prefix])
        domain=next((r for r in rows if int(r['rank'])==rank and r.get('sequence')=='1' and 'migration_steps' in r),None)
        if domain:row.update(rebuild=int(domain['rebuild_steps']),migration=int(domain['migration_steps']))
        if domain and result['mode'] in ('overlap-wait','overlap-poll'):
            row['finish_callbacks']=int(domain['steps'])-int(domain['rebuild_steps'])
        if result['ranks']==1 or result['mode']=='full':
            row.update(full=result['steps']+1,full_count_source='verified completed path: steps + segment initial force')
        if row['full'] is not None:
            row['fallback_counts']=({'m1':row['full']} if result['ranks']==1 else
                {'partition_off':row['full']} if result['mode']=='full' else
                {'setup':1,'rebuild_including_migration':row['rebuild'],
                 'other_full_reason_UNKNOWN':row['full']-1-row['rebuild'] if row['rebuild'] is not None else None})
        if row['finish_callbacks']:
            row['mean_callback_wall_ms']=row['finish_wall_ns']/row['finish_callbacks']/1e6 if row['finish_wall_ns'] is not None else None
            row['mean_callback_thread_cpu_ms']=row['finish_thread_cpu_ns']/row['finish_callbacks']/1e6 if row['finish_thread_cpu_ns'] is not None else None
            row['progress_per_callback']=row['progress']/row['finish_callbacks']
        row['finish_scope']='ordinary halo poll+finish callback, including full fallback; not pure network'
        output.append(row)
    return output


def diagnostics(root):
    recovery=root/'diagnostic-recovery-plan.json'
    group=json.loads(recovery.read_text())['group'] if recovery.exists() else 'diagnostic'
    data=[];cpus=[];stages=[];cpu_windows=[]
    files=sorted((root/group).glob('*/result.json'))
    oracles=defaultdict(set)
    for file in files:
        result=json.loads(file.read_text())
        if result['mode']=='full' and result['status']=='PASS':
            oracles[(result['case'],result['ranks'],result['warmup'],result['steps'])].add(json.dumps(result.get('scientific_outputs'),sort_keys=True))
    for file in files:
        r=json.loads(file.read_text());d=file.parent
        text=(d/'stdout.txt').read_text();rows=[fields(s) for s in text.splitlines() if s.startswith('DMGMD_PARTITION rank=')]
        timing=[fields(s) for s in text.splitlines() if s.startswith(('DMGMD_DOMAIN_TIMING ','DMGMD_DOMAIN_TRACE rank=','DMGMD_OVERLAP_TRACE '))]
        comm=[fields(s) for s in text.splitlines() if s.startswith(('DMGMD_DOMAIN_COMM ','DMGMD_COMM step='))]
        for x in timing:
            if x.get('sequence')=='1' and 'step_seconds_rank_mean' in x:
                stages.append(dict(case=r['case'],ranks=r['ranks'],backend=r['backend'],mode=r['mode'],
                                   directory=str(d),scope='instrumented formal loop, rank mean per step',**x))
        # Partition rows have no sequence: select the final `steps + setup` rows
        # per rank, preserving order within each rank, to exclude warmup.
        for rank in range(r['ranks']):
            rr=[x for x in rows if int(x['rank'])==rank][-(r['steps']+1):]
            complete=(r['mode']=='full' or len(rr)==r['steps']+1)
            active=[x for x in rr if x.get('mode')=='split']
            prepared=[x for x in rr if x.get('mode')=='split' or x.get('reason')=='empty-interior']
            classified=[x for x in rr if x.get('classified')=='1']
            trace=[x for x in timing if x.get('rank')==str(rank) and x.get('sequence')=='1' and 'pack_device_seconds' in x]
            overlap=[x for x in timing if x.get('rank')==str(rank) and 'early_cuda_ms' in x and
                     r['warmup']<int(x['step'])<=r['warmup']+r['steps']]
            envelopes={}
            for name in ('pack_enqueue','d2h','input_sync','mpi_waitall','h2d','output_sync','unpack_enqueue'):
                values=[(int(x[name+'_end_ns'])-int(x[name+'_begin_ns']))/1e6 for x in trace
                        if int(x[name+'_begin_ns'])>0 and int(x[name+'_end_ns'])>=int(x[name+'_begin_ns'])]
                envelopes[name+'_host_ms']=distribution(values)
            for name in ('pack_device_seconds','unpack_device_seconds','nep_device_seconds','mpi_call_seconds_local'):
                envelopes[name+'_in_ms']=distribution([float(x[name])*1000 for x in trace if name in x])
                envelopes[name+'_missing_rows']=sum(name not in x for x in trace)
            for name in ('early_cuda_ms','late_cuda_ms'):
                envelopes[name]=distribution([float(x[name]) for x in overlap])
            byte_keys=('halo_send_bytes_local','halo_recv_bytes_local','migration_send_bytes_local',
                       'migration_recv_bytes_local','control_send_bytes_local','control_recv_bytes_local')
            required_trace=('mpi_call_seconds_local','pack_device_seconds','unpack_device_seconds','nep_device_seconds',*byte_keys)
            trace_complete=(len(trace)==r['steps'] and {int(x['step']) for x in trace}==set(range(r['warmup']+1,r['warmup']+r['steps']+1))
                            and all(all(k in x for k in required_trace) for x in trace))
            expected=oracles[(r['case'],r['ranks'],r['warmup'],r['steps'])]
            scientific_match=len(expected)==1 and json.dumps(r.get('scientific_outputs'),sort_keys=True) in expected
            data.append(dict(case=r['case'],ranks=r['ranks'],backend=r['backend'],mode=r['mode'],rank=rank,
                status=r['status'] if complete and trace_complete and scientific_match else 'PARTIAL',directory=str(d),partition_rows=rr,
                trace_complete=trace_complete,trace_rows=len(trace),scientific_match=scientific_match,
                interior_owned=distribution([int(x['interior'])/int(x['owned']) for x in prepared if int(x['owned'])]),
                early_dependency=distribution([int(x['early'])/int(x['dependency']) for x in prepared if int(x['dependency'])]),
                cache_misses=len(classified),cache_hits=sum(x.get('classified')=='0' for x in prepared),
                classify_miss_host_seconds=distribution([float(x['classify_host_seconds']) for x in classified]),
                classify_hit_host_seconds=distribution([float(x['classify_host_seconds']) for x in prepared if x.get('classified')=='0']),
                extra_product_kernel_launches=5*len(active),
                classification_kernel_launches=sum(int(x['dependency'])>0 for x in classified)+2*sum(int(x['owned'])>0 for x in classified),
                classification_fill_launches=sum(3*int(int(x['local'])>0)+1 for x in classified),
                launch_count_source='observed split/miss counts times source launch conditions; independent Nsight kernel counts also retained',
                formal_loop_stage_distributions=envelopes,
                formal_loop_communication_bytes={k:sum(int(x[k]) for x in trace) if trace and all(k in x for x in trace) else None for k in byte_keys},
                fallback_reasons=dict(Counter(x['reason'] for x in rr if x.get('mode')=='full')),
                timing_rows=[x for x in timing if x.get('rank')==str(rank) or 'rank' not in x],
                communication_rows=[x for x in comm if x.get('rank')==str(rank)],
                caveat='Host timing/event pipeline envelopes overlap; cannot sum to pure compute/network time'))
        if (d/'cpu/summary.json').exists():
            c=json.loads((d/'cpu/summary.json').read_text())
            formal=[x for x in c['phases'] if x['phase']=='run' and x['sequence']==1]
            valid_windows=(len(formal)==r['ranks'] and {x['rank'] for x in formal}==set(range(r['ranks'])) and
                           all(x['valid_intervals']>0 and x['cpu_seconds'] is not None for x in formal))
            for x in formal:cpu_windows.append(dict(case=r['case'],ranks=r['ranks'],backend=r['backend'],mode=r['mode'],
                directory=str(d),scope='complete sampled windows within diagnostic loop marker; excludes initial force/final sync',**x))
            cpus.append(dict(case=r['case'],ranks=r['ranks'],backend=r['backend'],mode=r['mode'],directory=str(d),
                             status=r.get('cpu_status') if valid_windows else 'PARTIAL',phases=c['phases'],process_observed=[x for x in c['rows'] if x['scope']=='process_observed'],
                             job=c['job'],parse_errors=c['parse_errors']))
    save(root/'diagnostic-analysis.json',data);save(root/'cpu-analysis.json',cpus)
    save(root/'diagnostic-stages.json',stages);write_csv(root/'diagnostic-stages.csv',stages)
    save(root/'cpu-formal-loop.json',cpu_windows);write_csv(root/'cpu-formal-loop.csv',cpu_windows)
    return data,cpus


def analyze(root,figures=False):
    root=Path(root).resolve();plan=json.loads((root/'frozen-plan.json').read_text())
    raw=[];results=[];missing=[]
    jobs=list(plan['jobs'])
    if (root/'extension-plan.json').exists():jobs+=json.loads((root/'extension-plan.json').read_text())['jobs']
    locations=list(trial_locations(root,plan,jobs))
    for j,group,p in locations:
        if not p.exists():missing.append(j);continue
        r=json.loads(p.read_text());r.update(logical_id=j['id'],collection_group=group)
        if any(r.get(k)!=v for k,v in j.items() if k!='id'):
            r.update(status='INVALID',error='raw result configuration differs from frozen logical trial')
        raw.append(r)
    selected={p.resolve() for j,g,p in locations}
    attempts=[]
    for group in sorted({'main','extension'}|{g for j,g,p in locations}):
        for p in sorted((root/group).glob('*/result.json')):
            r=json.loads(p.read_text());attempts.append(dict(id=r['id'],group=group,status=r['status'],
                selected=p.resolve() in selected,directory=r['directory'],reason=r.get('error')))
    save(root/'raw-attempt-inventory.json',attempts);write_csv(root/'raw-attempt-inventory.csv',attempts)
    # Contract failure exclusions are deterministic, independent of trial speed.
    science=defaultdict(set)
    scientific_files=defaultdict(set)
    for r in raw:
        if r['status']=='PASS' and r['mode']=='full' and r['version']=='o4':
            science[(r['case'],r['ranks'],r['warmup'],r['steps'])].add(r['science_sha256'])
            scientific_files[(r['case'],r['ranks'],r['warmup'],r['steps'])].add(json.dumps(r.get('scientific_outputs'),sort_keys=True))
    for original in raw:
        r=dict(original);r['admission_status']=r['status']
        expected=science.get((r['case'],r['ranks'],r['warmup'],r['steps']),set())
        if r['status']=='PASS' and (len(expected)!=1 or r.get('science_sha256') not in expected):
            r.update(admission_status='INVALID',admission_reason='missing or unequal full scientific oracle')
        if r['status']=='PASS':
            expected_outputs=scientific_files.get((r['case'],r['ranks'],r['warmup'],r['steps']),set())
            if len(expected_outputs)!=1 or json.dumps(r.get('scientific_outputs'),sort_keys=True) not in expected_outputs:
                r.update(admission_status='INVALID',admission_reason='scientific output files differ from current full')
        if r['status']=='PASS':
            r['formal_counters']=counters(r)
            r['actual_behavior']=('M1 full fallback' if r['ranks']==1 else
                 'M2a overlap (may include full calls)' if any(x['overlap'] for x in r['formal_counters']) else
                 'M2a split serial (may include full calls)' if any(x['split'] for x in r['formal_counters']) else
                 'M2a full fallback' if r['mode']!='full' else 'M2a full')
        results.append(r)
    save(root/'trial-analysis.json',results)
    telemetry_analysis(root,[r for r in results if r['admission_status']=='PASS'])
    flatkeys=['id','logical_id','collection_group','case','ranks','backend','version','mode','repeat','atoms','steps','warmup','status','admission_status',
              'admission_reason','sequence','seconds','ms_per_step','atom_steps_per_second','process_wall_seconds',
              'actual_behavior','formal_counters','binary_sha256','input_sha256','science_sha256','scientific_outputs','formal_cpu','directory','error']
    write_csv(root/'trials.csv',[{k:r.get(k) for k in flatkeys} for r in results])
    groups=defaultdict(list)
    for r in results:
        if r['admission_status']=='PASS':groups[(r['case'],r['ranks'],r['backend'],r['version'],r['mode'])].append(r)
    cost_summary=[];cpu_cost_ratios=[]
    metrics=('split','full','classifications','overlap','progress','rebuild','migration',
             'mean_callback_wall_ms','mean_callback_thread_cpu_ms','progress_per_callback')
    for key,rr in sorted(groups.items()):
        case,p,back,version,mode=key
        for rank in range(p):
            per_rank=[r['formal_counters'][rank] for r in rr]
            cost_summary.append(dict(case=case,ranks=p,backend=back,version=version,mode=mode,rank=rank,
                trial_n=len(rr),metrics={k:distribution([x[k] for x in per_rank if x.get(k) is not None]) for k in metrics},
                scope='descriptive per-rank trial distribution; finish includes ordinary fallback, not pure network'))
            if version!='o4' or mode!='overlap-poll':continue
            wait=groups.get((case,p,back,version,'overlap-wait'),[])
            for metric in ('mean_callback_wall_ms','mean_callback_thread_cpu_ms'):
                poll_values={r['repeat']:r['formal_counters'][rank][metric] for r in rr if r['formal_counters'][rank].get(metric) is not None}
                wait_values={r['repeat']:r['formal_counters'][rank][metric] for r in wait if r['formal_counters'][rank].get(metric) is not None}
                seed=2026101606+int(hashlib.sha256(f'finish:{case}:{p}:{back}:{rank}:{metric}'.encode()).hexdigest()[:8],16)
                cpu_cost_ratios.append(dict(case=case,ranks=p,backend=back,rank=rank,metric=metric,
                    contrast='poll / wait component cost',scope='single initial-thread halo callback component; not total process CPU',
                    **paired_ratio(poll_values,wait_values,seed)))
    save(root/'formal-cost-summary.json',cost_summary);write_csv(root/'formal-cost-summary.csv',cost_summary)
    save(root/'finish-cost-ratios.json',cpu_cost_ratios);write_csv(root/'finish-cost-ratios.csv',cpu_cost_ratios)
    summaries=[]
    for key,rr in sorted(groups.items()):
        summaries.append(dict(zip(('case','ranks','backend','version','mode'),key),
                              seconds=distribution([r['seconds'] for r in rr]),
                              ms_per_step=distribution([r['ms_per_step'] for r in rr]),
                              samples=[dict(id=r['id'],repeat=r['repeat'],seconds=r['seconds']) for r in rr]))
    save(root/'summary.json',summaries)
    write_csv(root/'summary.csv',[dict(**{k:r[k] for k in ('case','ranks','backend','version','mode')},**r['ms_per_step']) for r in summaries])
    comparisons=[]
    def compare(case,p,backend,name,av,am,bv,bm):
        a={r['repeat']:r['seconds'] for r in groups[(case,p,backend,av,am)]}
        z={r['repeat']:r['seconds'] for r in groups[(case,p,backend,bv,bm)]}
        seed=2026101606+int(hashlib.sha256(f'{case}:{p}:{backend}:{name}'.encode()).hexdigest()[:8],16)
        ratio=paired_ratio(a,z,seed)
        comparisons.append(dict(case=case,ranks=p,backend=backend,contrast=name,numerator=[av,am],denominator=[bv,bm],**ratio))
    for case in plan['design']['cases']:
        for p in plan['design']['ranks']:
            for back in plan['design']['backends']:
                compare(case,p,back,'partition cost','o4','split-serial','o4','full')
                for mode in ('overlap-wait','overlap-poll'):
                    compare(case,p,back,'increment '+mode,'o4','split-serial','o4',mode)
                    compare(case,p,back,'net '+mode,'o4','full','o4',mode)
                compare(case,p,back,'poll vs wait','o4','overlap-wait','o4','overlap-poll')
                if [case,p] in plan['design']['history']['scope']:
                    for name,av,am,bv,bm in [
                        ('O2 full / O1 full selection interface','o2','full','o1','full'),
                        ('O2 serial / O2 full partition','o2','split-serial','o2','full'),
                        ('O3 full / O2 full lifecycle','o3','full','o2','full'),
                        ('O3 serial / O2 serial lifecycle','o3','split-serial','o2','split-serial'),
                        ('O4 full / O3 full interface','o4','full','o3','full'),
                        ('O4 serial / O3 serial interface','o4','split-serial','o3','split-serial')]:compare(case,p,back,name,av,am,bv,bm)
    for spec in plan['design'].get('degraded',[]):
        case,p=spec['case'],spec['ranks']
        for back in plan['design']['backends']:
            compare(case,p,back,'partition cost','o4','split-serial','o4','full')
            for mode in ('overlap-wait','overlap-poll'):
                compare(case,p,back,'increment '+mode,'o4','split-serial','o4',mode)
                compare(case,p,back,'net '+mode,'o4','full','o4',mode)
            compare(case,p,back,'poll vs wait','o4','overlap-wait','o4','overlap-poll')
    save(root/'ratios.json',comparisons);write_csv(root/'ratios.csv',comparisons)
    sensitivity=[]
    if (root/'continuation-plan.json').exists():
        interrupted=json.loads((root/'continuation-plan.json').read_text()).get('interrupted_repeats',[])
        for c in comparisons:
            case,p,back=c['case'],c['ranks'],c['backend'];av,am=c['numerator'];bv,bm=c['denominator']
            a={r['repeat']:r['seconds'] for r in groups[(case,p,back,av,am)] if r['repeat'] not in interrupted}
            z={r['repeat']:r['seconds'] for r in groups[(case,p,back,bv,bm)] if r['repeat'] not in interrupted}
            sensitivity.append(dict(case=case,ranks=p,backend=back,contrast=c['contrast'],
                excluded_repeats=interrupted,**paired_ratio(a,z,c['seed'] if 'seed' in c else 2026101606)))
    save(root/'interruption-sensitivity.json',sensitivity);write_csv(root/'interruption-sensitivity.csv',sensitivity)
    scaling=[]
    for key,rr in sorted(groups.items()):
        case,p,backend,ver,mode=key
        if ver!='o4' or case not in plan['design']['cases']:continue
        base=groups[(case,1,backend,ver,mode)]
        pair=paired_ratio({r['repeat']:r['seconds'] for r in base},{r['repeat']:r['seconds'] for r in rr})
        valid=pair['n']>=5
        scaling.append(dict(case=case,ranks=p,backend=backend,version=ver,mode=mode,
                            speedup=pair['estimate'] if valid else None,efficiency=pair['estimate']/p if valid else None,
                            ci95=pair['ci95'] if valid else None,n=pair['n'],path_change='P1 M1; P>1 M2a',
                            reason=None if valid else 'missing complete matched T1/TP'))
    save(root/'scaling.json',scaling);write_csv(root/'scaling.csv',scaling)
    calibration=[]
    for f in (root/'calibration').glob('*/result.json'):calibration.append(json.loads(f.read_text()))
    calstats=[]
    for r in calibration:
        r['launch_and_sampler_wall_seconds']=(r['t_end_ns']-r['t_begin_ns'])/1e9 if r.get('t_end_ns') and r.get('t_begin_ns') else None
    for metric in ('seconds','process_wall_seconds','launch_and_sampler_wall_seconds'):
        off={r['repeat']:r[metric] for r in calibration if r['status']=='PASS' and r['calibration_group']=='off' and r.get(metric) is not None}
        for mode in ('proc','trace'):
            other={r['repeat']:r[metric] for r in calibration if r['status']=='PASS' and r['calibration_group']==mode and r.get(metric) is not None}
            calstats.append(dict(metric=metric,contrast=mode+'/off',**paired_ratio(other,off)))
    save(root/'calibration-analysis.json',calstats)
    # Derived CPU outputs can be regenerated; raw ticks/markers are never edited.
    from analyze_cpu import analyze as analyze_cpu
    diagnostic_group=json.loads((root/'diagnostic-recovery-plan.json').read_text())['group'] if (root/'diagnostic-recovery-plan.json').exists() else 'diagnostic'
    for config in [*(root/diagnostic_group).glob('*/cpu/config.json'),*(root/'calibration').glob('*/cpu/config.json')]:
        markers=config.parent/'markers.jsonl'
        analyze_cpu(config.parent,markers_path=markers if markers.exists() else None)
    diag,cpu=diagnostics(root)
    profile=None
    if (root/'profiles').exists():
        from profile_overlap_o5 import analyze_profiles
        profile=analyze_profiles(root)
    extension=sorted({(r['case'],r['ranks']) for r in comparisons if (r['contrast'].startswith('net ') or r['contrast']=='poll vs wait')
                      and r['n']==5 and r['ci95'] and (r['ci95'][1]-r['ci95'][0])/r['estimate']>.10})
    expected_diag=sum(plan['design']['ranks'][1:])*len(plan['design']['cases'])*len(plan['design']['backends'])*len(plan['design']['modes'])
    expected_diag+=sum(s['ranks'] for s in plan['design'].get('degraded',[]))*len(plan['design']['backends'])*len(plan['design']['modes'])
    expected_cpu=(len(plan['design']['cases'])*(len(plan['design']['ranks'])-1)+len(plan['design'].get('degraded',[])))*len(plan['design']['backends'])*len(plan['design']['modes'])
    profile_jobs=json.loads((root/'profile-plan.json').read_text())['jobs'] if (root/'profile-plan.json').exists() else []
    profile_ok=bool(profile and len(profile_jobs)==20 and
                    set(profile['reports'])=={j['id'] for j in profile_jobs} and
                    all(r['status']=='PASS' for r in profile['reports'].values()))
    observed_modes=sorted({(r['metadata']['backend'],r['metadata']['mode']) for r in profile['reports'].values()
                           if r.get('witnesses',0)>0 and r['metadata'].get('case') in plan['design']['cases']}) if profile else []
    profile_evidence_ok=set(observed_modes)=={(back,mode) for back in plan['design']['backends'] for mode in ('wait','poll')}
    gate_files=('science-accepted-validation.json','degraded-science-v2-validation.json','history-science-accepted-validation.json','launch-whitelist-gate-validation.json')
    gates={}
    for name in gate_files:
        path=root/name
        if path.exists():
            rows=json.loads(path.read_text())
            gates[name]=dict(count=len(rows),passed=bool(rows) and all(r.get('passed') for r in rows))
        else:gates[name]=dict(count=0,passed=False)
    rebuilt_gate=root/'rebuilt-o2-gate.json'
    rebuilt=json.loads(rebuilt_gate.read_text()) if rebuilt_gate.exists() else {}
    gates['rebuilt-o2-gate.json']=dict(passed=rebuilt.get('status')=='PASS' and
          len(rebuilt.get('comparisons',[]))==12 and all(r['passed'] for r in rebuilt['comparisons']))
    from observe_o5_gpu_mapping import selection,analyze as analyze_gpu_mapping
    _,mapping_folder=selection(root)
    gpu_mapping_path=root/mapping_folder/'summary.json'
    if (root/mapping_folder/'raw.jsonl').exists():
        gpu_mapping=analyze_gpu_mapping(root)
    else:gpu_mapping=json.loads(gpu_mapping_path.read_text()) if gpu_mapping_path.exists() else {}
    gpu_mapping_ok=gpu_mapping.get('status')=='PASS' and gpu_mapping.get('trials')==88 and gpu_mapping.get('rank_rows')==384
    adoption=adoption_validation(root)
    adoption_ok=adoption['status'] in ('PASS','NOT_PLANNED')
    status=dict(stage='O5',status='PASS' if not missing and all(r['admission_status']=='PASS' for r in results) and
                not extension and len(diag)==expected_diag and all(d['status']=='PASS' for d in diag) and
                len(cpu)==expected_cpu and all(c['status']=='PASS' for c in cpu) and len(calibration)==15 and
                all(r['status']=='PASS' for r in calibration) and profile_ok and profile_evidence_ok and gpu_mapping_ok and adoption_ok and all(g['passed'] for g in gates.values()) else 'PARTIAL',
                planned_trials=len(jobs),completed=len(results),admitted=sum(r['admission_status']=='PASS' for r in results),
                raw_attempts=len(attempts),raw_attempt_status_counts=dict(Counter(r['status'] for r in attempts)),
                current_binary_admitted=sum(r['admission_status']=='PASS' and r['version']=='o4' for r in results),
                required_current_admitted=sum(r['admission_status']=='PASS' and r['version']=='o4' and not r.get('supplement') for r in results),
                degraded_admitted=sum(r['admission_status']=='PASS' and r.get('supplement')=='degraded' for r in results),
                historical_admitted=sum(r['admission_status']=='PASS' and r['version']!='o4' for r in results),
                status_counts=dict(Counter(r['admission_status'] for r in results)),missing=missing,
                extension_required=extension,diagnostic_rank_rows=len(diag),cpu_diagnostic_trials=len(cpu),
                expected_diagnostic_rank_rows=expected_diag,expected_cpu_diagnostic_trials=expected_cpu,
                calibration_trials=len(calibration),profiles_complete=profile_ok,profile_observed_backend_modes=observed_modes,
                diagnostic_runtime_gpu_mapping='PASS' if gpu_mapping_ok else 'NOT_OBSERVED_OR_PARTIAL',
                adoption_validation_status=adoption['status'],adoption_validation_trials=adoption['trials'],
                correctness_gates=gates,default_overlap='off',
                scope='single node, inherited memory policy, no clock lock or exclusive reservation',
                formal_cpu=None,overlap_ratio=None,overlap_ratio_reason='independent maximum clock/completion error UNKNOWN')
    save(root/'analysis-status.json',status)
    lines=['# O5 可重算统计表','',f'状态：{status["status"]}；已准入 {status["admitted"]}/{status["planned_trials"]} 个主/历史 trial。',
           '', '比值为中位数之比；95% 区间为按重复块配对的百分位 bootstrap（20,000 次），逐配置区间未做多重比较校正。',
           '', '| case | P | backend | 对比 | n | 比值 | 95% CI |','|---|---:|---|---|---:|---:|---|']
    for r in comparisons:
        val=f'{r["estimate"]:.5f}' if r['estimate'] is not None else 'N/A'
        ci=' – '.join(f'{v:.5f}' for v in r['ci95']) if r['ci95'] else 'N/A'
        lines.append(f'| {r["case"]} | {r["ranks"]} | {r["backend"]} | {r["contrast"]} | {r["n"]} | {val} | {ci} |')
    (root/'statistics.md').write_text('\n'.join(lines)+'\n')
    if figures:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(3,2,figsize=(12,11),sharex=True)
        for i,case in enumerate(plan['design']['cases']):
            for j,backend in enumerate(plan['design']['backends']):
                ax=axes[i,j]
                for mode,offset in [('overlap-wait',-.06),('overlap-poll',.06)]:
                    rows=[r for r in comparisons if r['case']==case and r['backend']==backend and r['contrast']=='net '+mode and r['n']>=5]
                    if rows:
                        x=[math.log2(r['ranks'])+offset for r in rows];y=[r['estimate'] for r in rows]
                        line=ax.plot(x,y,marker='o',label=mode)[0]
                        ax.vlines(x,[r['ci95'][0] for r in rows],[r['ci95'][1] for r in rows],color=line.get_color())
                ax.axhline(1,color='gray',linestyle='--');ax.set_title(case+' / '+backend)
                ax.set_xticks(range(4),['1 (M1)','2','4','8']);ax.set_xlabel('MPI ranks (4 physical cores/rank)')
                ax.set_ylabel('T(full) / T(overlap)');ax.legend();ax.grid(alpha=.2)
        fig.suptitle('O5 net speedup: ratio of medians, paired pointwise 95% bootstrap CI')
        fig.tight_layout();fig.savefig(root/'net-speedup.png',dpi=160);fig.savefig(root/'net-speedup.pdf');plt.close(fig)
    from summarize_overlap_o5 import render
    render(root)
    print(json.dumps({k:v for k,v in status.items() if k!='missing'},indent=2,ensure_ascii=False))
    return status


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--figures',action='store_true')
    a=p.parse_args();analyze(a.root,a.figures)
