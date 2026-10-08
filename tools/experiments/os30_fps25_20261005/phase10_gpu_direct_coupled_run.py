#!/usr/bin/env python3
"""Run native-FMA GDR coupled windows (modeled CPU, actual GDR/GPU/memory RTL)."""
import importlib.util,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
p=ROOT/'tools/experiments/os30_overlap_20261006/run.py'
spec=importlib.util.spec_from_file_location('base_coupled_run',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10'/os.environ.get('GDR_COUPLED_LABEL','gpu-direct-coupled-v1')
m.main()
