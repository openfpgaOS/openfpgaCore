#!/usr/bin/env python3
"""Passive count/light timing audit of unchanged frozen LOD5 arithmetic."""
import json,hashlib,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'unified-load-latency-v1'
SRC=P/'unified-load-components-v1/frozen'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a[:80],s.count(a));return s.replace(a,b)
def prepare():
 OUT.mkdir(exist_ok=False);f=OUT/'frozen';shutil.copytree(SRC,f)
 p=f/'tb_load_alias.v';s=p.read_text()
 s=rep(s,' output wire obs_active,obs_block);',''' output wire obs_active,obs_block,
 output wire [2:0] audit_state,output wire audit_fma,audit_tbusy,audit_lbusy,
 output wire audit_tdone,output wire [1:0] audit_recip_state,
 output wire [31:0] audit_tcycles,audit_lcycles);''')
 s=rep(s,'endmodule','''assign audit_state=load_view.load.state;
assign audit_fma=load_view.load.compute.fma_request_valid;
assign audit_tbusy=(load_view.load.compute.busy&&(load_view.load.compute.phase<5||load_view.load.compute.recip_completed!=load_view.load.compute.n));
assign audit_lbusy=(load_view.load.compute.busy&&load_view.load.compute.phase>=5&&!load_view.load.compute.arithmetic_done);
assign audit_tdone=(load_view.load.compute.phase>=5||load_view.load.compute.arithmetic_done);
assign audit_recip_state=load_view.load.compute.recip_state;
assign audit_tcycles=load_view.load.compute.transform_cycles;
assign audit_lcycles=load_view.load.compute.lighting_cycles;
endmodule''');p.write_text(s)
 p=f/'phase10_load_alias_test.cpp';s=p.read_text()
 s=rep(s,'static uint64_t cycles=0,alias_words=0,stalls=0;','''static uint64_t cycles=0,alias_words=0,stalls=0;
static bool measure=false;
static uint64_t stages[5],fmas,tbusy,lbusy,tdonewait,recipbusy;
''')
 s=s.replace('dma_allow&&!dma_active&&rnd()%4!=0','dma_allow&&!dma_active').replace('dma_active&&dma_allow&&(dma_held||rnd()%3!=0)','dma_active&&dma_allow')
 s=rep(s,' dma_held=t.dma_rvalid&&!t.dma_rready;t.clk=1;', ''' if(measure){stages[t.audit_state]++;fmas+=t.audit_fma;tbusy+=t.audit_tbusy;lbusy+=t.audit_lbusy;
  tdonewait+=t.audit_tbusy&&t.audit_tdone;recipbusy+=t.audit_recip_state!=0;}
 dma_held=t.dma_rvalid&&!t.dma_rready;t.clk=1;''')
 start=s.index(' reset();need(reg(0)==');end=s.index(' }catch(',start)
 body=r'''
 reset();need(reg(0)==0x4c4f4405,"ID");
 std::puts("count,lighting,power_two_w,total,fetch,compute,scan,wrapper,fma_issues,transform_busy,lighting_busy,reciprocal_tail,reciprocal_busy,transform_cycles,lighting_cycles");
 for(unsigned power=0;power<2;power++)for(unsigned lit=0;lit<2;lit++)for(unsigned n=1;n<=32;n++){
  Job j=jobs[0];j.h[0]=n;j.h[1]=0xfc0000;j.h[2]=0x3f800000;
  for(unsigned k=0;k<16;k++)j.h[3+k]=(k%5==0)?0x3f800000:0;
  j.h[3+15]=power?0x3f800000:0x40400000;
  j.h[19]=0x3f800000;j.h[20]=j.h[21]=0;j.h[22]=0x101010;j.h[23]=0x404040;
  j.h[24]=lit;j.h[25]=j.h[26]=65535;j.d.assign(n*15,0);
  for(unsigned v=0;v<n;v++){
   j.d[v*15]=0x3f800000;j.d[v*15+1]=0x40000000;j.d[v*15+2]=0x40400000;
   j.d[v*15+3]=0;j.d[v*15+4]=0x007f0000;j.d[v*15+5]=0x0000ff00;
  }
  configure(j);for(auto&x:stages)x=0;fmas=tbusy=lbusy=tdonewait=recipbusy=0;
  uint64_t before=cycles;measure=true;put(8,1);done();measure=false;
  need((reg(4)&0xef)==6,"timing job failed");need(fmas==(17+7*lit)*n,"FMA issue count");
  std::printf("%u,%u,%u,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%u,%u\n",n,lit,power,
   (unsigned long long)(cycles-before),(unsigned long long)stages[1],(unsigned long long)stages[2],
   (unsigned long long)stages[3],(unsigned long long)stages[0],(unsigned long long)fmas,
   (unsigned long long)tbusy,(unsigned long long)lbusy,(unsigned long long)tdonewait,(unsigned long long)recipbusy,
   t.audit_tcycles,t.audit_lcycles);
 }
'''
 p.write_text(s[:start]+body+s[end:])
 (OUT/'recipe.py').write_bytes(Path(__file__).read_bytes())
 (OUT/'sources.json').write_text(json.dumps({x.name:sha(x) for x in f.iterdir()},indent=2)+'\n')
def run():
 f=OUT/'frozen';d=OUT/'model';d.mkdir(exist_ok=False)
 cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','-Wno-WIDTH','--top-module','tb_load_alias','--Mdir',str(d/'obj'),'-CFLAGS','-O2 -std=c++17']+[str(x) for x in sorted(f.glob('*.v'))]+[str(f/'phase10_load_alias_test.cpp')]
 (d/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
 with (d/'build.log').open('x') as o:subprocess.run(cmd,stdout=o,stderr=subprocess.STDOUT,check=True)
 with (OUT/'latency.csv').open('x') as o:subprocess.run([str(d/'obj/Vtb_load_alias'),str(f/'native.bin')],stdout=o,stderr=subprocess.STDOUT,check=True)
 print(OUT/'latency.csv')
if __name__=='__main__':prepare();run()
