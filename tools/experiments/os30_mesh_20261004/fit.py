#!/usr/bin/env python3
"""One saved os30 seed fit of the real mesh hardware. No seed sweep/deploy."""
import argparse,os,shutil,sys
from pathlib import Path
from prepare import ROOT,OUT,stage
sys.path.insert(0,str(ROOT/'tools'))
import sweep_target_matrix as fit
def main():
    p=argparse.ArgumentParser();p.add_argument('--tag',default='mesh-v1')
    p.add_argument('--dc64',action='store_true');p.add_argument('--area',action='store_true')
    p.add_argument('--no-texmem',action='store_true',help='SM64-only private probe: remove unused dedicated texture chip')
    args=p.parse_args()
    staged=stage(OUT/('rtl-'+args.tag),compact=1,enable_dot=0,extended_isa=0,
                 code_pages=2,queue_bits=10,small_mlabs=not args.dc64)
    original_freeze=fit.freeze
    def freeze(out,rows):
        frozen=original_freeze(out,rows)
        for source in staged.glob('*.v'):
            relative='src/fpga/targets/pocket/core_top.v' if source.name=='core_top.v' else 'src/fpga/common/'+source.name
            shutil.copy2(source,frozen/relative)
        if args.dc64:
            cpu=ROOT/'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30_worker.v'
            dest=frozen/'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
            shutil.copy2(cpu,dest)
            rows[0]['mesh_cpu_data_cache_kib']=64
            rows[0]['cpu_sha256']=fit.digest(dest)
            rows[0]['generated_cpu_sha256']=fit.digest(cpu)
            fit.save(out/'inventory.json',rows)
        qsf=frozen/'src/fpga/targets/pocket/ap_core.qsf'
        extra=['gpu_span_walker.v']+[p.name for p in staged.glob('gpu_mesh*.v')]
        qsf.write_text(qsf.read_text()+''.join('\nset_global_assignment -name VERILOG_FILE ../../common/'+p for p in extra)+'\n')
        if args.area:
            # Area experiment on identical RTL; timing remains a required result.
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name OPTIMIZATION_MODE BALANCED\n'
                    'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA\n'
                    'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF\n')
        (out/'NOT_DEPLOYABLE.txt').write_text('Private mesh hardware: application producer and full transform/clip program not release validated.\n')
        fit.save(out/'frozen-sources.json',{str(p.relative_to(frozen)):fit.digest(p) for p in frozen.rglob('*') if p.is_file()})
        return frozen
    fit.freeze=freeze
    original_inventory=fit.inventory
    def inventory(**kwargs):
        rows=original_inventory(**kwargs)
        for row in rows:
            if row['variant']=='os30':
                remove={'INCLUDE_VERT_TRI','INCLUDE_XFORM','INCLUDE_GPU_SM64_GEOMETRY',
                        'INCLUDE_GPU_GEOMETRY_WORKER','INCLUDE_XFORM_VERTEX_RAM','INCLUDE_XFORM_RATIO_PIPE'}
                if args.dc64:remove.add('INCLUDE_TEX_QUEUE_MLAB')
                if args.no_texmem:remove.add('INCLUDE_TEX_MEM')
                row['defs']=' '.join(x for x in row['defs'].split() if x not in remove)
                row['defs']+=' INCLUDE_GPU_PLANE_INPUT INCLUDE_MIXER_RAMP_PIPE'
        return rows
    fit.inventory=inventory
    for key in ['OS30_GEOMETRY','OS30_GEOMETRY_WORKER','OS30_SDRAM166']:os.environ.pop(key,None)
    os.environ['OS30_DEPTH_PREFETCH']='1'
    sys.argv=[sys.argv[0],'--target','pocket','--variant','os30','--seed-offsets','0',
              '--jobs','1','--processors','4','--output',str(OUT/('fit-'+args.tag))]
    fit.main()
if __name__=='__main__':main()
