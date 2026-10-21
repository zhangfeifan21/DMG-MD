#!/usr/bin/env python3
"""Inventory every O5 attempt and hash its preserved raw evidence, offline."""
from pathlib import Path
from collections import Counter
import argparse
import hashlib
import json
from analyze_overlap_o5 import write_csv,save


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def inventory(root):
    root=Path(root).resolve();rows=[];files=[]
    selected={str(Path(r['directory']).resolve()) for r in json.loads((root/'trial-analysis.json').read_text())
              if r['admission_status']=='PASS'}
    for group in sorted(root.iterdir()):
        if not group.is_dir() or group.is_symlink():continue
        for launch in sorted(group.glob('*/launch.json')):
            directory=launch.parent;result=directory/'result.json'
            data=json.loads((result if result.exists() else launch).read_text())
            row={k:data.get(k) for k in ('id','case','ranks','backend','version','mode','warmup','steps','seconds','error','returncode')}
            row['id']=data.get('id',directory.name)
            row.update(group=group.name,directory=str(directory),status=data.get('status','PARTIAL') if result.exists() else 'PARTIAL',
                       selected_main_or_history=str(directory) in selected,
                       analysis_pool='main/history' if str(directory) in selected else
                           'independent adoption validation' if group.name=='adoption-validation' else 'auxiliary / excluded')
            if not result.exists():row['error']='process completion/result NOT_OBSERVED; raw launch retained'
            rows.append(row)
            for path in sorted(directory.rglob('*')):
                relative=path.relative_to(directory)
                if path.is_symlink():
                    files.append(dict(path=str(path.relative_to(root)),kind='symlink',target=str(path.readlink())))
                elif path.is_file() and '__pycache__' not in path.parts:
                    # These are regenerated from raw ticks/markers by analyze_cpu.
                    if relative.parts[0]=='cpu' and path.name in ('summary.json','summary.csv','summary.md','diagnostics.json','intervals.json'):continue
                    files.append(dict(path=str(path.relative_to(root)),kind='file',size=path.stat().st_size,sha256=digest(path)))
    for name in ('design.json','frozen-plan.json','continuation-plan.json','extension-plan.json','diagnostic-plan.json',
                 'diagnostic-recovery-plan.json','diagnostic-rank-iof-amendment-20261021.json','profile-plan.json',
                 'diagnostic-recovery-resolution.json','adoption-validation-plan.json','version-identities.json',
                 'version-provenance-resolution.json','historical-source-check.json'):
        p=root/name
        if p.is_file():files.append(dict(path=name,kind='file',size=p.stat().st_size,sha256=digest(p)))
    for folder in root.glob('gpu-mapping-audit*'):
        if not folder.is_dir():continue
        for p in folder.iterdir():
            if p.is_file() and p.name!='summary.json':
                files.append(dict(path=str(p.relative_to(root)),kind='file',size=p.stat().st_size,sha256=digest(p)))
    save(root/'all-attempts.json',rows);write_csv(root/'all-attempts.csv',rows)
    save(root/'raw-evidence-inventory.json',files)
    (root/'raw-evidence.sha256').write_text(''.join(f'{r["sha256"]}  {r["path"]}\n' for r in files if r['kind']=='file'))
    save(root/'inventory-summary.json',dict(attempts=len(rows),status_counts=dict(Counter(r['status'] for r in rows)),
        groups={g:dict(Counter(r['status'] for r in rows if r['group']==g)) for g in sorted({r['group'] for r in rows})},
        files=sum(r['kind']=='file' for r in files),symlinks=sum(r['kind']=='symlink' for r in files),
        hashed_bytes=sum(r.get('size',0) for r in files),
        scope='Preserved raw launch attempts, including interrupted/failed/superseded auxiliary attempts. Derived CPU summaries excluded; inputs/binaries separately identified by frozen plan.'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    inventory(p.parse_args().root)
