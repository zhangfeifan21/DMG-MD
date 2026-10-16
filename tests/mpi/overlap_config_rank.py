#!/usr/bin/env python3
"""Test-only rank-local configuration mismatch; exec the unchanged candidate."""
import os
import sys
if os.environ.get('OMPI_COMM_WORLD_RANK') == '1':
    for item in sys.argv[2:]:
        key, value = item.split('=', 1)
        os.environ[key] = value
os.execv(sys.argv[1], [sys.argv[1]])
