#!/usr/bin/env python3
"""Matched short uninstrumented controls, separate from O6 main performance."""
import argparse
import fcntl
from pathlib import Path
import random
import statistics as st

import o5_rank_output
from run_overlap_o5 import freeze
from run_overlap_o6 import identity_check, load, SCOPES, MODES
from analyze_overlap_o5 import distribution, save, write_csv
from cpu_binding import digest


def analyze(root):
    plan = load(root/'matched-control-plan.json')
    rows = []
    for entry in plan['jobs']:
        job, source = entry['job'], entry['profile_id']
        result = load(root/'matched-controls'/job['id']/'result.json')
        profile = load(root/'profiles'/source/'result.json')
        if result['status'] != 'PASS' or result['scientific_outputs'] != profile['scientific_outputs']:
            raise ValueError('matched control execution/scientific output differs')
        rows.append(dict(case=job['case'], ranks=job['ranks'], mode=job['mode'], repeat=job['repeat'],
            id=job['id'], profile_id=source, control_ms_per_step=result['ms_per_step'],
            profile_ms_per_step=profile['ms_per_step'], scientific_output_matches=True))
    summaries = []
    for case, ranks in SCOPES:
        for mode in MODES:
            samples = [r for r in rows if (r['case'],r['ranks'],r['mode'])==(case,ranks,mode)]
            summaries.append(dict(case=case, ranks=ranks, mode=mode,
                control=distribution([r['control_ms_per_step'] for r in samples]),
                profile=distribution([r['profile_ms_per_step'] for r in samples]),
                profile_over_control=st.median(r['profile_ms_per_step'] for r in samples)/st.median(r['control_ms_per_step'] for r in samples)))
    save(root/'matched-control-analysis.json', dict(status='PASS', rows=rows, summaries=summaries,
        scope='three short diagnostic controls; not main performance; no CI from n<5; clocks unlocked and phases not interleaved'))
    write_csv(root/'matched-control-trials.csv', rows)
    print('MATCHED_CONTROLS PASS', len(rows), flush=True)


def collect(root):
    experiment, base = identity_check(root)
    if load(root/'analysis-status.json')['profiles'] != 'PASS':
        raise ValueError('completed profiles required before matched controls')
    entries = []
    for original in base['profiles']:
        job = dict(original, id='control-'+original['id'])
        entries.append(dict(job=job, profile_id=original['id']))
    random.Random(2026102109).shuffle(entries)
    freeze(root/'matched-control-plan.json', dict(identity=base['identity'], tool_sha256=digest(__file__),
        order_seed=2026102109, jobs=entries, main_performance_sample=False,
        reason='exact same warmup/128-step windows as Nsight; quantify perturbation and check scientific files',
        statistics='three independent short processes per cell; descriptive medians/ranges; primary conclusions use five long blocks'))
    o5_rank_output.install()
    for entry in entries:
        result = experiment.trial('matched-controls', entry['job'])
        if result['status'] != 'PASS':
            raise RuntimeError('control failed; retain original attempt')
    analyze(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--analyze-only', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.analyze_only:
        analyze(root)
    else:
        with (root/'scheduler.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            collect(root)
