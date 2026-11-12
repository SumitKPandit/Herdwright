"""Run the real `bin/zed` in the fake world: `run_zed.py <command> [args..]`.

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

ZED = Path(__file__).resolve().parents[2] / "bin" / "zed"

zed = types.ModuleType("zed")
zed.__file__ = str(ZED)
sys.path[0] = str(ZED.parent)  # as when the program is run directly: not this folder, whose `store` and `agent` are fakes
sys.argv = [str(ZED), *sys.argv[1:]]
exec(compile(ZED.read_text(), str(ZED), "exec"), zed.__dict__)
zed.SPAWN_SHELL_SECONDS = 0
if os.environ.get("FAKE_NO_WATCHER"):
    zed.arm_watcher = lambda: None
zed.main(sys.argv[1:])
