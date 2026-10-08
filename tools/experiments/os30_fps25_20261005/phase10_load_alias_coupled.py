#!/usr/bin/env python3
"""Private actual LOAD/BRAM-alias/GPU/memory RTL with native-FMA modeled CPU."""
import hashlib,json,shutil,subprocess,os,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OLD=ROOT/'docs/measurements/os30-overlap-20261006/coupled'
GDR=P/'gpu-direct-coupled-v1';RTL=P/'load-bram-alias-v3';FUNC=P/'load-alias-functional-v2'
OUT=P/os.environ.get('LOD4_COUPLED_LABEL','load-alias-coupled-v1');F=OUT/'model/frozen';Q=OUT/'qsim'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a[:120],s.count(a));return s.replace(a,b)
def write(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def prepare():
 OUT.mkdir(exist_ok=False);Q.mkdir()
 for p in (GDR/'qsim').iterdir():
  if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,Q/p.name)
 # Same independently validated native-FMA arithmetic as fresh functional-v2.
 for n in ('fpu.c','fpu_fast.h'):
  assert sha(FUNC/'functional-qsim'/n)==sha(GDR/'qsim'/n)
  shutil.copy2(FUNC/'functional-qsim'/n,Q/n)
 p=Q/'coupled.h';p.write_text(p.read_text()+'\nuint64_t coupled_alias_refill(uint32_t addr,uint64_t earliest);\n')
 p=Q/'cache.c';s=p.read_text();s='#include "coupled.h"\n'+s
 s=rep(s,'    uint64_t start = sdram_burst_start(m, addr, t);','''    if(coupled_active() && (addr&~1023u)==0x13f80000u) {
        // Alias does not consume SDRAM bandwidth. Preserve an outstanding
        // dirty victim writeback's modeled serialization only.
        if(w->tag && w->dirty && m->ic_bus_free>t)t=m->ic_bus_free;
        uint64_t done=coupled_alias_refill(addr,t);
        w->tag=line+1;w->dirty=0;w->fill_start=done;
        return done;
    }
    uint64_t start = sdram_burst_start(m, addr, t);''')
 s=rep(s,'    m->cbo_ops++;','    m->cbo_ops++;\n    if(coupled_active() && (addr&~1023u)==0x13f80000u) {m->cyc+=4;m->pen_cbo+=4;}')
 p.write_text(s)
 shutil.copytree(OLD/'model/frozen',F)
 shutil.copy2(P/'vcache-narrow-v1/gpu_core.v',F/'common/gpu_core.v')
 rtl=[]
 for n,meta in json.loads((RTL/'sources.json').read_text()).items():
  if not n.endswith('.v') or n.startswith('tb_'):continue
  assert sha(RTL/n)==meta['sha256'],n
  shutil.copy2(RTL/n,F/'common'/n);rtl.append(n)
 p=F/'tb_sdram_cont.v';s=p.read_text()
 s=rep(s,'parameter CPU_FAIR_THRESHOLD=8,','parameter CPU_FAIR_THRESHOLD=8, parameter BRG_FAIR_THRESHOLD=12,')
 s=rep(s,'.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD)) sdram_arb','.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD), .BRG_FAIR_THRESHOLD(BRG_FAIR_THRESHOLD)) sdram_arb')
 s=rep(s,'output wire        m2_bvalid,','output wire        m2_bvalid,\n    output wire [1:0] m2_rresp,')
 s=rep(s,'.m2_rresp(),','.m2_rresp(m2_rresp),');p.write_text(s)
 p=F/'tb_gpu.v';s=p.read_text()
 s=rep(s,'parameter CPU_FAIR_THRESHOLD = 8,','parameter CPU_FAIR_THRESHOLD = 8, parameter BRG_FAIR_THRESHOLD = 12,')
 s=rep(s,'    // MMIO register interface (driven from C++)','''    input wire gd_reg_wr,
    input wire [9:0] gd_reg_addr,
    input wire [31:0] gd_reg_wdata,
    output wire [31:0] gd_reg_rdata,
    output wire gd_arvalid,gd_arready,gd_rvalid,gd_rready,gd_rlast,
    output wire [31:0] gd_araddr,
    output wire [7:0] gd_arlen,
    input wire alias_arvalid,input wire [31:0] alias_araddr,input wire [7:0] alias_arlen,
    output wire alias_arready,alias_rvalid,alias_rlast,
    output wire [31:0] alias_rdata,output wire [1:0] alias_rresp,input wire alias_rready,
    // MMIO register interface (driven from C++)''')
 inst='''wire [31:0] gd_rdata,view_read_data;
wire [1:0] gd_rresp;
wire [7:0] view_read_addr;
wire view_active,view_block,view_valid;
wire [5:0] view_count;
geometry_load_alias load_client(
 .clk(clk),.reset(!reset_n),.reg_wr(gd_reg_wr),.reg_addr(gd_reg_addr),
 .reg_wdata(gd_reg_wdata),.reg_wstrb(4'hf),.reg_rdata(gd_reg_rdata),
 .m_arvalid(gd_arvalid),.m_arready(gd_arready),.m_araddr(gd_araddr),.m_arlen(gd_arlen),
 .m_rvalid(gd_rvalid),.m_rready(gd_rready),.m_rdata(gd_rdata),.m_rresp(gd_rresp),.m_rlast(gd_rlast),
 .view_read_addr(view_read_addr),.view_read_data(view_read_data),
 .view_active(view_active),.view_block(view_block),.view_valid(view_valid),.view_count(view_count));
geometry_bram_alias alias_port(
 .clk(clk),.reset(!reset_n),.cpu_reset(1'b0),
 .s_arvalid(alias_arvalid),.s_arready(alias_arready),.s_araddr(alias_araddr),.s_arlen(alias_arlen),
 .s_rvalid(alias_rvalid),.s_rready(alias_rready),.s_rdata(alias_rdata),.s_rresp(alias_rresp),.s_rlast(alias_rlast),
 .s_awvalid(1'b0),.s_awready(),.s_awaddr(32'b0),.s_awlen(8'b0),
 .s_wvalid(1'b0),.s_wready(),.s_wdata(32'b0),.s_wstrb(4'b0),.s_wlast(1'b0),.s_bvalid(),.s_bresp(),
 .m_arvalid(),.m_arready(1'b0),.m_araddr(),.m_arlen(),
 .m_rvalid(1'b0),.m_rready(),.m_rdata(32'b0),.m_rresp(2'b0),.m_rlast(1'b0),
 .m_awvalid(),.m_awready(1'b0),.m_awaddr(),.m_awlen(),
 .m_wvalid(),.m_wready(1'b0),.m_wdata(),.m_wstrb(),.m_wlast(),.m_bvalid(1'b0),.m_bresp(2'b0),
 .view_read_addr(view_read_addr),.view_read_data(view_read_data),
 .view_active(view_active),.view_block(view_block),.view_valid(view_valid),.view_count(view_count));
'''
 s=rep(s,'tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD)) memory (',inst+'tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD),.BRG_FAIR_THRESHOLD(BRG_FAIR_THRESHOLD)) memory (')
 for port,value in dict(arvalid='gd_arvalid',arready='gd_arready',araddr="{6'd0,gd_araddr[25:0]}",arlen='gd_arlen',rvalid='gd_rvalid',rdata='gd_rdata',rlast='gd_rlast',rready='gd_rready').items():
  s,n=re.subn(r'\.m2_'+port+r'\([^)]*\)',f'.m2_{port}({value})',s);assert n==1,port
 s=rep(s,'    .m2_bvalid(),','    .m2_bvalid(),\n    .m2_rresp(gd_rresp),');p.write_text(s)
 shutil.copy2(HERE/'phase10_load_alias_coupled.cpp',F/'coupled.cpp')
 cmd=json.loads((OLD/'model/build.json').read_text())['command'];cmd=[x.replace(str(OLD),str(OUT)) for x in cmd]
 cmd += [str(F/'common'/n) for n in rtl]
 cmd += ['-GBRG_FAIR_THRESHOLD='+os.environ.get('LOD4_BRG_THRESHOLD','12')]
 write(OUT/'build-command.json',cmd)
 write(OUT/'inputs.json',dict(scope=__doc__,native_functional_model=str(FUNC),rtl=str(RTL),
  inherited_model=str(OLD),helper_sha256=sha(Path(__file__)),sources={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file()}))
def build():
 names='cpu fpu cache prof qelf lint gpu app env main'.split()
 with (OUT/'build-qsim.log').open('x') as log:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in names]],cwd=Q,stdout=log,stderr=subprocess.STDOUT,check=True)
 cmd=json.loads((OUT/'build-command.json').read_text())
 with (OUT/'model/build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 write(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu'),alias_cbo_extra_cycles=4,calibrated_cache_sha256=sha(Q/'cache.c')))
if __name__=='__main__':
 for action in sys.argv[1:]:globals()[action]()
