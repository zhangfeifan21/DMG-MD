"""Scoped O11 adoption experiment, derived from the accepted O9 collector.

No runtime changes. O9 selects B-wait; only fresh O11 blocks confirm adoption.
Every command uses immutable jobs and the existing audited O5 launcher.
"""
import argparse
from collections import defaultdict
import fcntl
import json
import math
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys

import run_benchmark as b
import run_overlap_o5 as o5
import run_overlap_o86 as o86
import o5_rank_output
from cpu_binding import Audit, digest
from analyze_overlap_o5 import paired_ratio, percentile, write_csv

CORE = [('carbon_200k', 2), ('water_400k', 8)]
EXTRA = [('water_400k', 4), ('carbon_1m', 2)]
DEGRADED = [('carbon_200k_drift', 2), ('carbon_thin_empty', 4)]
MODES = ['full', 'split-serial', 'producer-serial', 'producer-wait',
         'compact-serial', 'compact-wait', 'compact-poll', 'geometry-serial', 'geometry-wait']
PREVIOUS = {'o65':'dmgmd-overlap-o65-20261026', 'o85':'dmgmd-overlap-o85-20261027',
            'o86':'dmgmd-overlap-o86-20261028'}
REPORTS = {'o65':'overlap-o65-results-20261027.md', 'o85':'overlap-o85-results-20261027.md',
           'o86':'overlap-o86-results-20261028.md'}
o86.SCHEDULES['compact-poll']='producer-compact'
o5.MODES['compact-poll']=('serial','poll')
load = o86.load


def variants(case, ranks):
    if (case, ranks) in CORE:
        return [('candidate', m) for m in MODES] + [('o8', 'full')]
    return [('candidate', m) for m in ['full','compact-serial','compact-wait','compact-poll']] + [('o8','full')]


def jobs(group, steps, repeats=1):
    rng = random.Random(2026102910 + {'science':1,'pilot':2,'main':3,'cpu':4}[group])
    result=[]
    for repeat in range(repeats):
        scopes=[(c,p,k) for c,p in CORE+EXTRA+DEGRADED for k in o86.BACKENDS]
        rng.shuffle(scopes)
        for case,ranks,backend in scopes:
            vv=variants(case,ranks)
            if group=='cpu': vv=[('candidate', m) for m in ['full','compact-serial','compact-wait','compact-poll']]
            rng.shuffle(vv)
            for version,mode in vv:
                result.append(dict(id=f'{len(result):03d}-b{repeat}-{case}-p{ranks}-{backend}-{version}-{mode}',
                    case=case,ranks=ranks,backend=backend,version=version,mode=mode,
                    schedule=o86.SCHEDULES[mode],repeat=repeat,
                    warmup=2 if group=='science' else 1600 if case=='water_400k' else 200,
                    steps=steps[f'{case}-p{ranks}'],
                    **(dict(diagnostics='trace',sampling='proc') if group=='cpu' else {})))
    return result


def references():
    from run_overlap_o9 import references as get
    return get()


def copy(source, target):
    from run_overlap_o9 import copy_verified
    copy_verified(source,target)


def content_digester():
    """Hash shared input inodes once per verification pass, checking every entry."""
    seen={}
    def hashed(path):
        stat=path.stat()
        key=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        if key not in seen:
            value=digest(path)
            after=path.stat()
            if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=key:
                raise ValueError('file changed during hash: '+str(path))
            seen[key]=value
        return seen[key]
    return hashed


def freeze(root):
    hash_content=content_digester()
    root.mkdir(parents=True,exist_ok=True)
    if (root/'frozen-plan.json').exists():
        checked(root,offline=True)
        return
    provenance={}
    # Validate the sealed raw evidence, not merely a report saying PASS.
    for key,name in PREVIOUS.items():
        previous=b.ROOT/name
        inventory=load(previous/'raw-inventory.json')
        for path,expected in inventory['files'].items():
            if hash_content(previous/path)!=(expected['sha256'] if isinstance(expected,dict) else expected):
                raise ValueError('prior raw evidence changed: '+str(previous/path))
        required=['raw-inventory.json','candidate-evidence.json','final.patch']
        required += ['diagnostic-plan.json','replay-plan.json'] if key=='o65' else ['frozen-plan.json']
        if key=='o86': required+=['gates.json','coverage-proof.json','arithmetic-proof.json','cpu-analysis.json','wall-analysis.json']
        for name in required:
            copy(previous/name,root/'provenance'/key/name)
        copy(b.ROOT/'docs/status/performance'/REPORTS[key],root/'provenance'/key/'report.md')
        evidence=load(previous/'candidate-evidence.json')
        if evidence.get('correctness')!='PASS': raise ValueError('prior correctness missing')
        provenance[key]=dict(directory=str(previous),inventory_sha256=digest(previous/'raw-inventory.json'),
                            verified_files=len(inventory['files']))
        print('VERIFIED prior evidence',key,len(inventory['files']),flush=True)
    previous=b.ROOT/'dmgmd-overlap-o9-20261029'
    inventory=load(previous/'raw-inventory.json')
    for name,h in inventory['files'].items():
        if hash_content(previous/name)!=h: raise ValueError('O9 sealed evidence changed: '+name)
    for name in ('raw-inventory.json','analysis.json','frozen-plan.json','gates.json','fresh-mpi-completion.json'):
        copy(previous/name,root/'provenance/o9'/name)
    if load(previous/'analysis.json')['decision']!='CONFIRMED_SCOPED_CANDIDATE':
        raise ValueError('O9 candidate not confirmed')
    provenance['o9']=dict(directory=str(previous),verified_files=len(inventory['files']))
    source=b.ROOT/PREVIOUS['o86']
    copy(source/'dmg-md-candidate',root/'dmg-md-candidate')
    copy(b.ROOT/'dmgmd-overlap-o8-20261023/dmg-md-o8',root/'dmg-md-o8')
    if not (root/'dmg-md-o4').exists(): (root/'dmg-md-o4').symlink_to('dmg-md-candidate')
    o5.freeze(root/'design.json',dict(stage='O11 confirmation',cases=['carbon_200k','water_400k','carbon_1m'],
        ranks=[2,4,8],resource=dict(gpu_indices=list(range(8))),
        degraded=load(b.ROOT/'dmgmd-overlap-o5-20261016/design.json')['degraded']))
    for case in ('carbon_200k','water_400k'):
        for name in ('model.xyz','nep.txt'): copy(source/'inputs'/case/name,root/'inputs'/case/name)
    current=experiment(root)
    identity=current.identity(('candidate','o8'))
    old=load(source/'frozen-plan.json')
    if identity['devices']!=old['identity']['devices']: raise ValueError('GPU UUID order changed')
    for p in (2,8):
        if current.bindings[p]['mapping']!=old['bindings'][str(p)]['mapping']:
            raise ValueError('accepted CPU mapping changed')
    if identity['binaries']['candidate']['sha256']!=old['identity']['binaries']['candidate']['sha256']:
        raise ValueError('candidate differs from accepted binary')
    sources={str(p.relative_to(b.ROOT)):digest(p) for folder in ('src','include','tests/benchmark')
        for p in (b.ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    for name in sources: copy(b.ROOT/name,root/'source-files'/name)
    plan=dict(schema_version=2,stage='O11',selected='compact-wait',
        scope_policy=dict(reason='O9 confirmed only carbon_200k/P2 and water_400k/P8; independent O11 confirms these fixed scopes. water/P4 and carbon_1m/P2 remain exploratory; high-rebuild and empty-early are required negative controls.',
            excluded='No performance recommendation for P1, other P2/4/8 combinations, different hardware/models/boxes, cross-node or automatic thresholds. Full 3x4x2x4 matrix not claimed.',
            o10='Deferred conditional branch; not prerequisite. No mixed-backend change.',
            poll='Measured as control; wait preselected. Any new poll winner requires independent confirmation.'),
        selection=dict(candidate='B: same E/H compact producer',
            rule='B/A wait lower CI >1 in all four old scopes; C/B wait unconfirmed in all four; prefer unchanged E/H when no confirmed increment',
            o65='Compact radial/partial replay reduced C but D remained slower than full A. Producer keeps force consumers whole, eliminating those split executions. Do not add redundant compact force splitting without whole-step evidence.',
            o85='A retained as explicit control; carbon HostStaged was slower than full',
            o86='B selected; C retained as explicit control; water CudaAware slower than default full',
            confirmation='No reselection from new data. Other candidates exploratory only; any new winner needs a later independent experiment.'),
        provenance=provenance,identity=identity,bindings=current.bindings,sources=sources,
        evidence={str(p.relative_to(root)):digest(p) for p in (root/'provenance').rglob('*') if p.is_file()},
        scopes=CORE+EXTRA+DEGRADED,repeats=5,minimum_seconds=10,target_seconds=25,
        statistics=dict(seed=2026102915,resamples=40000,estimator='ratio of medians',
            interval='paired block percentile bootstrap; primary family 8 core same/default comparisons, Bonferroni 99.375% each; other contrasts exploratory 95%',
            extension='one additional five complete global blocks if any primary relative CI width >0.10; all attempts retained',
            adoption='Only preselected B-wait; same-backend and default simultaneous lower bounds >1 plus independent O8/full 95% lower bound >1. Recommend manual use only in exact confirmed scope; production defaults unchanged'),
        invalid='stop on failed/short/binding/science sample; retain all attempts; no automatic deletion/retry',
        conditions=dict(exclusive=False,clocks_locked=False,memory_residency='UNKNOWN'),
        science=jobs('science',{f'{c}-p{p}':3 for c,p in CORE+EXTRA+DEGRADED}),
        pilot=jobs('pilot',{f'{c}-p{p}':256 for c,p in CORE+EXTRA+DEGRADED}))
    o5.freeze(root/'frozen-plan.json',plan)
    o5.freeze(root/'reference-before.json',references())
    (root/'initial.patch').write_text(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=b.ROOT,text=True))
    (root/'initial-status.txt').write_text(subprocess.check_output(['git','status','--short'],cwd=b.ROOT,text=True))
    (root/'head.txt').write_text(subprocess.check_output(['git','rev-parse','HEAD'],cwd=b.ROOT,text=True))
    b.save(root/'machine.json',b.snapshot())
    for name,command in [('cuda',['nvcc','--version']),('mpi',['mpiexec','--version']),('ucx',['ucx_info','-v'])]:
        (root/f'{name}-version.txt').write_text(b.command_output(command))
    print('FROZEN O11 independent confirmation',flush=True)


def experiment(root):
    current=o5.Experiment(root)
    current.binaries.update({v:root/f'dmg-md-{v}' for v in ('candidate','o8')})
    return current


def checked(root,offline=False):
    plan=load(root/'frozen-plan.json')
    for name,expected in plan['sources'].items():
        if digest(b.ROOT/name)!=expected: raise ValueError('frozen source changed: '+name)
    for name,expected in plan['evidence'].items():
        if digest(root/name)!=expected: raise ValueError('prior evidence copy changed: '+name)
    for v,spec in plan['identity']['binaries'].items():
        if digest(root/f'dmg-md-{v}')!=spec['sha256']: raise ValueError('binary changed')
    for c,files in plan['identity']['inputs'].items():
        for name,expected in files.items():
            if digest(root/'inputs'/c/name)!=expected: raise ValueError('input changed')
    current=None if offline else experiment(root)
    if current:
        identity=current.identity(('candidate','o8'))
        for key in ('devices','libraries','environment_entry_sha256','binaries','inputs','sources'):
            if identity[key]!=plan['identity'][key]: raise ValueError('environment changed: '+key)
        for p,binding in current.bindings.items():
            if binding['mapping']!=plan['bindings'][str(p)]['mapping']: raise ValueError('CPU binding changed')
    return current,plan


def validate(root,group,job):
    directory=root/group/job['id']; result=load(directory/'result.json')
    if result['status']!='PASS' or any(result.get(k)!=v for k,v in job.items()):
        raise ValueError('failed or mismatched trial: '+job['id'])
    if result['binary_sha256']!=digest(root/f'dmg-md-{job["version"]}'): raise ValueError('trial binary changed')
    if result['binding']['status']!='PASS': raise ValueError('binding failed')
    text=(directory/'stdout.txt').read_text()
    partition,overlap=o5.MODES[job['mode']]
    actual=o5.validate_schedule(text,job['ranks'],overlap)
    split=result.get('actual_partition',{}).get('split_calls',0)
    if partition!='off' and not split and (job['case'],job['ranks']) not in DEGRADED:
        raise ValueError('all-fallback cannot confirm candidate')
    if overlap!='off' and actual['calls']!=split: raise ValueError('overlap/split differ')
    if job['version']=='candidate':
        configs=[b.fields(s) for s in text.splitlines() if s.startswith('DMGMD_SCHEDULE_CONFIG ')]
        if len(configs)!=1 or configs[0]['schedule']!=job['schedule']: raise ValueError('schedule differs')
    if job['schedule'].startswith('producer'):
        rows=[b.fields(s) for s in text.splitlines() if s.startswith('DMGMD_PARTITION_SUMMARY ')]
        for r in rows:
            if r['cumulative_producer_calls']!=r['cumulative_split_calls']: raise ValueError('producer not executed')
            if job['schedule']!='producer' and r['cumulative_compact_calls']!=r['cumulative_split_calls']:
                raise ValueError('compact not executed')
    if group in ('main','extension') and (result['seconds']<10 or result['formal_cpu'] is not None):
        raise ValueError('main timing/CPU contract failed')
    for name,expected in result['scientific_outputs'].items():
        if digest(directory/name)!=expected: raise ValueError('science artifact changed')
    return result


def trial(current,root,group,job):
    original=o5.controlled_environment
    def environment(source=None):
        env=original(source);env['DMGMD_DOMAIN_SCHEDULE']=job['schedule'];return env
    o5.controlled_environment=environment
    try: current.trial(group,job,minimum=10 if group in ('main','extension') else 0)
    finally: o5.controlled_environment=original
    result=validate(root,group,job)
    print(group,job['id'],round(result['seconds'],3),'s',flush=True)
    return result


def gates(root):
    current,plan=checked(root)
    directory=root/'gates';directory.mkdir(exist_ok=True)
    evidence={}
    for version in ('candidate','o8'):
        audit=Audit(directory/f'binding-{version}',current.bindings[8],current.binaries[version]);audit.close()
        for label,command in [
            ('environment',[sys.executable,str(b.ROOT/'tests/mpi/check_environment.py'),'--candidate',str(current.binaries[version]),'--devices',','.join(current.devices),'--ranks','8',*['--mpiexec-arg='+a for a in audit.mpi_args()]]),
            ('golden',[sys.executable,str(b.ROOT/'tests/baseline/run_baselines.py'),'--candidate',str(current.binaries[version]),'--device',current.devices[0]])]:
            stem=f'{version}-{label}'
            o5.freeze(directory/(stem+'-command.json'),dict(command=command,environment=o5.controlled_environment()))
            log=directory/(stem+'.log')
            if not (directory/(stem+'-pass.json')).exists():
                with log.open('x') as stream: subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,check=True,env=o5.controlled_environment(),timeout=1200)
                o5.freeze(directory/(stem+'-pass.json'),dict(sha256=digest(log)))
            if load(directory/(stem+'-pass.json'))['sha256']!=digest(log): raise ValueError('gate log changed')
            evidence[str(log.relative_to(root))]=digest(log)
            print('GATE PASS',stem,flush=True)
    rows=[trial(current,root,'science',j) for j in plan['science']]
    science_check(rows)
    o5.freeze(root/'gates.json',dict(status='PASS',evidence=evidence,science_jobs=plan['science'],
        unchanged_binary_acceptance='O8.6 sealed full MPI, schedule, nightly 42+12 for both B/C verified at freeze; O8 accepted control; no new binary or production source change'))


def science_check(rows):
    groups=defaultdict(list)
    for row in rows: groups[(row['case'],row['ranks'],row['backend'],row['repeat'])].append(row['scientific_outputs'])
    if any(any(x!=values[0] for x in values) for values in groups.values()):
        raise ValueError('same-window scientific outputs differ')


def run(root,group):
    current,plan=checked(root)
    gate=load(root/'gates.json')
    if gate['status']!='PASS': raise ValueError('gates required')
    for name,h in gate['evidence'].items():
        if digest(root/name)!=h: raise ValueError('gate changed')
    science_check([validate(root,'science',j) for j in gate['science_jobs']])
    if group=='pilot': selected=plan['pilot']
    else:
        pilot=[validate(root,'pilot',j) for j in plan['pilot']]
        steps={f'{c}-p{p}':math.ceil(25000/min(r['ms_per_step'] for r in pilot if (r['case'],r['ranks'])==(c,p))/100)*100 for c,p in CORE+EXTRA+DEGRADED}
        if group=='main': selected=jobs('main',steps,5)
        elif group=='extension':
            if not load(root/'analysis.json')['extension_required']: return
            selected=[dict(j,id='extension-'+j['id'],repeat=j['repeat']+5) for j in jobs('main',steps,5)]
        else: selected=jobs('cpu',steps,1)
        o5.freeze(root/f'{group}-plan.json',dict(jobs=selected,steps=steps))
    rows=[trial(current,root,group,j) for j in selected]
    science_check(rows)


def interval(a,z,primary=False):
    result=paired_ratio(a,z,seed=2026102915,resamples=40000)
    if primary and len(a)>=5:
        rng=random.Random(2026102915);blocks=sorted(a)
        values=[]
        for _ in range(40000):
            ix=rng.choices(blocks,k=len(blocks))
            values.append(statistics.median(a[i] for i in ix)/statistics.median(z[i] for i in ix))
        values.sort()
        result['ci_family']=[percentile(values,.05/16),percentile(values,1-.05/16)]
    return result


def completion(gates_passed, counts_complete, diagnostics_passed, contrasts, routes_complete):
    """Fail closed: NCU state and old sample counts cannot complete O11."""
    if not all((gates_passed,counts_complete,diagnostics_passed,routes_complete)) or len(contrasts)!=8:
        return 'PARTIAL'
    expected={(c,p,b,k) for c,p in CORE for b in o86.BACKENDS for k in ('same_backend_net','default_net')}
    if {(c.get('case'),c.get('ranks'),c.get('backend'),c.get('contrast')) for c in contrasts} != expected:
        return 'PARTIAL'
    scopes=defaultdict(list)
    for c in contrasts:
        ci=c.get('ratio',{}).get('ci_family')
        if not ci or c['ratio'].get('n',0)<5: return 'PARTIAL'
        scopes[(c['case'],c['ranks'],c['backend'])].append(ci[0]>1)
    return 'CONFIRMED_SCOPED_CANDIDATE' if any(len(v)==2 and all(v) for v in scopes.values()) else 'NO_ADOPTION'


def analyze(root):
    _,plan=checked(root,offline=True)
    rows=[]
    for group in ('main','extension'):
        if (root/f'{group}-plan.json').exists():
            rows += [validate(root,group,j) for j in load(root/f'{group}-plan.json')['jobs']]
    science_check(rows)
    table=defaultdict(dict)
    for r in rows:
        key=(r['case'],r['ranks'],r['backend'],r['version'],r['mode'])
        if r['repeat'] in table[key]: raise ValueError('duplicate block')
        table[key][r['repeat']]=r['ms_per_step']
    contrasts=[];complete=True
    for case,p in CORE+EXTRA+DEGRADED:
        for backend in o86.BACKENDS:
            for version,mode in variants(case,p):
                if len(table[case,p,backend,version,mode])<5: complete=False
            comparisons=[('same_backend_net','candidate','full',backend,'compact-wait'),
                         ('default_net','candidate','full','HostStaged','compact-wait'),
                         ('recovery','candidate','compact-serial',backend,'compact-wait'),
                         ('partition_cost','candidate','full',backend,'compact-serial'),
                         ('poll_vs_wait','candidate','compact-wait',backend,'compact-poll'),
                         ('poll_default_net','candidate','full','HostStaged','compact-poll'),
                         ('o8_default_net','o8','full','HostStaged','compact-wait'),
                         ('full_bridge','o8','full',backend,'full')]
            if (case,p) in CORE:
                comparisons += [('B_over_A','candidate','producer-wait',backend,'compact-wait'),
                    ('C_over_B','candidate','compact-wait',backend,'geometry-wait'),
                    ('A_net','candidate','full',backend,'producer-wait'),
                    ('C_net','candidate','full',backend,'geometry-wait'),
                    ('split_cost','candidate','full',backend,'split-serial')]
            for name,version,numerator,numbackend,denominator in comparisons:
                a=table[case,p,numbackend,version,numerator];z=table[case,p,backend,'candidate',denominator]
                if set(a)!=set(z) or len(a)<5: complete=False;continue
                primary=(case,p) in CORE and name in ('same_backend_net','default_net')
                contrasts.append(dict(case=case,ranks=p,backend=backend,contrast=name,primary=primary,
                    ratio=interval(a,z,primary),numerator=version+'/'+numerator,numerator_backend=numbackend,
                    denominator='candidate/'+denominator))
    primary=[c for c in contrasts if c['primary']]
    extension=any((c['ratio']['ci_family'][1]-c['ratio']['ci_family'][0])/c['ratio']['estimate']>.10 for c in primary)
    cpu=[]
    if (root/'cpu-plan.json').exists():
        for j in load(root/'cpu-plan.json')['jobs']:
            validate(root,'cpu',j)
            summary=load(root/'cpu'/j['id']/'cpu/summary.json')
            windows=[p for p in summary['phases'] if p['phase']=='run' and p['sequence']==1]
            if not summary['collector_complete'] or summary['marker_issues'] or len(windows)!=j['ranks'] or any(w['coverage']<.8 or w['mean_core_equivalent'] is None for w in windows):
                raise ValueError('CPU marker contract failed')
            cpu.append(dict(job=j,windows=windows))
    diagnostics=len(cpu)==48
    gate=load(root/'gates.json') if (root/'gates.json').exists() else {}
    gates_ok=gate.get('status')=='PASS'
    if gates_ok:
        for name,h in gate['evidence'].items():
            if digest(root/name)!=h: raise ValueError('gate evidence changed')
        science_check([validate(root,'science',j) for j in gate['science_jobs']])
    routes=all(any(c['contrast']==name for c in contrasts) for name in ('B_over_A','C_over_B','A_net','C_net'))
    outcome=completion(gates_ok,complete and (not extension or (root/'extension-plan.json').exists()),diagnostics,primary,routes)
    if references()!=load(root/'reference-before.json'): raise ValueError('read-only references changed')
    recommendations=[]
    if outcome!='PARTIAL':
        for case,p in CORE:
            for backend in o86.BACKENDS:
                selected=[c for c in contrasts if (c['case'],c['ranks'],c['backend'])==(case,p,backend)]
                nets=[c for c in selected if c['primary']]
                bridge=next(c for c in selected if c['contrast']=='o8_default_net')
                if len(nets)==2 and all(c['ratio']['ci_family'][0]>1 for c in nets) and bridge['ratio']['ci95'][0]>1:
                    recommendations.append(dict(case=case,ranks=p,backend=backend,mode='compact-wait'))
        outcome='SCOPED_MANUAL_USE' if recommendations else 'NO_ADOPTION'
    b.save(root/'analysis.json',dict(recommendations=recommendations,scope_policy=plan['scope_policy'],stage='O11',status='PARTIAL' if outcome=='PARTIAL' else 'COMPLETE',
        decision=outcome,implementation='UNCHANGED_ACCEPTED_BINARY',correctness='PASS' if gates_ok else 'PARTIAL',
        trials=len(rows),contrasts=contrasts,extension_required=extension,cpu_diagnostics=len(cpu),
        formal_cpu=None,production_defaults_changed=False,selected=plan['selected'],references_unchanged=True))
    b.save(root/'cpu-analysis.json',cpu)
    write_csv(root/'main-trials.csv',rows)
    write_csv(root/'contrasts.csv',contrasts)
    print('O11',outcome,'trials',len(rows),'CPU',len(cpu),'extension',extension,flush=True)
    return load(root/'analysis.json')


def seal(root):
    result=analyze(root)
    if result['status']!='COMPLETE': raise ValueError('necessary experiments missing')
    # Preserve transitive offline imports as well as the frozen benchmark sources.
    plan=load(root/'frozen-plan.json')
    for name,h in plan['identity']['sources'].items():
        if digest(b.ROOT/name)!=h: raise ValueError('analysis dependency changed: '+name)
    for folder in ('scripts','tests/baseline','tests/long_nve','tests/mpi','tests/lammps_reference'):
        for path in (b.ROOT/folder).rglob('*'):
            if path.is_file() and path.suffix in ('.py','.json'):
                copy(path,root/'source-files'/path.relative_to(b.ROOT))
    o5.freeze(root/'reference-after.json',references())
    hash_content=content_digester()
    inventory={str(p.relative_to(root)):hash_content(p) for p in root.rglob('*') if p.is_file()
        and p.name not in ('raw-inventory.json','scheduler.lock') and '__pycache__' not in p.parts}
    o5.freeze(root/'raw-inventory.json',dict(files=inventory))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['freeze','gates','pilot','main','extension','cpu','analyze','seal','verify'])
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args(argv);root=args.root.resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        o5_rank_output.install()
        if args.phase=='verify':
            for name,h in load(root/'raw-inventory.json')['files'].items():
                if digest(root/name)!=h: raise ValueError('raw evidence changed: '+name)
            analyze(root)
        elif args.phase in ('freeze','gates','analyze','seal'): globals()[args.phase](root)
        else: run(root,args.phase)


if __name__=='__main__': main()
