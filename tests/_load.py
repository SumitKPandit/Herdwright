"""Load a bin/hw-<name> script (no .py extension) as a module for unit tests."""
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import os
import sys

# a worker session carries these; tests must not depend on them
for _name in [k for k in os.environ if k.startswith(("HW_", "ZED_"))]:  # ZED_: HW_ before the rename
    del os.environ[_name]

HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME / "bin" / "lib"))


def load(name):
    path = str(HOME / "bin" / name)
    loader = SourceFileLoader(name.replace("-", "_"), path)
    spec = spec_from_loader(loader.name, loader)
    mod = module_from_spec(spec)
    loader.exec_module(mod)
    return mod
