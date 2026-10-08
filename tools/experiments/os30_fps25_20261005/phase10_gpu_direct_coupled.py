#!/usr/bin/env python3
"""Private native-FMA CPU estimate + actual GDR/GPU/SDRAM/CRAM1 coupled model."""
import argparse,hashlib,json,shutil,subprocess,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OLD=ROOT/'docs/measurements/os30-overlap-20261006/coupled'
OUT=P/os.environ.get('GDR_COUPLED_LABEL','gpu-direct-coupled-v1');F=OUT/'model/frozen';Q=OUT/'qsim'
NATIVE=P/'gpu-direct-native-v1';FMA=P/'qsim-fma26-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a[:120],s.count(a));return s.replace(a,b)
def write(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def prepare():
 OUT.mkdir(exist_ok=False)
 Q.mkdir()
 for p in (OLD/'qsim').iterdir():
  if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,Q/p.name)
 for n in ['fpu.c','fpu_fast.h']:shutil.copy2(FMA/n,Q/n)
 p=Q/'Makefile';p.write_text(rep(p.read_text(),'CFLAGS  := $(OPT)','CFLAGS  := -DQSIM_RETAINED_FMA26 $(OPT)'))
 p=Q/'env.c';s=p.read_text()
 a='    qenv *E = ctx;\n    if (addr - ENV_GPU_BASE < 0x40) {'
 assert s.count(a)==2
 s=s.replace(a,'''    qenv *E = ctx;
    if(addr-0x4b000000u<0x1000) {
        if(size!=4 || (addr&3))abort();
        *val=coupled_active()?coupled_gd_read(addr-0x4b000000u):0;
        return 0;
    }
    if (addr - ENV_GPU_BASE < 0x40) {''',1)
 pos=s.index('static int on_mmio_write(');before,after=s[:pos],s[pos:]
 after=rep(after,a,'''    qenv *E = ctx;
    if(addr-0x4b000000u<0x1000) {
        if(size!=4 || (addr&3))abort();
        if(coupled_active())coupled_gd_write(addr-0x4b000000u,val);
        return 0;
    }
    if (addr - ENV_GPU_BASE < 0x40) {''')
 p.write_text(before+after)
 p=Q/'coupled.h';s=p.read_text();s=rep(s,'int coupled_active(void);','''int coupled_active(void);
void coupled_gd_write(uint32_t off,uint32_t value);
uint32_t coupled_gd_read(uint32_t off);''');p.write_text(s)
 shutil.copytree(OLD/'model/frozen',F)
 shutil.copy2(P/'gpu-direct-ring-v1/gpu_core.v',F/'common/gpu_core.v')
 sources=json.loads((NATIVE/'sources.json').read_text());rtl=[]
 for n,meta in sources.items():
  if n.endswith('.v'):
   assert sha(NATIVE/n)==meta['sha256'],n
   shutil.copy2(NATIVE/n,F/'common'/n);rtl.append(n)
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
    output wire geo_request,geo_grant,geo_valid,geo_ready,geo_abort,geo_owned,geo_done,
    output wire [12:0] geo_words,
    output wire [31:0] geo_data,
    output wire gpu_cpu_ready,
    // MMIO register interface (driven from C++)''')
 s=rep(s,'gpu_core #(\n','gpu_core #(\n    .INCLUDE_GEOMETRY_INGRESS(1),\n')
 s=rep(s,'    // MMIO\n    .reg_wr(reg_wr),','''    .geo_request(geo_request),.geo_words(geo_words),.geo_grant(geo_grant),
    .geo_valid(geo_valid),.geo_data(geo_data),.geo_ready(geo_ready),.geo_abort(geo_abort),
    .geo_owned(geo_owned),.geo_done(geo_done),.gpu_cpu_pending(reg_wr),.gpu_cpu_ready(gpu_cpu_ready),
    // MMIO
    .reg_wr(reg_wr),''')
 inst='''wire [31:0] gd_rdata;
wire [1:0] gd_rresp;
geometry_direct_mmio direct_client(
 .clk(clk),.reset(!reset_n),.reg_wr(gd_reg_wr),.reg_addr(gd_reg_addr),
 .reg_wdata(gd_reg_wdata),.reg_wstrb(4'hf),.reg_rdata(gd_reg_rdata),
 .m_arvalid(gd_arvalid),.m_arready(gd_arready),.m_araddr(gd_araddr),.m_arlen(gd_arlen),
 .m_rvalid(gd_rvalid),.m_rready(gd_rready),.m_rdata(gd_rdata),.m_rresp(gd_rresp),.m_rlast(gd_rlast),
 .geo_request(geo_request),.geo_words(geo_words),.geo_grant(geo_grant),
 .geo_valid(geo_valid),.geo_data(geo_data),.geo_ready(geo_ready),.geo_abort(geo_abort),
 .geo_owned(geo_owned),.geo_done(geo_done));
'''
 s=rep(s,'tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD)) memory (',inst+'tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD),.BRG_FAIR_THRESHOLD(BRG_FAIR_THRESHOLD)) memory (')
 for port,value in dict(arvalid='gd_arvalid',arready='gd_arready',araddr="{6'd0,gd_araddr[25:0]}",arlen='gd_arlen',rvalid='gd_rvalid',rdata='gd_rdata',rlast='gd_rlast',rready='gd_rready').items():
  import re
  s,n=re.subn(r'\.m2_'+port+r'\([^)]*\)',f'.m2_{port}({value})',s);assert n==1,port
 s=rep(s,'    .m2_bvalid(),','    .m2_bvalid(),\n    .m2_rresp(gd_rresp),');p.write_text(s)
 shutil.copy2(HERE/'phase10_gpu_direct_coupled.cpp',F/'coupled.cpp')
 cmd=json.loads((OLD/'model/build.json').read_text())['command'];cmd=[x.replace(str(OLD),str(OUT)) for x in cmd]
 cmd += [str(F/'common'/n) for n in rtl]
 cmd += ['-GBRG_FAIR_THRESHOLD='+os.environ.get('GDR_BRG_THRESHOLD','12')]
 write(OUT/'build-command.json',cmd)
 write(OUT/'inputs.json',dict(scope=__doc__,native_fma_sources=str(FMA),native_rtl=str(NATIVE),ring_rtl=str(P/'gpu-direct-ring-v1'),
  inherited_model=str(OLD),helper_sha256=sha(Path(__file__)),sources={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file()}))
def build():
 names='cpu fpu cache prof qelf lint gpu app env main'.split()
 with (OUT/'build-qsim.log').open('w') as log:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in names]],cwd=Q,stdout=log,stderr=subprocess.STDOUT,check=True)
 cmd=json.loads((OUT/'build-command.json').read_text())
 with (OUT/'model/build.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 write(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))
if __name__=='__main__':
 import sys
 for action in sys.argv[1:]:globals()[action]()
