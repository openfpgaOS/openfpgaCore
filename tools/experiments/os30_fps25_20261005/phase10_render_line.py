#!/usr/bin/env python3
"""Private fixed-capacity render-cache line-size A/B and depth-stall attribution."""
import argparse,hashlib,importlib.util,json,shutil,subprocess,sys
from pathlib import Path
import phase10_depth_reject as prior
ROOT=prior.ROOT;P=prior.P;OLD=prior.OLD;OUT=P/'render-line32-v1'
sha=prior.sha;save=prior.save
EXTRA='''
// Counts16..47 partition depth-response wait by render-cache FSM state.
// Counts48..63 expose the producer-side barrier and window cases.
always @(posedge clk)if(reset_n&&dr_frag)begin
 if(gpu.fbss==gpu.FBSS_ZTEST_R_WAIT)
  dr_cycles[16+gpu_render_cache.state]<=dr_cycles[16+gpu_render_cache.state]+1;
 if(gpu.fbss==gpu.FBSS_IDLE&&gpu.p3_valid&&!gpu.p3_discard&&gpu.p3_z_test)begin
  if(gpu.z_acc_valid&&gpu.z_acc_addr!={gpu.p3_z_addr[25:2],2'b0})dr_cycles[48]<=dr_cycles[48]+1;
  if(!gpu.fb_read_barrier_clear)dr_cycles[49]<=dr_cycles[49]+1;
  if(gpu.zw_fill_busy)dr_cycles[50]<=dr_cycles[50]+1;
  if(gpu.tex_axi_arvalid||gpu.tex_m0_in_flight)dr_cycles[51]<=dr_cycles[51]+1;
  if(gpu.blend_arvalid&&gpu.blend_arready)dr_cycles[52]<=dr_cycles[52]+1;
 end
 if(!gpu.fp_pipe_stall&&gpu.p2b_valid&&gpu.p2b_z_test&&!gpu.p2b_discard)begin
  if(gpu.p2b_zf_acc_hit)dr_cycles[53]<=dr_cycles[53]+1;
  if(gpu.p2b_zf_zw_hit)dr_cycles[54]<=dr_cycles[54]+1;
  if(gpu.p2b_zf_zw_hit&&!gpu.p2b_zf_acc_hit&&gpu.z_acc_valid&&gpu.p2b_z_write)dr_cycles[55]<=dr_cycles[55]+1;
 end
 if(gpu.blend_arvalid&&gpu.blend_arready)dr_cycles[56]<=dr_cycles[56]+1;
 if(gpu_render_cache.m_arvalid&&gpu_render_cache.m_arready)dr_cycles[57]<=dr_cycles[57]+1;
 if(gpu_render_cache.m_rvalid)dr_cycles[58]<=dr_cycles[58]+1;
 if(gpu_render_cache.m_awvalid&&gpu_render_cache.m_awready)dr_cycles[59]<=dr_cycles[59]+1;
 if(gpu_render_cache.m_wvalid&&gpu_render_cache.m_wready)dr_cycles[60]<=dr_cycles[60]+1;
 if(gpu.fbss==gpu.FBSS_IDLE&&gpu.p3_valid&&!gpu.p3_discard&&gpu.p3_z_test
 &&!gpu.z_acc_valid&&!gpu.zw_fill_busy&&!gpu.fb_read_barrier_clear)dr_cycles[61]<=dr_cycles[61]+1;
 if(gpu.p2b_zf_fold&&!gpu.fp_pipe_stall)dr_cycles[62]<=dr_cycles[62]+1;
end
'''
PROBE=prior.PROBE.replace('[0:15]','[0:63]').replace('dr_i<16','dr_i<64')+EXTRA
def prepare(label):
    d=OUT/label;d.mkdir(parents=True,exist_ok=False);shutil.copytree(OLD/'model/frozen',d/'model/frozen');q=d/'qsim';q.mkdir()
    for p in (OLD/'qsim').iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
    p=d/'model/frozen/tb_gpu.v';s=p.read_text();assert s.count('endmodule')==1
    s=s.replace('endmodule',PROBE+'\nendmodule')
    if label=='line32':
        a='.WAYS(2), .SET_BITS(6), .WORD_BITS(4)';assert s.count(a)==1
        s=s.replace(a,'.WAYS(2), .SET_BITS(7), .WORD_BITS(3)')
    p.write_text(s)
    cmd=[s.replace(str(OLD),str(d)) for s in json.loads((OLD/'build-command.json').read_text())]
    save(d/'build-command.json',cmd)
    save(d/'inputs.json',dict(private_only=True,cache_data_capacity_bytes=8192,ways=2,line_bytes=32 if label=='line32' else 64,recipe_sha256=sha(Path(__file__)),probe_recipe_sha256=sha(Path(prior.__file__)),sources={str(p.relative_to(d)):sha(p) for p in d.rglob('*') if p.is_file()}))
    print(d)
def build(label):
    prior.OUT=OUT;prior.build(label)
def run(label):
    prior.OUT=OUT;prior.run(label)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);p.add_argument('label',choices=['baseline','line32']);a=p.parse_args();globals()[a.action](a.label)
