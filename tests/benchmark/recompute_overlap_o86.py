#!/usr/bin/env python3
"""Verify immutable O8.6 raw evidence and rebuild all tables without GPUs."""
import argparse
from pathlib import Path

from analyze_overlap_o5 import save
from analyze_overlap_o86 import wall_analysis, profile_analysis, cpu_analysis
from run_overlap_o86 import load, checked
from cpu_binding import digest

RAW_DIRECTORIES = ['initial-source', 'inputs', 'main', 'pilot', 'profiles', 'cpu', 'science',
                   'mpi-compact','mpi-geometry','schedule-compact','schedule-geometry','nightly-compact-tmp','nightly-geometry-tmp', 'analysis-source']
RAW_FILES = ['implementation-plan.json', 'diagnostic-amendment.json', 'design.json', 'frozen-plan.json',
             'collector-amendments.json',
             'frozen-plan.preflight.json', 'predata-amendment.json', 'dependency-proof.json',
             'mpi-execution-proof.json', 'coverage-proof.json', 'mpi-producer-loaded-runner.py',
             'performance-dispatch.py',
             'main-plan.json', 'main-recovery-plan.json', 'extension-plan.json', 'gates.json',
             'initial-status.txt', 'initial.patch', 'initial-source.json', 'initial-references.json',
             'final.patch', 'final-source.json', 'final-references.json', 'machine.json',
             'nightly-compact.json','nightly-geometry.json', 'dmg-md-baseline', 'dmg-md-candidate']


def raw_paths(root):
    files = {root/name for name in RAW_FILES if (root/name).is_file()}
    files.update(root.glob('*.log'))
    files.update(root.glob('*-version.txt'))
    files.update(root.glob('*-command.json'))
    files.update(root.glob('*-attempt*.json'))
    files.update(root.glob('*-loaded-runner.py'))
    files.update(root/name for name in ('run-gates.py','correctness-amendment.json','recompute.sh','handoff.json') if (root/name).is_file())
    for name in [*RAW_DIRECTORIES,*(p.name for p in root.glob('*-attempt*') if p.is_dir())]:
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
        formal_trials=walls['trials'], profiles=48, cpu_diagnostics=24, adoption='NONE',
        scope='carbon_200k/P2 and water_400k/P8, both backends, fixed K=4 binding',
        input='O8.5 A baseline and independent candidate retaining A, plus B/C; current O7+O8 retained; no old wall/replay samples reused',
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
