#!/usr/bin/env python3
"""Passive exact reciprocal-key reuse and source-blocking ceiling."""
import importlib.util,json,shutil,subprocess,sys
from pathlib import Path
import phase10_gpu_critical as c
P=c.P;ROOT=c.ROOT;sha=c.sha;save=c.save;rep=c.rep
OLD=c.OUT/'coupled';OUT=P/'gpu-reciprocal-trace-v2'
PROBE='''
integer reciprocal_file;
initial begin reciprocal_file=$fopen("REPLACE_RECIP_TRACE_PATH","w");if(!reciprocal_file)$fatal(1,"Trace open failed");end
reg [63:0] reciprocal_cycle=0;
always @(posedge clk)if(reset_n&&critical_enable)begin
 reciprocal_cycle<=reciprocal_cycle+1;
 if(gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active)begin
  if(gpu.persp_pss==gpu.PSS_CLZ)
   $fdisplay(reciprocal_file,"RECIP_KEY %0d %0d %0d %0d %0d %0d %0d %0d",reciprocal_cycle,
    gpu.persp_zinv_abs_r,gpu.persp_pass,
    gpu.sp_zinv_step_zero&&gpu.sp_zinv!=32'h00010000,
    gpu.src_done,gpu.fp_pipe_stall,gpu.persp_seg_a_ready,crit_zero_uv);
  if(gpu.persp_pss==gpu.PSS_RECIP_W||gpu.persp_pss==gpu.PSS_RECIP_SHIFT
   ||gpu.persp_pss>=gpu.PSS_NR_MUL_X&&gpu.persp_pss<=gpu.PSS_NR_CAPTURE)
   $fdisplay(reciprocal_file,"RECIP_STAGE %0d %0d %0d",reciprocal_cycle,gpu.persp_pss,
    gpu.persp_issue_stall&&!gpu.fp_pipe_stall&&!gpu.src_done);
 end
end
'''
def prepare():
    OUT.mkdir(exist_ok=False);shutil.copytree(OLD/'model/frozen',OUT/'model/frozen')
    shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
    p=OUT/'model/frozen/tb_gpu.v';s=rep(p.read_text(),'endmodule',PROBE.replace('REPLACE_RECIP_TRACE_PATH',str(OUT/'reciprocal.txt'))+'\nendmodule');p.write_text(s)
    cmd=[x.replace(str(OLD),str(OUT)) for x in json.loads((OLD/'build-command.json').read_text())]
    save(OUT/'build-command.json',cmd)
    save(OUT/'inputs.json',dict(scope=__doc__,parent=str(OLD),sources={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file()}))
def build():
    with (OUT/'build-qsim.log').open('x') as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
    cmd=json.loads((OUT/'build-command.json').read_text())
    with (OUT/'model/build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    save(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))
def run():
    path=ROOT/'tools/experiments/os30_overlap_20261006/run.py';spec=importlib.util.spec_from_file_location('runner',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.OUT=OUT;sys.argv=[str(path),'window-8','--elf',str(P.parent/'phase9/checks-state-f11/app.elf')];m.main()
    assert 'FATAL' not in (OUT/'run-window-8/run.log').read_text()
def analyze():
    run=OUT/'run-window-8';events=[]
    for line in (OUT/'reciprocal.txt').read_text().splitlines():
        if line.startswith('RECIP_KEY '):
            n=list(map(int,line.split()[1:]));assert len(n)==8
            events.append(dict(zip(['cycle','key','pass','bias','src_done','pipe_stall','segment_a_ready','zero_uv'],n),skip_cycles=0,source_blocked_cycles=0))
        elif line.startswith('RECIP_STAGE '):
            cycle,state,critical=map(int,line.split()[1:]);assert events and cycle>events[-1]['cycle']
            events[-1]['skip_cycles']+=1;events[-1]['source_blocked_cycles']+=critical
    assert events
    results=[]
    for size,kind in [(n,k) for n in (1,2,4,16,128) for k in ('fully_associative_lru','direct_mapped_xorfold')]:
        lru=[];direct=[None]*size;hits=cycles=critical=nonzero=first=0;passes=[0,0,0]
        for e in events:
            key=e['key'];index=(key^(key>>8)^(key>>16)^(key>>24))&(size-1)
            hit=key in lru if kind=='fully_associative_lru' else direct[index]==key
            if hit:
                hits+=1;cycles+=e['skip_cycles'];critical+=e['source_blocked_cycles']
                if not e['zero_uv']:nonzero+=e['source_blocked_cycles']
                passes[e['pass']]+=e['source_blocked_cycles']
                if e['pass'] in (0,1):first+=e['source_blocked_cycles']
            if key in lru:lru.remove(key)
            direct[index]=key
            lru.insert(0,key);lru=lru[:size]
        results.append(dict(entries=size,kind=kind,critical_by_pass_ms=[x/800000 for x in passes],lookups=len(events),hits=hits,hit_fraction=hits/len(events),skipped_state_ms_per_frame=cycles/800000,
            source_blocked_ms_per_frame=critical/800000,source_blocked_nonzero_uv_ms_per_frame=nonzero/800000,first_two_passes_blocked_ms_per_frame=first/800000))
    old=OLD/'run-baseline-8'
    for n in ['coupled.json','counters.csv','events.csv','scene.csv','geometry.csv','culling.csv',*[f'frame_{n:05}.bin' for n in range(360,368)]]:assert (run/n).read_bytes()==(old/n).read_bytes(),n
    save(OUT/'events.json',events)
    save(OUT/'result.json',dict(passed=True,passive_cycle_and_outputs_exact=True,lookup_count=len(events),results=results,
        scope='Fully associative LRU exact abs-denominator cache of unbiased NR results, cold at frame360. Future per-hit bias preserved separately. State-skipping counts are trace upper bounds, not measured frame-time improvements.',
        limitations=['Same eight fixed-step frames only. No cache RTL, area, timing or production change.',
            'Source-blocked count excludes simultaneous pixel-pipe stalls. It does not model changed future scheduling or transform all eight skipped cycles directly into frame savings.',
            'One global cache across spans/materials, with exact32-bit abs key and retained legacyQ16 format. A cache that invalidates more often has lower reuse.']))
    save(OUT/'final-manifest.json',dict(files={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts and 'build' not in p.parts},helper_sha256=sha(Path(__file__))))
    print(json.dumps(results,indent=2))
if __name__=='__main__':
    for a in sys.argv[1:]:globals()[a]()
