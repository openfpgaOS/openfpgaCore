from pathlib import Path
import json,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parents[3]
HERE=ROOT/'docs/measurements/os30-depth-prefetch-20261002'
for label in sys.argv[1:]:
    src=HERE/label/'os30' if label.startswith('gpu-') else HERE/label
    out=HERE/(label+'-ordered');out.mkdir(exist_ok=False)
    if label.startswith('gpu-'):
        manifest=json.loads((src/'manifest.json').read_text())
        common=HERE/label/'frozen/src/fpga/common';test=HERE/label/'frozen/src/fpga/test'
        sources=[test/'tb_gpu.v',*[common/n for n in ['gpu_core.v','gpu_edge_walker.v','gpu_tex_cache.v','gpu_color_depth_cache.sv']]]
        cmd=['verilator','--cc','--exe','--build','--trace','-j','3','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_gpu','--Mdir',str(out/'obj'),'-I'+str(common),'-CFLAGS',' '.join(manifest['cflags'])+' -I'+str(test),*['+define+'+d for d in manifest['variant']['defs'].split()],*manifest['flags'],*map(str,sources),str(ROOT/'tools/tests/gpu_depth_prefetch.cpp')]
    else:
        report=json.loads((src/'results.json').read_text());cmd=report['command'][:]
        cmd[cmd.index('--Mdir')+1]=str(out/'obj')
        cmd[cmd.index('-CFLAGS')+1]+=' -I'+str(src/'frozen')
        cmd=[str(ROOT/'tools/tests/gpu_depth_prefetch.cpp') if c.endswith('tb_gpu_retained_main.cpp') else c for c in cmd]
    with (out/'build.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    modes=[('quiet',[]),('gapped',['+gpu_rd_latency=24','+gpu_rd_latency_var=1','+gpu_wr_latency=17','+gpu_rd_beat_gap=7'])] if label.startswith('gpu-') else [(str(i),[f'+memory_traffic={i}']) for i in range(3)]
    for mode,args in modes:
        with (out/(mode+'.log')).open('w') as log:subprocess.run([str(out/'obj/Vtb_gpu'),*args],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        print(label,mode,(out/(mode+'.log')).read_text().splitlines()[-1],flush=True)
