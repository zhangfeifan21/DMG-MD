#!/usr/bin/env python3
"""Auditable, serial, resumable O5 experiments. Every raw trial is immutable.

Use --root for an independently prepared O5 directory containing design.json.
Pilot and science are separate from the frozen main plan. No external engine.
"""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

import run_benchmark as b
from cpu_sampling import Session
from analyze_cpu import analyze
sys.path.insert(0,str(b.ROOT/'scripts'))
from cpu_binding import Audit, resolve, digest

MODES={'full':('off','off'),'split-serial':('serial','off'),
       'overlap-wait':('serial','wait'),'overlap-poll':('serial','poll'),
       'verify-wait':('verify','wait'),'verify-poll':('verify','poll')}
MANIFEST=json.loads((b.HERE/'manifest.json').read_text())
ENVIRONMENT_KEYS=frozenset(('PATH','LD_LIBRARY_PATH','HOME','USER','LOGNAME','SHELL','LANG','LC_ALL','LC_CTYPE',
    'TZ','TMPDIR','TMP','TEMP','CUDA_HOME','OMPI_HOME','UCX_HOME','OMPI_MCA_mca_base_component_path',
    'PMIX_MCA_mca_base_component_path','OMPI_MCA_pml','OMPI_MCA_coll','UCX_WARN_UNUSED_ENV_VARS',
    'SLURM_JOB_NUM_NODES','SLURM_CPUS_ON_NODE','SLURM_JOB_ID','SLURM_JOBID','SLURM_NTASKS',
    'PBS_JOBID','PBS_NP','PBS_NODEFILE','LSB_JOBID','LSB_DJOB_NUMPROC','LSB_HOSTS'))


def controlled_environment(environment=None):
    source=os.environ if environment is None else environment
    return {k:v for k,v in source.items() if k in ENVIRONMENT_KEYS}


def signature(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True).encode()).hexdigest()


def validate_schedule(text,ranks,requested):
    actual=b.actual_dmgmd_overlap(text,requested)
    rows=[b.fields(s) for s in text.splitlines() if s.startswith('DMGMD_DOMAIN_SUMMARY ')]
    if ranks>1 and {(int(r['rank']),int(r['sequence'])) for r in rows}!={(r,s) for r in range(ranks) for s in (0,1)}:
        raise ValueError('incomplete domain rank/sequence counters')
    callbacks=sum(int(r['steps'])-int(r['rebuild_steps']) for r in rows) if requested!='off' else 0
    if actual['polls']>8*callbacks or (requested!='poll' and actual['polls']) or actual['calls']>callbacks:
        raise ValueError('progress/early count differs from ordinary halo schedule')
    actual['finish_callbacks']=callbacks
    actual['formal_finish_callbacks']=sum(int(r['steps'])-int(r['rebuild_steps']) for r in rows if r['sequence']=='1') if requested!='off' else 0
    return actual


def freeze(path, obj):
    if path.exists():
        if json.loads(path.read_text()) != obj: raise RuntimeError(f'frozen plan differs: {path}')
    else:
        with path.open('x') as f: json.dump(obj,f,indent=2,ensure_ascii=False);f.write('\n')
    return obj


class Experiment:
    def __init__(self, root):
        self.root=Path(root).resolve();self.design=json.loads((self.root/'design.json').read_text())
        self.devices=b.resolve_devices([str(x) for x in self.design['resource']['gpu_indices']],b.snapshot())
        self.bindings={p:resolve(p,4,'numa',nodes=[0,0,0,0,1,1,1,1]) for p in self.design['ranks']}
        self.binaries={'o4':self.root/'dmg-md-o4','o2':self.root/'build_o2/dmg-md',
                       'o1':self.root/'build_o1/dmg-md','o3':self.root/'build_o3/dmg-md'}
        linkage=subprocess.check_output(['ldd',str(self.binaries['o4'])],text=True)
        self.libraries={s:digest(s) for line in linkage.splitlines() for s in line.split()
                        if s.startswith('/') and Path(s).is_file()}
        self.inputs={}
        for name in self.design['cases']:
            case,_=b.geometry(MANIFEST['cases'][name],1)
            source=self.root/'inputs'/name
            if not source.exists():
                source.mkdir(parents=True)
                (source/'model.xyz').write_text(b.fixtures.generate_model(case,MANIFEST['seed']))
                (source/'nep.txt').write_text(b.fixtures.generate_potential(case))
            self.inputs[name]=(source,case)
        for spec in self.design.get('degraded',[]):
            name=spec['case'];source=self.root/'inputs'/name
            if name=='carbon_200k_drift':
                parent,case=self.inputs['carbon_200k']
                if not source.exists():
                    source.mkdir();lines=(parent/'model.xyz').read_text().splitlines()
                    for i in range(2,len(lines)):
                        atom=lines[i].split();atom[6]=f'{float(atom[6])+2.:.12e}';lines[i]=' '.join(atom)
                    (source/'model.xyz').write_text('\n'.join(lines)+'\n');shutil.copy2(parent/'nep.txt',source/'nep.txt')
            else:
                case=dict(b.fixtures.load_manifest()['cases'][spec['fixture']]);case.update(cells=spec['cells'],atoms=spec['atoms'])
                if not source.exists():
                    source.mkdir();(source/'model.xyz').write_text(b.fixtures.generate_model(case,MANIFEST['seed']))
                    (source/'nep.txt').write_text(b.fixtures.generate_potential(case))
            self.inputs[name]=(source,case)

    def identity(self, versions=('o4',)):
        sources={str(p.relative_to(b.ROOT)):digest(p) for p in [Path(__file__).resolve(),b.HERE/'run_benchmark.py',
                 b.ROOT/'scripts/cpu_binding.py',b.ROOT/'scripts/launch_dmgmd.py',b.HERE/'cpu_sampling.py',
                 b.HERE/'analyze_cpu.py',b.HERE/'manifest.json',
                 b.ROOT/'tests/long_nve/manifest.json',b.ROOT/'tests/long_nve/long_nve_common.py']}
        for name,h in sources.items():
            target=self.root/'tools'/h/Path(name).name
            if not target.exists():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(b.ROOT/name,target)
        return dict(devices=self.devices,libraries={p:digest(p) for p in self.libraries},environment_entry_sha256=digest(b.ROOT.parent/'env/md-mpi.sh'),
                    base_launch_environment=controlled_environment(),environment_whitelist=sorted(ENVIRONMENT_KEYS),
                    binaries={v:dict(path=str(self.binaries[v]),sha256=digest(self.binaries[v])) for v in versions},
                    inputs={n:{f:digest(d/f) for f in ('model.xyz','nep.txt')} for n,(d,c) in self.inputs.items()},
                    input_metadata={n:{k:c.get(k) for k in ('atoms','cells','generator','time_step_fs')} for n,(d,c) in self.inputs.items()},
                    sources=sources)

    def trial(self, group, job, minimum=0):
        directory=self.root/group/job['id']; spec=dict(job,devices=self.devices[:job['ranks']],
                            binary_sha256=digest(self.binaries[job['version']]),input_hashes=self.identity()['inputs'][job['case']])
        if (directory/'result.json').exists():
            old=json.loads((directory/'result.json').read_text())
            if old['spec_signature'] != signature(spec): raise RuntimeError('resume identity/configuration changed; use new batch')
            return old
        if directory.exists(): raise RuntimeError(f'incomplete raw trial preserved: {directory}; use new batch')
        directory.mkdir(parents=True)
        result=dict(spec,spec_signature=signature(spec),status='FAILED',directory=str(directory))
        audit=None;cpu=None
        try:
            source,case=self.inputs[job['case']]
            result['atoms']=case['atoms']
            for f in ('model.xyz','nep.txt'): (directory/f).symlink_to(source/f)
            (directory/'run.in').write_text(b.run_input('dmgmd',job['ranks'],case,job['warmup'],job['steps']))
            result['input_sha256']={f:digest(directory/f) for f in ('model.xyz','nep.txt','run.in')}
            partition,overlap=MODES[job['mode']]
            detailed=job.get('diagnostics')=='trace';sampling=job.get('sampling','off')
            env=controlled_environment()
            env.update(CUDA_DEVICE_ORDER='PCI_BUS_ID',CUDA_VISIBLE_DEVICES=','.join(spec['devices']),OMP_NUM_THREADS='1',
                       DMGMD_COMM_BACKEND=job['backend'],DMGMD_DOMAIN_PARTITION=partition,DMGMD_DOMAIN_OVERLAP=overlap,
                       DMGMD_OVERLAP_TRACE='1' if detailed and overlap!='off' else '0',
                       DMGMD_DOMAIN_TIMING=str(int(detailed)),DMGMD_DOMAIN_TRACE=str(int(detailed)),
                       DMGMD_DOMAIN_DIAGNOSTICS=str(int(detailed)),DMGMD_COMM_LOG_INTERVAL='1' if detailed else str(max(job['steps'],job['warmup'])+1))
            candidate=self.binaries[job['version']]
            command=[str(candidate)]
            if sampling=='proc':
                cpu=Session(directory/'cpu',job['id'],job['ranks'],candidate,.2,metadata=dict(
                    trial_directory=str(directory),visible_devices=spec['devices'],job=job))
                command=cpu.wrapper(command)
            audit=Audit(directory/'binding',self.bindings[job['ranks']],candidate)
            command=['mpiexec',*audit.mpi_args(),'-n',str(job['ranks']),*audit.wrapper(command)]
            result.update(command=command,launch_environment=env,
                          cleared_runtime_environment_keys=sorted(k for k in os.environ if k not in ENVIRONMENT_KEYS and
                              k.startswith(('CUDA_','DMGMD_','OMP_','OMPI_','UCX_','PMIX_','PRTE_'))))
            b.save(directory/'launch.json',result)
            result['t_begin_ns']=time.monotonic_ns()
            result['load_before']=Path('/proc/loadavg').read_text()
            result['cpu_stat_before']=Path('/proc/stat').read_text()
            if cpu: cpu.start()
            try:
                code,wall,timedout=b.execute(command,directory,env,1800,2,cpu,audit)
            finally:
                if cpu: cpu.stop('trial_finished')
            result.update(returncode=code,process_wall_seconds=wall,t_end_ns=time.monotonic_ns(),
                          load_after=Path('/proc/loadavg').read_text(),cpu_stat_after=Path('/proc/stat').read_text())
            text=(directory/'stdout.txt').read_text()
            result['binding']=audit.finish(text)
            audit=None
            if timedout or code: raise RuntimeError(f'exit={code},timeout={timedout}; retain logs including possible OOM')
            actual_gpu=result['binding']['gpu']
            norm=lambda s:s.lower().replace('gpu-','').replace('-','')
            if any(norm(g['cuda_uuid'])!=norm(spec['devices'][int(g['rank'])]) for g in actual_gpu):
                raise ValueError('actual GPU rank order differs from plan')
            seconds=b.parse_timing(text,'dmgmd',job['ranks'],case['atoms'],job['warmup'],job['steps'],job['backend'])
            if job['ranks']>1:
                result['actual_profiling']=b.actual_dmgmd_profiling(text,'trace' if detailed else 'off')
                config=next(b.fields(s) for s in text.splitlines() if s.startswith('DMGMD_DOMAIN_DIAGNOSTICS detailed='))
                if config['detailed']!=('on' if detailed else 'off'):
                    raise ValueError('runtime detailed logging differs from plan')
            if job['version']=='o4': result['actual_overlap']=validate_schedule(text,job['ranks'],overlap)
            if job['version']!='o1': result['actual_partition']=b.actual_dmgmd_partition(text,partition)
            if not detailed and any(s.startswith('DMGMD_OVERLAP_TRACE ') for s in text.splitlines()):
                raise ValueError('overlap trace appeared in uninstrumented trial')
            if job['version']=='o4':
                actual=result['actual_overlap'];split=result.get('actual_partition',{}).get('split_calls',0)
                if overlap!='off' and actual['calls']!=split:
                    raise ValueError('overlap and split execution counts differ')
            rows=[b.fields(s) for s in text.splitlines() if s.startswith(('DMGMD_PARTITION_SUMMARY ','DMGMD_OVERLAP_SUMMARY '))]
            result['execution_summaries']=rows
            if job['ranks']>1 and partition!='off':
                ps=[r for r in rows if 'cumulative_split_calls' in r]
                if {(int(r['rank']),int(r['sequence'])) for r in ps}!={(r,s) for r in range(job['ranks']) for s in (0,1)}:
                    raise ValueError('incomplete partition rank/sequence counters')
            if job['version']=='o4' and job['ranks']>1 and overlap!='off':
                osummaries=[b.fields(s) for s in text.splitlines() if s.startswith('DMGMD_OVERLAP_SUMMARY ')]
                if {(int(r['rank']),int(r['sequence'])) for r in osummaries}!={(r,s) for r in range(job['ranks']) for s in (0,1)}:
                    raise ValueError('incomplete overlap rank/sequence counters')
            result['final_thermo']=b.parse_thermo((directory/'thermo.out').read_text())
            result['science_sha256']=digest(directory/'thermo.out')
            result['scientific_outputs']={f:digest(directory/f) for f in ('thermo.out','neighbor.out') if (directory/f).exists()}
            result.update(seconds=seconds,ms_per_step=seconds*1000/job['steps'],sequence=1,
                          atom_steps_per_second=case['atoms']*job['steps']/seconds,
                          formal_cpu=None,status='PASS' if seconds>=minimum else 'INVALID')
            if seconds<minimum: result['error']='formal run shorter than minimum_seconds'
            if cpu:
                markers=b.write_trace_markers(text,cpu.directory) if detailed else None
                summary=analyze(cpu.directory,markers_path=markers)
                result['cpu_status']='PASS' if summary['collector_complete'] and all(m['status']=='verified' for m in summary['mappings']) else 'PARTIAL'
                if result['cpu_status']!='PASS': result['status']='INVALID'
        except (RuntimeError,ValueError,OSError,subprocess.SubprocessError) as e:
            result['error']=str(e)
        finally:
            if cpu: cpu.stop('trial_finally')
            if audit and not audit.file.closed:
                try: audit.finish((directory/'stdout.txt').read_text() if (directory/'stdout.txt').exists() else '')
                except ValueError as e: result['binding_error']=str(e)
            b.save(directory/'result.json',result)
        print(group,job['id'],result['status'],round(result.get('seconds',0),3),result.get('error',''),flush=True)
        return result

    def jobs(self, steps=100,warmup=100,modes=None,ps=None):
        return [dict(case=c,ranks=p,backend=back,mode=mode,version='o4',steps=steps,warmup=warmup)
                for c in self.design['cases'] for p in (ps or self.design['ranks'])
                for back in self.design['backends'] for mode in (modes or self.design['modes'])]

    def science(self):
        jobs=self.jobs(steps=5,warmup=2,modes=[*MODES])
        plan=[]
        for i,j in enumerate(jobs):plan.append(dict(j,id=f'{i:03d}-{j["case"]}-p{j["ranks"]}-{j["backend"]}-{j["mode"]}'))
        freeze(self.root/'science-plan.json',dict(identity=self.identity(),jobs=plan))
        results=[self.trial('science',j) for j in plan]
        comparisons=[]
        for j in results:
            reference=next(r for r in results if (r['case'],r['ranks'],r['backend'],r['mode'])==(j['case'],j['ranks'],j['backend'],'full'))
            ok=j['status']=='PASS' and j.get('science_sha256')==reference.get('science_sha256')
            comparisons.append(dict(id=j['id'],passed=ok,reference=reference['id']))
        b.save(self.root/'science-validation.json',comparisons)
        if not all(r['passed'] for r in comparisons): raise RuntimeError('science gate failed; stop affected experiments')

    def science_accepted(self):
        """Reuse exact-binary O4 P2/P4 verification; extend actual-input checks to P8."""
        completed=[json.loads(f.read_text()) for f in (self.root/'science').glob('*/result.json')]
        jobs=[]
        for i,j in enumerate(self.jobs(steps=5,warmup=2)):
            old=next((r for r in completed if all(r.get(k)==j[k] for k in j) and r['status']=='PASS'),None)
            jobs.append(dict(j,id=f'{i:03d}-{j["case"]}-p{j["ranks"]}-{j["backend"]}-{j["mode"]}',
                             reused_result=str(Path(old['directory'])/'result.json') if old else None))
        for case in self.design['cases']:
            for backend in self.design['backends']:
                needs_reference=False
                for mode in ('verify-wait','verify-poll'):
                    old=next((r for r in completed if (r['case'],r['ranks'],r['backend'],r['mode'],r['status'])==
                              (case,8,backend,mode,'PASS')),None)
                    jobs.append(dict(case=case,ranks=8,backend=backend,mode=mode,version='o4',
                                steps=old['steps'] if old else 1,warmup=old['warmup'] if old else 1,
                                id=f'{case}-p8-{backend}-{mode}',
                                reused_result=str(Path(old['directory'])/'result.json') if old else None))
                    needs_reference |= old is None
                if needs_reference:jobs.append(dict(case=case,ranks=8,backend=backend,mode='full',version='o4',steps=1,warmup=1,
                                                   id=f'{case}-p8-{backend}-verify-full-oracle',reused_result=None))
        freeze(self.root/'science-accepted-plan.json',dict(identity=self.identity(),jobs=jobs,
               reused_o4_gate='docs/status/validation/overlap-o4-20261016.md; exact binary 860c1ba3; P2/P4 verify'))
        results=[]
        for job in jobs:
            if job['reused_result']:
                r=json.loads(Path(job['reused_result']).read_text())
                binding=json.loads((Path(r['directory'])/'binding/resolved.json').read_text())
                if binding['mapping']!=self.bindings[job['ranks']]['mapping'] or r['binary_sha256']!=digest(self.binaries['o4']):
                    raise RuntimeError('reused science binding/binary differs')
                for name,h in r['input_sha256'].items():
                    if digest(Path(r['directory'])/name)!=h:raise RuntimeError('reused science input changed')
            else:r=self.trial('science-accepted',{k:v for k,v in job.items() if k!='reused_result'})
            if r['status']!='PASS':raise RuntimeError('science execution failed; review affected configuration')
            results.append(r)
        comparisons=[]
        for r in results:
            reference=next(z for z in results if all(z[k]==r[k] for k in ('case','ranks','backend','steps','warmup')) and z['mode']=='full')
            ok=r['science_sha256']==reference['science_sha256']
            comparisons.append(dict(directory=r['directory'],passed=ok,reference=reference['directory']))
        b.save(self.root/'science-accepted-validation.json',comparisons)
        if not all(r['passed'] for r in comparisons):raise RuntimeError('scientific bytes mismatch; stop before performance')

    def pilot(self):
        self.degraded_science_and_pilot()
        jobs=self.jobs()
        jobs+=self.jobs(warmup=200,modes=['full'])
        rng=random.Random(self.design['order_seed']);rng.shuffle(jobs)
        jobs=[dict(j,id=f'{i:03d}-{j["case"]}-p{j["ranks"]}-{j["backend"]}-{j["mode"]}-w{j["warmup"]}') for i,j in enumerate(jobs)]
        freeze(self.root/'pilot-plan.json',dict(identity=self.identity(),order_seed=self.design['order_seed'],jobs=jobs))
        results=[self.trial('pilot',j) for j in jobs]
        b.save(self.root/'pilot-results.json',results)
        if any(r['status']!='PASS' for r in results): raise RuntimeError('pilot has failures; review before freeze')
        configs={}
        for name in self.design['cases']:
            rows=[r for r in results if r['case']==name]
            ratios=[]
            for p in self.design['ranks']:
                for back in self.design['backends']:
                    a=next(r for r in rows if (r['ranks'],r['backend'],r['mode'],r['warmup'])==(p,back,'full',100))
                    z=next(r for r in rows if (r['ranks'],r['backend'],r['mode'],r['warmup'])==(p,back,'full',200))
                    ratios.append(dict(ranks=p,backend=back,ratio=z['seconds']/a['seconds']))
            warmup=200;unstable=[r for r in ratios if abs(r['ratio']-1)>.05]
            extra=[]
            for level in (400,800):
                if not unstable:break
                previous=warmup;warmup=level;next_unstable=[]
                for row in unstable:
                    p,back=row['ranks'],row['backend']
                    job=dict(case=name,ranks=p,backend=back,mode='full',version='o4',steps=100,warmup=level,
                             id=f'extension-{name}-p{p}-{back}-full-w{level}')
                    z=self.trial('pilot',job);extra.append(z)
                    if z['status']!='PASS':raise RuntimeError('extended warmup pilot failed')
                    a=next(r for r in [*rows,*extra] if (r['ranks'],r['backend'],r['mode'],r['warmup'])==(p,back,'full',previous))
                    ratio=z['seconds']/a['seconds'];ratios.append(dict(ranks=p,backend=back,ratio=ratio,comparison=[previous,level]))
                    if abs(ratio-1)>.05:next_unstable.append(dict(ranks=p,backend=back))
                unstable=next_unstable
            configs[name]=dict(steps=math.ceil(20/min(r['seconds']/r['steps'] for r in [*rows,*extra])/100)*100,
                               warmup=warmup,warmup_comparison=ratios,unresolved_warmup=unstable)
        freeze(self.root/'pilot-resolution.json',configs)
        if any(c['unresolved_warmup'] for c in configs.values()):raise RuntimeError('unresolved warmup sensitivity; review before formal freeze')

    def degraded_science_and_pilot(self):
        specs=self.design.get('degraded',[])
        if not specs:return
        science=[];pilot=[]
        for spec in specs:
            for back in self.design['backends']:
                for mode in self.design['modes']:
                    base=dict(case=spec['case'],ranks=spec['ranks'],backend=back,version='o4',mode=mode)
                    science.append(dict(base,steps=20,warmup=5,id=f'{spec["case"]}-{back}-{mode}'))
                    pilot.append(dict(base,steps=100,warmup=50,id=f'{spec["case"]}-{back}-{mode}'))
        freeze(self.root/'degraded-science-v2-plan.json',dict(identity=self.identity(),jobs=science))
        rows=[self.trial('degraded-science-v2',j) for j in science]
        checks=[]
        for r in rows:
            ref=next(z for z in rows if (z['case'],z['backend'],z['mode'])==(r['case'],r['backend'],'full'))
            checks.append(dict(directory=r['directory'],passed=r['status']=='PASS' and r.get('science_sha256')==ref.get('science_sha256')))
        b.save(self.root/'degraded-science-v2-validation.json',checks)
        if not all(c['passed'] for c in checks):raise RuntimeError('degraded scientific consistency failed')
        random.Random(self.design['order_seed']+3).shuffle(pilot)
        freeze(self.root/'degraded-pilot-plan.json',dict(identity=self.identity(),order_seed=self.design['order_seed']+3,jobs=pilot))
        rows=[self.trial('degraded-pilot',j) for j in pilot]
        if any(r['status']!='PASS' for r in rows):raise RuntimeError('degraded pilot failed')
        configs={s['case']:dict(warmup=200,steps=math.ceil(20/min(r['seconds']/r['steps'] for r in rows if r['case']==s['case'])/100)*100) for s in specs}
        freeze(self.root/'degraded-pilot-resolution.json',configs)

    def formal_plan(self):
        if (self.root/'frozen-plan.json').exists():
            plan=json.loads((self.root/'frozen-plan.json').read_text())
            if self.identity(('o1','o2','o3','o4'))!=plan['identity'] or self.design!=plan['design']:
                raise RuntimeError('identity/design changed after freeze; use new batch')
            for p,binding in self.bindings.items():
                if binding['mapping']!=plan['bindings'][str(p)]['mapping']:
                    raise RuntimeError('resolved CPU mapping changed after freeze; use new batch')
            return plan
        self.rebuilt_o2_gate()
        configs=json.loads((self.root/'pilot-resolution.json').read_text())
        unresolved=[(case,row['ranks'],row['backend']) for case,config in configs.items() for row in config.get('unresolved_warmup',[])]
        resolution_hash=None
        if unresolved:
            # Never silently freeze the failed short-window warmup check. A
            # separate, preserved long-window pilot must resolve every cell.
            path=self.root/'formal-config-resolution.json'
            accepted=json.loads(path.read_text())
            if accepted['identity']!=self.identity():raise RuntimeError('warmup resolution identity differs')
            for case,p,back in unresolved:
                pair=next(row for row in accepted['comparisons'] if (row['case'],row['ranks'],row['backend'])==(case,p,back))
                trials=[]
                for phase in ('before','after'):
                    raw=Path(pair[phase]);r=json.loads(raw.read_text())
                    if digest(raw)!=pair[phase+'_sha256'] or r['status']!='PASS' or (r['case'],r['ranks'],r['backend'],r['mode'],r['version'])!=(case,p,back,'full','o4'):
                        raise RuntimeError('warmup resolution raw trial invalid')
                    if r['binary_sha256']!=digest(self.binaries['o4']) or r['input_hashes']!=self.identity()['inputs'][case]:
                        raise RuntimeError('warmup resolution binary/input differs')
                    trials.append(r)
                a,z=trials
                if a['steps']!=z['steps'] or z['steps']<1000 or a['warmup']>=z['warmup'] or z['warmup']!=accepted['configs'][case]['warmup'] or abs(z['seconds']/a['seconds']-1)>.05:
                    raise RuntimeError('long-window warmup sensitivity remains unresolved')
            configs=accepted['configs'];resolution_hash=digest(path)
        extras=json.loads((self.root/'degraded-pilot-resolution.json').read_text()) if self.design.get('degraded') else {}
        rng=random.Random(self.design['order_seed']+1);jobs=[]
        for repeat in range(5):
            block=self.jobs()
            for spec in self.design.get('degraded',[]):
                for backend in self.design['backends']:
                    for mode in self.design['modes']:
                        block.append(dict(case=spec['case'],ranks=spec['ranks'],backend=backend,version='o4',mode=mode,supplement='degraded'))
            for case,p in self.design['history']['scope']:
                for backend in self.design['backends']:
                    for version,mode in [('o1','full'),('o2','full'),('o2','split-serial'),('o3','full'),('o3','split-serial')]:
                        block.append(dict(case=case,ranks=p,backend=backend,version=version,mode=mode))
            rng.shuffle(block)
            for j in block:
                config={**configs,**extras}[j['case']]
                j.update(repeat=repeat,**{k:config[k] for k in ('steps','warmup')})
                j['id']=f'{len(jobs):04d}-b{repeat}-{j["case"]}-p{j["ranks"]}-{j["backend"]}-{j["version"]}-{j["mode"]}'
                jobs.append(j)
        return freeze(self.root/'frozen-plan.json',dict(design=self.design,order_seed=self.design['order_seed']+1,identity=self.identity(('o1','o2','o3','o4')),
                                                     bindings=self.bindings,configs=configs,degraded_configs=extras,config_resolution_sha256=resolution_hash,jobs=jobs))

    def rebuilt_o2_gate(self):
        target=self.root/'rebuilt-o2-gate.json';candidate=self.binaries['o2'];h=digest(candidate)
        if target.exists():
            if json.loads(target.read_text())['binary_sha256']!=h:raise RuntimeError('rebuilt O2 gate identity changed')
            return
        directory=self.root/'rebuilt-o2-gate';directory.mkdir()
        audit=Audit(directory/'preflight-binding',self.bindings[8],candidate);audit.close()
        command=[sys.executable,str(b.ROOT/'tests/mpi/check_environment.py'),'--candidate',str(candidate),
                 '--devices',','.join(self.devices),'--ranks','8']+['--mpiexec-arg='+a for a in audit.mpi_args()]
        golden=[sys.executable,str(b.ROOT/'tests/baseline/run_baselines.py'),'--candidate',str(candidate),'--device','0']
        for name,cmd in [('preflight',command),('golden',golden)]:
            b.save(directory/(name+'-command.json'),dict(command=cmd))
            with (directory/(name+'.log')).open('x') as stream:subprocess.run(cmd,stdout=stream,stderr=subprocess.STDOUT,check=True)
        refs=[json.loads(f.read_text()) for group in ('science','science-accepted') for f in (self.root/group).glob('*/result.json')]
        comparisons=[]
        for case,p in self.design['history']['scope']:
            for backend in self.design['backends']:
                reference=next(r for r in refs if (r['case'],r['ranks'],r['backend'],r['mode'],r['steps'],r['warmup'],r['status'])==
                               (case,p,backend,'full',5,2,'PASS'))
                for mode in ('full','split-serial'):
                    r=self.trial('history-rebuilt-science',dict(case=case,ranks=p,backend=backend,mode=mode,version='o2',steps=5,warmup=2,
                                 id=f'{case}-p{p}-{backend}-{mode}'))
                    ok=r['status']=='PASS' and r['science_sha256']==reference['science_sha256']
                    comparisons.append(dict(directory=r['directory'],reference=reference['directory'],passed=ok))
                    if not ok:raise RuntimeError('rebuilt O2 science gate failed')
        b.save(target,dict(binary_sha256=h,comparisons=comparisons,status='PASS'))

    def main(self):
        plan=self.formal_plan()
        for j in plan['jobs']:
            if self.identity(('o1','o2','o3','o4'))!=plan['identity']: raise RuntimeError('identity changed after freeze; use new batch')
            r=self.trial('main',j,10)
            if r['status']=='PASS':
                key=f'{j["case"]}-p{j["ranks"]}'
                oracle=self.root/'science-main';oracle.mkdir(exist_ok=True)
                path=oracle/(key+'.json')
                if path.exists() and json.loads(path.read_text())['sha256']!=r['science_sha256']:
                    b.save(oracle/(key+'-MISMATCH.json'),dict(trial=j['id'],actual=r['science_sha256'],previous=json.loads(path.read_text())))
                    raise RuntimeError('scientific bytes differ; stop affected experiments before continuing')
                if not path.exists():b.save(path,dict(trial=j['id'],sha256=r['science_sha256']))
                if j['version']=='o4':
                    outputs=oracle/(key+'-outputs.json')
                    if outputs.exists() and json.loads(outputs.read_text())['hashes']!=r['scientific_outputs']:
                        raise RuntimeError('scientific output files differ; stop before further trials')
                    if not outputs.exists():b.save(outputs,dict(trial=j['id'],hashes=r['scientific_outputs']))

    def calibration(self):
        configs=json.loads((self.root/'pilot-resolution.json').read_text());jobs=[]
        pilot=json.loads((self.root/'pilot-results.json').read_text())
        step_seconds=min(r['seconds']/r['steps'] for r in pilot if (r['case'],r['ranks'],r['backend'],r['mode'])==
                         ('carbon_200k',2,'CudaAware','overlap-wait'))
        calibration_steps=math.ceil(20/step_seconds/100)*100
        rng=random.Random(self.design['order_seed']+2)
        for repeat in range(5):
            groups=['off','proc','trace'];rng.shuffle(groups)
            for mode in groups:
                jobs.append(dict(case='carbon_200k',ranks=2,backend='CudaAware',version='o4',mode='overlap-wait',
                                 steps=calibration_steps,warmup=configs['carbon_200k']['warmup'],repeat=repeat,
                                 sampling='proc' if mode!='off' else 'off',diagnostics='trace' if mode=='trace' else 'off',
                                 calibration_group=mode,id=f'b{repeat}-{mode}'))
        freeze(self.root/'calibration-plan.json',dict(identity=self.identity(),order_seed=self.design['order_seed']+2,jobs=jobs))
        results=[self.trial('calibration',j,10) for j in jobs]
        b.save(self.root/'calibration-results.json',results)
        if any(r['status']!='PASS' for r in results): raise RuntimeError('calibration has failed/invalid trials')

    def diagnostic(self):
        plan=self.formal_plan()
        pilot=json.loads((self.root/'pilot-results.json').read_text())
        pilot += [json.loads(f.read_text()) for f in (self.root/'degraded-pilot').glob('*/result.json')]
        jobs=self.jobs(steps=50,warmup=5,ps=[2,4,8])
        for spec in self.design.get('degraded',[]):
            for backend in self.design['backends']:
                for mode in self.design['modes']:
                    jobs.append(dict(case=spec['case'],ranks=spec['ranks'],backend=backend,version='o4',mode=mode,steps=50,warmup=5,supplement='degraded'))
        for i,j in enumerate(jobs):
            config={**plan['configs'],**plan['degraded_configs']}[j['case']]
            fastest=min(r['seconds']/r['steps'] for r in pilot if (r['case'],r['ranks'],r['status'])==(j['case'],j['ranks'],'PASS'))
            j.update(warmup=config['warmup'],steps=max(200,math.ceil(3/fastest/100)*100))
            j.update(sampling='proc',diagnostics='trace',id=f'{i:03d}-{j["case"]}-p{j["ranks"]}-{j["backend"]}-{j["mode"]}')
        freeze(self.root/'diagnostic-plan.json',dict(identity=self.identity(),jobs=jobs))
        for j in jobs:self.trial('diagnostic',j)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('phase',choices=['science','science-accepted','pilot','freeze','calibration','main','diagnostic'])
    a=p.parse_args()
    with (a.root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        exp=Experiment(a.root)
        if a.phase=='freeze':exp.formal_plan()
        else:getattr(exp,a.phase.replace('-','_'))()
    return 0

if __name__=='__main__':raise SystemExit(main())
