#!/usr/bin/env python3
"""Private bitmap-safe depth AR priority, with consistent cache request selection."""
import argparse,json,shutil
from pathlib import Path
import phase10_depth_bitmap as prior
ROOT=prior.ROOT;P=prior.P;OLD=P/'depth-bitmap-v1/bitmap64';OUT=P/'depth-priority-v1'
sha=prior.sha;save=prior.save
def sub(s,a,b):
 assert s.count(a)==1,(s.count(a),a[:100]);return s.replace(a,b)
PROBE='''
// Independent pending upstream-AW/B line scoreboard and handshake assertions.
reg [19:0] dp_first[0:15],dp_last[0:15];
reg [15:0] dp_valid=0;reg [3:0] dp_head=0,dp_tail=0;
reg dp_was_stalled=0;reg [25:0] dp_addr;reg [7:0] dp_len;reg dp_priority;
reg [63:0] dp_cycles[0:7];integer dp_i,dp_q;
reg [63:0] dp_aw_wait=0,dp_aw_wait_max=0;
wire dp_prio=gpu_cache_depth_priority;
initial for(dp_i=0;dp_i<8;dp_i=dp_i+1)dp_cycles[dp_i]=0;
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)begin
  dp_valid<=0;dp_head<=0;dp_tail<=0;dp_was_stalled<=0;dp_aw_wait<=0;
 end else begin
  if(gpu.m_wr_bvalid)begin dp_valid[dp_head]<=0;dp_head<=dp_head+1'b1;end
  if(gpu.m_wr_awvalid&&gpu.m_wr_awready)begin
   dp_valid[dp_tail]<=1;dp_first[dp_tail]<=gpu.m_wr_awaddr[25:6];
   dp_last[dp_tail]<=(gpu.m_wr_awaddr[25:0]+({18'd0,gpu.m_wr_awlen}<<2))>>6;
   dp_tail<=dp_tail+1'b1;
  end
  if(dp_was_stalled)begin
   if(!gpu_s_rd_arvalid||gpu_s_rd_araddr[25:0]!=dp_addr||gpu_s_rd_arlen!=dp_len||dp_prio!=dp_priority)
    $fatal(1,"Depth priority changed a stalled upstream AR");
  end
  dp_was_stalled<=gpu_s_rd_arvalid&&!gpu_s_rd_arready;
  dp_addr<=gpu_s_rd_araddr[25:0];dp_len<=gpu_s_rd_arlen;dp_priority<=dp_prio;
  if(gpu_s_wr_awvalid&&!gpu_s_wr_awready)begin
   dp_aw_wait<=dp_aw_wait+1;
   if(dp_aw_wait+1>dp_aw_wait_max)dp_aw_wait_max<=dp_aw_wait+1;
  end else dp_aw_wait<=0;
  if(dp_prio)begin
   dp_cycles[0]<=dp_cycles[0]+1;
   if(!gpu_s_rd_arvalid)$fatal(1,"Priority without read");
   if(gpu.z_flush_valid||gpu.z_src_pending_valid||gpu.fbwq_req_valid||gpu.fbwq_stage_valid)
    $fatal(1,"Untracked staged write while priority read pending");
   for(dp_i=0;dp_i<16;dp_i=dp_i+1)begin
    if(dp_valid[dp_i]&&(dp_first[dp_i]==gpu_s_rd_araddr[25:6]||dp_last[dp_i]==gpu_s_rd_araddr[25:6]))
     $fatal(1,"Priority read overlaps unretired write burst");
    dp_q=(gpu.fbwq_rd_ptr+dp_i)&15;
    if(dp_i<gpu.fbwq_count&&gpu.fbwq_addr[dp_q][25:6]==gpu_s_rd_araddr[25:6])
     $fatal(1,"Priority read overlaps queued write");
   end
   if(gpu.m_wr_awvalid&&(gpu.m_wr_awaddr[25:6]==gpu_s_rd_araddr[25:6]
    ||((gpu.m_wr_awaddr[25:0]+({18'd0,gpu.m_wr_awlen}<<2))>>6)==gpu_s_rd_araddr[25:6]))
     $fatal(1,"Priority read overlaps pending AW");
   if(gpu_s_rd_arready)dp_cycles[1]<=dp_cycles[1]+1;
   if(gpu_s_rd_arready&&gpu_s_wr_awvalid)dp_cycles[2]<=dp_cycles[2]+1;
   if(gpu_render_cache.state==gpu_render_cache.IDLE&&!gpu_render_cache.flush_req)begin
    if(!gpu_s_rd_arready||gpu_s_wr_awready||gpu_render_cache.lookup_addr!=gpu_s_rd_araddr[25:0])
     $fatal(1,"Inconsistent priority cache selection");
   end
  end
  if(gpu_s_wr_awvalid&&gpu_s_wr_awready)dp_cycles[3]<=dp_cycles[3]+1;
  if(gpu_s_rd_arvalid&&gpu_s_rd_arready)dp_cycles[4]<=dp_cycles[4]+1;
 end
end
final begin
 for(dp_i=0;dp_i<8;dp_i=dp_i+1)$display("DEPTH_PRIORITY %0d %0d",dp_i,dp_cycles[dp_i]);
 $display("DEPTH_PRIORITY_AW_MAX %0d",dp_aw_wait_max);
end
'''
def wire_top(s):
 s=sub(s,'wire        gpu_cache_soft_reset, gpu_cache_tex_flush;',
       'wire        gpu_cache_soft_reset, gpu_cache_tex_flush;\nwire gpu_cache_depth_priority;')
 s=sub(s,'.cache_tex_flush(gpu_cache_tex_flush),',
       '.cache_tex_flush(gpu_cache_tex_flush),\n    .cache_depth_read_priority(gpu_cache_depth_priority),')
 s=sub(s,'.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4)',
       '.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4), .DEPTH_READ_PRIORITY(1)')
 pin='.s_arvalid(gpu_s_rd_arvalid),' if '.s_arvalid(gpu_s_rd_arvalid),' in s else '.s_arvalid(gpu_rd_arvalid),'
 s=sub(s,pin,'.s_ar_depth_priority(gpu_cache_depth_priority), '+pin)
 return s
def prepare(label):
 d=OUT/label;d.mkdir(parents=True,exist_ok=False);shutil.copytree(OLD/'model/frozen',d/'model/frozen');q=d/'qsim';q.mkdir()
 for p in (OLD/'qsim').iterdir():
  if p.suffix in('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
 p=d/'model/frozen/common/gpu_core.v';s=p.read_text()
 s=sub(s,'    output wire        cache_tex_flush,','    output wire        cache_tex_flush,\n    output wire        cache_depth_read_priority,')
 s=sub(s,'reg        blend_arvalid;','reg        blend_arvalid;\nreg blend_ar_depth_priority;')
 s=sub(s,'assign cache_tex_flush    = tex_flush_req;', '''assign cache_tex_flush    = tex_flush_req;
assign cache_depth_read_priority=!dma_owns_ar&&blend_owns_m0&&blend_arvalid&&blend_ar_depth_priority;''')
 s=sub(s,'        blend_arvalid    <= 0;','        blend_arvalid    <= 0;\n        blend_ar_depth_priority <= 0;')
 # Reset and accepted-read clearing. Keep acceptance clear conditional.
 s=sub(s,"            if (blend_arvalid && blend_arready)\n                blend_arvalid <= 1'b0;", "            if (blend_arvalid && blend_arready) begin\n                blend_arvalid <= 1'b0;\n                blend_ar_depth_priority <= 0;\n            end")
 # Set zero for each non-depth launch; special depth launch sets safe bypass.
 s=s.replace("blend_arvalid <= 1'b1;","blend_arvalid <= 1'b1;\n                            blend_ar_depth_priority <= 0;")
 s=s.replace('blend_arvalid <= 1;','blend_arvalid <= 1;\n                        blend_ar_depth_priority <= 0;')
 marker="blend_arvalid <= 1'b1;\n                            blend_ar_depth_priority <= 0;\n                            blend_araddr  <= (GPU_Z_READ_WINDOW > 1)"
 s=sub(s,marker,marker.replace('blend_ar_depth_priority <= 0;','blend_ar_depth_priority <= !fb_write_drain_complete;'))
 # Soft-reset arm has a single clearing assignment with different indentation.
 soft="            blend_arvalid <= 1'b0;\n            blend_arlen_r"
 if soft in s:s=sub(s,soft,"            blend_arvalid <= 1'b0;\n            blend_ar_depth_priority <= 0;\n            blend_arlen_r")
 p.write_text(s)
 p=d/'model/frozen/common/gpu_color_depth_cache.sv';s=p.read_text()
 s=sub(s,'    parameter MAX_WRITE_WORDS = 8','    parameter MAX_WRITE_WORDS = 8,\n    parameter DEPTH_READ_PRIORITY = 0')
 s=sub(s,'    input wire s_arvalid,','    input wire s_arvalid,\n    input wire s_ar_depth_priority,')
 s=sub(s,'wire [ADDR_W-1:0] lookup_addr=state==IDLE ? (s_awvalid ? s_awaddr : s_araddr) : addr;',
 '''wire select_depth_read=DEPTH_READ_PRIORITY&&s_arvalid&&s_ar_depth_priority;
wire select_write=s_awvalid&&!select_depth_read;
wire [ADDR_W-1:0] lookup_addr=state==IDLE ? (select_write ? s_awaddr : s_araddr) : addr;''')
 s=sub(s,'assign s_awready=state==IDLE && !flush_req;','assign s_awready=state==IDLE && !flush_req && !select_depth_read;')
 s=sub(s,'assign s_arready=state==IDLE && !flush_req && !s_awvalid;','assign s_arready=state==IDLE && !flush_req && !select_write;')
 s=sub(s,'else if(s_awvalid) begin','else if(select_write) begin');p.write_text(s)
 p=d/'model/frozen/tb_gpu.v';s=wire_top(p.read_text());s=sub(s,'endmodule',PROBE+'\nendmodule');p.write_text(s)
 # Standalone integration patch against the same frozen whole-core baseline.
 coretop=P/'vcache-narrow-v1/fit-seed2/frozen/src/fpga/targets/pocket/core_top.v'
 integ=d/'integration';integ.mkdir();s=coretop.read_text()
 # Production wrapper uses different cache dimensions formatting; normalize only this instantiation.
 s=sub(s,'.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4)', '.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4)')
 (integ/'core_top.v').write_text(wire_top(s))
 cmd=[x.replace(str(OLD),str(d)) for x in json.loads((OLD/'build-command.json').read_text())]
 save(d/'build-command.json',cmd);recipes=d/'recipes';recipes.mkdir();shutil.copy2(Path(__file__),recipes/Path(__file__).name)
 save(d/'inputs.json',dict(private_only=True,recipe_sha256=sha(Path(__file__)),core_top_baseline_sha256=sha(coretop),sources={str(p.relative_to(d)):sha(p) for p in d.rglob('*') if p.is_file()}))
 print(d)
def build(label):prior.base.prior.OUT=OUT;prior.base.prior.build(label)
def run(label):prior.base.prior.OUT=OUT;prior.base.prior.run(label)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);p.add_argument('label',choices=['priority']);a=p.parse_args();globals()[a.action](a.label)
