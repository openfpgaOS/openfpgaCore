#!/usr/bin/env python3
"""Private conservative pending-write bitmap coverage; unchanged baseline DUT."""
import argparse,json,shutil
from pathlib import Path
import phase10_render_line as base
ROOT=base.ROOT; P=base.P; OLD=base.OLD; OUT=P/'depth-bitmap-v1'
sha=base.sha;save=base.save
RTL='''
// Private experiment: monotonic pending-write hash bitmap. The request
// register blocks reads until its line has been marked. Bits survive all
// queue/AW/W/B stages and clear only at the existing complete GPU write drain.
// Hash collisions add waits. This relaxes only the aligned depth-window read;
// blend, prefetch, fences, flip and clear retain their original barriers.
function [5:0] depth_pending_hash(input [GPU_ADDR_W-1:0] a);
 integer k;begin
 depth_pending_hash=0;
 for(k=6;k<GPU_ADDR_W;k=k+1)
  depth_pending_hash[(k-6)%6]=depth_pending_hash[(k-6)%6]^a[k];
 end
endfunction
reg [63:0] depth_pending_lines;
always @(posedge clk)begin
 if(!reset_n||soft_reset)depth_pending_lines<=0;
 else if(fb_write_drain_complete)depth_pending_lines<=0;
 else if(fbwq_req_valid)depth_pending_lines[depth_pending_hash(fbwq_req_addr)]<=1;
end
wire fb_depth_read_barrier_clear=fb_write_drain_complete
 ||((GPU_Z_READ_WINDOW<=16)&&!z_flush_valid&&!z_src_pending_valid&&!wc_busy
    &&!fbwq_req_valid&&!fbwq_stage_valid
    &&!depth_pending_lines[depth_pending_hash(p3_z_addr)]);
'''
PROBE=base.PROBE+'''
function [7:0] bm_hash(input [25:0] a,input integer bits);
 integer k;begin
 bm_hash=0;
 for(k=6;k<26;k=k+1)bm_hash[(k-6)%bits]=bm_hash[(k-6)%bits]^a[k];
 end
endfunction
reg [63:0] bm64=0;
reg [127:0] bm128=0;
reg [255:0] bm256=0;
reg [63:0] bm_counts[0:15];integer bm_i;
initial for(bm_i=0;bm_i<16;bm_i=bm_i+1)bm_counts[bm_i]=0;
wire bm_pre_clear=!gpu.z_flush_valid&&!gpu.z_src_pending_valid&&!gpu.wc_busy
 &&!gpu.fbwq_req_valid&&!gpu.fbwq_stage_valid;
wire bm_depth_miss=dr_frag&&gpu.fbss==gpu.FBSS_IDLE
 &&gpu.p3_valid&&!gpu.p3_discard&&gpu.p3_z_test
 &&!gpu.p3_flags[gpu.SPAN_TRANSLUC]&&!gpu.blend_group_active
 &&!gpu.z_acc_valid
 &&!(gpu.GPU_Z_READ_WINDOW>1&&!gpu.zw_snoop_pending
      &&gpu.zw_valid[gpu.p3_z_addr[gpu.ZW_LOW-1:2]]
      &&gpu.zw_base==gpu.p3_z_addr[25:gpu.ZW_LOW])
 &&!gpu.zw_fill_busy&&!gpu.tex_axi_arvalid&&!gpu.tex_m0_in_flight;
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)begin bm64<=0;bm128<=0;bm256<=0;end
 else begin
  // All registered request writes are observed before they leave req/stage.
  // clear and set are exclusive because drain_complete includes !req_valid.
  if(gpu.fb_write_drain_complete)begin bm64<=0;bm128<=0;bm256<=0;end
  else if(gpu.fbwq_req_valid)begin
   bm64[bm_hash(gpu.fbwq_req_addr,6)]<=1;
   bm128[bm_hash(gpu.fbwq_req_addr,7)]<=1;
   bm256[bm_hash(gpu.fbwq_req_addr,8)]<=1;
  end
 end
 if(reset_n)begin
  if(gpu.fb_write_drain_complete)bm_counts[0]<=bm_counts[0]+1;
  if(gpu.fbwq_req_valid)bm_counts[1]<=bm_counts[1]+1;
  if(bm_depth_miss)begin
   bm_counts[2]<=bm_counts[2]+1;
   if(!gpu.fb_write_drain_complete)begin
    bm_counts[3]<=bm_counts[3]+1;
    if(bm_pre_clear)begin
     bm_counts[4]<=bm_counts[4]+1;
     if(!bm64[bm_hash(gpu.p3_z_addr,6)])bm_counts[5]<=bm_counts[5]+1;
     if(!bm128[bm_hash(gpu.p3_z_addr,7)])bm_counts[6]<=bm_counts[6]+1;
     if(!bm256[bm_hash(gpu.p3_z_addr,8)])bm_counts[7]<=bm_counts[7]+1;
    end
   end
  end
 end
end
final for(bm_i=0;bm_i<16;bm_i=bm_i+1)$display("DEPTH_BITMAP %0d %0d",bm_i,bm_counts[bm_i]);
'''
def prepare(label):
 d=OUT/label;d.mkdir(parents=True,exist_ok=False)
 shutil.copytree(OLD/'model/frozen',d/'model/frozen');q=d/'qsim';q.mkdir()
 for p in (OLD/'qsim').iterdir():
  if p.suffix in('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
 p=d/'model/frozen/tb_gpu.v';s=p.read_text();assert s.count('endmodule')==1
 p.write_text(s.replace('endmodule',PROBE+'\nendmodule'))
 if label=='bitmap64':
  p=d/'model/frozen/common/gpu_core.v';s=p.read_text()
  marker='wire fbwq_output_idle = !m_wr_awvalid && !m_wr_wvalid;'
  assert s.count(marker)==1;s=s.replace(marker,RTL+'\n'+marker)
  marker='''end else if (!zw_fill_busy && !tex_axi_arvalid && !tex_m0_in_flight
                                  && fb_read_barrier_clear) begin'''
  assert s.count(marker)==1;s=s.replace(marker,marker.replace('fb_read_barrier_clear','fb_depth_read_barrier_clear'))
  p.write_text(s)
 cmd=[x.replace(str(OLD),str(d)) for x in json.loads((OLD/'build-command.json').read_text())]
 save(d/'build-command.json',cmd)
 recipes=d/'recipes';recipes.mkdir();shutil.copy2(Path(__file__),recipes/Path(__file__).name)
 save(d/'inputs.json',dict(private_only=True,passive_only=label=='baseline',recipe_sha256=sha(Path(__file__)),sources={str(p.relative_to(d)):sha(p) for p in d.rglob('*') if p.is_file()}))
 print(d)
def build(label):
 base.prior.OUT=OUT;base.prior.build(label)
def run(label):
 base.prior.OUT=OUT;base.prior.run(label)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);p.add_argument('label',choices=['baseline','bitmap64']);a=p.parse_args();globals()[a.action](a.label)
