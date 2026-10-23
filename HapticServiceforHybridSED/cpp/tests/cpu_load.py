"""Bounded normal-priority CPU contender; 'unrestricted' leaves CPU scheduling to Windows."""
import sys
import time

if sys.argv[1:] != ['unrestricted']:
    raise SystemExit('CPU affinity mode is disabled; pass unrestricted')
deadline = time.monotonic() + 120
value = 1
while time.monotonic() < deadline:
    for _ in range(8192):
        value = (1664525 * value + 1013904223) & 0xffffffff
