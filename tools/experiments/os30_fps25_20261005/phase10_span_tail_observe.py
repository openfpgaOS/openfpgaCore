#!/usr/bin/env python3
"""Passive bound for final-record lookahead into the following walker chunk."""
import importlib.util,json,re,shutil,subprocess,sys
from pathlib import Path
import phase10_gpu_critical as c
ROOT=c.ROOT;P=c.P;sha=c.sha;save=c.save;rep=c.rep
OLD=P/'span-lookahead-coupled-v1/simple';OUT=P/'span-tail-observe-v1'
PROBE='''
reg [63:0] tail_counts[0:15];integer ti;
reg tail_previous=0,tail_ready_previous=0;
wire tail_candidate=gpu.state==gpu.S_FRAG_PIPE&&gpu.src_done&&gpu.spanprod_active
 &&gpu.cmd_is_tri_walker&&gpu.spanprod_idx==gpu.spanprod_last_idx&&!gpu.tri_walker_done;
wire tail_ready=tail_candidate&&!gpu.p0a_valid&&!gpu.p0_valid&&!gpu.persp_active;
initial for(ti=0;ti<16;ti=ti+1)tail_counts[ti]=0;
always @(posedge clk)if(reset_n&&critical_enable)begin
 tail_previous<=tail_candidate;tail_ready_previous<=tail_ready;
 if(tail_candidate)begin
  tail_counts[0]<=tail_counts[0]+1;
  if(!tail_previous)tail_counts[1]<=tail_counts[1]+1;
  if(gpu.fbss==gpu.FBSS_ZTEST_R_WAIT)tail_counts[4]<=tail_counts[4]+1;
  if(gpu.fp_pipe_stall)tail_counts[5]<=tail_counts[5]+1;
 end
 if(tail_ready)begin
  tail_counts[2]<=tail_counts[2]+1;
  if(!tail_ready_previous)tail_counts[3]<=tail_counts[3]+1;
  if(gpu.fbss==gpu.FBSS_ZTEST_R_WAIT)tail_counts[6]<=tail_counts[6]+1;
  if(gpu.fbss==gpu.FBSS_IDLE&&gpu.p3_valid&&!gpu.p3_discard&&gpu.p3_z_test&&!gpu.fb_read_barrier_clear)
   tail_counts[7]<=tail_counts[7]+1;
  if(gpu.p1_valid||gpu.p2_valid||gpu.p2b_valid||gpu.p3_valid)tail_counts[8]<=tail_counts[8]+1;
 end
 if(gpu.state==gpu.S_TRI_FILL)tail_counts[9]<=tail_counts[9]+1;
 if(gpu.state==gpu.S_SPANPROD_EMIT&&gpu.spanprod_idx==0)tail_counts[10]<=tail_counts[10]+1;
end
final for(ti=0;ti<16;ti=ti+1)$display("SPAN_TAIL %0d %0d",ti,tail_counts[ti]);
'''
def prepare():
    OUT.mkdir(exist_ok=False);shutil.copytree(OLD/'model/frozen',OUT/'model/frozen')
    shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
    p=OUT/'model/frozen/tb_gpu.v';p.write_text(rep(p.read_text(),'endmodule',PROBE+'\nendmodule'))
    cmd=[s.replace(str(OLD),str(OUT)) for s in json.loads((OLD/'build-command.json').read_text())]
    save(OUT/'build-command.json',cmd);save(OUT/'inputs.json',dict(scope=__doc__,sources={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file()}))
def build():
    with (OUT/'build-qsim.log').open('x') as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
    cmd=json.loads((OUT/'build-command.json').read_text())
    with (OUT/'model/build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    save(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))
def run():
    p=ROOT/'tools/experiments/os30_overlap_20261006/run.py';spec=importlib.util.spec_from_file_location('runner',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.OUT=OUT;sys.argv=[str(p),'window-8','--elf',str(P.parent/'phase9/checks-state-f11/app.elf')];m.main()
def analyze():
    d=OUT/'run-window-8';old=OLD/'run-window-8';assert 'FATAL' not in (d/'run.log').read_text()
    for n in ['coupled.json','counters.csv','events.csv','scene.csv','geometry.csv','culling.csv',*[f'frame_{n:05}.bin' for n in range(360,368)]]:assert (d/n).read_bytes()==(old/n).read_bytes(),n
    raw={int(i):int(n) for i,n in re.findall(r'SPAN_TAIL (\d+) (\d+)',(d/'run.log').read_text())};assert len(raw)==16
    result=dict(passed=True,passive_cycle_and_output_exact=True,counts=raw,
        candidate_tail_ms=raw[0]/800000,candidate_episodes_per_frame=raw[1]/8,
        handoff_ready_tail_ms=raw[2]/800000,handoff_ready_episodes_per_frame=raw[3]/8,
        handoff_ready_depth_wait_ms=raw[6]/800000,handoff_ready_write_barrier_ms=raw[7]/800000,
        all_walker_fill_ms=raw[9]/800000,first_chunk_emits_per_frame=raw[10]/8,
        scope='Upper bound on overlap window only; no hypothetical cycles are deducted from measured frame time.')
    save(OUT/'result.json',result);save(OUT/'final-manifest.json',dict(files={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts and 'build' not in p.parts},helper_sha256=sha(Path(__file__))))
    print(json.dumps(result,indent=2))
if __name__=='__main__':
    for a in sys.argv[1:]:globals()[a]()
