#!/usr/bin/env python3
"""Bounded actual LOD4/GPU coupled windows, with an estimated CPU."""
import importlib.util,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
p=ROOT/'tools/experiments/os30_overlap_20261006/run.py'
spec=importlib.util.spec_from_file_location('base_coupled_run',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10'/os.environ.get('LOD4_COUPLED_LABEL','load-alias-coupled-v1')
m.main()
assert 'FATAL' not in (m.OUT/('run-'+sys.argv[1])/'run.log').read_text()
