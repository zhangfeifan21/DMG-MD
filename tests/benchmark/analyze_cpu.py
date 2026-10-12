#!/usr/bin/env python3
"""Recompute CPU counters from a trial directory (or its cpu directory), without GPUs."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import re

from cpu_sampling import save


def read_jsonl(path):
    rows, errors = [], []
    if not path.exists():
        return [], [dict(reason='missing_file', path=str(path))]
    for number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            row = json.loads(line)
            if row.get('schema_version') != 1:
                raise ValueError('unsupported schema_version')
            rows.append(row)
        except (ValueError, AttributeError) as error:
            errors.append(dict(line=number, reason=str(error)))
    return rows, errors


def interval(a, b):
    """No child-time addition, thread re-addition, gap interpolation or PID reuse."""
    if any(r.get('status') != 'ok' for r in (a,b)):
        return None
    keys = ('pid', 'tid', 'start_ticks', 'process_start_ticks', 'clk_tck', 'clock_domain_id')
    if any(a.get(k) != b.get(k) for k in keys) or b['sample_id'] != a['sample_id'] + 1:
        return None
    wall = (b['t_ns'] - a['t_ns']) / 1e9
    ticks = a['clk_tck']
    u, s = b['utime_ticks'] - a['utime_ticks'], b['stime_ticks'] - a['stime_ticks']
    if wall <= 0 or ticks <= 0 or min(u,s) < 0:
        return None
    result = dict(begin_ns=a['t_ns'], end_ns=b['t_ns'],
                  read_begin_ns=a['t_begin_ns'], read_end_ns=b['t_end_ns'],
                  begin_sample=a['sample_id'], end_sample=b['sample_id'],
                  wall_seconds=wall, user_seconds=u/ticks, system_seconds=s/ticks,
                  cpu_seconds=(u+s)/ticks, core_equivalent=(u+s)/ticks/wall)
    for key in ('voluntary_ctxt_switches', 'nonvoluntary_ctxt_switches'):
        delta = b.get(key,0) - a.get(key,0)
        result[key] = delta if delta >= 0 and key in a and key in b else None
    return result


def capacity_signature(cap):
    return (cap.get('status'), cap.get('hierarchy_complete'), cap.get('capacity_id'),
            tuple(cap.get('allowed_cpus') or []),
            tuple((a['path'],a.get('cpu.max'),a.get('cpuset.cpus.effective'))
                  for a in cap.get('ancestors',[])))


def shared_capacity(caps):
    """Shared domains count once; disjoint domains need no shared finite limit."""
    if not caps or any(c.get('status') != 'ok' or not c.get('hierarchy_complete') for c in caps):
        return None, 'incomplete_capacity_hierarchy'
    domains={}
    for cap in caps:
        domains.setdefault(cap.get('capacity_id'),[]).append(cap)
    allowed_sets, finite_ancestors, total = [], [], 0
    for group in domains.values():
        if len({c.get('quota_cores') for c in group}) != 1:
            return None, 'capacity_changed_during_read'
        allowed=set().union(*(set(c['allowed_cpus']) for c in group))
        ancestors={a['path'] for a in group[0].get('ancestors',[])
                   if a.get('cpu.max') and not a['cpu.max'].startswith('max ')}
        if any(allowed & other for other in allowed_sets) or any(ancestors & other for other in finite_ancestors):
            return None, 'unsupported_capacity_topology'
        allowed_sets.append(allowed)
        finite_ancestors.append(ancestors)
        quota=group[0].get('quota_cores')
        total+=min(len(allowed),quota) if quota is not None else len(allowed)
    return (total,None) if total > 0 else (None,'zero_capacity')


def uuid(value):
    value = value.lower().removeprefix('gpu-').replace('-','')
    if not re.fullmatch('[0-9a-f]{32}',value):
        raise ValueError('invalid CUDA UUID')
    return value


def mapping(config, records, stdout):
    bindings = []
    for line in stdout.splitlines():
        if line.startswith('DMGMD_MPI rank='):
            bindings.append(dict(x.split('=',1) for x in line.split()[1:] if '=' in x))
    regs = [r for r in records if r['type']=='rank_map']
    verified = {r['rank'] for r in records if r['type']=='exec_verified'}
    rows=[]
    for rank in range(config['ranks']):
        rs=[r for r in regs if r['rank']==rank]
        bs=[b for b in bindings if b.get('rank')==str(rank)]
        row=dict(rank=rank,status='unavailable',reason='missing_registration_exec_or_binding',
                 gpu_uuid=None,registration=rs[0] if len(rs)==1 else None,
                 binding=bs[0] if len(bs)==1 else None)
        if len(rs)==1 and len(bs)==1 and rank in verified:
            r,b=rs[0],bs[0]
            try:
                normalized=uuid(b['cuda_uuid'])
                for name in ('local_rank','local_size','world_size'):
                    if int(b[name])!=r[name]:
                        raise ValueError(name+' mismatch')
                if b['hostname']!=r['hostname']:
                    raise ValueError('hostname mismatch')
                visible=config.get('metadata',{}).get('visible_devices')
                if visible and normalized not in [uuid(v) for v in visible]:
                    raise ValueError('GPU not in requested visible devices')
                row.update(status='verified',reason=None,gpu_uuid=normalized)
            except (KeyError,ValueError) as error:
                row.update(status='conflict',reason=str(error))
        elif len(rs)>1 or len(bs)>1:
            row.update(status='conflict',reason='duplicate_rank_record')
        rows.append(row)
    used=[r['gpu_uuid'] for r in rows if r['status']=='verified']
    if len(used)!=len(set(used)):
        for row in rows:
            if row['gpu_uuid'] and used.count(row['gpu_uuid'])>1:
                row.update(status='conflict',reason='duplicate_gpu_uuid')
    return rows


def marker_windows(markers):
    opened, windows, issues = {}, [], []
    for m in markers:
        try:
            key=tuple(m[k] for k in ('rank','pid','process_start_ticks','clock_domain_id','sequence','phase'))
            if m['clock_id']!='CLOCK_MONOTONIC':
                raise ValueError('unsupported_marker_clock')
            if m['edge']=='begin':
                if key in opened:
                    raise ValueError('duplicate_begin')
                opened[key]=m
            elif m['edge']=='end':
                begin=opened.pop(key)
                if m['t_ns']<=begin['t_ns']:
                    raise ValueError('nonpositive_marker_window')
                windows.append(dict(begin, end_ns=m['t_ns']))
            else:
                raise ValueError('unknown_edge')
        except (KeyError,ValueError) as error:
            issues.append(str(error))
    issues.extend(['missing_end']*len(opened))
    return windows,issues


def summarize_intervals(items):
    wall=sum(i['wall_seconds'] for i in items)
    u=sum(i['user_seconds'] for i in items)
    s=sum(i['system_seconds'] for i in items)
    peak=max(items,key=lambda i:i['core_equivalent']) if items else None
    return dict(valid_intervals=len(items),observed_wall_seconds=wall,
                user_seconds=u if items else None,system_seconds=s if items else None,
                cpu_seconds=u+s if items else None,
                mean_core_equivalent=(u+s)/wall if wall else None,
                cpu_percent_one_core=100*(u+s)/wall if wall else None,
                peak_core_equivalent=peak['core_equivalent'] if peak else None,
                peak_window_seconds=peak['wall_seconds'] if peak else None)


def observer_summary(records):
    streams={}
    for record in records:
        if record['type']=='collector':
            values=dict(record['self_sample'],sample_id=record['sample_id'],
                        clock_domain_id=record['clock_domain_id'])
            streams.setdefault(('collector',values['pid'],values.get('start_ticks')),[]).append(values)
        elif record['type']=='observer_sample':
            streams.setdefault((record['observer'],record['pid'],record.get('start_ticks')),[]).append(record)
    return [dict(observer=name,pid=pid,start_ticks=start,**summarize_intervals(
                [v for a,b in zip(rows,rows[1:]) if (v:=interval(a,b)) is not None]))
            for (name,pid,start),rows in streams.items()]


def cgroup_summary(records):
    # Same cgroup may appear in every rank and ancestor. Deduplicate by path and
    # sample_id, choosing its first actual read; never add cgroup CPU to rank CPU.
    streams={}
    seen=set()
    for r in records:
        if r['type']!='capacity' or 't_begin_ns' not in r:
            continue
        for ancestor in r.get('ancestors',[]):
            key=(r['host_id'],ancestor['path'])
            identity=(*key,r['sample_id'])
            if identity in seen:
                continue
            seen.add(identity)
            try:
                values=dict((k,int(v)) for k,v in (line.split() for line in ancestor['cpu.stat'].splitlines()))
            except (KeyError,TypeError,AttributeError,ValueError):
                values={}
            streams.setdefault(key,[]).append(dict(sample_id=r['sample_id'],values=values,
                                                   t_ns=(r['t_begin_ns']+r['t_end_ns'])//2))
    result=[]
    for (host,path),samples in streams.items():
        row=dict(host_id=host,path=path,scope='whole_cgroup_including_observers_and_other_jobs')
        for metric in ('usage_usec','user_usec','system_usec','nr_periods','nr_throttled','throttled_usec'):
            intervals=[]
            for a,b in zip(samples,samples[1:]):
                av,bv=a['values'].get(metric),b['values'].get(metric)
                if (av is not None and bv is not None and bv>=av and
                    b['sample_id']==a['sample_id']+1 and b['t_ns']>a['t_ns']):
                    intervals.append((bv-av,(b['t_ns']-a['t_ns'])/1e9))
            row[metric+'_delta']=sum(v for v,w in intervals) if intervals else None
            row[metric+'_observed_wall_seconds']=sum(w for v,w in intervals) if intervals else None
        result.append(row)
    return result


def analyze(directory, output=None, markers_path=None):
    directory=Path(directory)
    if not (directory/'config.json').exists():
        directory=directory/'cpu'
    config=json.loads((directory/'config.json').read_text())
    records,errors=read_jsonl(directory/'samples.jsonl')
    output=Path(output) if output else directory
    output.mkdir(parents=True,exist_ok=True)
    trial=Path(config.get('metadata',{}).get('trial_directory',directory.parent))
    stdout=(trial/'stdout.txt').read_text() if (trial/'stdout.txt').exists() else ''
    maps=mapping(config,records,stdout)
    groups={}
    identities={}
    caps={}
    for row in records:
        if row['type'] in ('proc_sample','task_sample'):
            # Keep unavailable samples inside the series, breaking the interval.
            key=(row['type'],row['rank'],row['pid'],row.get('tid'))
            if row.get('status') == 'ok':
                identities[key] = row['start_ticks']
            identity = identities.get(key)
            groups.setdefault((*key, identity),[]).append(row)
        if row['type']=='capacity':
            caps[row['rank'],row['sample_id']]=row
    windows,marker_issues=[],[]
    if markers_path:
        mr,me=read_jsonl(Path(markers_path))
        errors.extend(me)
        windows,marker_issues=marker_windows(mr)
    rows,intervals,phases=[],[],[]
    for (kind,rank,pid,tid,start_ticks), samples in sorted(groups.items(),key=lambda item:str(item[0])):
        items=[v for a,b in zip(samples,samples[1:]) if (v:=interval(a,b)) is not None]
        for i in items:
            intervals.append(dict(i,scope=kind,rank=rank,pid=pid,tid=tid))
        row=dict(scope='process_observed' if kind=='proc_sample' else 'thread_observed',
                 rank=rank,pid=pid,tid=tid,start_ticks=start_ticks,phase=None,sequence=None,run_cpu_utilization=None,
                 **summarize_intervals(items))
        good=[s for s in samples if s['status']=='ok']
        span=(good[-1]['t_ns']-good[0]['t_ns'])/1e9 if len(good)>1 else 0
        row.update(coverage=row['observed_wall_seconds']/span if span else None,
                   missing_samples=len(samples)-len(good),
                   skipped_sample_ids=sum(max(0,b['sample_id']-a['sample_id']-1) for a,b in zip(samples,samples[1:])),
                   max_sampled_threads=max((s['num_threads'] for s in good),default=None),
                   status='ok' if items else 'insufficient_samples',
                   context_switch_scope='main_task' if kind=='proc_sample' else 'task',
                   capacity_percent=None,capacity_reason='not_applicable_to_task',
                   cycles=None,instructions=None,cpu_migrations=None,
                   perf_reason='optional_raw_diagnostic_only',
                   run_cpu_reason='process_scope_requires_markers_for_phase_attribution',
                   left_censored=True,right_censored=True)
        for key in ('voluntary_ctxt_switches','nonvoluntary_ctxt_switches'):
            vals=[i[key] for i in items]
            row[key]=sum(vals) if vals and all(v is not None for v in vals) else None
        if kind=='proc_sample':
            denom=0.
            why='insufficient_samples' if not items else None
            for i in items:
                ca=[caps.get((r,i['begin_sample']),{}) for r in range(config['ranks'])]
                cb=[caps.get((r,i['end_sample']),{}) for r in range(config['ranks'])]
                if any(capacity_signature(a)!=capacity_signature(b) for a,b in zip(ca,cb)):
                    why='capacity_changed'
                    break
                k,reason=shared_capacity(ca)
                if reason:
                    why=reason
                    break
                denom+=k*i['wall_seconds']
            row.update(capacity_percent=100*row['cpu_seconds']/denom if denom and not why else None,
                       capacity_reason=why,
                       capacity_scope='rank_contribution_to_shared_domain_upper_capacity')
            for window in windows:
                if (window['rank']!=rank or window['pid']!=pid or not good or
                    window['process_start_ticks']!=good[0]['start_ticks'] or
                    window['clock_domain_id']!=good[0]['clock_domain_id']):
                    continue
                subset=[i for i in items if i['read_begin_ns']>=window['t_ns'] and
                        i['read_end_ns']<=window['end_ns']]
                summary=summarize_intervals(subset)
                marker_wall=(window['end_ns']-window['t_ns'])/1e9
                phases.append(dict(rank=rank,phase=window['phase'],sequence=window['sequence'],
                                   marker_wall_seconds=marker_wall,
                                   coverage=summary['observed_wall_seconds']/marker_wall,**summary))
        rows.append(row)
    process_rows=[r for r in rows if r['scope']=='process_observed']
    # Process-only subtotal; thread rows are never added again. In a /proc sweep
    # ranks have distinct read timestamps: do not fabricate a synchronized peak.
    cpu=[r['cpu_seconds'] for r in process_rows if r['cpu_seconds'] is not None]
    job=dict(scope='observed_process_subtotal',cpu_seconds=sum(cpu) if cpu else None,
             ranks_observed=len({r['rank'] for r in process_rows}),ranks_expected=config['ranks'],
             status='partial',core_equivalent=None,capacity_percent=None,
             peak_core_equivalent=None,peak_window_seconds=None,
             capacity_reason='per_rank_windows_not_synchronized',reason='per_rank_windows_not_synchronized',
             sum_rank_mean_core_equivalent=sum(r['mean_core_equivalent'] for r in process_rows
                                               if r['mean_core_equivalent'] is not None) if cpu else None)
    # Supply exact job metrics only when windows really coincide, without interpolation.
    byrank={r:[i for i in intervals if i['scope']=='proc_sample' and i['rank']==r]
            for r in range(config['ranks'])}
    job_intervals=[]
    for first in byrank[0]:
        match=[next((i for i in byrank[r] if i['begin_ns']==first['begin_ns'] and
                     i['end_ns']==first['end_ns']),None) for r in byrank]
        if any(i is None for i in match):
            continue
        cs=[caps.get((r,i['begin_sample']),{}) for r,i in enumerate(match)]
        ce=[caps.get((r,i['end_sample']),{}) for r,i in enumerate(match)]
        k,why=shared_capacity(cs)
        if any(capacity_signature(a)!=capacity_signature(b) for a,b in zip(cs,ce)):
            k,why=None,'capacity_changed'
        total=sum(i['cpu_seconds'] for i in match)
        job_intervals.append(dict(cpu_seconds=total,wall_seconds=first['wall_seconds'],
                                  capacity_seconds=k*first['wall_seconds'] if k else None,reason=why))
    if job_intervals:
        total=sum(i['cpu_seconds'] for i in job_intervals)
        wall=sum(i['wall_seconds'] for i in job_intervals)
        denom=[i['capacity_seconds'] for i in job_intervals]
        peak=max(job_intervals,key=lambda i:i['cpu_seconds']/i['wall_seconds'])
        job.update(scope='common_exact_windows',cpu_seconds=total,core_equivalent=total/wall,
                   status='ok',reason=None,common_intervals=len(job_intervals),
                   peak_core_equivalent=peak['cpu_seconds']/peak['wall_seconds'],
                   peak_window_seconds=peak['wall_seconds'],
                   capacity_reason=next((i['reason'] for i in job_intervals if i['reason']),None),
                   capacity_percent=100*total/sum(denom) if all(v is not None for v in denom) else None)
    exit_path=directory/'collector-exit.json'
    exit_record=json.loads(exit_path.read_text()) if exit_path.exists() else {}
    summary=dict(schema_version=1,trial_id=config['trial_id'],scope='process_observed',
                 mappings=maps,rows=rows,job=job,phases=phases,parse_errors=errors,
                 observers=observer_summary(records),cgroups=cgroup_summary(records),
                 marker_issues=marker_issues,
                 collector_complete=(exit_record.get('returncode') == 0 and
                                     any(r['type']=='collector_end' and r['status']=='ok' for r in records)),
                 collector_exit=exit_record,
                 diagnostics=[r for r in records if r['type'].startswith('diagnostics')],
                 limitations=['initialization and exit tails are censored',
                              'short-lived threads may be missed',
                              'no run/step attribution without valid markers',
                              'CPU wait causes and library thread roles are unknown'])
    save(output/'summary.json',summary)
    save(output/'intervals.json',intervals)
    columns=sorted(set().union(*(set(r) for r in rows))) if rows else ['scope','rank','status']
    with (output/'summary.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('trial',type=Path)
    p.add_argument('--output',type=Path,help='default: trial/cpu; writes summary JSON/CSV and intervals JSON')
    p.add_argument('--markers',type=Path,help='optional schema-v1 CLOCK_MONOTONIC begin/end JSONL')
    p.add_argument('--dry-run',action='store_true',help='show paths, without reading samples or writing output')
    args=p.parse_args()
    if args.dry_run:
        print(json.dumps(dict(trial=str(args.trial),output=str(args.output) if args.output else None,
                              markers=str(args.markers) if args.markers else None)))
        return
    analyze(args.trial,args.output,args.markers)


if __name__=='__main__':
    main()
