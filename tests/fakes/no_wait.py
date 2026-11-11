"""Run a Python program with its waits cut short: `no_wait.py <program> [args..]`.

Nothing in the fake world takes time (the fake herdr answers at once), so a pause the program takes for a real shell
or a real agent CLI, like the 1.5 s `bin/zed spawn` gives a new pane's shell, only makes the tests slow. The program
runs unchanged in every other way, as `__main__`, with its own path and arguments.
"""
import runpy
import sys
import time

MAX_WAIT = 0.05  # seconds: the longest any one `time.sleep` lasts
_sleep = time.sleep
time.sleep = lambda seconds: _sleep(min(seconds, MAX_WAIT))

sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
