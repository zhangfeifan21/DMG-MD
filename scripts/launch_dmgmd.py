#!/usr/bin/env python3
"""Launch a local Open MPI job with a verified physical-core budget per rank."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from cpu_binding import Audit, resolve, save


def add_options(p):
    p.add_argument('--cpu-cores-per-rank', type=int)
    p.add_argument('--cpu-placement', choices=['core','numa','rankfile'], default='core')
    p.add_argument('--cpu-rankfile', type=Path)
    p.add_argument('--cpu-numa-nodes', help='comma-separated NUMA nodes, cycled by rank')


def plan_for(args, ranks):
    return resolve(ranks, args.cpu_cores_per_rank, args.cpu_placement, args.cpu_rankfile,
                   [int(n) for n in args.cpu_numa_nodes.split(',')] if args.cpu_numa_nodes else None,
                   args.mpiexec_arg)


def main():
    p=argparse.ArgumentParser(description=__doc__); add_options(p)
    p.add_argument('-n','--ranks', type=int, required=True)
    p.add_argument('--mpiexec',default='mpiexec'); p.add_argument('--mpiexec-arg', action='append',default=[])
    p.add_argument('--audit-directory',type=Path,required=True)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--cpu-only',action='store_true',help='skip DMG-MD GPU log check for launcher acceptance')
    p.add_argument('command',nargs=argparse.REMAINDER)
    a=p.parse_args(); command=a.command[1:] if a.command[:1]==['--'] else a.command
    if not a.cpu_cores_per_rank or not command: p.error('physical cores and command are required')
    plan=plan_for(a,a.ranks)
    if a.dry_run: print(json.dumps(plan,indent=2)); return 0
    if a.audit_directory.exists(): p.error('audit directory already exists')
    audit=Audit(a.audit_directory,plan,command[0])
    if not a.cpu_only:
        for name,query in [('gpu-topology',['nvidia-smi','topo','-m']),
                           ('gpu-pci',['nvidia-smi','--query-gpu=index,uuid,pci.bus_id','--format=csv'])]:
            r=subprocess.run(query,capture_output=True,text=True)
            save(a.audit_directory/(name+'.json'),dict(command=query,returncode=r.returncode,stdout=r.stdout,stderr=r.stderr))
    cmd=[a.mpiexec,*a.mpiexec_arg,*audit.mpi_args(),'-n',str(a.ranks),*audit.wrapper(command)]
    save(a.audit_directory/'command.json',dict(command=cmd,launch_environment={k:v for k,v in os.environ.items()
        if k.startswith(('CUDA_','DMGMD_','OMP_','OMPI_','UCX_','PMIX_','PRTE_','SLURM_','PBS_','LSB_')) or k in ('PATH','LD_LIBRARY_PATH')}))
    with (a.audit_directory/'stdout.txt').open('w') as out, (a.audit_directory/'stderr.txt').open('w') as err:
        proc=subprocess.Popen(cmd,stdout=out,stderr=err,start_new_session=True)
        try:
            while proc.poll() is None:
                audit.poll()
                try: proc.wait(timeout=.2)
                except subprocess.TimeoutExpired: pass
        except BaseException:
            import sys
            sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests/benchmark'))
            from run_benchmark import stop_process_tree
            stop_process_tree(proc); raise
    audit.finish((a.audit_directory/'stdout.txt').read_text(),gpu_check=not a.cpu_only)
    return proc.returncode

if __name__=='__main__': raise SystemExit(main())
