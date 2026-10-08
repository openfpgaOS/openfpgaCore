#!/usr/bin/env python3
"""Run the full os30 acceptance fixture with the independent worker enabled."""
import copy
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'tools'))
import check_gpu_target_matrix as check
os.environ['OS30_GEOMETRY']='1'
os.environ['OS30_DEPTH_PREFETCH']='1'
os.environ['OS30_GEOMETRY_WORKER']='0'
row=next(r for r in check.inventory() if r['variant']=='os30')
if '--legacy' not in sys.argv:
    if '--mlab-queues' not in sys.argv:
        row['defs']=' '.join(d for d in row['defs'].split() if d!='INCLUDE_TEX_QUEUE_MLAB')
    row['defs']+=' EXCLUDE_GPU_XFORM_MAC INCLUDE_GPU_GEOMETRY_WORKER'
check.inventory=lambda **kwargs:[copy.deepcopy(row)]
label=sys.argv[1]
sys.argv=[sys.argv[0],'--variant','os30','--jobs','1','--output',
          str(ROOT/'docs/measurements/os30-worker-20261003'/('accept-'+label))]
check.main()
