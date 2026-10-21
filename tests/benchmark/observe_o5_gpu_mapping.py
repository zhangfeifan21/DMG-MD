#!/usr/bin/env python3
"""Independent NVML PID/GPU evidence during O5 diagnostics, never main trials."""
from pathlib import Path
import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import time


COMMAND=['nvidia-smi','--query-compute-apps=pid,gpu_uuid','--format=csv,noheader,nounits']


def save(path,value):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)


def clock_domain():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()+':'+os.readlink('/proc/self/ns/time')


def identity(pid,candidate):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return int(fields[19]) if fields[0]!='Z' and os.readlink(f'/proc/{pid}/exe')==candidate else None
    except (OSError,ValueError):return None


def normalize(uuid):return uuid.lower().replace('gpu-','').replace('-','')


def selection(root):
    resolution=root/'diagnostic-recovery-resolution.json'
    if resolution.exists():
        plan=json.loads(resolution.read_text())
        return plan['group'],plan['gpu_mapping_folder']
    path=root/'diagnostic-recovery-plan.json'
    if path.exists():
        plan=json.loads(path.read_text())
        return plan['group'],plan['gpu_mapping_folder']
    return 'diagnostic','gpu-mapping-audit'


def window_observations(samples,directory,rank,pid,start,domain,begin,end):
    found=[]
    for index,s in enumerate(samples):
        if s.get('clock_domain_id')!=domain or s['t_begin_ns']<begin or s['t_end_ns']>end:continue
        for r in s.get('ranks',[]):
            if (r['directory'],r['rank'],r['pid'],r['start_ticks'])==(directory,rank,pid,start) and r['identity_stable']:
                found.append(dict(index=index,**r))
    return found


def analyze(root):
    group,folder_name=selection(root)
    folder=root/folder_name;meta=json.loads((folder/'metadata.json').read_text())
    samples=[json.loads(s) for s in (folder/'raw.jsonl').read_text().splitlines() if s.strip()]
    rows=[];trials=0
    for file in sorted((root/group).glob('*/result.json')):
        trials+=1;r=json.loads(file.read_text());d=file.parent
        host=json.loads((d/'binding/resolved.json').read_text())['host']
        domain=host['boot_id']+':'+host['namespaces']['time']
        segments=[dict(v.split('=',1) for v in s.split()[1:] if '=' in v) for s in (d/'stdout.txt').read_text().splitlines()
                  if s.startswith('DMGMD_DOMAIN_TRACE_SEGMENT ')]
        for rank in range(r['ranks']):
            reg=json.loads((d/'binding'/f'rank-{rank}.json').read_text());pid,start=reg['pid'],reg['start_ticks']
            segment=next((s for s in segments if s['rank']==str(rank) and s['sequence']=='1' and
                          s['pid']==str(pid) and s['clock']=='CLOCK_MONOTONIC'),None)
            row=dict(id=r['id'],case=r['case'],ranks=r['ranks'],backend=r['backend'],mode=r['mode'],rank=rank,
                directory=str(d),pid=pid,start_ticks=start,expected_uuid=r['devices'][rank],status='NOT_OBSERVED',
                initialization_observations=0,formal_observations=0,observed_gpu_sets=[])
            if segment and domain==meta['clock_domain_id']:
                begin,end=int(segment['begin_ns']),int(segment['end_ns'])
                initial=window_observations(samples,str(d),rank,pid,start,meta['clock_domain_id'],0,begin)
                formal=window_observations(samples,str(d),rank,pid,start,meta['clock_domain_id'],begin,end)
                initial=[x for x in initial if x['gpu_uuids']];formal=[x for x in formal if x['gpu_uuids']]
                observed=initial+formal;expected=[normalize(r['devices'][rank])]
                mismatches=[x for x in observed if sorted(normalize(u) for u in x['gpu_uuids'])!=expected]
                row.update(initialization_observations=len(initial),formal_observations=len(formal),
                    formal_marker_ns=[begin,end],formal_sample_indices=[x['index'] for x in formal],
                    observed_gpu_sets=sorted({tuple(x['gpu_uuids']) for x in observed}),mismatches=mismatches)
                if mismatches:row['status']='INVALID'
                elif initial and len(formal)>=meta['minimum_formal_observations']:row['status']='PASS'
            rows.append(row)
    result=dict(status='PASS' if trials==88 and len(rows)==384 and all(r['status']=='PASS' for r in rows) else 'PARTIAL',
        trials=trials,rank_rows=len(rows),rows=rows,query_failures=sum(s.get('returncode',0)!=0 for s in samples),
        scope='independent diagnostic runs only; startup and complete NVML query windows inside formal loop markers',
        main_runtime_gpu_polling=False,limitations='sampled CUDA context ownership; not kernel utilization or memory locality; main uses initialization UUID plus fixed-device source invariant')
    save(folder/'summary.json',result);return result


def collect(root,interval=.5):
    group,folder_name=selection(root)
    recovery=root/'diagnostic-recovery-plan.json'
    state_file=root/json.loads(recovery.read_text())['state_file'] if recovery.exists() else root/'continuation-20261019/state.json'
    folder=root/folder_name;folder.mkdir(exist_ok=False)
    plan=json.loads((root/'frozen-plan.json').read_text());candidate=plan['identity']['binaries']['o4']['path']
    metadata=dict(clock_id='CLOCK_MONOTONIC',clock_domain_id=clock_domain(),command=COMMAND,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),interval_seconds=interval,
        minimum_formal_observations=2,main_query_enabled=False,candidate=candidate,
        binary_sha256=plan['identity']['binaries']['o4']['sha256'])
    save(folder/'metadata.json',metadata);started=False
    with (folder/'raw.jsonl').open('x') as stream:
        while True:
            state=json.loads(state_file.read_text());phase=state['phase']
            if phase in ('failed','finished','profile','analysis-final'):
                if started or phase in ('failed','finished'):break
            if phase!='diagnostic':time.sleep(.5);continue
            if not started:
                metadata['nvidia_smi_version']=subprocess.check_output(['nvidia-smi','--version'],text=True)
                save(folder/'metadata.json',metadata)
            started=True;begin=time.monotonic_ns();regs=[]
            for d in (root/group).glob('*'):
                if not d.is_dir() or (d/'result.json').exists():continue
                for f in (d/'binding').glob('rank-*.json'):
                    reg=json.loads(f.read_text());pid,start=reg['pid'],reg['start_ticks']
                    if identity(pid,candidate)==start:regs.append(dict(directory=str(d.resolve()),rank=reg['rank'],pid=pid,start_ticks=start))
            try:query=subprocess.run(COMMAND,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
            except subprocess.TimeoutExpired as error:
                stdout=error.stdout.decode(errors='replace') if isinstance(error.stdout,bytes) else error.stdout or ''
                query=subprocess.CompletedProcess(COMMAND,-1,stdout,str(error))
            apps={}
            if query.returncode==0:
                for cells in csv.reader(io.StringIO(query.stdout)):
                    if len(cells)==2 and cells[0].strip().isdigit():apps.setdefault(int(cells[0]),[]).append(cells[1].strip())
            for r in regs:
                r.update(identity_stable=identity(r['pid'],candidate)==r['start_ticks'],gpu_uuids=sorted(set(apps.get(r['pid'],[]))))
            stream.write(json.dumps(dict(t_begin_ns=begin,t_end_ns=time.monotonic_ns(),clock_domain_id=metadata['clock_domain_id'],
                returncode=query.returncode,stdout=query.stdout,stderr=query.stderr,ranks=regs))+'\n');stream.flush()
            time.sleep(interval)
    result=analyze(root);print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--analyze-only',action='store_true');args=parser.parse_args();root=args.root.resolve()
    if args.analyze_only:print(json.dumps({k:v for k,v in analyze(root).items() if k!='rows'}))
    else:collect(root)
