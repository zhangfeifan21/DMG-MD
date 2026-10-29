#!/usr/bin/env python3
"""Verify immutable O8.5 raw evidence and rebuild all tables without GPUs."""
import argparse
from pathlib import Path

from analyze_overlap_o5 import save
from analyze_overlap_o85 import wall_analysis, profile_analysis, cpu_analysis
from run_overlap_o85 import load, checked
from cpu_binding import digest

RAW_DIRECTORIES = ['initial-source', 'inputs', 'main', 'pilot', 'profiles', 'cpu', 'science',
                   'mpi-producer', 'schedule-producer', 'nightly-tmp', 'analysis-source']
RAW_FILES = ['implementation-plan.json', 'diagnostic-amendment.json', 'design.json', 'frozen-plan.json',
             'collector-amendments.json',
             'frozen-plan.preflight.json', 'predata-amendment.json', 'dependency-proof.json',
             'mpi-execution-proof.json', 'coverage-proof.json', 'mpi-producer-loaded-runner.py',
             'performance-dispatch.py',
             'main-plan.json', 'main-recovery-plan.json', 'extension-plan.json', 'gates.json',
             'initial-status.txt', 'initial.patch', 'initial-source.json', 'initial-references.json',
             'final.patch', 'final-source.json', 'final-references.json', 'machine.json',
             'nightly.json', 'dmg-md-baseline', 'dmg-md-candidate']


def raw_paths(root):
    files = {root/name for name in RAW_FILES if (root/name).is_file()}
    files.update(root.glob('*.log'))
    files.update(root.glob('*-version.txt'))
    files.update(root.glob('*-command.json'))
    for name in RAW_DIRECTORIES:
        directory = root/name
        files.update(p for p in directory.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    return sorted(files)


def verify(root):
    inventory = load(root/'raw-inventory.json')
    for name, expected in inventory['files'].items():
        if digest(root/name) != expected:
            raise ValueError('raw evidence changed: '+name)
    print('PASS raw evidence SHA-256:', len(inventory['files']), flush=True)


def recompute(root):
    checked(root, offline=True)
    verify(root)
    walls = wall_analysis(root)
    profile_analysis(root)
    cpu_analysis(root)
    if walls['extension_scopes'] and not (root/'extension-plan.json').exists():
        raise ValueError('predeclared extension required before completion')
    save(root/'candidate-evidence.json', dict(status='COMPLETE', implementation='PASS', correctness='PASS',
        formal_trials=walls['trials'], profiles=24, cpu_diagnostics=8, adoption='NONE',
        scope='carbon_200k/P2 and water_400k/P8, both backends, fixed K=4 binding',
        input='current O7+O8 baseline, separate candidate; no O6.5 wall/replay samples reused',
        contrasts=walls['contrasts'], followup='independent O9 screening/confirmation required'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--seal', action='store_true', help='write a new raw hash inventory after all artifacts are final')
    args = p.parse_args(); root = args.root.resolve()
    if args.seal:
        if (root/'raw-inventory.json').exists(): raise ValueError('raw inventory already sealed')
        paths = raw_paths(root)
        save(root/'raw-inventory.json', dict(files={str(p.relative_to(root)):digest(p) for p in paths},
            bytes=sum(p.stat().st_size for p in paths), symlinks='hash resolved file contents; preserve link paths'))
    recompute(root)


if __name__ == '__main__': main()
