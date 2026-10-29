#!/usr/bin/env python3
"""Recompute O6.5 execution coverage, steady activity and paired contrasts."""
import argparse
from array import array
from collections import Counter
import csv
import json
from pathlib import Path
import statistics as st
import struct

import analyze_overlap_o6 as o6
from analyze_overlap_o5 import distribution, paired_ratio, save, write_csv
from run_benchmark import fields
from run_overlap_o65 import SCOPES, MODES, load, main_jobs
from cpu_binding import digest


def steady_groups(kernels, traces, warmup, steps):
    """O7+O8 mapping: one cache inspection and two Verlet kernels per step.

    Thermo is demand-driven and is not a step delimiter. Setup forces are
    excluded by starting at cache inspection and ending at the following VV2.
    """
    ordered = sorted(kernels, key=lambda k: k['start'])
    inspections = [i for i, k in enumerate(ordered) if k['name'] == 'inspect_domain_cache']
    if len(inspections) != warmup+steps or len(traces) != warmup+steps:
        raise ValueError('cache inspection / step trace count differs')
    expected = list(range(1, warmup+steps+1))
    if [int(t['step']) for t in traces] != expected:
        raise ValueError('unexpected global step order')
    result = []
    for ordinal, index in enumerate(inspections):
        trace = traces[ordinal]
        if trace['sequence'] != ('0' if ordinal < warmup else '1'):
            raise ValueError('run segment differs from step order')
        end = next((i for i in range(index+1, len(ordered))
                    if ordered[i]['name'] == 'velocity_verlet_range'), None)
        if end is None:
            raise ValueError('missing second Verlet')
        group = ordered[index:end+1]
        if sum(k['name'] == 'inspect_domain_cache' for k in group) != 1:
            raise ValueError('step lacks bounded VV2 delimiter')
        force = [k for k in group if k['name'] in o6.KERNELS]
        if [k['name'] for k in force] not in (o6.KERNELS, o6.KERNELS*2):
            raise ValueError('incomplete force pipeline')
        if ordinal >= warmup:
            result.append(dict(trace=trace, kernels=group, force=force))
    return result


def read_array(path, code):
    with Path(path).open('rb') as stream:
        count, = struct.unpack('<Q', stream.read(8))
        result = array(code)
        result.fromfile(stream, count)
        if stream.read(1):
            raise ValueError('unexpected trailing snapshot data')
    return result


def coverage(mask, count, width, complement=False):
    if width not in (32, 64) or count < 0 or len(mask) < count:
        raise ValueError('invalid CUDA coverage dimensions')
    histogram = Counter()
    for start in range(0, count, width):
        active = sum(bool(mask[c]) != complement for c in range(start, min(start+width, count)))
        histogram[active] += 1
    total = sum(histogram.values())
    return dict(width=width, groups=total, nonempty=total-histogram[0],
        empty=histogram[0], full=histogram[width], mixed=total-histogram[0]-histogram[width],
        active_lanes=sum(k*v for k, v in histogram.items()),
        active_lane_histogram=dict(sorted(histogram.items())),
        padded_lanes=(-count) % width,
        definition='full means all hardware lanes; tail padding is inactive; static coverage, not achieved occupancy')


def snapshot_report(directory):
    N, O, D, layout, neighbor, workspace, step, call, mr, ma, dim, sums_dim = map(
        int, (directory/'meta.txt').read_text().split())
    if not 0 < O <= D <= N:
        raise ValueError('invalid O/D/L')
    masks = {key: read_array(directory/(key+'.bin'), 'i') for key in ('interior', 'early')}
    gid = read_array(directory/'gid.bin', 'Q')
    vnn = read_array(directory/'verlet_nn.bin', 'i')
    vnl = read_array(directory/'verlet_nl.bin', 'i')
    counts = {key: read_array(directory/('nn_'+key+'.bin'), 'i') for key in ('radial', 'angular')}
    # Re-derive the two-hop proof from the saved actual Verlet graph. The
    # production arrays are never sorted or modified by this analysis.
    safe = [False]*D
    capacity = len(vnl)//N
    for c in range(D):
        if not 0 <= vnn[c] <= capacity:
            raise ValueError('invalid Verlet count')
        previous = -1
        owned_row = c < O
        for e in range(vnn[c]):
            j = vnl[e*N+c]
            if not 0 <= j < N or j == c or gid[j] <= previous:
                raise ValueError('invalid / unsorted Verlet row')
            previous = gid[j]
            owned_row &= j < O
        safe[c] = owned_row
    interior = [False]*O
    early = [False]*D
    for c in range(O):
        if safe[c] and all(safe[vnl[e*N+c]] for e in range(vnn[c])):
            interior[c] = True
            early[c] = True
            for e in range(vnn[c]):
                early[vnl[e*N+c]] = True
    if any(bool(masks['interior'][c]) != interior[c] for c in range(O)) or any(
            bool(masks['early'][c]) != early[c] for c in range(D)):
        raise ValueError('saved masks differ from actual graph proof')
    phases = []
    for key, limit, names in [('interior', O, ('I', 'B')), ('early', D, ('E', 'H'))]:
        for complement, name in enumerate(names):
            selected = [c for c in range(limit) if bool(masks[key][c]) != bool(complement)]
            phases.append(dict(phase=name, count=len(selected), center_fraction=len(selected)/limit,
                warp=coverage(masks[key], limit, 32, bool(complement)),
                block=coverage(masks[key], limit, 64, bool(complement)),
                neighbors={kind: dict(edges=sum(nn[c] for c in selected),
                    count_histogram=dict(sorted(Counter(nn[c] for c in selected).items())))
                    for kind, nn in counts.items()}))
    # Validate actual filtered row domains and gid order before measuring work.
    for kind, maximum in [('radial', mr), ('angular', ma)]:
        rows = read_array(directory/('nl_'+kind+'.bin'), 'i')
        for c in range(D):
            previous = -1
            if not 0 <= counts[kind][c] <= maximum:
                raise ValueError('invalid filtered count')
            for e in range(counts[kind][c]):
                j = rows[e*N+c]
                if not 0 <= j < N or gid[j] <= previous:
                    raise ValueError('invalid filtered row')
                if c < O and j >= D:
                    raise ValueError('force consumer lacks neighbor producer')
                previous = gid[j]
    return dict(snapshot=str(directory), O=O, D=D, L=N, layout=layout,
        neighbor_generation=neighbor, workspace_generation=workspace, step=step, call=call,
        graph_proof='PASS', phases=phases,
        arrays={p.name:dict(bytes=p.stat().st_size, sha256=digest(p)) for p in sorted(directory.iterdir())})


def profile_analysis(root):
    frozen = load(root/'frozen-design.json')
    # Reuse the tested CUPTI/CPU API matching and trial aggregation, replacing
    # only the old every-step-thermo delimiter with the audited O8 delimiter.
    o6.steady_groups = steady_groups
    o6.SCOPES = SCOPES
    plan = dict(profiles=frozen['profiles'], identity=frozen['identity'])
    save(root/'profile-analysis-plan.json', plan)
    reports, rows = [], []
    for job in frozen['profiles']:
        report, raw = o6.profile_trial(root/'profiles'/job['id'], job)
        reports.append(report)
        rows.extend(raw)
    for report in reports:
        spec = report['spec']
        reference = next(j for j in frozen['profiles'] if (j['case'], j['repeat'], j['mode']) ==
                         (spec['case'], spec['repeat'], 'full'))
        if load(root/'profiles'/spec['id']/'result.json')['scientific_outputs'] != load(
                root/'profiles'/reference['id']/'result.json')['scientific_outputs']:
            raise ValueError('same-window profile scientific files differ')
    save(root/'kernel-trials.json', dict(status='PASS', reports=reports))
    write_csv(root/'kernel-steps.csv', rows)
    o6.summarize(root, reports)
    # Freeze a new, actual ordinary-step window; never reuse old launch skips.
    captures = []
    for case, ranks in SCOPES:
        job = next(j for j in frozen['profiles'] if (j['case'], j['mode'], j['repeat']) == (case, 'split-serial', 0))
        per_rank = []
        for rank in range(ranks):
            text = (root/'profiles'/job['id']/'rank-output'/f'rank-{rank}.stdout').read_text()
            rows = [fields(s) for s in text.splitlines() if s.startswith('DMGMD_PARTITION rank=')]
            eligible = {int(r['step']): r for r in rows if int(r['step']) >= job['warmup']+16 and r['mode']=='split'}
            per_rank.append(eligible)
        common = sorted(set.intersection(*(set(rows) for rows in per_rank)))
        if not common:
            raise ValueError('no common ordinary capture step')
        step = common[0]
        rows = [r[step] for r in per_rank]
        calls = {int(r['call']) for r in rows}
        if len(calls) != 1:
            raise ValueError('rank call indices differ')
        captures.append(dict(job=job, step=step, call=calls.pop(), rank_records=rows))
    from run_overlap_o5 import freeze
    freeze(root/'capture-plan.json', dict(captures=captures,
        ncu_windows=[dict(report['spec'], **w) for report in reports if report['spec']['repeat']==0 for w in report['ncu_windows']]))
    print('PASS O65 profiles:', len(reports), flush=True)


def wall_analysis(root):
    frozen = load(root/'frozen-design.json')
    o6.SCOPES = SCOPES
    plan = dict(main=main_jobs(root), identity=frozen['identity'],
        main_minimum_seconds=10, statistics=dict(seed=frozen['bootstrap_seed']))
    if (root/'extension-plan.json').exists():
        plan['main'] += load(root/'extension-plan.json')['jobs']
    report = o6.main_analysis(root, plan)
    if report['missing'] or not all(row['passed'] for row in report['scientific_file_checks']):
        raise ValueError('incomplete or scientifically invalid wall matrix')
    if report['status'] != 'PASS' and not (root/'extension-plan.json').exists():
        raise ValueError('incomplete wall matrix or extension required')
    if report['status'] != 'PASS':
        report.update(status='PASS', remaining_uncertainty='predeclared one extension exhausted; wide contrasts remain INCONCLUSIVE')
        save(root/'wall-analysis.json', report)
    print('PASS O65 formal walls:', report['trials'], flush=True)


def replay_analysis(root):
    frozen = load(root/'replay-plan.json')
    blocks = {}
    raw = []
    for job in frozen['jobs']:
        directory = root/'replays'/job['id']/'output'
        result = load(directory/'result.json')
        if result['status'] != 'PASS' or result['seed'] != job['seed']:
            raise ValueError('failed replay')
        timings = list(csv.DictReader((directory/'timings.csv').open()))
        values = {v: [float(r['cuda_ms']) for r in timings if r['variant']==v] for v in 'ABCD'}
        if any(len(v) != frozen['iterations'] for v in values.values()):
            raise ValueError('incomplete paired replay')
        key = (job['case'], job['repeat'])
        blocks.setdefault(key, []).append({v: st.mean(samples) for v, samples in values.items()})
        raw.append(dict(job, **result, cuda_ms={v: st.mean(samples) for v, samples in values.items()}))
    summaries = []
    for case, ranks in SCOPES:
        by_variant = {v: {} for v in 'ABCD'}
        for repeat in range(5):
            values = blocks[(case, repeat)]
            if len(values) != ranks:
                raise ValueError('incomplete rank aggregate')
            for v in 'ABCD':
                by_variant[v][repeat] = st.mean(row[v] for row in values)
        summaries.append(dict(case=case, ranks=ranks,
            cuda_ms={v: distribution(list(values.values())) for v, values in by_variant.items()},
            contrasts={a+'/'+b:paired_ratio(by_variant[a], by_variant[b], seed=2026102667)
                for a, b in [('A', 'B'), ('C', 'A'), ('C', 'D'), ('D', 'A')]},
            compact_build_host_ms=distribution([r['compact_build_host_ms'] for r in raw if r['case']==case]),
            compact_device_bytes=sorted({r['compact_device_bytes'] for r in raw if r['case']==case})))
    save(root/'replay-analysis.json', dict(status='PASS', processes=len(raw),
        independent_blocks=5, summaries=summaries, raw=raw,
        interpretation='diagnostic warm-cache target execution; excludes preparation, compaction and consumers; not whole-step net benefit'))
    print('PASS O65 replay:', len(raw), 'processes in five paired blocks', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['profiles', 'walls', 'snapshots', 'replays'])
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.phase == 'profiles': profile_analysis(root)
    elif args.phase == 'walls': wall_analysis(root)
    elif args.phase == 'replays': replay_analysis(root)
    else:
        reports = [snapshot_report(p) for p in sorted((root/'snapshots').glob('*/rank-*'))]
        if len(reports) != sum(p for _, p in SCOPES):
            raise ValueError('incomplete snapshot matrix')
        save(root/'snapshot-analysis.json', dict(status='PASS', reports=reports))
        print('PASS O65 snapshots:', len(reports), flush=True)


if __name__ == '__main__':
    main()
