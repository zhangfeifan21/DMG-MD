#!/usr/bin/env python3
"""Archive O6 sources and verify immutable raw evidence before offline replay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from run_benchmark import ROOT


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def source_paths():
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    changed = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    changed_docs = set(subprocess.check_output(['git','diff','--name-only'],cwd=ROOT,text=True).splitlines()) | set(changed)
    return sorted({p for p in tracked+changed if p and (p.startswith(('src/','include/','scripts/','tests/benchmark/'))
        or p.startswith('tests/') and p.endswith(('.py','.json'))
        or p in ('CMakeLists.txt','tests/CMakeLists.txt','AGENTS.md')
        or p.startswith('docs/') and p in changed_docs)})


def freeze(root):
    if (root/'raw-evidence-inventory.json').exists():
        raise ValueError('inventory already frozen; use --verify')
    archive = root/'source-files'
    tools = {}
    for name in source_paths():
        source, target = ROOT/name, archive/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if name.endswith(('.py','.json')):
            tools[name] = digest(source)
    for name in ('tests/long_nve/long_nve_common.py','tests/long_nve/manifest.json'):
        target = archive/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/name, target)
        tools[name] = digest(ROOT/name)
    (root/'final.patch').write_bytes(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT))
    top = {'frozen-plan.json','design.json','ncu-plan.json','ncu-windows.json','dmg-md-o4',
           'machine.json','reference-before.json','reference-after.json','initial.patch','final.patch',
           'ncu-device-query.json','ncu-driver-policy.txt','configure.log','ctest-cpu.log','ctest-o6.log',
           'ctest-o6-final.log','matched-control-plan.json','recompute.sh'}
    prefixes = {'inputs','gates','science','profiles','main','ncu','matched-controls','source-files','provenance'}
    raw = {}
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] in prefixes or str(relative) in top or path.name.endswith(('-driver.log','-version.txt')):
            raw[str(relative)] = dict(sha256=digest(path), bytes=path.stat().st_size)
    attempts = []
    for group in ('science','profiles','main','ncu','matched-controls'):
        for path in sorted((root/group).glob('*/result.json')):
            meta = json.loads(path.read_text())
            attempts.append(dict(group=group, directory=str(path.parent.relative_to(root)),
                                 status=meta['status'], error=meta.get('error')))
    (root/'all-attempts.json').write_text(json.dumps(attempts,indent=2)+'\n')
    inventory = dict(schema_version=1, raw=raw, tools=tools,
                     excludes='derived analysis tables and summaries; raw attempts are never overwritten')
    (root/'raw-evidence-inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
    print('FROZEN', len(raw), 'raw files;',len(attempts),'attempts')


def verify(root):
    inventory = json.loads((root/'raw-evidence-inventory.json').read_text())
    for name, expected in inventory['raw'].items():
        path = root/name
        if not path.is_file() or path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']:
            raise ValueError(f'raw evidence differs: {name}')
    for name, expected in inventory['tools'].items():
        if digest(ROOT/name) != expected:
            raise ValueError(f'analysis dependency differs: {name}; inspect source-files snapshot')
    print('VERIFIED',len(inventory['raw']),'raw files and',len(inventory['tools']),'source dependencies')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    (verify if args.verify else freeze)(args.root.resolve())
