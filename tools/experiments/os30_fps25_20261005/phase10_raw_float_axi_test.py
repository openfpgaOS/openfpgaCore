#!/usr/bin/env python3
"""Actual AXI peripheral + raw command processor + GPU parser smoke test."""
import argparse,hashlib,json,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';OUT=P/'raw-float-gpu-v1';T=OUT/'axi-tests'
BASE=P/'vcache-narrow-v1/model/frozen';FIT=P/'vcache-narrow-v1/fit-seed2/frozen/src/fpga'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sub(s,a,b):assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def build():
 T.mkdir(exist_ok=False);frozen=T/'frozen';frozen.mkdir()
 tb=(ROOT/'src/fpga/test/tb_axi_periph.v').read_text()
 tb=sub(tb,'    input wire reset_n,','    input wire reset_n,\n    input wire gpu_enable,allow_barrier,\n    output wire [31:0] fence_reached,')
 tb,n=re.subn(r'reg  \[31:0\] gpu_reg_rdata;\nalways @\(\*\) begin.*?\nend\n','wire [31:0] gpu_reg_rdata;\n',tb,flags=re.S);assert n==1
 baseline=json.loads((P/'vcache-narrow-v1/model/build.json').read_text())['command']
 params=[x[2:].split('=') for x in baseline if x.startswith('-G') and x[2:].split('=')[0] not in ['CPU_FAIR_THRESHOLD','CRAM_REFRESH_EXTRA']]
 gpu='\ngpu_core #(\n'+',\n'.join('    .%s(%s)'%tuple(x) for x in params)+'''
) gpu (
 .clk(clk),.reset_n(reset_n),.gpu_enable(gpu_enable),
 .reg_wr(dbg_gpu_reg_wr),.reg_addr(dbg_gpu_reg_addr),.reg_wdata(dbg_gpu_reg_wdata),.reg_rdata(gpu_reg_rdata),
 .m_rd_arready(1'b0),.m_rd_rvalid(1'b0),.m_rd_rdata(32'b0),.m_rd_rlast(1'b0),
 .m_wr_awready(1'b0),.m_wr_wready(1'b0),.m_wr_bvalid(1'b0),
 .gpu_tex_mem_arready(1'b0),.gpu_tex_mem_rvalid(1'b0),.gpu_tex_mem_rdata(32'b0),.gpu_tex_mem_rlast(1'b0),
 .gpu_tex_mem_up_busy(1'b0),.sram_rdata(32'b0),.sram_busy(1'b0),.sram_rdata_valid(1'b0),
 .slave_swap_pending(1'b0),.memory_barrier_done(allow_barrier),.fence_reached(fence_reached)
);
'''
 (frozen/'tb_axi_periph.v').write_text(sub(tb,'endmodule',gpu+'\nendmodule'))
 shutil.copy2(HERE/'phase10_raw_float_axi_test.cpp',frozen/'test.cpp')
 processor=P/'raw-float-processor-v3';manifest=json.loads((processor/'sources.json').read_text())
 arithmetic=[processor/n for n in ['phase10_raw_float_processor.v','geometry_vertex_packet.v',
  'phase10_float_cull_mem.v','phase10_float_fixed_pipe.v','GeometryFmaProbe.v']]
 for p in arithmetic:assert sha(p)==manifest[p.name]['sha256'],p
 inputs=[OUT/'gpu_core.v',OUT/'geometry_raw_float_ring.v',*arithmetic,
  FIT/'common/axi_periph_slave.v',FIT/'targets/pocket/snac_shifter.v',FIT/'targets/pocket/apf/common.v',
  BASE/'altsyncram_stub.v',*[BASE/'common'/n for n in ['gpu_edge_walker.v','gpu_tex_cache.v','sync_fifo.v']]]
 for p in inputs:shutil.copy2(p,frozen/p.name)
 files=[frozen/p.name for p in inputs]+[frozen/'tb_axi_periph.v']
 defines=[x for x in baseline if x.startswith('+define+')]+['+define+INCLUDE_GPU_RAW_FLOAT_WORKER']
 cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_axi_periph','--Mdir',str(T/'obj'),'-I'+str(frozen),'-CFLAGS','-O2 -std=c++17',*defines,*map(str,files),str(frozen/'test.cpp')]
 with (T/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 record=dict(command=cmd,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in files+[frozen/'test.cpp']},origin_sha256={str(p.relative_to(ROOT)):sha(p) for p in inputs},recipe_sha256=sha(Path(__file__)),simulator_sha256=sha(T/'obj/Vtb_axi_periph'))
 (T/'build.json').write_text(json.dumps(record,indent=2)+'\n')
def run():
 with (T/'run.log').open('x') as f:subprocess.run([str(T/'obj/Vtb_axi_periph')],stdout=f,stderr=subprocess.STDOUT,check=True)
 text=(T/'run.log').read_text();print(text);result=json.loads(text.splitlines()[-1]);result['build_sha256']=sha(T/'build.json');(T/'result.json').write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['build','run']);args=parser.parse_args();globals()[args.action]()
