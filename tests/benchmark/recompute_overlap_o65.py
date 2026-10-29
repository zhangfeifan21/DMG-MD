#!/usr/bin/env python3
"""Verify immutable O6.5 raw evidence and recompute all conclusions without GPU.

--seal creates the initial raw-evidence inventory after collection is complete.
Subsequent invocations verify every inventoried byte before recomputing.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics as st

from analyze_overlap_o5 import save
from analyze_overlap_o65 import (profile_analysis, wall_analysis, replay_analysis,
                                 snapshot_report)
from run_overlap_o65 import SCOPES, load
from cpu_binding import digest

DERIVED = {'raw-inventory.json', 'kernel-trials.json', 'kernel-steps.csv',
    'profile-analysis-plan.json', 'snapshot-analysis.json', 'replay-analysis.json',
    'candidate-evidence.json', 'wall-analysis.json', 'main-trials.csv',
    'wall-summary.csv', 'wall-ratios.csv', 'summary.md', 'recompute.log',
    'final-state.json', 'final.patch', 'final-source.json', 'recompute.sh',
    'ncu-analysis.json'}


def raw_files(root):
    return sorted(p for p in root.rglob('*') if p.is_file() and p.name!='scheduler.lock'
        and '__pycache__' not in p.parts and p.suffix!='.pyc'
        and not (p.parent==root and p.name in DERIVED))


def inventory(root, seal):
    path=root/'raw-inventory.json'
    if seal:
        if path.exists(): raise ValueError('raw evidence is already sealed')
        save(path, dict(files={str(p.relative_to(root)):dict(sha256=digest(p),bytes=p.stat().st_size)
                              for p in raw_files(root)}))
    frozen=load(path)['files']
    if {str(p.relative_to(root)) for p in raw_files(root)}!=set(frozen):
        raise ValueError('raw evidence file set changed')
    for name,metadata in frozen.items():
        if (root/name).stat().st_size!=metadata['bytes'] or digest(root/name)!=metadata['sha256']:
            raise ValueError('raw evidence changed: '+name)
    return dict(files=len(frozen),bytes=sum(r['bytes'] for r in frozen.values()))


def gates(root):
    frozen=load(root/'diagnostic-plan.json')
    for name,expected in frozen['binaries'].items():
        if digest(root/name)!=expected: raise ValueError('binary changed')
    if load(root/'diagnostic-gates.json')['status']!='PASS': raise ValueError('missing correctness gate')
    for name,sha256 in load(root/'diagnostic-gates.json')['logs'].items():
        if digest(root/(name+'.log'))!=sha256:
            raise ValueError('diagnostic gate log changed: '+name)
    long=load(root/'long-nve.json')
    expected_cases={'carbon_crystal','dense_water','batio3_zbl','carbon_nep5',
        'dense_water_typewise_cutoff','batio3_flexible_zbl','batio3_typewise_zbl_cutoff'}
    if long['candidate_sha256']!=frozen['binaries']['dmg-md-diagnostic'] or long['completed_configs']!=42 or long['total_configs']!=42 or set(long['cases'])!=expected_cases:
        raise ValueError('nightly contract differs')
    if 'Skipped' in (root/'ctest-gpu.log').read_text(): raise ValueError('GPU CTest was skipped')
    if load(root/'reference-before.json')!=load(root/'reference-after.json'):
        raise ValueError('read-only reference checkout state changed')
    bridge=load(root/'input-bridge.json')
    if bridge['status']!='PASS' or not all(row['exact_match'] for row in bridge['inputs'].values()):
        raise ValueError('accepted O8 input identity differs')


def dependencies(root):
    expected=load(root/'analysis-source/dependencies.json')
    source_root=Path(__file__).resolve().parents[2]
    for name,sha256 in expected.items():
        if digest(source_root/name)!=sha256:
            raise ValueError('analysis dependency changed: '+name+'; use the artifact recompute.sh')


def activity(root):
    report=load(root/'replay-activity.json')
    if report['status']!='PASS': raise ValueError('missing actual compact activity')
    for trial in report['reports']:
        directory=root/'replay-activity'/trial['case']
        db=sqlite3.connect(f'file:{directory}/trace.sqlite?mode=ro',uri=True)
        db.row_factory=sqlite3.Row
        try:
            names=dict(db.execute('select id,value from StringIds'))
            prefix='find_force_radial' if trial['target']=='radial' else 'find_partial_force_angular'
            kernels=[dict(row,name=names[row['shortName']],
                          demangled_name=names[row['demangledName']],
                          mangled_name=names[row['mangledName']]) for row in db.execute(
                'select * from CUPTI_ACTIVITY_KIND_KERNEL order by start') if prefix in names[row['shortName']]]
        finally: db.close()
        if kernels!=trial['kernels']: raise ValueError('replay activity differs from raw CUPTI')
        result=load(directory/'output/result.json')
        compact=[k for k in kernels if 'ILb1E' in k['mangled_name']]
        original=[k for k in kernels if 'ILb0E' in k['mangled_name']]
        expected=sorted([(result['early_count']+63)//64]*3+[(result['late_count']+63)//64]*3)
        if (len(kernels)!=18 or len(compact)!=6 or len(original)!=12
                or any(k['blockX']!=64 for k in kernels)
                or sorted(k['gridX'] for k in compact)!=expected
                or any(k['gridX']!=(result['center_count']+63)//64 for k in original)):
            raise ValueError('actual replay launch mapping differs')


def hardware(root):
    job=load(root/'ncu-plan.json')['job']
    directory=root/'ncu'/job['id']
    result=load(directory/'result.json')
    if any(result.get(key)!=value for key,value in job.items()):
        raise ValueError('NCU probe differs from frozen ordinary window')
    output=(directory/'combined-rank-output.txt').read_text()
    denied='ERR_NVGPUCTRPERM' in output
    if denied and result.get('status')!='INCONCLUSIVE':
        raise ValueError('NCU permission result differs from raw output')
    save(root/'ncu-analysis.json',dict(result=result,
        hardware_metrics='UNAVAILABLE' if denied else result['status'],
        exact_wall_decomposition='UNKNOWN'))


def candidates(root, snapshots, evidence):
    replay=load(root/'replay-analysis.json')
    masks=[]
    for case,ranks in SCOPES:
        reports=[r for r in snapshots if Path(r['snapshot']).parent.name==case]
        names=('I','B') if ranks==2 else ('E','H')
        phases={name:[next(p for p in report['phases'] if p['phase']==name) for report in reports] for name in names}
        majority_warps=all(p['warp']['nonempty']*2>p['warp']['groups'] for group in phases.values() for p in group)
        majority_blocks=all(p['block']['nonempty']*2>p['block']['groups'] for group in phases.values() for p in group)
        masks.append(dict(case=case,target_phases=names,
            hypothesis='each complementary phase covers more than half the original grid on every captured rank',
            warp_status='SUPPORTED' if majority_warps else 'REJECTED',
            block_status='SUPPORTED' if majority_blocks else 'REJECTED',
            scope='exact static fact about the frozen ordinary snapshot only',
            center_fraction={name:[p['center_fraction'] for p in group] for name,group in phases.items()},
            nonempty_warp_fraction={name:[p['warp']['nonempty']/p['warp']['groups'] for p in group] for name,group in phases.items()},
            nonempty_block_fraction={name:[p['block']['nonempty']/p['block']['groups'] for p in group] for name,group in phases.items()}))
    kernel_reports=load(root/'kernel-trials.json')['reports']
    import analyze_overlap_o6 as o6
    increments=[]
    hotspot_matches=[]
    for case,ranks in SCOPES:
        by_kernel={}
        for name in o6.KERNELS:
            paired=[]
            for repeat in range(3):
                values={}
                for mode in ('full','split-serial'):
                    report=next(r for r in kernel_reports if (r['spec']['case'],r['spec']['mode'],r['spec']['repeat'])==(case,mode,repeat))
                    values[mode]=st.mean(sum(value for key,value in rank['kernel_ms_mean'].items()
                        if key.startswith(name+'/')) for rank in report['ranks'])
                paired.append(values['split-serial']-values['full'])
            by_kernel[name]=dict(paired_ms=paired,median_ms=st.median(paired),blocks=3)
        largest=max(by_kernel,key=lambda name:by_kernel[name]['median_ms'])
        expected='find_force_radial' if ranks==2 else 'find_partial_force_angular'
        hotspot_matches.append(largest==expected)
        increments.append(dict(case=case,kernels=by_kernel,largest_increment=largest))
    decisions=[]
    for row in replay['summaries']:
        contrast=row['contrasts']['C/D']
        low,high=contrast['ci95']
        decision='SUPPORTED' if low>1 else 'REJECTED' if high<1 else 'INCONCLUSIVE'
        decisions.append(dict(case=row['case'],target='radial' if row['ranks']==2 else 'angular partial',
            hypothesis='compact mapping lowers target kernel time relative to complementary full grids',
            status=decision,ratio_C_over_D=contrast,
            candidate='ascending center indices; complete physical arrays/stride and original arithmetic',
            enter_whole_step='SUPPORTED for independent experiment' if decision=='SUPPORTED' else 'not established by this diagnostic',
            maintenance=row['compact_build_host_ms'],device_bytes=row['compact_device_bytes'],
            net_whole_step='NOT_TESTED; O9 must validate integration, cache maintenance and independent net wall benefit'))
    save(root/'candidate-evidence.json',dict(stage='O6.5',implementation='COMPLETE',correctness='PASS',
        experiment='COMPLETE',production_adoption='NONE',raw_evidence=evidence,
        masks=masks,candidates=decisions,kernel_increments=increments,
        hypotheses=[dict(hypothesis='new steady hotspots match carbon radial and water/P8 angular partial',
                         status='SUPPORTED' if all(hotspot_matches) else 'REJECTED',
                         evidence='paired split-minus-full kernel increments; three independent profile blocks'),
            dict(hypothesis='specific occupancy, memory transaction/cache or scheduler cause',
                 status='INCONCLUSIVE',evidence='ncu-analysis.json; static coverage cannot identify these metrics'),
            dict(hypothesis='replay difference exactly decomposes original pipeline or whole-step wall',
                 status='REJECTED',evidence='different producer/cache/reset context; timing boundaries exclude maintenance/consumers')],
        handoff='O8.5 producer-only splitting remains the next planned experiment; compact execution is a separate O9 candidate'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--seal',action='store_true')
    args=parser.parse_args();root=args.root.resolve()
    evidence=inventory(root,args.seal)
    dependencies(root);gates(root);profile_analysis(root);wall_analysis(root);replay_analysis(root);activity(root);hardware(root)
    import analyze_overlap_o6 as o6
    o6.summarize(root,load(root/'kernel-trials.json')['reports'])
    summary=root/'summary.md'
    summary.write_text(summary.read_text().replace('# O6 raw-data summary','# O6.5 raw-data summary',1))
    reports=[snapshot_report(root/'snapshots'/case/f'rank-{rank}') for case,ranks in SCOPES for rank in range(ranks)]
    for capture in load(root/'capture-plan.json')['captures']:
        case=capture['job']['case']
        for rank,actual in enumerate(capture['rank_records']):
            report=next(r for r in reports if Path(r['snapshot'])==root/'snapshots'/case/f'rank-{rank}')
            counts={p['phase']:p['count'] for p in report['phases']}
            if counts['I']!=int(actual['interior']) or counts['E']!=int(actual['early']):
                raise ValueError('independent snapshot masks disagree with logged production counts')
    save(root/'snapshot-analysis.json',dict(status='PASS',reports=reports))
    candidates(root,reports,evidence)
    print('PASS O65 complete offline recomputation:',evidence['files'],'immutable evidence files',flush=True)


if __name__=='__main__': main()
