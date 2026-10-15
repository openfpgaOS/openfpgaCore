#!/usr/bin/env python3
"""Fresh passive native task capture; never modifies a bound replay or app."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time

HERE=Path(__file__).resolve().parent
P=HERE.parent.parent
D=P/'depth-hazard-v1'
Q=P/'queued-front-v1'
OUT=HERE/'native-task-v3'
BASE=D/'baseline-v1'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p): return json.loads(p.read_text())
def write(p,v): p.write_text(json.dumps(v,indent=2)+'\n')
def verify(d):
    for p,h in d.items(): assert sha(Path(p))==h,p
def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a))
    return s.replace(a,b)

def build():
    OUT.mkdir(exist_ok=False)
    base=read(BASE/'build.json');verify(base['inputs_sha256'])
    raw_source=Path(base['model'])/'frozen/src/fpga/common/geometry_raw_load.v'
    assert 'original 24-byte Vtx DMA' in raw_source.read_text()
    (OUT/'source-layout-proof.json').write_text(json.dumps({'source_vertex_bytes':24,'proof':'Actual bound raw DMA component documents24B and consumes raw_word0..5','raw_source':str(raw_source),'sha256':sha(raw_source)},indent=2)+'\n')
    symfile=Q/'app-build-v2/symbols.txt'
    symbols={}
    for line in symfile.read_text().splitlines():
        f=line.split()
        if len(f)==4:symbols[f[3]]=(int(f[0],16),int(f[1],16),f[2])
    points=['gfx_sp_vertex','load_alias_vertex','gfx_cpu_indexed_run','gfx_run_dl',
            'gfx_sp_tri1','gfx_clip_triangle.constprop.0','gfx_gpu_vtx_cache_batch_impl',
            'gfx_cpu_materialize_fog','load_alias_copy']
    sym='''#pragma once
namespace mesh_task_trace {
struct Symbol { uint32_t pc,bytes; const char* name; };
inline const std::vector<Symbol> symbols={
'''
    ranges=sorted((pc,size,name) for name,(pc,size,typ) in symbols.items() if typ in ('t','T'))
    sym+=''.join(f'{{0x{pc:08x}u,{size}u,{json.dumps(name)}}},\n' for pc,size,name in ranges)+'};\n'
    sym+='inline const std::vector<uint32_t> call_points={'+','.join(str(symbols[n][0])+'u' for n in points)+'};\n'
    sym+=f'inline constexpr uint32_t PC_gfx_sp_vertex={symbols["gfx_sp_vertex"][0]}u;\n'
    for name in ['rsp','rdp','triangle_material_ready','triangle_vc_elig']:
        sym+=f'inline constexpr uint32_t ADDR_{name}={symbols[name][0]}u;\n'
    sym+='}\n'
    (OUT/'task_symbols_v1.hpp').write_text(sym)
    source=(BASE/'native_profiled.cpp').read_text()
    source=once(source,'#include "gpu_observer.hpp"','#include "gpu_observer.hpp"\n#include "task_observer_v3.hpp"')
    source=once(source,'    gpu_bottleneck::Observer gpu_profile;','    gpu_bottleneck::Observer gpu_profile;\n    mesh_task_trace::Observer task_profile(out);')
    marker='        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;'
    source=once(source,marker,'        task_profile.sample(c,start==UINT64_MAX?0:entries,*tb,tb->vlSymsp->TOP__tb_system,tb->vlSymsp->TOP__tb_system__cpu_sys__cpu,peek);\n'+marker)
    source=once(source,'    gpu_profile.write(out+"/gpu-profile");','    task_profile.write(out);\n    gpu_profile.write(out+"/gpu-profile");')
    main=OUT/'native_profiled.cpp';main.write_text(source)
    cmd=[x.replace(str(BASE/'native_profiled.cpp'),str(main)).replace(str(BASE/'native_profiled'),str(OUT/'native_profiled')) for x in base['command']]
    cmd[1:1]=['-I'+str(HERE),'-I'+str(OUT)]
    inputs=dict(base['inputs_sha256'])
    for path in [Path(__file__),HERE/'task_observer_v3.hpp',OUT/'task_symbols_v1.hpp',main,symfile,BASE/'build.json',OUT/'source-layout-proof.json']:
        inputs[str(path)]=sha(path)
    start=time.monotonic()
    with (OUT/'build.log').open('w') as f: rc=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT).returncode
    verify(inputs)
    write(OUT/'build.json',dict(base,command=cmd,returncode=rc,seconds=time.monotonic()-start,
          inputs_sha256=inputs,task_call_points={n: symbols[n][0] for n in points},
          task_observer='passive pre-edge read-only function-entry, raw-completion and PC attribution'))
    print('build',rc)
    raise SystemExit(rc)

def run():
    spec=importlib.util.spec_from_file_location('task_replay',Q/'replay_v3.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.G=HERE
    args=argparse.Namespace(label='native-task-v3',max_cycles=15000000)
    m.run(args)

def check():
    verify(read(OUT/'build.json')['inputs_sha256'])
    baseline=read(BASE/'run/manifest.json')['outputs_sha256']
    candidate=read(OUT/'run/manifest.json')['outputs_sha256']
    # Host process provenance and stdout name the new executable; all measured
    # core state, samples and binary outputs must be byte-identical.
    excluded={'process.json','run.log'}
    compared=[];differences=[]
    for name,h in baseline.items():
        if name in excluded:continue
        a=BASE/'run'/name;b=OUT/'run'/name
        assert sha(a)==h,(name,'old evidence changed')
        if not b.exists() or sha(b)!=h: differences.append(name)
        else:compared.append(name)
    obs=read(OUT/'run/task-observer.json')
    report=dict(status='PASS_EXACT_PASSIVITY' if not differences else 'FAIL_PASSIVITY',
                compared_count=len(compared),compared=compared,differences=differences,
                excluded=sorted(excluded),observer=obs,
                original_result=read(BASE/'run/result.json'),new_result=read(OUT/'run/result.json'),
                original_manifest_sha256=sha(BASE/'run/manifest.json'),
                new_manifest_sha256=sha(OUT/'run/manifest.json'))
    write(OUT/'passivity.json',report)
    print(json.dumps({k:report[k] for k in ['status','compared_count','differences','observer']}))
    assert not differences

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['build','run','check']);a=ap.parse_args()
    globals()[a.action]()
