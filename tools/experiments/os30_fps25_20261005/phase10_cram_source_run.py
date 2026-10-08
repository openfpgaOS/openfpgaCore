#!/usr/bin/env python3
"""Run a bounded actual CRAM1 vertex source experiment (CPU model unchanged)."""
import importlib.util,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
p=ROOT/'tools/experiments/os30_overlap_20261006/run.py'
spec=importlib.util.spec_from_file_location('base_coupled_run',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/cram-source-coupled-v1'
m.main()
dest=m.OUT/('run-'+sys.argv[1]);p=dest/'inputs.json'
v=json.loads(p.read_text());v['environment']['CRAM_VERTEX_ROUTE']=os.environ.get('CRAM_VERTEX_ROUTE','0')
v['limits']+=' Immutable source preload and source address translation are outside measured time; observed source pool only.'
p.write_text(json.dumps(v,indent=2)+'\n')
assert 'FATAL' not in (dest/'run.log').read_text()
