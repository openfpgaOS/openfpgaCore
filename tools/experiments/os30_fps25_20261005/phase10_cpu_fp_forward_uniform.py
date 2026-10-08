#!/usr/bin/env python3
"""Private uniform FP tags plus exact RS1 local priority mux; no CAD."""
import difflib, hashlib, itertools, json, re, shutil, subprocess, sys
from pathlib import Path
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'cpu-control-compose-v1/VexiiRiscv.v'
T=P/'cpu-fp-tag-uniform-v1'
O=P/'cpu-fp-forward-uniform-v1'
PARENT='878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e'
TAG='4a88f46b509dece4878f00cd6ef6af1152f6e26e98017b3f4c92f768e8d24518'
TAG_MANIFEST='f31c7f7b79715538836e255993f829a71a00dbe9040ae02e46e2dfb4dacc2758'
PREFIX='execute_lane0_bypasser_float_RS1'
OUTPUT='execute_ctrl1_down_float_RS1_lane0'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def bindings():
 assert sha(B)==PARENT and sha(T/'VexiiRiscv.v')==TAG and sha(T/'manifest.json')==TAG_MANIFEST
 m=json.loads((T/'manifest.json').read_text());assert m['passed']
 for path,h in m['evidence_sha256'].items():assert sha(R/path)==h,path
def prepare():
 bindings();assert not O.exists();source=B.read_text();tags=(T/'VexiiRiscv.v').read_text()
 old=re.findall(r'^  assign '+OUTPUT+r' = .*?;$',source,re.M);assert len(old)==1;old=old[0]
 assert tags.count(old)==1
 d=[f'execute_ctrl{i}_down_lane0_float_WriteBackPlugin_logic_DATA_lane0' for i in range(4,13)]+[PREFIX+'_port_data']
 # Reuse the literal selected RS2 hierarchy, changing only source operand names.
 rs2=re.search(r'  // Exact earliest-stage FP RS2 forwarding, factored into local groups\.\n.*?32\'h0\);',source,re.S)
 assert rs2 is not None
 new=rs2.group(0).replace('RS2','RS1').replace('rs2','rs1')
 assert new.count('wire [31:0]')==3 and new.count('assign ')==1
 candidate=tags.replace(old,new,1);muxonly=source.replace(old,new,1)
 hunks=json.loads((T/'hunks.json').read_text())+[dict(kind='RS1 local-priority data mux',old=old,new=new)]
 assert len(hunks)==4
 # All four literal changes commute and reverse byte exactly.
 orders=0
 for order in itertools.permutations(hunks):
  q=source
  for h in order:assert q.count(h['old'])==1;q=q.replace(h['old'],h['new'],1)
  assert q==candidate
  for h in reversed(order):assert q.count(h['new'])==1;q=q.replace(h['new'],h['old'],1)
  assert q==source;orders+=1
 declarations=lambda s:re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$',s,re.M)
 assert declarations(source)==declarations(candidate)
 assert [l for l in source.splitlines() if '<=' in l]==[l for l in candidate.splitlines() if '<=' in l]
 # Source-literal old selector network, including actual default and priority.
 declarations=[]
 for line in source.splitlines():
  if not re.match(r'^  (wire|reg)\s+',line):continue
  name=line.strip().rstrip(';').split()[-1]
  if ((PREFIX in name and ('bypassEnables_' in name or name.endswith('_sel'))) or name.startswith('_zz_'+OUTPUT)):declarations.append(line)
 aliases='\n'.join(re.findall(r'^  assign _zz_'+OUTPUT+r'(?:_\d+)? = .*?;$',source,re.M));assert aliases.count('assign ')==16
 a=source.index('  assign _zz_'+PREFIX+'_bypassEnables_bools_0 =');b=source.index(old,a)+len(old)
 block=source[a:b].replace('assign '+OUTPUT+' =','assign old_result =')
 newblock=new.replace('assign '+OUTPUT+' =','assign new_result =')
 # Negative control: incorrectly place the middle group ahead of the youngest.
 bad=new.replace('fp_rs1_group','bad_group').replace('assign '+OUTPUT+' =','assign bad_result =')
 first=f'(|{PREFIX}_bypassEnables[2:0]) ? bad_group0 :\n    (|{PREFIX}_bypassEnables[5:3]) ? bad_group1 :'
 wrong=f'(|{PREFIX}_bypassEnables[5:3]) ? bad_group1 :\n    (|{PREFIX}_bypassEnables[2:0]) ? bad_group0 :'
 assert bad.count(first)==1;bad=bad.replace(first,wrong,1)
 probe='module fp_rs1_mux_probe(input wire [9:0] enables,input wire [319:0] payload,output wire [31:0] old_result,new_result,bad_result);\n'
 probe+='\n'.join(declarations)+'\nwire [9:0] '+PREFIX+'_bypassEnables=enables;\n'
 for i,name in enumerate(d):probe+=f'wire [31:0] {name}=payload[{32*i}+:32];\n'
 probe+=aliases+'\n'+block+'\n'+newblock+'\n'+bad+'\nendmodule\n'
 O.mkdir();shutil.copy2(B,O/'baseline.v');shutil.copy2(T/'VexiiRiscv.v',O/'tags-only.v');shutil.copy2(__file__,O/'recipe.py')
 (O/'VexiiRiscv.v').write_text(candidate);(O/'rs1-only.v').write_text(muxonly)
 put(O/'hunks.json',hunks);put(O/'rs1-hunk.json',dict(old=old,new=new))
 for name,left,right in [('candidate',source,candidate),('rs1-only',source,muxonly),('rs1-on-tags',tags,candidate)]:
  (O/(name+'.patch')).write_text(''.join(difflib.unified_diff(left.splitlines(True),right.splitlines(True),fromfile='parent/VexiiRiscv.v',tofile=name+'/VexiiRiscv.v')))
 put(O/'prepared.json',dict(parent_sha256=PARENT,candidate_sha256=sha(O/'VexiiRiscv.v'),tag_only_sha256=TAG,rs1_only_sha256=sha(O/'rs1-only.v'),recipe_sha256=sha(Path(__file__)),hunks=4,all_forward_reverse_orders=orders,added_state_bits=0,added_cycles=0,all_sequential_lines_exact=True,all_register_port_parameter_declarations_exact=True,scope='26 previously unfactored FP tag enables plus RS1 3x3 data hierarchy copied literally from selected RS2. Retains ctrl9/RS3 tag split and all original matching qualifiers, payloads and priorities.',native_and_physical_pending=True,production_modified=False))
 q=O/'literal';q.mkdir();(q/'probe.v').write_text(probe);(q/'main.cpp').write_text(CPP)
 print('SOURCE',sha(O/'VexiiRiscv.v'))

CPP=r'''#include "Vfp_rs1_mux_probe.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <stdexcept>
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);try{
 Vfp_rs1_mux_probe t;uint64_t checks=0,negative=0;uint32_t random=0x194aec31;
 auto check=[&](unsigned enable){t.enables=enable;t.eval();unsigned selected=10;
  for(unsigned i=0;i<10;i++)if((enable>>i)&1){selected=i;break;}
  uint32_t want=selected==10?0:t.payload[selected];
  if(t.old_result!=want||t.new_result!=want){std::fprintf(stderr,"enable%03x old%08x new%08x want%08x\n",enable,t.old_result,t.new_result,want);throw std::runtime_error("priority mismatch");}
  negative+=t.old_result!=t.bad_result;checks++;};
 for(unsigned enable=0;enable<1024;enable++){
  for(unsigned k=0;k<10;k++)t.payload[k]=0;check(enable);
  for(unsigned source=0;source<10;source++)for(unsigned bit=0;bit<32;bit++){
   for(unsigned k=0;k<10;k++)t.payload[k]=0;t.payload[source]=1u<<bit;check(enable);
  }
  const uint32_t fpbits[10]={0,0x80000000,0x7f800000,0xff800000,0x7fc12345,0x7fa54321,1,0x807fffff,0x3f800000,0xffffffff};
  for(unsigned shift=0;shift<10;shift++){for(unsigned k=0;k<10;k++)t.payload[k]=fpbits[(k+shift)%10];check(enable);}
  for(unsigned sample=0;sample<16;sample++){for(unsigned k=0;k<10;k++){random^=random<<13;random^=random>>17;random^=random<<5;t.payload[k]=random;}check(enable);}
 }
 if(negative==0)throw std::runtime_error("negative control did not fail");
 std::printf("{\"passed\":true,\"selector_vectors\":1024,\"bit_basis_per_selector\":320,\"tagged_fp_patterns_per_selector\":10,\"random_vectors_per_selector\":16,\"checks\":%llu,\"negative_priority_failures\":%llu,\"two_state_only\":true}\n",(unsigned long long)checks,(unsigned long long)negative);
 }catch(const std::exception&e){std::fprintf(stderr,"FAIL%s\n",e.what());return 1;}return 0;}
'''

def prove():
 bindings();p=json.loads((O/'prepared.json').read_text());assert sha(O/'VexiiRiscv.v')==p['candidate_sha256'] and sha(Path(__file__))==p['recipe_sha256']
 d=O/'literal';commands=dict(lint=['verilator','--lint-only','--Wno-fatal','--top-module','VexiiRiscv',str(O/'VexiiRiscv.v')],build=['verilator','--cc','--exe','--build','-j','4','--Wno-fatal','--top-module','fp_rs1_mux_probe','--Mdir',str(d/'obj'),str(d/'probe.v'),str(d/'main.cpp')])
 put(O/'commands.json',commands)
 for name,cmd in commands.items():
  with (O/(name+'.log')).open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 run=subprocess.run([str(d/'obj/Vfp_rs1_mux_probe')],capture_output=True,text=True,check=True);(O/'proof.log').write_text(run.stdout+run.stderr);r=json.loads(run.stdout)
 assert r['passed'] and r['checks']==355328 and r['negative_priority_failures']>0
 result=dict(passed=True,rs1_mux=r,tag_comparisons=json.loads((T/'result.json').read_text()),composition_orders=24,new_state_bits=0,new_cycles=0,whole_CPU_lint_pass=True,native_and_physical_pending=True)
 put(O/'result.json',result)
 (O/'REPORT.txt').write_text('Exact uniform FP forwarding component\n\nThe 26 new tag comparisons exhaust all 14 binary inputs each. The retained ctrl9/RS3 comparison is byte-identical.\nRS1 uses the literal selected RS2 hierarchy with only operand names changed. The old actual selector network and all aliases are extracted from the parent; all 1,024 enables include all-disabled, default-only, and multiple writers. For each fixed enable vector both networks are bitwise data selectors with no payload-dependent controls. The zero and 320 independent payload basis vectors therefore prove equality for every 320-bit payload; exceptional IEEE encodings and random payloads are supplemental.\nAll 24 source-hunk orders and reversals reproduce the exact combined/parent files. All register, port and parameter declarations and every sequential assignment line remain exact. No extra state or cycle is introduced. This is a two-state combinational/source proof, not routed timing or native integration qualification.\n')
 files=[Path(__file__),P/'cpu-control-compose-v1/manifest.json',T/'manifest.json',T/'result.json',T/'inventory.json',*O.iterdir(),d/'probe.v',d/'main.cpp'];files=[x for x in files if x.is_file()]
 put(O/'manifest.json',dict(passed=True,native_and_physical_pending=True,evidence_sha256={str(x.relative_to(R)):sha(x) for x in files}));print(run.stdout.strip());print('MANIFEST',sha(O/'manifest.json'))

if __name__=='__main__':assert sys.argv[1] in ('prepare','prove');globals()[sys.argv[1]]()
