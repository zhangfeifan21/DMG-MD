"""Preserve independent MPI rank logs for verbose O5 diagnostics.

Open MPI's merged output can interleave records larger than its IOF chunks.
Redirect each rank before exec, then make a derived concatenation for existing
parsers. Native PRRTE output files also showed cross-rank fragments on this host.
Neither the per-rank logs nor the original launcher stream are edited.
"""
from pathlib import Path
import json
import hashlib
import re
import os
import sys


def merge_rank_output(directory, ranks):
    directory = Path(directory)
    found = {}
    direct=list((directory/'rank-output').glob('rank-*.stdout'))
    candidates=direct or list((directory/'mpioutput').rglob('stdout'))+list(directory.glob('mpioutput.*.out'))
    for path in candidates:
        match = (re.fullmatch(r'rank-(\d+)\.stdout',path.name) if direct else
                 re.fullmatch(r'rank\.(\d+)', path.parent.name) if path.name=='stdout'
                 else re.fullmatch(r'mpioutput\..+@\d+\.(\d+)\.out',path.name))
        if match:
            rank = int(match[1])
            if rank in found:
                raise ValueError(f'duplicate stdout for rank {rank}')
            found[rank] = path
    if set(found) != set(range(ranks)):
        raise ValueError(f'incomplete independent rank logs: {sorted(found)}')
    contents = [found[rank].read_bytes() for rank in range(ranks)]
    if any(not text.endswith(b'\n') for text in contents):
        raise ValueError('incomplete rank output record')
    original = directory / 'stdout.txt'
    original.rename(directory / 'stdout.merged.txt')
    original.write_bytes(b''.join(contents))
    provenance = dict(
        derived='stdout.txt', original_merged='stdout.merged.txt',
        order=list(range(ranks)),
        sources=[dict(rank=rank, path=str(found[rank].relative_to(directory)),
                      sha256=hashlib.sha256(contents[rank]).hexdigest())
                 for rank in range(ranks)],
        derived_sha256=hashlib.sha256(original.read_bytes()).hexdigest())
    (directory / 'rank-output-provenance.json').write_text(
        json.dumps(provenance, indent=2) + '\n')
    return provenance


def install():
    """Adapt the frozen runner without changing its source or primary launches."""
    import run_benchmark as benchmark
    from cpu_binding import Audit
    original_wrapper, original_execute = Audit.wrapper, benchmark.execute

    def wrapper(audit,command):
        return [sys.executable,str(Path(__file__).resolve()),'wrap',str(audit.directory.parent),
                '--',*original_wrapper(audit,command)]

    def execute(command, directory, environment, timeout, interval,
                cpu_session=None, binding_audit=None):
        result = original_execute(command, directory, environment, timeout,
                                  interval, cpu_session, binding_audit)
        ranks = int(command[command.index('-n') + 1])
        merge_rank_output(directory, ranks)
        return result

    Audit.wrapper, benchmark.execute = wrapper, execute


def redirect_and_exec(directory,command):
    rank=int(os.environ['OMPI_COMM_WORLD_RANK']);size=int(os.environ['OMPI_COMM_WORLD_SIZE'])
    if not 0<=rank<size or not command:raise ValueError('invalid rank or command')
    folder=Path(directory)/'rank-output';folder.mkdir(exist_ok=True)
    for fd,suffix in ((1,'stdout'),(2,'stderr')):
        opened=os.open(folder/f'rank-{rank}.{suffix}',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o660)
        os.dup2(opened,fd);os.close(opened)
    os.execvpe(command[0],command,os.environ)


if __name__=='__main__':
    if len(sys.argv)<5 or sys.argv[1]!='wrap' or sys.argv[3]!='--':
        raise SystemExit('usage: o5_rank_output.py wrap DIRECTORY -- COMMAND...')
    redirect_and_exec(sys.argv[2],sys.argv[4:])
