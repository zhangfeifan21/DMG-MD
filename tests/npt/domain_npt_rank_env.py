#!/usr/bin/env python3
"""Set a deliberately different environment value on non-root ranks, then exec."""
import os
import sys

key, root_value, other_value, executable = sys.argv[1:]
rank = int(os.environ['OMPI_COMM_WORLD_RANK'])
os.environ[key] = root_value if rank == 0 else other_value
os.execv(executable, [executable])
