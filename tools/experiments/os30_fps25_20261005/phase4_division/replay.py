#!/usr/bin/env python3
"""Reuse retained GPU A replay and its original F5 comparisons; no new RTL."""
import importlib.util,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).parents[1]/'validation'
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('retained_replay',HERE/'replay.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase4/division'
m.capture_path=lambda label:m.OUT/('capture-'+label)
m.main()
