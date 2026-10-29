#!/usr/bin/env python3
"""Verify sealed O9 evidence and recompute using the preserved analysis sources.

No GPU, MPI execution, or mutable checkout sources are used. Reference status is
replayed from its sealed final observation, not re-observed years after the run.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args();root=args.root.resolve()
    inventory=json.loads((root/'raw-inventory.json').read_text())
    for name,expected in inventory['files'].items():
        if sha(root/name)!=expected: raise ValueError('sealed evidence changed: '+name)
    print('PASS sealed evidence',len(inventory['files']),flush=True)
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(root/'source-files/tests/benchmark'))
    module=importlib.import_module('overlap_o9_confirmation')
    if Path(module.__file__).resolve().parent!=root/'source-files/tests/benchmark':
        raise ValueError('analysis must use sealed source')
    module.references=lambda:json.loads((root/'reference-after.json').read_text())
    module.analyze(root)


if __name__=='__main__': main()
