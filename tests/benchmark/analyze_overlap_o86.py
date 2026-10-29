#!/usr/bin/env python3
"""Offline O8.6 paired walls, actual 5/10/7-launch pipelines and marker CPU."""
import argparse
from collections import defaultdict
from pathlib import Path
import sqlite3
import statistics as st

from analyze_overlap_o5 import distribution, paired_ratio, save, write_csv, telemetry_analysis
from run_benchmark import fields
from run_overlap_o86 import SCOPES, BACKENDS, VARIANTS, SCHEDULES, load, checked, main_jobs, validate_trial

KERNELS = ['find_neighbor_list_large_box', 'find_descriptor', 'find_force_radial',
           'find_partial_force_angular', 'gpu_find_force_many_body_domain']
PRODUCER_KERNELS = KERNELS[:2]+KERNELS


def pipeline(mode, ordinary=True):
    if not ordinary or mode == 'full': return KERNELS
    return PRODUCER_KERNELS if SCHEDULES[mode].startswith('producer') else KERNELS*2


def steady_groups(kernels, traces, warmup, steps):
    ordered = sorted(kernels, key=lambda k:k['start'])
    inspections = [i for i, k in enumerate(ordered) if k['name'] == 'inspect_domain_cache']
    if len(inspections) != warmup+steps or [int(t['step']) for t in traces] != list(range(1, warmup+steps+1)):
        raise ValueError('cache inspection/trace step coverage differs')
    result = []
    for ordinal, index in enumerate(inspections):
        end = next((i for i in range(index+1, len(ordered)) if ordered[i]['name'] == 'velocity_verlet_range'), None)
        if end is None or any(k['name'] == 'inspect_domain_cache' for k in ordered[index+1:end]):
            raise ValueError('missing bounded VV2 step delimiter')
        trace = traces[ordinal]
        if trace['sequence'] != ('0' if ordinal < warmup else '1'):
            raise ValueError('run segment differs')
        group = ordered[index:end+1]
        force = [k for k in group if k['name'] in KERNELS]
        if [k['name'] for k in force] not in (KERNELS, KERNELS*2, PRODUCER_KERNELS):
            raise ValueError('incomplete/reordered force pipeline')
        if ordinal >= warmup: result.append(dict(trace=trace, kernels=group, force=force))
    return result


def wall_analysis(root):
    _, plan = checked(root, offline=True)
    jobs = main_jobs(root)
    if (root/'extension-plan.json').exists(): jobs += load(root/'extension-plan.json')['jobs']
    trials = [validate_trial(root, job, 'main') for job in jobs]
    table = defaultdict(dict)
    for trial in trials:
        key = (trial['case'], trial['backend'], trial['version']+'/'+trial['mode'])
        if trial['repeat'] in table[key]: raise ValueError('duplicate paired block')
        table[key][trial['repeat']] = trial['ms_per_step']
    summaries, contrasts, extensions = [], [], set()
    for case, ranks in SCOPES:
        for backend in BACKENDS:
            same = [t for t in trials if (t['case'], t['backend']) == (case, backend)]
            if len({str(t['scientific_outputs']) for t in same}) != 1:
                raise ValueError('same-window formal scientific outputs differ')
            for version, mode in VARIANTS:
                samples = table[case, backend, version+'/'+mode]
                if len(samples) < 5: raise ValueError('fewer than five independent blocks')
                summaries.append(dict(case=case, ranks=ranks, backend=backend, version=version,
                    mode=mode, ms_per_step=distribution(list(samples.values()))))
            comparisons = [
                ('full_bridge','baseline/full','candidate/full',backend),
                ('original_split_bridge','baseline/split-serial','candidate/split-serial',backend),
                ('A_serial_bridge','baseline/producer-serial','candidate/producer-serial',backend),
                ('A_wait_bridge','baseline/producer-wait','candidate/producer-wait',backend)]
            for label,mode in [('A','producer'),('B','compact'),('C','geometry')]:
                comparisons += [
                    (label+'_split_cost','candidate/'+mode+'-serial','candidate/full',backend),
                    (label+'_recovery','candidate/'+mode+'-serial','candidate/'+mode+'-wait',backend),
                    (label+'_same_backend_net','candidate/full','candidate/'+mode+'-wait',backend),
                    (label+'_default_net','candidate/full','candidate/'+mode+'-wait','HostStaged')]
            for mode in ('serial','wait'):
                comparisons += [
                    ('B_over_A_'+mode,'candidate/producer-'+mode,'candidate/compact-'+mode,backend),
                    ('C_over_B_'+mode,'candidate/compact-'+mode,'candidate/geometry-'+mode,backend)]
            for name, numerator, denominator, numerator_backend in comparisons:
                a = table[case, numerator_backend, numerator]
                z = table[case, backend, denominator]
                # Cross-backend comparisons use complete common block IDs. An
                # extension of only one backend is reported separately below.
                common = set(a)&set(z)
                if len(common) < 5: raise ValueError('incomplete paired contrast')
                ratio = paired_ratio({k:a[k] for k in common}, {k:z[k] for k in common},
                                     seed=plan['design']['seeds']['bootstrap'])
                low, high = ratio['ci95']
                contrasts.append(dict(case=case, ranks=ranks, backend=backend, contrast=name,
                    numerator=numerator, numerator_backend=numerator_backend, denominator=denominator,
                    ratio=ratio, median_difference_ms=st.median(a[k] for k in common)-st.median(z[k] for k in common),
                    conclusion='NUMERATOR_SLOWER' if low > 1 else 'DENOMINATOR_SLOWER' if high < 1 else 'INCONCLUSIVE'))
                if (high-low)/ratio['estimate'] > .10: extensions.add((case, backend))
    result = dict(status='PASS', trials=len(trials), summaries=summaries, contrasts=contrasts,
        extension_scopes=[list(s) for s in sorted(extensions)], production_adoption='NONE',
        formal_cpu=None, limitations=['single node', 'not exclusive', 'clocks not locked',
        'physical memory residency UNKNOWN', 'pointwise intervals; no automatic adoption'])
    maintenance=[]
    for trial in trials:
        if trial['version']!='candidate' or trial['mode']=='full': continue
        groups=defaultdict(dict)
        for row in trial['execution_summaries']:
            if 'cumulative_classifications' in row:
                groups[int(row['rank'])][int(row['sequence'])]=row
        for rank,segments in groups.items():
            before,after=segments[0],segments[1]
            maintenance.append(dict(id=trial['id'],case=trial['case'],backend=trial['backend'],
                mode=trial['mode'],repeat=trial['repeat'],rank=rank,
                classifications=int(after['cumulative_classifications'])-int(before['cumulative_classifications']),
                classify_miss_ms_per_step=(float(after['cumulative_classify_seconds'])-float(before['cumulative_classify_seconds']))*1000/trial['steps']))
    save(root/'maintenance-trials.json',dict(status='PASS',ranks=maintenance,
        boundary='cache-miss host envelope only, including allocation, scan/scatter/count readback and possible preceding stream work in serial; cache-hit CPU checks measured separately in traces and included in whole-step walls; not pure GPU cost'))
    save(root/'wall-analysis.json', result)
    write_csv(root/'wall-contrasts.csv', contrasts)
    write_csv(root/'main-trials.csv', [{k:t.get(k) for k in ('id', 'case', 'ranks', 'backend', 'version',
        'mode', 'repeat', 'warmup', 'steps', 'seconds', 'ms_per_step', 'formal_cpu', 'binary_sha256')} for t in trials])
    telemetry_analysis(root, trials)
    print('PASS O8.6 formal walls:', len(trials), flush=True)
    return result


def profile_trial(directory, job):
    db = sqlite3.connect(f'file:{directory}/trace.sqlite?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    ranks, all_rows = [], []
    try:
        names = dict(db.execute('select id,value from StringIds'))
        tids = dict(db.execute('select rank,globalTid from MPI_RANKS'))
        if set(tids) != set(range(job['ranks'])): raise ValueError('missing MPI rank mapping')
        for rank, tid in tids.items():
            pid = tid//(1 << 24)*(1 << 24)
            kernels = [dict(k) for k in db.execute('select * from CUPTI_ACTIVITY_KIND_KERNEL where globalPid=? order by start', (pid,))]
            for k in kernels: k['name'] = names[k['shortName']]
            text = (directory/'rank-output'/f'rank-{rank}.stdout').read_text()
            traces = [fields(s) for s in text.splitlines() if s.startswith('DMGMD_DOMAIN_TRACE rank=')]
            parts = {int(r['step']):r for s in text.splitlines() if s.startswith('DMGMD_PARTITION rank=') for r in [fields(s)]}
            groups = steady_groups(kernels, traces, job['warmup'], job['steps'])
            runtime = {r['correlationId']:dict(r) for r in db.execute(
                'select correlationId,start,end from CUPTI_ACTIVITY_KIND_RUNTIME where globalTid=?', (tid,))}
            per_kernel, host, counts = defaultdict(list), defaultdict(list), defaultdict(int)
            selections=[]
            maintenance_device=defaultdict(list)
            for group in groups:
                trace, force = group['trace'], group['force']
                step, ordinary = int(trace['step']), trace['class'] == 'ordinary'
                expected = pipeline(job['mode'], ordinary)
                if [k['name'] for k in force] != expected:
                    raise ValueError(f'actual launch pipeline mismatch: {job["id"]}/rank{rank}/step{step}')
                counts[trace['class']] += 1
                if not ordinary: continue
                part = parts.get(step)
                if job['mode'] != 'full' and (not part or part['mode'] != 'split'):
                    raise ValueError('fallback cannot count as candidate execution')
                if part and job['schedule'].startswith('producer') and part.get('schedule') != job['schedule']:
                    raise ValueError('producer schedule not executed')
                for i, k in enumerate(force):
                    phase = 'full'
                    if len(force) == 10: phase = 'early' if i < 5 else 'late'
                    if len(force) == 7: phase = 'early' if i < 2 else 'late' if i < 4 else 'full'
                    api = runtime.get(k['correlationId'])
                    if not api: raise ValueError('missing matching CPU launch API')
                    elapsed = (k['end']-k['start'])/1e6
                    per_kernel[k['name']+'/'+phase].append(elapsed)
                    host['launch_api_ms'].append((api['end']-api['start'])/1e6)
                    if len(force) == 7 and i >= 4 and (k['gridX']*k['blockX'] < int(part['owned' if i != 5 else 'dependency'])):
                        raise ValueError('force launch lacks full center range')
                    if len(force)==7 and i<4:
                        expected_grid=(int(part['early' if i<2 else 'late'])+63)//64 if job['schedule'] in ('producer-compact','producer-geometry') else (int(part['dependency'])+63)//64
                        if k['gridX']!=expected_grid or k['blockX']!=64:
                            raise ValueError('actual producer grid mismatch')
                        if job['schedule'] in ('producer-compact','producer-geometry'):
                            symbol=names[k['demangledName']]
                            if '<true>' not in symbol and '<(bool)1>' not in symbol:
                                raise ValueError('compact specialization not observed: '+symbol)
                    all_rows.append(dict(id=job['id'], case=job['case'], ranks=job['ranks'], backend=job['backend'],
                        version=job['version'], mode=job['mode'], rank=rank, step=step, kernel=k['name'],
                        phase=phase, duration_ms=elapsed, launch_api_ms=(api['end']-api['start'])/1e6,
                        stream=k['streamId'], grid=[k['gridX'], k['gridY'], k['gridZ']],
                        block=[k['blockX'], k['blockY'], k['blockZ']]))
                for kernel in group['kernels']:
                    if kernel['name'] in ('safe_rows','closed_rows','count_early','geometry_rows','scatter_centers') or 'Scan' in kernel['name']:
                        maintenance_device[kernel['name']].append((kernel['end']-kernel['start'])/1e6)
                if part:
                    selections.append(dict(step=step,**{k:part[k] for k in ('schedule','early','interior','owned','dependency','local','classified','classify_host_seconds','early_grid','late_grid','late','inset','partition_bytes','partition_capacity_bytes','list_temp_bytes') if k in part}))
                    for name in ('early','owned','dependency','late','interior'):
                        host[name+'_count'].append(int(part.get(name,0)))
                    for name,num,den in [('E_D','early','dependency'),('H_D','late','dependency'),('E_O','early','owned')]:
                        host[name].append(int(part.get(num, int(part['dependency'])-int(part['early']))) / max(1,int(part[den])))
                    if len(force)==7:
                        host['early_gpu_ms'].append(sum((k['end']-k['start'])/1e6 for k in force[:2]))
                    host['classify_'+('miss' if part['classified']=='1' else 'hit')+'_ms'].append(float(part['classify_host_seconds'])*1000)
                    for name in ('partition_bytes','partition_capacity_bytes','list_temp_bytes','inset'):
                        if name in part: host[name].append(float(part[name]))
                    host['classify_ms'].append(float(part['classify_host_seconds'])*1000)
                    counts['classifications'] += int(part['classified'])
                    host['mask_logical_bytes'].append(int(part.get('partition_bytes',12*int(part['local'])+12)))
                for name, begin, end in [('input_sync', 'input_sync_begin_ns', 'input_sync_end_ns'),
                    ('output_sync', 'output_sync_begin_ns', 'output_sync_end_ns'),
                    ('mpi_waitall', 'mpi_waitall_begin_ns', 'mpi_waitall_end_ns')]:
                    if int(trace[begin]) and int(trace[end]): host[name+'_ms'].append((int(trace[end])-int(trace[begin]))/1e6)
                for key in ('collective_calls', 'halo_send_bytes_local', 'halo_recv_bytes_local'):
                    host[key].append(int(trace[key]))
            if not counts['ordinary']: raise ValueError('no steady ordinary steps')
            # All five/seven/ten basic launches must share the original stream.
            streams = {k['streamId'] for g in groups for k in g['force']}
            defaults={k['streamId'] for g in groups for k in g['kernels'] if k['name'] in
                      ('inspect_domain_cache','velocity_verlet_range','clear_owned_properties')}
            if len(streams)!=1 or streams!=defaults:
                raise ValueError('force pipeline differs from original default integration stream')
            ranks.append(dict(rank=rank, counts=dict(counts), basic_launches=pipeline(job['mode']),
                selection_steps=selections,
                maintenance_device={k:dict(count=len(v),total_ms=sum(v),mean_ms=st.mean(v)) for k,v in maintenance_device.items()},
                kernels_ms_mean={k:st.mean(v) for k, v in per_kernel.items()},
                host={k:distribution(v) for k, v in host.items()},
                launch_api_ms_per_ordinary_step=sum(host['launch_api_ms'])/counts['ordinary']))
        return dict(job=job, status='PASS', ranks=ranks), all_rows
    finally:
        db.close()


def profile_analysis(root):
    _, plan = checked(root, offline=True)
    reports, rows, science = [], [], {}
    for job in plan['profiles']:
        result = validate_trial(root, job, 'profiles')
        key = (job['case'], job['backend'])
        if key in science and science[key] != result['scientific_outputs']:
            raise ValueError('diagnostic scientific output differs')
        science[key] = result['scientific_outputs']
        report, raw = profile_trial(root/'profiles'/job['id'], job)
        reports.append(report); rows.extend(raw)
    mapping={}
    for report in reports:
        job=report['job']
        for rank in report['ranks']:
            if job['version']!='candidate' or job['mode'] not in ('producer-serial','compact-serial','producer-wait','compact-wait'): continue
            key=(job['case'],job['backend'],job['mode'].split('-')[-1],rank['rank'])
            values=[{k:s[k] for k in ('step','early','interior','owned','dependency','local')} for s in rank['selection_steps']]
            if key in mapping and mapping[key]!=values: raise ValueError('A/B did not use the same center counts')
            mapping[key]=values
    save(root/'kernel-trials.json', dict(status='PASS', reports=reports))
    write_csv(root/'partition-steps.csv',[dict(id=r['job']['id'],case=r['job']['case'],backend=r['job']['backend'],
        mode=r['job']['mode'],rank=rank['rank'],**s) for r in reports for rank in r['ranks'] for s in rank['selection_steps']])
    write_csv(root/'kernel-steps.csv', rows)
    print('PASS O8.6 actual pipeline traces:', len(reports), flush=True)


def cpu_analysis(root):
    _, plan = checked(root, offline=True)
    reports = []
    for job in plan['cpu']:
        validate_trial(root, job, 'cpu')
        summary = load(root/'cpu'/job['id']/'cpu/summary.json')
        formal = [p for p in summary['phases'] if p['phase'] == 'run' and p['sequence'] == 1]
        if not summary['collector_complete'] or summary['marker_issues'] or len(formal) != job['ranks']:
            raise ValueError('incomplete formal CPU markers')
        if any(p['coverage'] < .8 or p['mean_core_equivalent'] is None for p in formal):
            raise ValueError('incomplete marker CPU coverage')
        reports.append(dict(job=job, formal_rank_windows=formal, synchronized_job_cpu=None,
            reason='rank /proc read windows differ; no synchronized subtotal invented'))
    save(root/'cpu-analysis.json', dict(status='PASS', reports=reports))
    print('PASS O8.6 marker CPU diagnostics:', len(reports), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['walls', 'profiles', 'cpu', 'all'])
    p.add_argument('--root', type=Path, required=True)
    args = p.parse_args(); root = args.root.resolve()
    if args.phase in ('walls', 'all'): wall_analysis(root)
    if args.phase in ('profiles', 'all'): profile_analysis(root)
    if args.phase in ('cpu', 'all'): cpu_analysis(root)


if __name__ == '__main__': main()
