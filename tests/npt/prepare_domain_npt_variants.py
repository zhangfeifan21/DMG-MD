#!/usr/bin/env python3
"""Build sparse NEP5/typewise/ZBL local-NPT differential fixtures.

Reuse MPI domain boundary geometries and registered long-NVE potential transforms.
These are deterministic short contracts, not thermodynamic evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / 'tests/mpi'), str(ROOT / 'tests/long_nve')]
import run_mpi_domain as mpi_cases
import long_nve_common as long_nve


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def model(lines):
    header = ('pbc="T T T" Lattice="80 0 0 0 24 0 0 0 24" '
              'Properties=species:S:1:pos:R:3:vel:R:3')
    return str(len(lines)) + '\n' + header + '\n' + '\n'.join(lines) + '\n'


def mixed(source):
    rows = []
    # One cluster lies inside a slab, one straddles the P2/P4 x=40 face.
    for shift in (0, 24):
        for index, row in enumerate(source.splitlines()[2:]):
            f = row.split()
            f[1] = str(float(f[1]) + shift)
            f[4:7] = [str(0.001 if index % 2 else -0.001), '0.0002', '-0.0001']
            rows.append(' '.join(f))
    return model(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--ensemble', choices=('npt_ber', 'npt_scr'), default='npt_ber')
    a = p.parse_args()
    out = a.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    pot = ROOT / 'tests/baseline/inputs/potentials'
    carbon = (pot / 'nep_C.txt').read_text()
    water = (pot / 'nep_water.txt').read_text()
    bto = (pot / 'nep_BaTiO3_zbl.txt').read_text()
    transform = long_nve.POTENTIAL_TRANSFORMS
    typewise = transform['typewise_cutoff_equivalent'](water).replace(
        'cutoff 6.0 4.0 6.0 4.0 251 80', 'cutoff 6.0 4.0 5.8 3.8 251 80')
    if 'cutoff 6.0 4.0 5.8 3.8 251 80' not in typewise: raise ValueError('missing mixed cutoff')
    # Two-hop chain straddles P4 x=20; second chain straddles periodic x=0.
    chain = model(['C 19.5 12 12 0.004 0 0', 'C 22 12 12 -0.003 0 0',
                   'C 28.5 12 12 0.002 0 0', 'C 79.5 3 12 -0.004 0 0',
                   'C 2 3 12 0.003 0 0', 'C 8.5 3 12 -0.002 0 0'])
    fixtures = {
        'nep5': (chain, transform['nep5_equivalent'](carbon)),
        'typewise': (mixed(mpi_cases.WATER_VARIANT_MODEL), typewise),
        'zbl': (mixed(mpi_cases.BTO_VARIANT_MODEL), bto),
        'flexible-zbl': (mixed(mpi_cases.BTO_VARIANT_MODEL), transform['flexible_zbl_equivalent'](bto)),
        'typewise-zbl': (mixed(mpi_cases.BTO_VARIANT_MODEL), transform['typewise_zbl_cutoff'](bto)),
    }
    inventory = {'provenance': {'generator': str(Path(__file__).resolve()),
        'generator_sha256': sha(Path(__file__).resolve()),
        'mpi_fixture_source': str(Path(mpi_cases.__file__).resolve()),
        'mpi_fixture_sha256': sha(Path(mpi_cases.__file__).resolve()),
        'transform_source': str(Path(long_nve.__file__).resolve()),
        'transform_sha256': sha(Path(long_nve.__file__).resolve()),
        'source_potential_sha256': {x.name: sha(x) for x in sorted(pot.glob('*.txt'))},
        'scope': 'sparse artificial boundary configurations; short algebraic comparisons only, not material or sampling evidence'},
        'fixtures': {}}
    for name, (atoms, potential) in fixtures.items():
        target = out / name; target.mkdir()
        (target / 'model.xyz').write_text(atoms)
        (target / 'nep.txt').write_text(potential)
        (target / 'run.in').write_text('potential nep.txt\ntime_step 0.001\n'
            f'ensemble {a.ensemble} 300 300 40 0 425 200\ndump_thermo 1\n'
            'dump_xyz 1 trajectory.xyz precision double velocity force potential virial\ndump_restart 1\nrun 2\n')
        inventory['fixtures'][name] = {x: sha(target / x) for x in ('model.xyz', 'nep.txt', 'run.in')}
    (out / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')


if __name__ == '__main__': main()
