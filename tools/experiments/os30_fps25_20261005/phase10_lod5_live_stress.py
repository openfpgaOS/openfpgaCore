#!/usr/bin/env python3
"""Extend actual CPU/LOD5 proof with physical contention, held alias reads, and CPU reset."""
import hashlib,json,shutil,sys
from pathlib import Path
import phase10_lod5_live_native as N
ROOT=N.ROOT;P=N.P;OUT=P/'lod5-live-native-v2';OLD=N.OUT
sha=N.sha
EXTRA=r'''
// Test-only controls live at an otherwise ignored LOD5 word. No RTL module modified.
reg probe_enable=0,reset_arm=0;reg[3:0]cpu_reset_count=0;
wire test_cpu_reset=cpu_reset_count!=0;
wire fixture_cpu_reset_n=reset_n&&!test_cpu_reset;
assign dbg_test_reset=test_cpu_reset;
reg probe_wait=0;reg[4:0]probe_index=0;reg[4:0]probe_beat=0;
wire probe_arvalid=probe_enable&&!probe_wait;
wire probe_arready,probe_rvalid,probe_rlast;wire[31:0]probe_rdata;
wire[31:0]probe_araddr=32'h003c0000+{21'b0,probe_index,6'b0};
integer texture_beats=0,texture_bursts=0,texture_wait=0,geo_wait=0,cpu_resets=0;
reg[11:0]preload_count=0;
function automatic[31:0]texture_pattern(input[21:0]a);texture_pattern=32'h6d217f03^(a*32'h9e3779b9);endfunction
always@(posedge clk_cpu)begin
 if(!reset_n)begin
  if(preload_count<2048)begin
   bd_we<=1;bd_word_addr<=22'h0f0000+preload_count;bd_wdata_word<=texture_pattern(22'h0f0000+preload_count);preload_count<=preload_count+1;
  end else bd_we<=0;
  probe_enable<=0;probe_wait<=0;probe_index<=0;probe_beat<=0;reset_arm<=0;cpu_reset_count<=0;
 end else begin
  bd_we<=0;
  if(geometry_reg_wr&&geometry_reg_addr==10'h0fc)begin
   if(geometry_reg_wdata==1)probe_enable<=1;
   if(geometry_reg_wdata==0)probe_enable<=0;
   if(geometry_reg_wdata==2)reset_arm<=1;
  end
  if(cpu_reset_count!=0)cpu_reset_count<=cpu_reset_count-1;
  if(reset_arm&&geometry_arvalid&&geometry_arready)begin
   cpu_reset_count<=8;reset_arm<=0;cpu_resets<=cpu_resets+1;
   if(!geometry_load.active)$fatal(1,"reset trigger before active LOAD");
  end
  if(probe_arvalid&&!probe_arready)texture_wait<=texture_wait+1;
  if(geometry_arvalid&&!geometry_arready)geo_wait<=geo_wait+1;
  if(probe_arvalid&&probe_arready)begin probe_wait<=1;probe_beat<=0;texture_bursts<=texture_bursts+1;end
  if(probe_rvalid)begin
   if(!probe_wait)$fatal(1,"texture response without owner");
   if(probe_rdata!==texture_pattern(probe_araddr[23:2]+probe_beat))$fatal(1,"physical texture/source collision");
   if(probe_rlast!==(probe_beat==15))$fatal(1,"texture RLAST");
   texture_beats<=texture_beats+1;probe_beat<=probe_beat+1;
   if(probe_rlast)begin probe_wait<=0;probe_index<=probe_index+1;end
  end
  if(tex_arvalid)$fatal(1,"fixture texture fill master must not overlap real GPU texture request");
 end
end
// Backpressure is applied consistently to both sides of the result response.
reg[3:0]alias_phase=0;
always@(posedge clk_cpu)alias_phase<=alias_phase+1;
wire alias_hold=geometry_view_active&&(alias_phase<5);
wire alias_raw_rvalid;
assign cpu_sdram_rvalid=alias_raw_rvalid&&!alias_hold;
assign dbg_native_uploads=native_upload_words;
assign dbg_native_loads=native_load_bursts;
assign dbg_native_alias=native_alias_beats;
assign dbg_native_stalls=native_alias_stalls;
assign dbg_native_texture=texture_beats;
assign dbg_native_resets=cpu_resets;
assign dbg_native_geo_wait=geo_wait;
'''
def prepare():
 OUT.mkdir(exist_ok=False)
 for mode in ('control','queues'):
  d=OUT/mode;d.mkdir();shutil.copytree(OLD/mode/'frozen',d/'frozen')
  top=d/'frozen/docs/measurements/os30-dual-20261004/validation/baseline128/tb_irq.v';t=top.read_text()
  t=t.replace('module tb_system (','module tb_system (\noutput wire dbg_test_reset,\noutput wire[31:0]dbg_native_uploads,dbg_native_loads,dbg_native_alias,dbg_native_stalls,dbg_native_texture,dbg_native_resets,dbg_native_geo_wait,')
  pos=t.index('wire geometry_reg_wr;');t=t[:pos]+EXTRA+'\n'+t[pos:]
  t=t.replace('cpu_system cpu_sys (\n    .clk    (clk_cpu),\n    .reset_n(reset_n)','cpu_system cpu_sys (\n    .clk    (clk_cpu),\n    .reset_n(fixture_cpu_reset_n)')
  t=t.replace('geometry_bram_alias alias_shim(.clk(clk_cpu),.reset(!reset_n),.cpu_reset(!reset_n)','geometry_bram_alias alias_shim(.clk(clk_cpu),.reset(!reset_n),.cpu_reset(!fixture_cpu_reset_n)')
  t=t.replace('.s_rvalid(cpu_sdram_rvalid),.m_rvalid(alias_normal_rvalid)', '.s_rvalid(alias_raw_rvalid),.m_rvalid(alias_normal_rvalid)')
  t=t.replace('.s_rready(cpu_sdram_rready),.m_rready(alias_normal_rready)', '.s_rready(cpu_sdram_rready&&!alias_hold),.m_rready(alias_normal_rready)')
  # Reset only CPU/peripheral access state; CRAM arbiter, LOAD, shared SDRAM retain fabric reset.
  begin=t.index('axi_periph_slave periph (');end=t.index('// gpu_core —',begin)
  t=t[:begin]+t[begin:end].replace('.reset_n(reset_n)', '.reset_n(fixture_cpu_reset_n)')+t[end:]
  t=t.replace('.s0_arvalid(tex_arvalid),.s0_arready(tex_arready),.s0_araddr(tex_araddr),.s0_arlen(tex_arlen),.s0_rvalid(tex_rvalid),.s0_rdata(tex_rdata),.s0_rlast(tex_rlast)', '.s0_arvalid(probe_arvalid),.s0_arready(probe_arready),.s0_araddr(probe_araddr),.s0_arlen(8\'d15),.s0_rvalid(probe_rvalid),.s0_rdata(probe_rdata),.s0_rlast(probe_rlast)')
  t+='\nassign tex_arready=0;assign tex_rvalid=0;assign tex_rdata=0;assign tex_rlast=0;\n' if False else ''
  # Idle GPU texture side is explicit; synthetic contender uses same selected arbiter/physical adapter.
  t=t.replace('geometry_cram1_read_arbiter cram_arbiter(',"assign tex_arready=0;assign tex_rvalid=0;assign tex_rdata=0;assign tex_rlast=0;\ngeometry_cram1_read_arbiter cram_arbiter(")
  top.write_text(t)
  host=top.with_name('main.cpp');s=host.read_text()
  # Retire CPU-boundary bookkeeping on explicitly canceled CPU reset. Fabric trackers stay live.
  m='    auto track_r = [&](MasterTrk &t,';assert m in s
  # Put counter printing next to final summary, without changing simulation stop logic.
  m='    std::printf("\\n=== SUMMARY ===\\n");'
  if m not in s:m='    std::printf("=== SUMMARY ===\\n");'
  assert m in s
  s=s.replace(m,'    std::printf("NATIVE_HW uploads=%u loads=%u alias=%u stalls=%u texture=%u resets=%u geo_wait=%u\\n",tb->dbg_native_uploads,tb->dbg_native_loads,tb->dbg_native_alias,tb->dbg_native_stalls,tb->dbg_native_texture,tb->dbg_native_resets,tb->dbg_native_geo_wait);\n'+m)
  # Valid output during local reset is canceled according to CPU target reset contract.
  marker='        // Per-master'; # exact insertion located after cycle tick below in follow-up if needed
  a=s.index('        if (tb->dbg_i_arvalid');b=s.index('\n',s.index('track_r(trk_per,',a));block=s[a:b]
  s=s[:a]+'        if(tb->dbg_test_reset){trk_i.pending.clear();trk_mem.pending.clear();trk_per.pending.clear();}\n        else {\n'+block+'\n        }'+s[b:]
  host.write_text(s)
  p=json.loads((OLD/mode/'prepared.json').read_text());p['command']=[x.replace(str(OLD/mode),str(d))for x in p['command']];p['source_sha256']={str(f.relative_to(d)):sha(f)for f in(d/'frozen').rglob('*')if f.is_file()};N.save(d/'prepared.json',p)
 shutil.copytree(OLD/'firmware',OUT/'firmware')
 shutil.copyfile(Path(__file__).with_name('phase10_lod5_live_stress_kernel.c'),OUT/'firmware/kernel.c')
 shutil.copyfile(Path(__file__).with_name('phase10_lod5_live_stress_invoke.S'),OUT/'firmware/invoke.S')
 shutil.copyfile(OLD/'firmware-command.json',OUT/'firmware-command-original.json')
 cmd=[x.replace(str(OLD/'firmware'),str(OUT/'firmware'))for x in json.loads((OLD/'firmware-command.json').read_text())];N.save(OUT/'firmware-command.json',cmd)
 N.save(OUT/'prepared.json',dict(recipe_sha256=sha(Path(__file__)),base_sha256={str(p.relative_to(ROOT)):sha(p)for p in [OLD/'firmware.json',OLD/'control/prepared.json',OLD/'queues/prepared.json']},scope=__doc__))
def model(mode):N.OUT=OUT;N.model(mode)
def run(mode,traffic):N.OUT=OUT;N.run(mode,traffic)
def firmware_image():N.OUT=OUT;N.firmware_image()
if __name__=='__main__':globals()[sys.argv[1]](*sys.argv[2:])
