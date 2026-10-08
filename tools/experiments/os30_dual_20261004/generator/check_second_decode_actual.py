#!/usr/bin/env python3
"""Compile the frozen decoded-buffer state blocks against an independent model."""
from pathlib import Path
import re,json,hashlib,subprocess,shutil
ROOT=Path(__file__).resolve().parents[4];ART=ROOT/'docs/measurements/os30-dual-20261004'
SRC=ART/'decode-second-skid-draft-v1/immediate';OUT=ART/'decode-second-skid-actual-proof-v1'
SHA='f703a709e272a70084ee567ee59719da1e4ff351462fd64f4355ae9e61e47fc5'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def run(cmd,log):
 with log.open('w') as f:subprocess.run([str(x) for x in cmd],check=True,stdout=f,stderr=subprocess.STDOUT)
def main():
 assert sha(SRC/'VexiiRiscv.v')==SHA
 s=(SRC/'VexiiRiscv.v').read_text();fields=json.loads((SRC/'payload-map.json').read_text());edits=json.loads((SRC/'changed-source-blocks.json').read_text())
 extra=[b for a,b in edits if a=='  wire decode_load_pending0;'][0].removesuffix('\n  wire decode_load_pending0;')
 assert s.count(extra)==1
 control=[b for a,b in edits if 'decode_ctrls_3_up_valid <=' in a][0]
 cancel='''      if(when_DecodePipelinePlugin_l95_4) begin
        decode_ctrls_3_up_LANE_SEL_0 <= 1'b0;
      end
      if(when_DecodePipelinePlugin_l95_5) begin
        decode_ctrls_3_up_LANE_SEL_1 <= 1'b0;
      end'''
 assert s.count(control+'\n'+cancel)==1
 module='''module decoded_buffer(input clk,reset,input incoming_valid,dispatch_ready,input [3:0] flushes,
input [1:0] incoming_mask,input [379:0] incoming_data,
output [12:0] status,output [379:0] head,tail,bank0,bank1,field_output);
wire decode_ctrls_2_down_isValid=incoming_valid;
wire decode_ctrls_3_up_isReady=dispatch_ready;
wire decode_ctrls_2_down_LANE_SEL_0=incoming_mask[0];
wire decode_ctrls_2_down_LANE_SEL_1=incoming_mask[1];
reg decode_ctrls_3_up_valid,decode_ctrls_3_up_LANE_SEL_0,decode_ctrls_3_up_LANE_SEL_1;
'''
 flush_names=['early0_EnvPlugin_logic_flushPort_valid','CsrAccessPlugin_logic_flushPort_valid','early0_BranchPlugin_logic_flushPort_valid','LsuPlugin_logic_flushPort_valid']
 for n,name in enumerate(flush_names):module+=f'wire {name}=flushes[{n}];\n'
 for field in fields:
  hi,lo=field['msb'],field['lsb'];width=field['bits'];sl=str(lo) if width==1 else f'{hi}:{lo}'
  decl=re.findall(r'^  wire\s+(?:\[\d+:0\]\s+)?'+field['head']+r';$',s,re.M);assert len(decl)==1
  module+=decl[0]+'\n'+f"wire [{width-1}:0] {field['input']}=incoming_data[{sl}];\n"
 module+=extra+'\n'
 for lane in range(2):module+=f'wire when_DecodePipelinePlugin_l95_{lane+4}=(!decode_ctrls_3_up_isReady)&&decode_dispatch_flush;\n'
 reset=''.join(f"      {name} <= 1'b0;\n" for name in ['decode_ctrls_3_up_valid','decode_ctrls_3_up_LANE_SEL_0','decode_ctrls_3_up_LANE_SEL_1'])
 for line in reset.splitlines():assert line in s
 module+='  always @(posedge clk or posedge reset) begin\n    if(reset) begin\n'+reset+'    end else begin\n'+control+'\n'+cancel+'\n    end\n  end\n'
 module+='''assign status={decode_dispatch_tail_trap_pending,decode_dispatch_tail_mask,decode_ctrls_3_up_LANE_SEL_1,decode_ctrls_3_up_LANE_SEL_0,decode_dispatch_up_forget,decode_dispatch_up_cancel,decode_dispatch_flush,decode_dispatch_head_ready,decode_dispatch_write_bank,decode_dispatch_read_bank,decode_dispatch_skid_full,decode_ctrls_3_up_valid};
assign head=decode_dispatch_head;assign tail=decode_dispatch_tail;
assign bank0=decode_dispatch_bank0;assign bank1=decode_dispatch_bank1;
'''
 module+='assign field_output={'+','.join(x['head'] for x in reversed(fields))+'};\nendmodule\n'
 OUT.mkdir(exist_ok=False);(OUT/'actual.v').write_text(module)
 traps=[next(x['lsb'] for x in fields if x['head']==f'decode_ctrls_3_up_TRAP_{lane}') for lane in range(2)]
 cpp=r'''#include "Vdecoded_buffer.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
Vdecoded_buffer d;
uint32_t rng=0x825fb135;uint32_t rnd(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
bool h=0,s=0;unsigned hm=0,tm=0;uint32_t hd[12]={},td[12]={};
uint64_t cases=0,held=0,fullflush=0,blockedflush=0,partial=0,asyncs=0;
void req(bool x,const char* why){if(!x){std::fprintf(stderr,"FAIL cycle%llu %s\n",(unsigned long long)cases,why);std::exit(1);}}
bool bit(const uint32_t *v,unsigned n){return(v[n/32]>>(n%32))&1;}
void observe(bool data_known){
 const unsigned st=d.status;const bool f=d.flushes!=0,r=!h||d.dispatch_ready;
 req(bool(st&1)==h && bool(st&2)==s,"occupancy");
 req(((st>>8)&3)==hm && ((st>>10)&3)==tm,"head/tail mask");
 req(bool(st&16)==r && bool(st&32)==f,"ready/flush");
 req(bool(st&64)==(f&&!s) && bool(st&128)==(f&&!s),"cancel/forget");
 const bool tr=s&&!f&&((tm&1&&bit(td,TRAP0))||(tm&2&&bit(td,TRAP1)));
 req(bool(st&4096)==tr,"buffered trap");
 for(unsigned i=0;i<12;i++){
  req(d.head[i]==d.field_output[i],"90-field alias packing");
  if(data_known)req(d.head[i]==hd[i],"head payload recurrence");
  if(s)req(d.tail[i]==td[i],"tail payload recurrence");
 }
}
void step(){
 d.clk=0;d.eval();const bool f=d.flushes!=0,r=!h||d.dispatch_ready;
 uint32_t nh[12],nt[12];for(unsigned i=0;i<12;i++){nh[i]=r?(s?td[i]:d.incoming_data[i]):hd[i];nt[i]=s?td[i]:d.incoming_data[i];}
 unsigned nm=r?(f?0:(s?tm:unsigned(d.incoming_mask))):hm;
 if(!d.dispatch_ready&&f)nm=0;
 bool hn=r?((s||d.incoming_valid)&&!f):h,sn=!r&&!f&&(s||d.incoming_valid);
 unsigned tnext=s?tm:unsigned(d.incoming_mask);
 held+=s&&!r&&!f;fullflush+=s&&f;blockedflush+=s&&f&&!r;partial+=(hm==1||hm==2);
 if(d.reset){hn=0;sn=0;nm=0;}
 d.clk=1;d.eval();d.clk=0;d.eval();h=hn;s=sn;hm=nm;tm=tnext;
 std::memcpy(hd,nh,sizeof hd);std::memcpy(td,nt,sizeof td);++cases;
 observe(!d.reset);
}
void reset(){d.clk=0;d.reset=0;d.eval();d.reset=1;d.eval();h=0;s=0;hm=0;++asyncs;observe(false);d.reset=0;d.eval();}
void data(unsigned pos,bool ones=false){for(unsigned i=0;i<12;i++)d.incoming_data[i]=ones?0xffffffff:0;d.incoming_data[11]&=0x0fffffff;if(pos<380)d.incoming_data[pos/32]^=1u<<(pos%32);}
int main(){
 d.reset=0;d.incoming_valid=0;d.dispatch_ready=0;d.flushes=0;d.incoming_mask=0;reset();
 // Every data bit and its complement traverse empty bypass, held tail, pop,
 // flush and all independent lane-mask shapes in the actual extracted RTL.
 for(unsigned bit=0;bit<380;bit++)for(unsigned state=0;state<3;state++)
 for(unsigned ctl=0;ctl<128;ctl++){
  reset();d.flushes=0;d.dispatch_ready=1;d.incoming_valid=state!=0;d.incoming_mask=ctl&3;data((bit+1)%380);step();
  if(state==2){d.dispatch_ready=0;d.incoming_valid=1;d.incoming_mask=(ctl>>2)&3;data((bit+2)%380,true);step();}
  d.incoming_valid=ctl&1;d.dispatch_ready=(ctl>>1)&1;d.flushes=(ctl>>2)&15;d.incoming_mask=(ctl>>4)&3;
  d.reset=(ctl>>6)&1;d.eval();if(d.reset){h=0;s=0;hm=0;observe(false);}
  data(bit,ctl&8);step();d.reset=0;
 }
 reset();
 for(unsigned i=0;i<1000000;i++){
  d.incoming_valid=rnd()&1;d.dispatch_ready=(rnd()&3)==0;d.flushes=(i%41==0)?(1u<<(rnd()&3)):0;
  d.incoming_mask=rnd()&3;for(unsigned w=0;w<12;w++)d.incoming_data[w]=rnd();d.incoming_data[11]&=0x0fffffff;
  if(i%1031==0)reset();step();
 }
 std::printf("PASS actual380-bit/90-field buffer cycles=%llu held=%llu full_flush=%llu blocked_flush=%llu partial=%llu asynchronous_resets=%llu\n",(unsigned long long)cases,(unsigned long long)held,(unsigned long long)fullflush,(unsigned long long)blockedflush,(unsigned long long)partial,(unsigned long long)asyncs);
}
'''.replace('TRAP0',str(traps[0])).replace('TRAP1',str(traps[1]))
 (OUT/'proof.cpp').write_text(cpp)
 run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','decoded_buffer','--Mdir',OUT/'obj','-CFLAGS','-std=c++17 -O2',OUT/'actual.v',OUT/'proof.cpp'],OUT/'build.log')
 assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(OUT/'build.log').read_text())
 run([OUT/'obj/Vdecoded_buffer'],OUT/'proof.log')
 shutil.copy2(__file__,OUT/Path(__file__).name)
 (OUT/'manifest.json').write_text(json.dumps(dict(passed=True,candidate_sha256=SHA,actual_payload_and_storage_blocks_extracted=True,all_90_head_aliases_observed=True,independent_reference='Explicit S2M tail plus StageLink head, not original one-slot backpressure',local_induction=str(SRC/'local-refinement.json'),proof_sha256=sha(OUT/'actual.v'),result_sha256=sha(OUT/'proof.log'),script_sha256=sha(Path(__file__)),architectural_CPU_qualification='Pending retained14 fixture suite'),indent=2)+'\n')
 print((OUT/'proof.log').read_text())
if __name__=='__main__':main()
