#!/usr/bin/env python3
"""Active-capacity replay, isolated from the retained no-capacity coverage."""
import importlib.util
from pathlib import Path
import sys

p=Path(__file__).with_name('phase4_combined_validate.py')
spec=importlib.util.spec_from_file_location('active_validator',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.OUT=m.CAMP/'phase4/combined/active'
m.OUT.mkdir(exist_ok=True)
m.CAP=m.CAMP/'phase4/texmem-capture-v2'
action,mode=sys.argv[1:]
assert mode in ('normal','fps')
if action=='replay':m.replay(mode,[1,2])
elif action=='analyze':m.analyze(mode)
else:raise ValueError(action)
