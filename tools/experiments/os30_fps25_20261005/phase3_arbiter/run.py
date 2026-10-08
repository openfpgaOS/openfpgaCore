#!/usr/bin/env python3
"""Private threshold8/2 replay with passive testbench wires and host statistics."""
import argparse, hashlib, json, re, shutil, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
BASE=ROOT/'docs/measurements/os30-simple-20261005/validation/replay-single-A'
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase3/arbiter'
PRIOR=ROOT/'docs/measurements/os30-fps25-20261005/validation/replays-single-A/phase2-exact-v1-fps/2'
FRAMES=(360,600,720)
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,data): p.write_text(json.dumps(data,indent=2)+'\n')
def replace(s,a,b):
    assert s.count(a)==1,(a,s.count(a))
    return s.replace(a,b)

def prepare():
    m=json.loads((BASE/'manifest.json').read_text())
    assert sha(BASE/'obj/Vtb_gpu')==m['simulator_sha256']
    for rel,h in m['sources'].items(): assert sha(BASE/rel)==h,rel
    dest=OUT/'frozen';assert not dest.exists()
    OUT.mkdir(parents=True,exist_ok=True);shutil.copytree(BASE/'frozen',dest)
    p=dest/'tb_gpu.v';s=p.read_text()
    s=replace(s,'    parameter INCLUDE_PERSPECTIVE = 1,','    parameter CPU_FAIR_THRESHOLD = 8,\n    parameter INCLUDE_PERSPECTIVE = 1,')
    ports='''    // Passive observations only; no new behavior in synthesized production RTL.
    output wire obs_cpu_valid, obs_cpu_ready, obs_cpu_rvalid, obs_cpu_rlast,
    output wire obs_audio_valid, obs_audio_ready, obs_audio_rvalid, obs_audio_rlast,
    output wire obs_scan_req, obs_scan_done,
    output wire [1:0] obs_arb_state, obs_grant,
    output wire obs_gpu_arvalid, obs_gpu_arready, obs_gpu_awvalid, obs_gpu_awready,
    output wire obs_gpu_wvalid, obs_gpu_wready,
'''
    s=replace(s,'    // Status outputs\n',ports+'    // Status outputs\n')
    s=replace(s,'tb_sdram_cont memory (','''assign obs_cpu_valid=ag1_valid;
assign obs_cpu_ready=ag1_ready;
assign obs_audio_valid=ag3_valid;
assign obs_audio_ready=ag3_ready;
assign obs_scan_req=scan_req;
assign obs_scan_done=scan_done;
assign obs_gpu_arvalid=gpu_rd_arvalid;
assign obs_gpu_arready=gpu_rd_arready;
assign obs_gpu_awvalid=gpu_wr_awvalid;
assign obs_gpu_awready=gpu_wr_awready;
assign obs_gpu_wvalid=gpu_wr_wvalid;
assign obs_gpu_wready=gpu_wr_wready;
tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD)) memory (''')
    for who in ('cpu','audio'):
        n=1 if who=='cpu' else 3
        for sig in ('rvalid','rlast'):
            s=replace(s,f'    .m{n}_{sig}(),',f'    .m{n}_{sig}(obs_{who}_{sig}),')
    s=replace(s,'    .inj_burst_rd(scan_req),','    .dbg_arb_state(obs_arb_state),\n    .dbg_grant(obs_grant),\n    .inj_burst_rd(scan_req),')
    p.write_text(s)
    p=dest/'tb_sdram_cont.v';s=p.read_text()
    s=replace(s,'module tb_sdram_cont #(\n','module tb_sdram_cont #(\n    parameter CPU_FAIR_THRESHOLD=8,\n')
    s=replace(s,'axi_sdram_arbiter #(.GPU_WRITE_QUEUE_DEPTH(16))','axi_sdram_arbiter #(.GPU_WRITE_QUEUE_DEPTH(16), .CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD))')
    p.write_text(s)
    p=dest/'replay.cpp';s=p.read_text()
    s=replace(s,'static uint64_t cycles, busy_cycles;','static uint64_t cycles, busy_cycles;\n#include "observer.hpp"')
    s=replace(s,'        tb->clk = 0; tb->eval();','        tb->clk = 0; tb->eval();\n        observe_tick();')
    s=replace(s,'    const uint64_t start_cycles = cycles, start_busy = busy_cycles;','    const uint64_t start_cycles = cycles, start_busy = busy_cycles;\n    obs_enabled = true;')
    s=replace(s,'    tb->final();','    observe_report();\n    tb->final();')
    p.write_text(s);shutil.copy2(HERE/'observer.hpp',dest/'observer.hpp')
    changed=[]
    for rel,h in m['sources'].items():
        if rel.startswith('frozen/') and sha(OUT/rel)!=h: changed.append(rel)
    assert sorted(changed)==['frozen/replay.cpp','frozen/tb_gpu.v','frozen/tb_sdram_cont.v'],changed
    report=dict(scope=__doc__,baseline_manifest_sha256=sha(BASE/'manifest.json'),baseline_simulator_sha256=m['simulator_sha256'],
        changed=changed,production_sources_unchanged=True,thresholds=[8,2],frames=list(FRAMES),traffic=2,
        sources={str(p.relative_to(OUT)):sha(p) for p in sorted(dest.rglob('*')) if p.is_file()},
        scripts={str(p.relative_to(ROOT)):sha(p) for p in (Path(__file__),HERE/'observer.hpp')})
    write(OUT/'inputs.json',report)

def build(threshold):
    report=json.loads((OUT/'inputs.json').read_text())
    for rel,h in report['sources'].items(): assert sha(OUT/rel)==h,rel
    dest=OUT/f'threshold{threshold}';dest.mkdir(exist_ok=False)
    m=json.loads((BASE/'manifest.json').read_text());cmd=[]
    for arg in m['command']:
        arg=arg.replace(str(BASE/'frozen'),str(OUT/'frozen')).replace(str(BASE/'obj'),str(dest/'obj'))
        cmd.append(arg)
    cmd.append(f'-GCPU_FAIR_THRESHOLD={threshold}')
    with (dest/'build.log').open('w') as f: subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(dest/'build.json',dict(threshold=threshold,command=cmd,simulator_sha256=sha(dest/'obj/Vtb_gpu'),inputs_sha256=sha(OUT/'inputs.json')))

def run(threshold):
    dest=OUT/f'threshold{threshold}';m=json.loads((dest/'build.json').read_text());exe=dest/'obj/Vtb_gpu'
    assert sha(exe)==m['simulator_sha256']
    rows=[]
    for frame in FRAMES:
        stem=f'frame_{frame:05d}';vec=PRIOR/(stem+'.vec');binary=dest/(stem+'.bin');log=dest/(stem+'.log')
        assert not log.exists()
        cmd=[str(exe),str(vec),str(binary),'+memory_traffic=2']
        with log.open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
        stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',log.read_text())}
        assert stats['cycles']==stats['window_cycles']
        assert sum(stats[k] for k in ('gpu_service','cpu_service','bridge_service','audio_service','idle'))==stats['cycles']
        assert stats['scan_latency_max']<6361,'Missed synthetic scanout scheduling deadline'
        data=binary.read_bytes();old=(PRIOR/(stem+'.bin')).read_bytes()
        assert len(data)==len(old)==307212
        assert data==old,'Framebuffer/depth/fence/full-memory hash difference'
        if threshold==8:
            oldstats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',(PRIOR/(stem+'.log')).read_text())}
            for key in ('cycles','busy_cycles','batches'):assert stats[key]==oldstats[key],key
        rows.append(dict(frame=frame,threshold=threshold,stats=stats,vector_sha256=sha(vec),output_sha256=sha(binary),original_output_sha256=sha(PRIOR/(stem+'.bin')),
            log_sha256=sha(log),original_log_sha256=sha(PRIOR/(stem+'.log')),exact_original_output=True,
            baseline_noninterference_checked=threshold==8,simulator_sha256=sha(exe)))
        write(dest/'results.json',rows)
        print(threshold,frame,stats['cycles'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','build','run']);p.add_argument('--threshold',type=int,choices=[8,2]);a=p.parse_args()
    if a.action=='prepare':prepare()
    else:
        assert a.threshold is not None
        (build if a.action=='build' else run)(a.threshold)
