"""Run the real `bin/hw` in the fake world: `run_hw.py <command> [args..]`.

One thing differs from the shipped program: the pause `spawn` gives a new pane's shell to reach its prompt
(SPAWN_SHELL_SECONDS). A pane of the fake herdr has no shell to wait for. Every other wait is the product's own.

With FAKE_NO_WATCHER set, `start` and `spawn` on OpenCode start no long-lived watcher: a test that is not about that
watcher looks through one of its own, and a profile has one watcher at a time. Starting it and stopping it again
would let it look first, and be stopped half-way through reporting what it saw.
"""
import os
import sys
import types
from pathlib import Path

HW = Path(__file__).resolve().parents[2] / "bin" / "hw"

hw = types.ModuleType("hw")
hw.__file__ = str(HW)
sys.path[0] = str(HW.parent)  # as when the program is run directly: not this folder, whose `store` and `agent` are fakes
sys.argv = [str(HW), *sys.argv[1:]]
exec(compile(HW.read_text(), str(HW), "exec"), hw.__dict__)
hw.SPAWN_SHELL_SECONDS = 0
if os.environ.get("FAKE_NO_WATCHER"):
    hw.arm_watcher = lambda: None
hw.main(sys.argv[1:])
