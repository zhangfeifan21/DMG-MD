#!/usr/bin/env python3
"""Recompute O6 paired walls and actual steady CUDA execution from raw reports."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3
import statistics as st

from analyze_overlap_o5 import distribution, paired_ratio, write_csv, save
from run_overlap_o6 import KERNELS, SCOPES, MODES, load
from run_benchmark import fields


def steady_groups(kernels, traces, warmup, steps):
    """Map by ordered device work, never by splicing host clock domains.

    Each run segment computes one setup force, then one thermo per step.
    Exact counts and five-kernel sequences must agree before any rows are used.
    """
    ordered = sorted(kernels, key=lambda k:k['start'])
    starts = [k['start'] for k in ordered]
    norms = [k for k in ordered if k['name'] == 'normalize_global_thermo']
    if len(norms) != warmup + steps:
        raise ValueError(f'thermo launch count {len(norms)} != {warmup+steps}')
    formal = [r for r in traces if r['sequence'] == '1']
    if len(formal) != steps or len(traces) != warmup+steps:
        raise ValueError('incomplete or unexpected per-step trace records')
    expected_steps = list(range(warmup+1, warmup+steps+1))
    if [int(r['step']) for r in formal] != expected_steps:
        raise ValueError('formal step order differs from frozen input')
    result = []
    for i, trace in enumerate(formal):
        index = warmup+i
        # Consecutive normalizations delimit one complete force pipeline.
        previous, end = norms[index-1]['end'], norms[index]['end']
        group = [k for k in ordered[bisect_left(starts,previous):bisect_right(starts,end)]
                 if k['end'] <= end]
        # The first formal step follows a separate setup force. Locate the
        # actual step's cache inspection and preceding first Verlet launch.
        inspections = [k for k in group if k['name']=='inspect_domain_cache']
        if len(inspections) != 1:
            raise ValueError('step must contain exactly one cache inspection')
        begin = max(k['start'] for k in group if k['name']=='velocity_verlet_range'
                    and k['start'] < inspections[0]['start'])
        group = [k for k in group if k['start'] >= begin]
        force = [k for k in group if k['name'] in KERNELS]
        names = [k['name'] for k in force]
        if names not in (KERNELS, KERNELS*2):
            raise ValueError(f'unexpected force pipeline at step {trace["step"]}: {names}')
        result.append(dict(trace=trace, kernels=group, force=force))
    return result


def profile_trial(directory, spec):
    meta = load(directory/'result.json')
    if meta['status'] != 'PASS':
        raise ValueError(f'failed profile: {directory}')
    if any(meta.get(key) != value for key, value in spec.items()):
        raise ValueError('profile configuration differs from frozen spec')
    db = sqlite3.connect(f'file:{directory}/trace.sqlite?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        names = dict(db.execute('select id,value from StringIds'))
        rank_tids = {r['rank']:r['globalTid'] for r in db.execute('select * from MPI_RANKS')}
        if set(rank_tids) != set(range(spec['ranks'])):
            raise ValueError('Nsight MPI rank mapping incomplete')
        rows, rank_reports, ncu_windows = [], [], []
        for rank, tid in rank_tids.items():
            pid = (tid//(1 << 24))*(1 << 24)
            kernels = [dict(r) for r in db.execute(
                'select * from CUPTI_ACTIVITY_KIND_KERNEL where globalPid=? order by start', (pid,))]
            for kernel in kernels:
                kernel['name'] = names[kernel['shortName']]
            text = (directory/'rank-output'/f'rank-{rank}.stdout').read_text()
            traces = [fields(s) for s in text.splitlines() if s.startswith('DMGMD_DOMAIN_TRACE rank=')]
            partitions = {int(r['step']):r for s in text.splitlines() if s.startswith('DMGMD_PARTITION rank=')
                          for r in [fields(s)]}
            groups = steady_groups(kernels, traces, spec['warmup'], spec['steps'])
            correlations = {k['correlationId'] for group in groups if group['trace']['class']=='ordinary'
                            for k in group['force']}
            if not correlations:
                raise ValueError('empty steady ordinary window')
            runtime = {r['correlationId']:dict(r) for r in db.execute(
                'select correlationId,start,end from CUPTI_ACTIVITY_KIND_RUNTIME '
                'where globalTid=? and correlationId between ? and ?',
                (tid,min(correlations),max(correlations)))}
            ordinary, per_kernel, phase_totals = [], defaultdict(list), defaultdict(list)
            launch_api = []
            for group in groups:
                trace, force = group['trace'], group['force']
                step = int(trace['step'])
                split = len(force) == 10
                expected_split = spec['mode'] != 'full' and trace['class'] == 'ordinary'
                partition = partitions.get(step)
                if split != expected_split or (partition and (partition['mode']=='split') != split):
                    raise ValueError(f'actual pipeline differs from requested ordinary/fallback behavior: rank {rank}, step {step}')
                if rank == 0 and step == spec['warmup']+64:
                    skip = sum(k['name'] in KERNELS and k['start'] < force[0]['start'] for k in kernels)
                    ncu_windows.append(dict(rank=rank, step=step, launch_skip=skip, launch_count=len(force),
                        expected_kernels=[k['name'] for k in force],
                        ordinary=trace['class']=='ordinary', source_profile=spec['id']))
                if trace['class'] != 'ordinary':
                    continue
                ordinary.append(trace)
                for index, k in enumerate(force):
                    phase = ('early' if index < 5 else 'late') if split else 'full'
                    duration_ms = (k['end']-k['start'])/1e6
                    per_kernel[(k['name'], phase)].append(duration_ms)
                    api = runtime.get(k['correlationId'])
                    if api is None:
                        raise ValueError('kernel lacks matching CPU CUDA launch API')
                    launch_api.append((api['end']-api['start'])/1e6)
                    rows.append(dict(id=spec['id'], case=spec['case'], ranks=spec['ranks'], mode=spec['mode'],
                        repeat=spec['repeat'], rank=rank, step=step, kernel=k['name'], phase=phase,
                        duration_ms=duration_ms, cuda_launch_api_ms=(api['end']-api['start'])/1e6,
                        start_ns=k['start'], end_ns=k['end'], correlation_id=k['correlationId'],
                        grid=[k['gridX'], k['gridY'], k['gridZ']], block=[k['blockX'], k['blockY'], k['blockZ']],
                        registers_per_thread=k['registersPerThread'], local_memory_per_thread=k['localMemoryPerThread'],
                        stream=k['streamId'], interior=partition.get('interior') if partition else None,
                        early=partition.get('early') if partition else None,
                        owned=partition.get('owned') if partition else None,
                        dependency=partition.get('dependency') if partition else None))
                phase_totals['force_kernel_sum_ms'].append(sum((k['end']-k['start'])/1e6 for k in force))
                phase_totals['other_gpu_kernel_sum_ms'].append(sum((k['end']-k['start'])/1e6 for k in group['kernels'] if k['name'] not in KERNELS))
            if not ordinary:
                raise ValueError('empty steady ordinary window')
            host_intervals = [('input_sync', 'input_sync_begin_ns', 'input_sync_end_ns'),
                              ('output_sync', 'output_sync_begin_ns', 'output_sync_end_ns'),
                              ('mpi_waitall', 'mpi_waitall_begin_ns', 'mpi_waitall_end_ns'),
                              ('decision', 'decision_begin_ns', 'decision_end_ns')]
            host = {}
            for name, start, end in host_intervals:
                values = [(int(t[end])-int(t[start]))/1e6 for t in ordinary if int(t[start]) and int(t[end])]
                host[name+'_ms_mean'] = st.mean(values) if values else None
            host['mpi_call_ms_mean'] = st.mean(float(t['mpi_call_seconds_local'])*1000 for t in ordinary)
            host['halo_send_bytes_mean'] = st.mean(int(t['halo_send_bytes_local']) for t in ordinary)
            host['collective_calls_mean'] = st.mean(int(t['collective_calls']) for t in ordinary)
            ps = [partitions[int(t['step'])] for t in ordinary if int(t['step']) in partitions]
            host['classifications'] = sum(int(p['classified']) for p in ps)
            host['classify_host_ms_mean'] = st.mean(float(p['classify_host_seconds'])*1000 for p in ps) if ps else None
            host['classification_cache_hits'] = sum(not int(p['classified']) for p in ps)
            rank_reports.append(dict(rank=rank, ordinary_steps=len(ordinary), rebuild_steps=spec['steps']-len(ordinary),
                kernel_ms_mean={f'{name}/{phase}':st.mean(values) for (name, phase), values in per_kernel.items()},
                kernel_ms_median={f'{name}/{phase}':st.median(values) for (name, phase), values in per_kernel.items()},
                phase_totals_ms_mean={key:st.mean(values) for key, values in phase_totals.items()},
                force_launches_per_step=5 if spec['mode']=='full' else 10,
                cuda_launch_api_ms_per_step=sum(launch_api)/len(ordinary), host=host))
        return dict(spec=spec, status='PASS', ranks=rank_reports, ncu_windows=ncu_windows), rows
    finally:
        db.close()


def main_analysis(root, plan):
    trials, missing = [], []
    for spec in plan['main']:
        path = root/'main'/spec['id']/'result.json'
        if not path.exists():
            missing.append(spec['id']); continue
        trial = load(path)
        if trial['status'] != 'PASS':
            missing.append(spec['id']); continue
        if any(trial.get(key) != value for key, value in spec.items()):
            raise ValueError('main configuration differs from frozen spec')
        if trial['binary_sha256'] != plan['identity']['binaries']['o4']['sha256'] or trial['seconds'] < plan['main_minimum_seconds']:
            raise ValueError('invalid binary identity or too-short formal trial')
        trials.append(trial)
    science = []
    for trial in trials:
        refs = [t for t in trials if (t['case'], t['ranks'], t['repeat'], t['mode']) ==
                (trial['case'], trial['ranks'], trial['repeat'], 'full')]
        if not refs:
            continue
        science.append(dict(id=trial['id'], passed=trial['scientific_outputs']==refs[0]['scientific_outputs']))
    summaries, ratios, extension = [], [], []
    for case, ranks in SCOPES:
        group = [t for t in trials if (t['case'], t['ranks']) == (case, ranks)]
        by_mode = {mode:{t['repeat']:t['ms_per_step'] for t in group if t['mode']==mode} for mode in MODES}
        for mode, values in by_mode.items():
            summaries.append(dict(case=case, ranks=ranks, mode=mode, **distribution(list(values.values()))))
        for numerator, denominator in [('split-serial','full'), ('split-serial','overlap-wait'), ('full','overlap-wait')]:
            ratio = paired_ratio(by_mode[numerator], by_mode[denominator], seed=plan['statistics']['seed'])
            ratios.append(dict(case=case, ranks=ranks, numerator=numerator, denominator=denominator, **ratio))
            if ratio['ci95'] and (ratio['ci95'][1]-ratio['ci95'][0])/ratio['estimate'] > .1:
                extension.append([case, ranks])
    result = dict(status='PARTIAL' if missing or extension or not all(s['passed'] for s in science) else 'PASS',
                  missing=missing, extension_scopes=sorted(set(map(tuple,extension))),
                  trials=len(trials), scientific_file_checks=science, summaries=summaries, ratios=ratios)
    save(root/'wall-analysis.json', result)
    write_csv(root/'main-trials.csv', [{k:t.get(k) for k in ('id','case','ranks','mode','repeat','steps','warmup','seconds','ms_per_step','science_sha256','formal_cpu')} for t in trials])
    write_csv(root/'wall-summary.csv', summaries)
    write_csv(root/'wall-ratios.csv', ratios)
    return result


def analyze(root, profiles_only=False):
    plan = load(root/'frozen-plan.json')
    reports, rows, missing = [], [], []
    for spec in plan['profiles']:
        directory = root/'profiles'/spec['id']
        if not (directory/'trace.sqlite').exists():
            missing.append(spec['id']); continue
        report, raw = profile_trial(directory, spec)
        reports.append(report); rows.extend(raw)
    science = []
    for report in reports:
        spec = report['spec']
        refs = [r for r in reports if (r['spec']['case'],r['spec']['ranks'],r['spec']['repeat'],r['spec']['mode'])==
                (spec['case'],spec['ranks'],spec['repeat'],'full')]
        if refs:
            result = load(root/'profiles'/spec['id']/'result.json')
            reference = load(root/'profiles'/refs[0]['spec']['id']/'result.json')
            passed = result['scientific_outputs'] == reference['scientific_outputs']
            science.append(dict(id=spec['id'], passed=passed))
            if not passed:
                raise ValueError('profile scientific output differs from same-window full')
    save(root/'kernel-trials.json', dict(status='PARTIAL' if missing else 'PASS', reports=reports,
                                        scientific_file_checks=science, missing=missing))
    write_csv(root/'kernel-steps.csv', rows)
    summaries = []
    for case, ranks in SCOPES:
        for mode in MODES:
            group = [r for r in reports if (r['spec']['case'], r['spec']['ranks'], r['spec']['mode']) == (case, ranks, mode)]
            keys = sorted({key for r in group for rank in r['ranks'] for key in rank['kernel_ms_mean']})
            for key in [*keys, 'force_kernel_sum_ms', 'other_gpu_kernel_sum_ms', 'cuda_launch_api_ms_per_step']:
                values = []
                for report in group:
                    if key in keys:
                        value = st.mean(rank['kernel_ms_mean'][key] for rank in report['ranks'])
                    elif key == 'cuda_launch_api_ms_per_step':
                        value = st.mean(rank[key] for rank in report['ranks'])
                    else:
                        value = st.mean(rank['phase_totals_ms_mean'][key] for rank in report['ranks'])
                    values.append(value)
                summaries.append(dict(case=case, ranks=ranks, mode=mode, metric=key,
                                      aggregation='mean ordinary steps per rank; rank mean per independent trial',
                                      **distribution(values)))
    save(root/'kernel-summary.json', summaries)
    write_csv(root/'kernel-summary.csv', summaries)
    save(root/'ncu-windows.json', [dict(report['spec'], **window) for report in reports if report['spec']['repeat']==0 for window in report['ncu_windows']])
    if not profiles_only:
        walls = main_analysis(root, plan)
        if (root/'ncu-plan.json').exists():
            from profile_overlap_o6_ncu import analyze as analyze_ncu
            analyze_ncu(root)
        if (root/'matched-control-plan.json').exists():
            from run_overlap_o6_controls import analyze as analyze_controls
            analyze_controls(root)
        save(root/'analysis-status.json', dict(walls=walls['status'], profiles='PARTIAL' if missing else 'PASS',
            matched_controls='NOT_ANALYZED' if not (root/'matched-control-analysis.json').exists() else load(root/'matched-control-analysis.json')['status'],
            hardware_metrics='NOT_ANALYZED' if not (root/'ncu-analysis.json').exists() else load(root/'ncu-analysis.json')['status']))
    summarize(root, reports)
    print('PROFILE_TRIALS', len(reports), 'MISSING',len(missing), flush=True)


def summarize(root, reports):
    """Human-readable tables; sums are formed per trial before taking medians."""
    lines = ['# O6 raw-data summary', '', 'Actual CUPTI kernels, ms/ordinary step. '
             'Rank means within each trial; median across independent trials.', '']
    for case, ranks in SCOPES:
        lines += [f'## {case}/P{ranks}', '', '| kernel | full | serial early+late | wait early+late |',
                  '| --- | ---: | ---: | ---: |']
        for name in KERNELS:
            values = []
            for mode in MODES:
                samples = [st.mean(sum(value for key,value in rank['kernel_ms_mean'].items()
                                       if key.startswith(name+'/')) for rank in report['ranks'])
                           for report in reports if (report['spec']['case'],report['spec']['ranks'],report['spec']['mode'])==(case,ranks,mode)]
                values.append(f'{st.median(samples):.6f}' if samples else 'N/A')
            lines.append('| '+ ' | '.join([name,*values])+' |')
        lines += ['']
    path = root/'wall-analysis.json'
    if path.exists():
        wall = load(path)
        lines += ['## Unprofiled walls', '', '| case | P | mode | n | median ms/step | min | max |',
                  '| --- | ---: | --- | ---: | ---: | ---: | ---: |']
        for row in wall['summaries']:
            if row['n']:
                lines.append(f'| {row["case"]} | {row["ranks"]} | {row["mode"]} | {row["n"]} | {row["median"]:.6f} | {row["min"]:.6f} | {row["max"]:.6f} |')
        lines += ['', '| case | P | numerator/denominator | n | ratio | CI95 |',
                  '| --- | ---: | --- | ---: | ---: | --- |']
        for row in wall['ratios']:
            if row['estimate'] is not None:
                lines.append(f'| {row["case"]} | {row["ranks"]} | {row["numerator"]}/{row["denominator"]} | {row["n"]} | {row["estimate"]:.6f} | {row["ci95"]} |')
    (root/'summary.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--profiles-only', action='store_true')
    args = parser.parse_args()
    analyze(args.root.resolve(), args.profiles_only)
