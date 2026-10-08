#!/usr/bin/env python3
"""Exercise actual GPU ring + actual AXI peripheral interlock, no raster model."""
import hashlib,json,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]; HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'gpu-direct-ring-v1'; T=OUT/'tests'; T.mkdir(exist_ok=False)
BASE=P/'vcache-narrow-v1/model/frozen'
FIT=P/'vcache-narrow-v1/fit-seed2/frozen/src/fpga'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sub(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
tb=(ROOT/'src/fpga/test/tb_axi_periph.v').read_text()
tb=sub(tb,'    input wire reset_n,','''    input wire reset_n,
    input wire geo_request,input wire [12:0] geo_words,output wire geo_grant,
    input wire geo_valid,input wire [31:0] geo_data,output wire geo_ready,
    input wire geo_abort,output wire geo_done,output wire geo_owned,
    input wire allow_barrier,
    output wire gpu_write_pending,output wire gpu_write_ready,
    output wire [11:0] append_ptr,output wire [11:0] publish_ptr,output wire [11:0] read_ptr,
    input wire [11:0] peek_addr,output wire [31:0] peek_data,
    output wire [31:0] fence_reached,''')
tb,n=re.subn(r'reg  \[31:0\] gpu_reg_rdata;\nalways @\(\*\) begin.*?\nend\n', 'wire [31:0] gpu_reg_rdata;\n',tb,flags=re.S);assert n==1
tb=sub(tb,'axi_periph_slave #(\n','axi_periph_slave #(\n    .INCLUDE_GEOMETRY_INGRESS(1),\n')
tb=sub(tb,'    .gpu_reg_wr   (dbg_gpu_reg_wr),','''    .gpu_write_ready(gpu_write_ready),.gpu_write_pending(gpu_write_pending),
    .gpu_reg_wr   (dbg_gpu_reg_wr),''')
build=json.loads((P/'vcache-narrow-v1/model/build.json').read_text())['command']
params=[x[2:].split('=') for x in build if x.startswith('-G') and x[2:].split('=')[0] not in ['CPU_FAIR_THRESHOLD','CRAM_REFRESH_EXTRA']]
gpu='\ngpu_core #(\n    .INCLUDE_GEOMETRY_INGRESS(1),\n'+',\n'.join('    .%s(%s)'%tuple(x) for x in params)+'''
) gpu (
 .clk(clk),.reset_n(reset_n),.gpu_enable(1'b1),
 .geo_request(geo_request),.geo_words(geo_words),.geo_grant(geo_grant),
 .geo_valid(geo_valid),.geo_data(geo_data),.geo_ready(geo_ready),
 .geo_abort(geo_abort),.geo_done(geo_done),.geo_owned(geo_owned),
 .gpu_cpu_pending(gpu_write_pending),.gpu_cpu_ready(gpu_write_ready),
 .reg_wr(dbg_gpu_reg_wr),.reg_addr(dbg_gpu_reg_addr),.reg_wdata(dbg_gpu_reg_wdata),.reg_rdata(gpu_reg_rdata),
 .m_rd_arready(1'b0),.m_rd_rvalid(1'b0),.m_rd_rdata(32'b0),.m_rd_rlast(1'b0),
 .m_wr_awready(1'b0),.m_wr_wready(1'b0),.m_wr_bvalid(1'b0),
 .gpu_tex_mem_arready(1'b0),.gpu_tex_mem_rvalid(1'b0),.gpu_tex_mem_rdata(32'b0),.gpu_tex_mem_rlast(1'b0),
 .gpu_tex_mem_up_busy(1'b0),.sram_rdata(32'b0),.sram_busy(1'b0),.sram_rdata_valid(1'b0),
 .slave_swap_pending(1'b0),.memory_barrier_done(allow_barrier),.fence_reached(fence_reached)
);
assign append_ptr=gpu.ring_wr_addr;
assign publish_ptr=gpu.ring_wrptr;
assign read_ptr=gpu.ring_rdptr;
assign peek_data=gpu.ring_bram[peek_addr];
'''
tb=sub(tb,'endmodule',gpu+'\nendmodule')
(T/'tb_axi_periph.v').write_text(tb)
shutil.copy2(HERE/'phase10_gpu_direct_ring_test.cpp',T/'test.cpp')
files=[T/'tb_axi_periph.v',OUT/'gpu_core.v',OUT/'axi_periph_slave.v',
       FIT/'targets/pocket/snac_shifter.v',FIT/'targets/pocket/apf/common.v',
       BASE/'altsyncram_stub.v']
files += [BASE/'common'/n for n in ['gpu_edge_walker.v','gpu_tex_cache.v','sync_fifo.v']]
defines=[x for x in build if x.startswith('+define+')]
cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_axi_periph','--Mdir',str(T/'obj'),'-I'+str(BASE/'common'),'-CFLAGS','-O2 -std=c++17',*defines,*map(str,files),str(T/'test.cpp')]
with (T/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
(T/'build.json').write_text(json.dumps(dict(command=cmd,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in files+[T/'test.cpp']},recipe_sha256=sha(Path(__file__)),simulator_sha256=sha(T/'obj/Vtb_axi_periph')),indent=2)+'\n')
with (T/'run.log').open('x') as f:r=subprocess.run([str(T/'obj/Vtb_axi_periph')],stdout=f,stderr=subprocess.STDOUT)
print((T/'run.log').read_text());assert r.returncode==0,r.returncode
result=json.loads((T/'run.log').read_text().splitlines()[-1]);result['build_sha256']=sha(T/'build.json')
(T/'result.json').write_text(json.dumps(result,indent=2)+'\n')
