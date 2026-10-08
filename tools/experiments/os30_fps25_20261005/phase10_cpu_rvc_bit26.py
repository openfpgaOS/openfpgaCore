#!/usr/bin/env python3
"""Private zero-cycle RVC bit26 Boolean flattening, scoped to measured STA endpoint."""
import hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-fp-rs3-mux-v1/VexiiRiscv.v';O=P/'cpu-rvc-bit26-v1'
I='AlignerPlugin_logic_extractors_0_ctx_instruction';F='AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst';E='AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def blocks(s):
 for m in re.finditer(r'^  always @\(\*\) begin\n',s,re.M):
  level=0
  for t in re.finditer(r'\b(begin|end)\b',s[m.start():]):
   level+=1 if t[1]=='begin' else -1
   if not level:
    yield s[m.start():m.start()+t.end()];break
EQ='''  // Direct bit-plane form of the existing RVC decoder, not a new instruction.
  wire rvc_rd_is_sp = (AlignerPlugin_logic_extractors_0_ctx_instruction[11:7] == 5'd2);
  wire rvc_instruction_bit26 =
    (((switch_Rvc_l55==5'h00)||(switch_Rvc_l55==5'h09)||(switch_Rvc_l55==5'h0d)||
      (switch_Rvc_l55==5'h16)||(switch_Rvc_l55==5'h17)) && AlignerPlugin_logic_extractors_0_ctx_instruction[7]) |
    (((switch_Rvc_l55==5'h02)||(switch_Rvc_l55==5'h03)||(switch_Rvc_l55==5'h05)||
      (switch_Rvc_l55==5'h06)||(switch_Rvc_l55==5'h07)||(switch_Rvc_l55==5'h0e)||
      (switch_Rvc_l55==5'h0f)||((switch_Rvc_l55==5'h0b)&&rvc_rd_is_sp)) && AlignerPlugin_logic_extractors_0_ctx_instruction[5]) |
    (((switch_Rvc_l55==5'h08)||(switch_Rvc_l55==5'h0a)||
      ((switch_Rvc_l55==5'h0b)&&!rvc_rd_is_sp)||
      ((switch_Rvc_l55==5'h0c)&&(AlignerPlugin_logic_extractors_0_ctx_instruction[11:10]==2'b10))) && AlignerPlugin_logic_extractors_0_ctx_instruction[12]) |
    (((switch_Rvc_l55==5'h12)||(switch_Rvc_l55==5'h13)) && AlignerPlugin_logic_extractors_0_ctx_instruction[2]);
'''
def prepare():
 O.mkdir(exist_ok=False);s=BASE.read_text();shutil.copy2(BASE,O/'baseline.v')
 assignment='      decode_ctrls_0_up_Decode_INSTRUCTION_0 = '+F+';';assert s.count(assignment)==1
 candidate=s.replace(assignment,assignment+'\n      decode_ctrls_0_up_Decode_INSTRUCTION_0[26] = rvc_instruction_bit26;')
 marker='  assign AlignerPlugin_logic_feeder_lanes_0_isRvc =';assert candidate.count(marker)==1
 candidate=candidate.replace(marker,EQ+'\n'+marker)
 (O/'VexiiRiscv.v').write_text(candidate)
 # Extract the literal original decompressor including its nested subdecoders.
 decl=[];names=set()
 for line in s.splitlines():
  if not re.match(r'^  (wire|reg)\s+',line):continue
  name=line.strip().rstrip(';').split()[-1]
  if F in name or name in (E,'switch_Rvc_l55') or name.startswith('when_Rvc_'):
   decl.append(line);names.add(name)
 assignments=[]
 for line in s.splitlines():
  m=re.match(r'^  assign (\w+) = ',line)
  if m and m[1] in names:assignments.append(line)
 chosen=[]
 for b in blocks(s):
  targets=set(re.findall(r'\b(\w+)(?:\[[^\]]+\])? =(?!=) ',b))
  if targets & names:
   assert targets<=names,targets-names
   chosen.append(b)
 assert len(chosen)==10,len(chosen)
 text='module rvc_bit26_probe(input wire[31:0] raw,output wire[31:0] old_instruction,new_instruction,output wire illegal);\n'
 text+='wire[31:0] '+I+'=raw;\n'+'\n'.join(decl+assignments+chosen)+'\n'+EQ
 text+='assign old_instruction=raw[1:0]==2\'b11 ? raw : '+F+';\n'
 text+='assign new_instruction=raw[1:0]==2\'b11 ? raw : {'+F+'[31:27],rvc_instruction_bit26,'+F+'[25:0]};\n'
 text+='assign illegal=(raw[1:0]!=2\'b11)&&'+E+';\nendmodule\n';(O/'probe.v').write_text(text)
 save(O/'prepared.json',dict(parent_cpu_sha256=sha(BASE),candidate_cpu_sha256=sha(O/'VexiiRiscv.v'),added_state=0,added_cycles=0,scope='Only compressed instruction data bit26; original raw instruction, all other decompressed bits, illegal decode and controls unchanged',extracted_always_blocks=len(chosen),extracted_assignments=len(assignments),extracted_declarations=len(decl)))
 shutil.copy2(__file__,O/'recipe.py')
CPP=r'''#include "Vrvc_bit26_probe.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
int main(){Vrvc_bit26_probe t;uint64_t checks=0,legal=0,illegal=0,raw=0;uint32_t rand=0x84ef9651;
 for(unsigned low=0;low<65536;low++)for(unsigned high=0;high<19;high++){
  uint32_t h=0;if(high>0&&high<=16)h=1u<<(high-1);else if(high==17)h=0xffff;else if(high==18){rand^=rand<<13;rand^=rand>>17;rand^=rand<<5;h=rand&0xffff;}
  t.raw=(h<<16)|low;t.eval();
  if(t.old_instruction!=t.new_instruction){std::fprintf(stderr,"mismatch raw%08x old%08x new%08x illegal%d\n",t.raw,t.old_instruction,t.new_instruction,t.illegal);return1;}
  checks++;if((low&3)==3)raw++;else if(t.illegal)illegal++;else legal++;
 }
 std::printf("{\"passed\":true,\"low16_encodings\":65536,\"upper16_patterns_per_encoding\":19,\"exact_checks\":%llu,\"uncompressed\":%llu,\"defined_legal_compressed\":%llu,\"illegal_compressed_sim_values\":%llu}\n",(unsigned long long)checks,(unsigned long long)raw,(unsigned long long)legal,(unsigned long long)illegal);
 return0;}
'''.replace('return1','return 1').replace('return0','return 0')
def prove():
 d=O/'proof';d.mkdir(exist_ok=False);(d/'main.cpp').write_text(CPP)
 cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','rvc_bit26_probe','--Mdir',str(d/'obj'),'-CFLAGS','-O2 -std=c++17',str(O/'probe.v'),str(d/'main.cpp')]
 save(d/'command.json',cmd)
 with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 with (d/'run.log').open('x') as f:subprocess.run([str(d/'obj/Vrvc_bit26_probe')],stdout=f,stderr=subprocess.STDOUT,check=True)
 result=json.loads((d/'run.log').read_text());assert result['passed'];save(d/'result.json',result);print(result)
if __name__=='__main__':globals()[sys.argv[1]]()
