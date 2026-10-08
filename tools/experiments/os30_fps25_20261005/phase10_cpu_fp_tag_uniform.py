#!/usr/bin/env python3
"""Split every remaining FP forwarding six-bit tag; preserve data/priority/state."""
import difflib,hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'cpu-control-compose-v1/VexiiRiscv.v';O=P/'cpu-fp-tag-uniform-v1'
PARENT='878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def prepare():
 assert sha(B)==PARENT and not O.exists();source=B.read_text();candidate=source;hunks=[];inventory=[];modules=[]
 for rs in range(1,4):
  E=f'execute_lane0_bypasser_float_RS{rs}_bypassEnables';anchor='  always @(*) begin\n'+f'    {E}[0]'
  assert candidate.count(anchor)==1
  start=candidate.index(anchor);end=candidate.index('\n  end',start)+len('\n  end');oldblock=candidate[start:end];newblock=oldblock;wires=''
  for stage in range(4,13):
   index=stage-4
   rd=f'execute_ctrl{stage}_down_RD_PHYS_lane0';src=f'execute_ctrl1_down_RS{rs}_PHYS_lane0'
   rf=f'execute_ctrl{stage}_down_RD_RFID_lane0';sf=f'execute_ctrl1_down_RS{rs}_RFID_lane0'
   lane=f'execute_ctrl{stage}_up_LANE_SEL_lane0';en=f'execute_ctrl{stage}_up_RD_ENABLE_lane0'
   lo=f'os30_fp_rs{rs}_ctrl{stage}_tag_lo_eq';hi=f'os30_fp_rs{rs}_ctrl{stage}_tag_hi_eq'
   old=f'    {E}[{index}] = ((({lane} && {en}) && ({rd} == {src})) && ({rf} == {sf}));'
   new=f'    {E}[{index}] = (({lane} && {en}) && {lo} && {hi});'
   wire=(f'  (* keep *) wire {lo} = ({rd}[2:0] == {src}[2:0]);\n'+
         f'  (* keep *) wire {hi} = ({{{rf}, {rd}[4:3]}} == {{{sf}, {src}[4:3]}});\n')
   if rs==3 and stage==9:
    assert source.count(wire)==1 and source.count(new)==1
    inventory.append(dict(rs=rs,stage=stage,index=index,scope='Retained exact previously factored comparator'));continue
   assert newblock.count(old)==1 and candidate.count(lo)==0 and candidate.count(hi)==0
   newblock=newblock.replace(old,new,1);wires+=wire
   inventory.append(dict(rs=rs,stage=stage,index=index,scope='New two kept3-bit equality nodes; original lane/RD qualifiers retained',old=old,new=new,wires=wire))
   number=len(modules)
   bodies=[]
   for name,body in [('old',old),('new',wire+new),('bad',wire.replace(f'{{{rf}, {rd}[4:3]}} == {{{sf}, {src}[4:3]}}',f'{rd}[4:3] == {src}[4:3]')+new)]:
    for a,b in [(E+f'[{index}]','y'),(rd,'rd'),(src,'rs'),(rf,'rd_rf'),(sf,'rs_rf'),(lane,'lane'),(en,'en')]:body=body.replace(a,b)
    body=body.replace('    y = ','  assign y = ')
    bodies.append(f'module tag_{number}_{name}(input [4:0] rd,rs,input rd_rf,rs_rf,lane,en,output y);\n{body}\nendmodule\n')
   modules.append(''.join(bodies))
  newblock='  // Uniform exact six-bit FP tag match; no change to ownership or priority.\n'+wires+newblock
  assert candidate.count(oldblock)==1;candidate=candidate.replace(oldblock,newblock,1);hunks.append(dict(rs=rs,old=oldblock,new=newblock))
 assert len(modules)==26 and len(inventory)==27
 declarations=lambda s:re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$',s,re.M)
 assert declarations(source)==declarations(candidate)
 assert [l for l in source.splitlines() if '<=' in l]==[l for l in candidate.splitlines() if '<=' in l]
 reverse=candidate
 for h in reversed(hunks):assert reverse.count(h['new'])==1;reverse=reverse.replace(h['new'],h['old'],1)
 assert reverse==source
 O.mkdir();shutil.copy2(B,O/'baseline.v');shutil.copy2(__file__,O/'recipe.py');(O/'VexiiRiscv.v').write_text(candidate)
 put(O/'hunks.json',hunks);put(O/'inventory.json',inventory)
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(source.splitlines(True),candidate.splitlines(True),fromfile='878e/VexiiRiscv.v',tofile='uniform-tags/VexiiRiscv.v')))
 put(O/'prepared.json',dict(parent_sha256=PARENT,candidate_sha256=sha(O/'VexiiRiscv.v'),recipe_sha256=sha(Path(__file__)),new_comparators=26,retained_comparators=1,new_kept_wires=52,new_state_bits=0,new_cycles=0,payload_priority_unchanged=True,all_sequential_lines_exact=True,native_and_CAD_pending=True,production_modified=False,
  timing_limit='Current worstRS1 path has2 tag/qualifier LUTs followed by3 priority and3 payload LUTs. A tag-only factor does not promise reduced whole-path depth; RS1 local-priority mux remains a separate recommendation.'))
 d=O/'literal';d.mkdir();probe=''.join(modules)+'module proof(input [4:0] rd,rs,input rd_rf,rs_rf,lane,en,output [25:0] old_y,new_y,bad_y);\n'
 for i in range(26):
  for kind in ('old','new','bad'):probe+=f'tag_{i}_{kind} t{i}_{kind}(rd,rs,rd_rf,rs_rf,lane,en,{kind}_y[{i}]);\n'
 probe+='endmodule\n';(d/'proof.v').write_text(probe)
 (d/'main.cpp').write_text(r'''#include "Vproof.h"
#include "verilated.h"
#include <cstdio>
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);Vproof t;unsigned vectors=0,checks=0,negative=0;
 for(unsigned v=0;v<(1u<<14);v++){t.rd=v&31;t.rs=(v>>5)&31;t.rd_rf=(v>>10)&1;t.rs_rf=(v>>11)&1;t.lane=(v>>12)&1;t.en=(v>>13)&1;t.eval();
  unsigned ref=t.lane&&t.en&&(t.rd==t.rs)&&(t.rd_rf==t.rs_rf)?((1u<<26)-1):0;
  if(t.old_y!=t.new_y||t.new_y!=ref){std::fprintf(stderr,"mismatch %u\n",v);return 1;}
  negative+=__builtin_popcount(t.old_y^t.bad_y);checks+=26;vectors++;
 }
 if(negative!=1664)return 2;
 std::printf("{\"passed\":true,\"literal_comparators\":26,\"input_vectors_per_comparator\":%u,\"actual_RTL_comparisons\":%u,\"negative_RFID_omission_failures\":%u,\"two_state_only\":true}\n",vectors,checks,negative);return 0;}
''')
 print('SOURCE',sha(O/'VexiiRiscv.v'))
def prove():
 p=json.loads((O/'prepared.json').read_text());assert sha(O/'VexiiRiscv.v')==p['candidate_sha256'] and sha(Path(__file__))==p['recipe_sha256'];d=O/'literal'
 commands=dict(lint=['verilator','--lint-only','--Wno-fatal','--top-module','VexiiRiscv',str(O/'VexiiRiscv.v')],build=['verilator','--cc','--exe','--build','-j','4','--Wno-fatal','--top-module','proof','--Mdir',str(d/'obj'),str(d/'proof.v'),str(d/'main.cpp')])
 put(O/'commands.json',commands)
 for name,cmd in commands.items():
  with (O/(name+'.log')).open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run([str(d/'obj/Vproof')],capture_output=True,text=True,check=True);(O/'proof.log').write_text(r.stdout+r.stderr);result=json.loads(r.stdout)
 assert result['passed'] and result['actual_RTL_comparisons']==425984 and result['negative_RFID_omission_failures']==1664
 put(O/'result.json',result)
 files=[Path(__file__),P/'cpu-control-compose-v1/manifest.json',P/'cpu-fp-rs3-tag-v1/manifest.json',*list(O.iterdir()),d/'proof.v',d/'main.cpp'];files=[x for x in files if x.is_file()]
 put(O/'manifest.json',dict(passed=True,native_and_physical_pending=True,evidence_sha256={str(x.relative_to(R)):sha(x) for x in files}));print(r.stdout.strip());print('MANIFEST',sha(O/'manifest.json'))
if __name__=='__main__':assert sys.argv[1] in ('prepare','prove');globals()[sys.argv[1]]()
