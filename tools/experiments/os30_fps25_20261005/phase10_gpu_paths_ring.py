#!/usr/bin/env python3
"""Actual GPU CPU-ring full/wrap test, both command-DMA parameter values."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'gpu-paths-review-v11/ring';BASE=P/'gpu-paths-review-v11/model'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 OUT.mkdir(exist_ok=False);F=OUT/'frozen';F.mkdir()
 build=json.loads((BASE/'command.json').read_text())
 defs=[s for s in build if s.startswith('+define+') and s!='+define+EXCLUDE_GPU_COMMAND_DMA']
 params=dict(s[2:].split('=') for s in build if s.startswith('-G'))
 for k in ['CPU_FAIR_THRESHOLD','CRAM_REFRESH_EXTRA','INCLUDE_COMMAND_DMA']:params.pop(k)
 text='''module tb_ring(input wire clk,reset_n,reg_wr,input wire[3:0]reg_addr,
 input wire[31:0]reg_wdata,input wire[11:0]peek_addr,
 input wire arready,rvalid,rlast,input wire[31:0]rdata,
 output wire arvalid,output wire[31:0]araddr,output wire[7:0]arlen,
 output wire[11:0]append0,append1,publish0,publish1,read0,read1,
 output wire[31:0]peek0,peek1,
 output wire overflow0,overflow1,mode0,mode1,busy0,busy1,dma_busy1);
'''
 for i in (0,1):
  ps=dict(params,INCLUDE_COMMAND_DMA=str(i))
  text+='gpu_core #(\n'+',\n'.join(f'.{k}({v})' for k,v in ps.items())+f'\n) gpu{i} (\n'+'''
 .clk(clk),.reset_n(reset_n),.gpu_enable(1'b1),
 .reg_wr(reg_wr),.reg_addr(reg_addr),.reg_wdata(reg_wdata),
 .m_rd_arready(arready),.m_rd_rvalid(rvalid),.m_rd_rdata(rdata),.m_rd_rlast(rlast),
 .m_wr_awready(1'b0),.m_wr_wready(1'b0),.m_wr_bvalid(1'b0),
 .gpu_tex_mem_arready(1'b0),.gpu_tex_mem_rvalid(1'b0),.gpu_tex_mem_rdata(32'b0),.gpu_tex_mem_rlast(1'b0),
 .gpu_tex_mem_up_busy(1'b0),.sram_rdata(32'b0),.sram_busy(1'b0),.sram_rdata_valid(1'b0),
 .slave_swap_pending(1'b0),.memory_barrier_done(1'b1),
'''+f'.busy(busy{i})'+(',.m_rd_arvalid(arvalid),.m_rd_araddr(araddr),.m_rd_arlen(arlen)' if i else '')+'\n);\n'
  for port,sig in [('append','ring_wr_addr'),('publish','ring_wrptr'),('read','ring_rdptr'),('peek','ring_bram[peek_addr]'),('overflow','cpu_ring_overflow'),('mode','cpu_ring_mode')]:
   text+=f'assign {port}{i}=gpu{i}.{sig};\n'
 text+='assign dma_busy1=gpu1.dma_busy;\nendmodule\n'
 (F/'tb_ring.v').write_text(text);shutil.copy2(HERE/'phase10_gpu_paths_ring.cpp',F/'test.cpp')
 files=[F/'tb_ring.v']
 for n in ['gpu_core.v','gpu_edge_walker.v','gpu_tex_cache.v','sync_fifo.v']:
  shutil.copy2(BASE/'frozen/common'/n,F/n);files.append(F/n)
 shutil.copy2(BASE/'frozen/altsyncram_stub.v',F/'altsyncram_stub.v');files.append(F/'altsyncram_stub.v')
 cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_ring','--Mdir',str(OUT/'obj'),'-I'+str(F),'-CFLAGS','-std=c++17 -O2',*defs,*map(str,files),str(F/'test.cpp')]
 with (OUT/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 (OUT/'build.json').write_text(json.dumps(dict(command=cmd,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in files+[F/'test.cpp']},simulator_sha256=sha(OUT/'obj/Vtb_ring'),recipe_sha256=sha(Path(__file__))),indent=2)+'\n')
 with (OUT/'run.log').open('x') as f:r=subprocess.run([str(OUT/'obj/Vtb_ring')],stdout=f,stderr=subprocess.STDOUT)
 print((OUT/'run.log').read_text());assert r.returncode==0
 result=json.loads((OUT/'run.log').read_text().splitlines()[-1]);result['build_sha256']=sha(OUT/'build.json')
 (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
