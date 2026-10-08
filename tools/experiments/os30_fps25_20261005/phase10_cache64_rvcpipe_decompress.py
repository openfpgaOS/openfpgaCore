#!/usr/bin/env python3
"""Literal full-output RVC/RAW relocation contract; pipeline correctness tested separately."""
import hashlib,json,re,shutil,subprocess
from pathlib import Path
import phase10_cache64_rvcpipe as m
from phase10_cpu_rvc_bit26 import blocks
R,P,O,B=m.R,m.P,m.O,m.B
sha,put=m.sha,m.put
D=O/'decompression-proof';D.mkdir(exist_ok=False)
F='AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst';E='AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal';I='AlignerPlugin_logic_extractors_0_ctx_instruction';RAW='decode_ctrls_0_up_Decode_INSTRUCTION_RAW_0';STAGED='decode_ctrls_1_up_Decode_INSTRUCTION_RAW_0'
paths={'reference':B/'VexiiRiscv.v','candidate':O/'helper-audit/candidate-matched-helpers.v'}
for label,path in paths.items():
 s=path.read_text();stage=0 if label=='reference' else 1;OUT=f'decode_ctrls_{stage}_up_Decode_INSTRUCTION_0';FAULT=f'decode_ctrls_{stage}_up_Decode_DECOMPRESSION_FAULT_0';names={OUT,FAULT,RAW,'AlignerPlugin_logic_feeder_lanes_0_isRvc'}
 if stage:names.add('when_AlignerPlugin_l192')
 decl=[]
 for line in s.splitlines():
  if not re.match(r'^  (wire|reg)\s+',line):continue
  name=line.strip().rstrip(';').split()[-1]
  if F in name or name in (E,'switch_Rvc_l55') or name.startswith('when_Rvc_'):names.add(name)
  if name in names:decl.append(line)
 assigns=[]
 for line in s.splitlines():
  z=re.match(r'^  assign (\w+) = ',line)
  if z and z[1] in names:assigns.append(line)
 chosen=[]
 for block in blocks(s):
  targets=set(re.findall(r'\b(\w+)(?:\[[^\]]+\])? =(?!=) ',block))
  if targets&names:assert targets<=names,targets-names;chosen.append(block)
 assert len(chosen)==13,(label,len(chosen))
 text=f'module {label}(input wire[31:0] raw, output wire[31:0] instruction, raw_mtval, output wire illegal);\nwire[31:0] {I}=raw;\n'
 if stage:text+=f'wire[31:0] {STAGED}={RAW};\n'
 text+='\n'.join(decl+assigns+chosen)+'\n'
 if not stage:
  start=s.index('  // Direct bit-plane form of the existing RVC decoder, not a new instruction.');end=s.index('  assign AlignerPlugin_logic_feeder_lanes_0_isRvc =',start);text+=s[start:end]
 text+=f'assign instruction={OUT};\nassign illegal={FAULT};\nassign raw_mtval={RAW};\nendmodule\n';(D/(label+'.v')).write_text(text)
top='''module probe(input wire[31:0] raw,output wire[31:0] old_instruction,new_instruction,old_mtval,new_mtval,output wire old_illegal,new_illegal);
reference a(raw,old_instruction,old_mtval,old_illegal);
candidate b(raw,new_instruction,new_mtval,new_illegal);
endmodule
''';(D/'probe.v').write_text(top)
cpp=r'''#include "Vprobe.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
int main(){Vprobe t;uint64_t count=0;uint32_t rng=0x93d1429e;auto next=[&](){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;};auto check=[&](uint32_t raw){t.raw=raw;t.eval();count++;if(t.old_instruction!=t.new_instruction||t.old_illegal!=t.new_illegal||t.old_mtval!=t.new_mtval){fprintf(stderr,"mismatch raw%08x inst%08x/%08x ill%d/%d mtval%08x/%08x\n",raw,t.old_instruction,t.new_instruction,t.old_illegal,t.new_illegal,t.old_mtval,t.new_mtval);return false;}return true;};
for(unsigned low=0;low<65536;low++)for(unsigned i=0;i<19;i++){uint32_t high=i==0?0:i<=16?1u<<(i-1):i==17?65535:next()&65535;if(!check((high<<16)|low))return 1;}
for(unsigned i=0;i<1000000;i++)if(!check(next()))return 1;
printf("{\"passed\":true,\"low16_encodings\":65536,\"upper_patterns\":19,\"random32\":1000000,\"exact_checks\":%llu}\n",(unsigned long long)count);return 0;}
''';(D/'main.cpp').write_text(cpp)
cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','probe','--Mdir',str(D/'obj'),'-CFLAGS','-O2 -std=c++17']+[str(D/n) for n in ['reference.v','candidate.v','probe.v','main.cpp']]
put(D/'command.json',cmd)
with (D/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
with (D/'run.log').open('x') as f:subprocess.run([str(D/'obj/Vprobe')],stdout=f,stderr=subprocess.STDOUT,check=True)
r=json.loads((D/'run.log').read_text());assert r['passed'];r.update(source_sha256={label:sha(p) for label,p in paths.items()},pipeline_cycle_equivalence_claimed=False,scope='Literal original and relocated RVC expansion, illegal flags and masked raw mtval agree for selected input. Stage stalls/flush/pipeline latency need whole-CPU tests; illegal X values use two-state simulator.')
put(D/'result.json',r);shutil.copy2(__file__,D/'recipe.py');print(json.dumps(r,indent=2))
