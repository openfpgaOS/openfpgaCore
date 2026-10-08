#!/usr/bin/env python3
"""Fit a raster-only feasibility backend at saved seed 2. NOT FOR DEPLOYMENT."""
import argparse
import os
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-parallel-20261004'
sys.path.insert(0,str(ROOT/'tools'))
import sweep_target_matrix as fit


def main():
    p=argparse.ArgumentParser();p.add_argument('--edges',action='store_true');args=p.parse_args()
    original_freeze=fit.freeze
    def freeze(out,rows):
        frozen=original_freeze(out,rows)
        source=OUT/('edges-rtl' if args.edges else 'planes-rtl')
        for name in ['gpu_core.v','gpu_edge_walker.v','audio_mixer.v']:
            shutil.copy2(source/name,frozen/'src/fpga/common'/name)
        if args.edges:
            shutil.copy2(source/'gpu_span_walker.v',frozen/'src/fpga/common/gpu_span_walker.v')
            qsf=frozen/'src/fpga/targets/pocket/ap_core.qsf'
            qsf.write_text(qsf.read_text()+'\nset_global_assignment -name VERILOG_FILE ../../common/gpu_span_walker.v\n')
        (out/'NOT_DEPLOYABLE.txt').write_text('Raster backend only: upstream mesh/triangle processor absent.\n')
        fit.save(out/'frozen-sources.json',{str(p.relative_to(frozen)):fit.digest(p)
            for p in frozen.rglob('*') if p.is_file()})
        return frozen
    fit.freeze=freeze
    original_inventory=fit.inventory
    def inventory(**kwargs):
        rows=original_inventory(**kwargs)
        for row in rows:
            if row['variant']=='os30':
                remove={'INCLUDE_VERT_TRI','INCLUDE_XFORM','INCLUDE_GPU_SM64_GEOMETRY',
                        'INCLUDE_GPU_GEOMETRY_WORKER','INCLUDE_XFORM_VERTEX_RAM','INCLUDE_XFORM_RATIO_PIPE'}
                row['defs']=' '.join(x for x in row['defs'].split() if x not in remove)
                row['defs']+=' INCLUDE_GPU_PLANE_INPUT INCLUDE_MIXER_RAMP_PIPE'
        return rows
    fit.inventory=inventory
    for name in ['OS30_GEOMETRY','OS30_GEOMETRY_WORKER','OS30_SDRAM166']:
        os.environ.pop(name,None)
    os.environ['OS30_DEPTH_PREFETCH']='1'
    sys.argv=[sys.argv[0],'--target','pocket','--variant','os30','--seed-offsets','0',
              '--jobs','1','--processors','4','--output',str(OUT/('fit-backend-v2' if args.edges else 'fit-backend-v1'))]
    fit.main()


if __name__=='__main__':main()
