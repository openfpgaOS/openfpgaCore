#!/usr/bin/env python3
"""Replay the single cold-helper layout trial using its original captured bytes."""
import importlib.util
from pathlib import Path
import sys

p=Path(__file__).with_name('phase4_combined_validate.py')
spec=importlib.util.spec_from_file_location('cold_validator',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.OUT=m.CAMP/'phase4/combined-cold/active';m.OUT.mkdir(exist_ok=True)
# Adapter keeps the retained validator fixed; private aliases contain symlinks
# only to immutable captures and are never publication destinations.
m.CAP=m.CAMP/'phase4/combined-cold/capture-aliases';m.CAP.mkdir(exist_ok=True)
mode=sys.argv[1];assert mode in ('normal','fps')
target=m.CAMP/'phase4/texmem-capture-v2'/('capture-combined-cold-'+mode)
alias=m.CAP/('capture-combined-'+mode)
if not alias.exists():alias.symlink_to(target,target_is_directory=True)
assert alias.resolve()==target
m.analyze(mode)
m.replay(mode,[1,2])
