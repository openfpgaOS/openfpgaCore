#!/usr/bin/env python3
"""Saved-seed os30 geometry footprint screen; production definitions are unchanged.

This measures the existing fixed-point transform/light implementation. It is
not a completed SM64 accelerator or a performance measurement. Each process
overrides only its private inventory and freezes all source inputs before work.
"""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-geometry-fit-20261002'
sys.path.insert(0,str(ROOT/'tools'))
import sweep_target_matrix as fit
import check_gpu_target_matrix as acceptance

CUTS={
    'matrix_ram': {'EXCLUDE_GPU_XFORM_MAC'},
    'matrix_regs': {'EXCLUDE_GPU_XFORM_MAC','INCLUDE_XFORM_VERTEX_RAM'},
    'matrix_light_ram': {'EXCLUDE_GPU_XFORM_MAC','EXCLUDE_GPU_LIGHT'},
    'matrix_light_regs': {'EXCLUDE_GPU_XFORM_MAC','EXCLUDE_GPU_LIGHT','INCLUDE_XFORM_VERTEX_RAM'},
}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=['fit','accept'])
    p.add_argument('candidate',choices=CUTS)
    args=p.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    inventory=fit.inventory
    baseline=next(r for r in inventory() if r['variant']=='os30')
    assert 'INCLUDE_Z_PREFETCH' not in baseline['defs'].split()
    assert baseline['seed']==2
    row=copy.deepcopy(baseline)
    row['original_defs']=row['defs']
    row['defs']=' '.join(d for d in row['defs'].split() if d not in CUTS[args.candidate])
    row['experiment']=args.candidate
    (OUT/f'{args.candidate}-definitions.json').write_text(json.dumps(row,indent=2)+'\n')
    replacement=lambda **kwargs:[copy.deepcopy(row)]
    dest=OUT/f'{args.stage}-{args.candidate}'
    sys.argv=[sys.argv[0],'--output',str(dest),'--variant','os30','--jobs','1']
    if args.stage=='fit':
        fit.inventory=replacement
        sys.argv+=['--target','pocket','--seed-offsets','0','--processors','4']
        fit.main()
    else:
        acceptance.inventory=replacement
        acceptance.main()

if __name__=='__main__':
    main()
