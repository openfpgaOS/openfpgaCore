#!/usr/bin/env python3
"""Private depth-window failed-writer fold: passive baseline and exact RTL A/B."""
import argparse,hashlib,importlib.util,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OLD=P/'gpu-cache-maint-observe-v2';OUT=P/'depth-reject-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
PROBE='''
// Passive per-fragment blocker/eligibility counters, no DUT changes.
reg [63:0] dr_cycles[0:15];
integer dr_i;
initial for(dr_i=0;dr_i<16;dr_i=dr_i+1)dr_cycles[dr_i]=0;
wire dr_frag=gpu.state==gpu.S_FRAG_PIPE;
wire dr_new=gpu.p2b_valid&&gpu.p2b_z_test&&!gpu.p2b_discard
 &&!gpu.p2b_flags[gpu.SPAN_TRANSLUC]&&!gpu.p2b_zf_acc_hit
 &&gpu.p2b_zf_zw_hit&&gpu.z_acc_valid&&gpu.p2b_z_write&&!gpu.p2b_zf_pass;
always @(posedge clk)if(reset_n&&dr_frag)begin
 dr_cycles[0]<=dr_cycles[0]+1;
 if(gpu.fp_pipe_stall)dr_cycles[1]<=dr_cycles[1]+1;
 if(gpu.p1_valid&&!gpu.p1_response_ready)dr_cycles[2]<=dr_cycles[2]+1;
 if(gpu.fbss!=gpu.FBSS_IDLE)dr_cycles[3]<=dr_cycles[3]+1;
 if(gpu.p3_valid&&!gpu.p3_discard&&gpu.p3_z_test)dr_cycles[4]<=dr_cycles[4]+1;
 if(gpu.p3_valid&&!gpu.p3_discard&&gpu.sp_truecolor&&gpu.sp_blend&&(!gpu.p3_cb_ready||gpu.p3_cb_stale))dr_cycles[5]<=dr_cycles[5]+1;
 if(gpu.fb_write_buffer_stall)dr_cycles[6]<=dr_cycles[6]+1;
 if(gpu.m_wr_inflight_near_full)dr_cycles[7]<=dr_cycles[7]+1;
 if(gpu.cmap_pipe_wait)dr_cycles[8]<=dr_cycles[8]+1;
 if(dr_new)dr_cycles[9]<=dr_cycles[9]+1;
 if(dr_new&&!gpu.fp_pipe_stall)dr_cycles[10]<=dr_cycles[10]+1;
 if(gpu.p2b_valid&&!gpu.fp_pipe_stall)dr_cycles[11]<=dr_cycles[11]+1;
 if(gpu.p2b_valid&&gpu.p2b_z_test&&!gpu.p2b_discard&&!gpu.fp_pipe_stall)dr_cycles[12]<=dr_cycles[12]+1;
 if(gpu.p2b_zf_fold&&!gpu.p2b_zf_pass&&!gpu.fp_pipe_stall)dr_cycles[13]<=dr_cycles[13]+1;
 if(gpu.fbss==gpu.FBSS_ZTEST_R_WAIT)dr_cycles[14]<=dr_cycles[14]+1;
 if(gpu.fbss==gpu.FBSS_ZTEST_ACC_EVAL)dr_cycles[15]<=dr_cycles[15]+1;
end
final for(dr_i=0;dr_i<16;dr_i=dr_i+1)$display("DEPTH_REJECT %0d %0d",dr_i,dr_cycles[dr_i]);
'''
def prepare(label):
    dest=OUT/label;dest.mkdir(parents=True,exist_ok=False)
    shutil.copytree(OLD/'model/frozen',dest/'model/frozen');q=dest/'qsim';q.mkdir()
    for p in (OLD/'qsim').iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
    p=dest/'model/frozen/tb_gpu.v';s=p.read_text();assert s.count('endmodule')==1
    p.write_text(s.replace('endmodule',PROBE+'\nendmodule'))
    core=dest/'model/frozen/common/gpu_core.v';baseline=core.read_text()
    if label=='candidate':
        old='|| (p2b_zf_zw_hit && (!z_acc_valid || !p2b_z_write)));';assert baseline.count(old)==1
        core.write_text(baseline.replace(old,'|| (p2b_zf_zw_hit && (!z_acc_valid || !p2b_z_write || !p2b_zf_pass)));'))
    cmd=[s.replace(str(OLD),str(dest)) for s in json.loads((OLD/'build-command.json').read_text())]
    save(dest/'build-command.json',cmd)
    save(dest/'inputs.json',dict(private_only=True,baseline_sha256=sha(OLD/'model/frozen/common/gpu_core.v'),candidate=label=='candidate',recipe_sha256=sha(Path(__file__)),sources={str(p.relative_to(dest)):sha(p) for p in dest.rglob('*') if p.is_file()}))
    print(dest)
def build(label):
    d=OUT/label;q=d/'qsim'
    with (d/'build-qsim.log').open('x') as log:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=q,stdout=log,stderr=subprocess.STDOUT,check=True)
    cmd=json.loads((d/'build-command.json').read_text())
    with (d/'model/build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    save(d/'model/build.json',dict(command=cmd,simulator_sha256=sha(d/'model/obj/Vtb_gpu'),passive_only=label=='baseline'))
def run(label):
    p=ROOT/'tools/experiments/os30_overlap_20261006/run.py';spec=importlib.util.spec_from_file_location('runner',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.OUT=OUT/label;sys.argv=[str(p),'window-8','--elf',str(P.parent/'phase9/checks-state-f11/app.elf')];m.main()
    assert 'FATAL' not in (m.OUT/'run-window-8/run.log').read_text()
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','build','run']);a.add_argument('label',choices=['baseline','candidate']);args=a.parse_args();globals()[args.action](args.label)
