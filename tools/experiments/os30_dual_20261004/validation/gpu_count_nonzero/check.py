#!/usr/bin/env python3
"""Private GPU count shadow: extracted sequential checks and retained rendering."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[4]
OUT=ROOT/'docs/measurements/os30-dual-20261004/validation/gpu-count-nonzero'
sys.path.insert(0,str(ROOT/'tools'))
from sweep_target_matrix import inventory

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(cmd,log,**kwargs):
    with log.open('w') as f:subprocess.run(list(map(str,cmd)),check=True,stdout=f,stderr=subprocess.STDOUT,**kwargs)

def focused():
    dest=OUT/'focused';dest.mkdir(exist_ok=False)
    modules=[]
    for label,filename in [('baseline','baseline-gpu_core.v'),('candidate','gpu_core.v')]:
        text=(OUT/filename).read_text();candidate=label=='candidate'
        decls=[]
        for pattern in [r'reg \[15:0\] spanprod_count \[0:3\];',r'reg spanprod_cur_valid;',
                        r'reg spanprod_cur_count_valid;',r'reg \[15:0\] spanprod_cur_count_raw;']:
            found=re.findall(pattern,text);assert len(found)==1,(label,pattern);decls+=found
        if candidate:
            for pattern in [r'reg \[3:0\] spanprod_count_nonzero;',r'reg spanprod_cur_count_nonzero_raw;']:
                found=re.findall(pattern,text);assert len(found)==1;decls+=found
        valid=re.search(r'    if \(!reset_n \|\| soft_reset\)\n        spanprod_cur_valid.*?spanprod_cur_valid <= 1\'b1;',text,re.S)[0]
        captures=re.findall(r'        spanprod_cur_count(?:_raw|_valid|_nonzero_raw) <= [^;]+;',text)
        assert len(captures)==(3 if candidate else 2)
        write=re.search(r'    if \(spanprod_count_we\)(?: begin)?\n        spanprod_count\[spanprod_count_wa\] <= spanprod_count_wd;'+
            (r'\n        spanprod_count_nonzero\[spanprod_count_wa\] <= [^;]+;\n    end' if candidate else ''),text)[0]
        assigns=re.search(r'assign spanprod_cur_count = .*?assign spanprod_cur_nonzero = [^;]+;',text,re.S)[0]
        modules.append(f'''module {label}(input clk,reset_n,soft_reset,select_req,
 input spanprod_count_we,input [1:0] spanprod_count_wa,spanprod_idx,
 input [15:0] spanprod_count_wd,input [3:0] spanprod_cnt_valid,
 output [15:0] spanprod_cur_count,output spanprod_cur_nonzero);
 wire spanprod_select=reset_n&&!soft_reset&&select_req;
 {chr(10).join(decls)}
 always @(posedge clk) begin
 {valid}
 if(spanprod_select)begin
 {chr(10).join(captures)}
 end
 {write}
 end
 {assigns}
 endmodule
''')
    ports='clk,reset_n,soft_reset,select_req,spanprod_count_we,spanprod_count_wa,spanprod_idx,spanprod_count_wd,spanprod_cnt_valid'
    modules.append(f'''module tb(input clk,reset_n,soft_reset,select_req,
 input spanprod_count_we,input [1:0] spanprod_count_wa,spanprod_idx,
 input [15:0] spanprod_count_wd,input [3:0] spanprod_cnt_valid,
 output [15:0] baseline_count,candidate_count,output baseline_nonzero,candidate_nonzero);
 baseline old_gpu({ports},baseline_count,baseline_nonzero);
 candidate new_gpu({ports},candidate_count,candidate_nonzero);
 endmodule
''')
    (dest/'tb.v').write_text('\n'.join(modules))
    shutil.copy2(HERE/'focused.cpp',dest/'main.cpp')
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','tb',
             '--Mdir',dest/'obj',dest/'tb.v',dest/'main.cpp']
    run(command,dest/'build.log');run([dest/'obj/Vtb'],dest/'test.log')
    (dest/'manifest.json').write_text(json.dumps(dict(command=list(map(str,command)),
        inputs={str(p):sha(p) for p in [OUT/'gpu_core.v',OUT/'baseline-gpu_core.v',dest/'tb.v',dest/'main.cpp']},
        simulator_sha256=sha(dest/'obj/Vtb'),log_sha256=sha(dest/'test.log')),indent=2)+'\n')
    print((dest/'test.log').read_text(),flush=True)

def rendering():
    for key in ['OS30_GEOMETRY','OS30_GEOMETRY_WORKER','OS30_DEPTH_PREFETCH','OS30_SDRAM166']:os.environ.pop(key,None)
    row=next(x for x in inventory() if x['target']=='pocket' and x['variant']=='os30')
    dest=OUT/'rendering';dest.mkdir(exist_ok=False)
    common=dest/'common';test=dest/'test'
    shutil.copytree(ROOT/'src/fpga/common',common);test.mkdir()
    for path in [ROOT/'src/fpga/test/tb_gpu.v',ROOT/'src/fpga/test/tb_gpu_acceptance_main.cpp',
                 ROOT/'src/fpga/test/tb_gpu_retained_main.cpp',*sorted((ROOT/'src/fpga/test').glob('*.h'))]:shutil.copy2(path,test/path.name)
    target=ROOT/'src/fpga/targets/pocket';definitions=['+define+'+x for x in row['defs'].split()]
    cmd=['verilator','-E','-P','-I'+str(target),'-I'+str(target/'apf'),*definitions,str(target/'core_top.v')]
    pre=subprocess.run(cmd,check=True,text=True,capture_output=True).stdout;(dest/'preprocessed.v').write_text(pre)
    match=re.search(r'gpu_core\s*#\((.*?)\)\s*gpu\s*\(',pre,re.S)
    block=re.sub(r'//[^\n]*','',match[1]);params={k:int(v) for k,v in re.findall(r'\.(\w+)\(\s*(\d+)\s*\)',block)}
    assert len(params)==block.count('.')
    core=(common/'gpu_core.v').read_text();core=core[core.index('module gpu_core'):core.index('// AXI4 Read Master')]
    defaults={k:int(v) for k,v in re.findall(r'parameter\s+(\w+)\s*=\s*(\d+)\s*[,\n]',core)};defaults.update(params)
    fixture=(test/'tb_gpu.v').read_text();forwarded=re.findall(r'parameter\s+(\w+)\s*=',fixture[:fixture.index('input  wire')])
    flags=['-G'+k+'='+str(defaults[k]) for k in forwarded]
    cflags=['-std=c++17','-O2']
    if 'INCLUDE_TRANSLUC' in row['defs'].split():cflags+=['-DGPU_TEST_TRANSLUC']
    if not defaults['INCLUDE_COMPACT_SPAN'] and not defaults['INCLUDE_COLUMN_LIST']:cflags+=['-DGPU_TEST_OS30_LEAN']
    for feature,define,enabled in [('INCLUDE_DIRECT_COLOR','GPU_TEST_TRUECOLOR',True),('INCLUDE_PERSPECTIVE','GPU_TEST_NO_PERSPECTIVE',False),('INCLUDE_DEPTH','GPU_TEST_NO_DEPTH',False),('INCLUDE_PARAM_SPAN_LIST','GPU_TEST_NO_PARAM_SPAN_LIST',False),('INCLUDE_XFORM_RGB','GPU_TEST_XFORM',True),('INCLUDE_PARAM_TRI','GPU_TEST_NO_PARAM_TRI',False),('INCLUDE_VERT_TRI','GPU_TEST_NO_VERT_TRI',False),('INCLUDE_PALETTE','GPU_TEST_NO_PALETTE',False),('INCLUDE_PARAM_SPAN_Q29','GPU_TEST_NO_PARAM_SPAN_Q29',False),('INCLUDE_COMMAND_DMA','GPU_TEST_NO_COMMAND_DMA',False),('INCLUDE_CLIP_TRI','GPU_TEST_NO_CLIP_TRI',False),('INCLUDE_GPU_XFORM_MAC','GPU_TEST_NO_MAC',False)]:
        if bool(defaults[feature])==enabled:cflags+=['-D'+define]
    if 'INCLUDE_GPU_SM64_GEOMETRY' in row['defs'].split():cflags+=['-DGPU_TEST_NO_MAC','-DGPU_TEST_GEOMETRY']
    # Observation-only invariant on the actual GPU, enabled in both builds.
    needle='// ============================================================'
    at=fixture.index(needle,fixture.index(');'))
    fixture=fixture[:at]+'''always @(negedge clk) if(reset_n) begin
    if(gpu.spanprod_cur_nonzero !== (gpu.spanprod_cur_count != 16'd0))
        $fatal(1,"GPU count shadow disagrees with effective count");
end
'''+fixture[at:]
    (test/'tb_gpu.v').write_text(fixture)
    manifest=dict(variant=row,parameters=defaults,flags=flags,cflags=cflags,
        sources={str(p):sha(p) for p in [OUT/'gpu_core.v',OUT/'baseline-gpu_core.v',
        *common.glob('*.*'),*test.glob('*.*')] if p.is_file()})
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    scenarios=[('quiet',[]),('delayed',['+gpu_rd_latency=24','+gpu_rd_latency_var=1','+gpu_wr_latency=17']),
               ('gapped',['+gpu_rd_latency=24','+gpu_rd_latency_var=1','+gpu_wr_latency=17','+gpu_rd_beat_gap=7'])]
    def one(label):
        job=dest/label;job.mkdir();source=OUT/('baseline-gpu_core.v' if label=='baseline' else 'gpu_core.v')
        sources=[test/'tb_gpu.v',source,common/'gpu_geometry_worker.v',common/'gpu_edge_walker.v',
                 common/'gpu_tex_cache.v',common/'gpu_color_depth_cache.sv',test/'tb_gpu_retained_main.cpp']
        command=['verilator','--cc','--exe','--build','--trace','-j','2','--assert','-Wall','-Wno-fatal','-Wno-BADVLTPRAGMA',
            '--top-module','tb_gpu','--Mdir',str(job/'obj'),'-I'+str(common),'-CFLAGS',' '.join(cflags),*definitions,*flags,*map(str,sources)]
        run(command,job/'build.log');results={}
        for name,plus in scenarios:
            log=job/(name+'.log');run([job/'obj/Vtb_gpu',*plus],log,cwd=job,timeout=300)
            text=log.read_text();m=re.search(r'Acceptance Results: (\d+) passed, (\d+) failed',text)
            assert m and int(m[1])>0 and int(m[2])==0
            cycles={k:int(v) for k,v in re.findall(r'^CYCLES (\w+) (\d+)$',text,re.M)}
            assert len(cycles)==38,len(cycles)
            results[name]=dict(passed=int(m[1]),cycles=cycles,log_sha256=sha(log))
        (job/'manifest.json').write_text(json.dumps(dict(command=command,simulator_sha256=sha(job/'obj/Vtb_gpu')),indent=2)+'\n')
        return label,results
    with ThreadPoolExecutor(max_workers=2) as pool:results=dict(pool.map(one,['baseline','candidate']))
    for name,_ in scenarios:
        for field in ['passed','cycles']:assert results['baseline'][name][field]==results['candidate'][name][field],(name,field)
    (dest/'results.json').write_text(json.dumps(dict(identical_pixels_and_cycles=True,runs=results),indent=2)+'\n')
    print('PASS: 38 retained GPU workloads have identical pixels and cycles across all 3 latency scenarios.',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['focused','rendering'])
    parser.add_argument('--output',type=Path,default=OUT,
                        help='Fresh evidence directory; frozen baseline/candidate are copied when needed.')
    args=parser.parse_args();destination=args.output.resolve()
    if destination!=OUT:
        destination.mkdir(parents=True,exist_ok=True)
        for filename in ['baseline-gpu_core.v','gpu_core.v','manifest.json']:
            target=destination/filename
            if target.exists():assert sha(target)==sha(OUT/filename),target
            else:shutil.copy2(OUT/filename,target)
        OUT=destination
    if args.action=='focused':focused()
    else:rendering()
