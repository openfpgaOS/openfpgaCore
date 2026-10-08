#!/usr/bin/env python3
"""Private GShare-only BTB-event cofactor; no architectural PC/state changes."""
import difflib,hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'cpu-parallel-control-v1/VexiiRiscv.v';O=P/'cpu-gshare-btb-prehash-v1'
PARENT='3bbe3886be4edceb73b4e338314ee22c6f0777b9197689ba184d5c14d85cba96'
H='HistoryPlugin_logic_onFetch_';Y='fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH'
PC='PcPlugin_logic_harts_0_';BT='BtbPlugin_logic_';FB='fetch_logic_ctrls_2_down_BtbPlugin_logic_chunksLogic_0_'
TP='TrapPlugin_logic_harts_0_trap_';BP='early0_BranchPlugin_logic_'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def prepare():
 assert sha(B)==PARENT and not O.exists();s=B.read_text()
 start=s.index('  // Precompute the two priority groups before the late branch redirect.\n')
 end=s.index('  assign fetch_logic_ctrls_0_down_GSharePlugin_logic_BYPASS_valid',start)
 old=s[start:end]
 rev=lambda p:'{'+','.join(f'{p}[{i}]' for i in range(2,15))+'}'
 new=(
 '  // Cofactor only the GShare address on the BTB history event.\n'
 '  // Branch PC/history valids alias; trap PC/history valids stay independent.\n'
 '  // A hit can update history without taking the BTB target. No state is added.\n'
 f'  wire [31:0] os30_gshare_pc_miss = {TP}pcPort_valid ? {TP}pcPort_payload_pc :\n'
 f'      ({BP}pcPort_valid ? {BP}pcPort_payload_pc : {PC}self_flow_payload_pc);\n'
 f'  wire [31:0] os30_gshare_pc_hit = {TP}pcPort_valid ? {TP}pcPort_payload_pc :\n'
 f'      ({BP}pcPort_valid ? {BP}pcPort_payload_pc :\n'
 f'       ({FB}predict_TAKEN ? {BT}pcPort_payload_pc : {PC}self_flow_payload_pc));\n'
 f'  wire [11:0] os30_gshare_hist_miss = {H}ports_2_valid ? {H}ports_2_payload_history :\n'
 f'      ({H}ports_1_valid ? {H}ports_1_payload_history : {H}value);\n'
 f'  wire [11:0] os30_gshare_hist_hit = {H}ports_2_valid ? {H}ports_2_payload_history :\n'
 f'      ({H}ports_1_valid ? {H}ports_1_payload_history :\n'
 f'       ({FB}readRsp_ENTRY_isBranch ?\n'
 f'        {{fetch_logic_ctrls_2_down_Prediction_BRANCH_HISTORY[10:0],{FB}predict_TAKEN}} :\n'
 '        fetch_logic_ctrls_2_down_Prediction_BRANCH_HISTORY));\n'
 f'  (* keep *) wire [12:0] os30_gshare_hash_miss = {rev("os30_gshare_pc_miss")} ^ {{1\'b0,os30_gshare_hist_miss}};\n'
 f'  (* keep *) wire [12:0] os30_gshare_hash_hit = {rev("os30_gshare_pc_hit")} ^ {{1\'b0,os30_gshare_hist_hit}};\n'
 f'  assign {Y} = {H}ports_0_valid ? os30_gshare_hash_hit : os30_gshare_hash_miss;\n')
 assert s.count(old)==1;candidate=s.replace(old,new,1)
 declarations=lambda a:re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$',a,re.M)
 assert declarations(s)==declarations(candidate)
 assert [l for l in s.splitlines() if '<=' in l]==[l for l in candidate.splitlines() if '<=' in l]
 assert candidate.replace(new,old,1)==s
 def assign(name):
  matches=re.findall(r'^  assign '+re.escape(name)+r' = .*;\n',s,re.M);assert len(matches)==1,name
  return matches[0]
 def always(first):
  matches=re.findall(r'  always @\(\*\) begin\n    '+re.escape(first)+r'.*?\n  end\n',s,re.S);assert len(matches)==1,first
  return matches[0]
 # These are source lemmas, not environmental assumptions about valid inputs.
 branch=assign(BP+'pcPort_valid')+assign(BP+'historyPort_valid')
 assert branch==(f'  assign {BP}pcPort_valid = {BP}jumpLogic_doIt;\n'+f'  assign {BP}historyPort_valid = {BP}jumpLogic_doIt;\n')
 assert assign(BT+'historyPort_valid')==f'  assign {BT}historyPort_valid = ((fetch_logic_ctrls_2_up_isValid && (! {BT}applyIt_correctionSent)) && (|{FB}hitCalc_HIT));\n'
 assert assign(PC+'self_flow_valid')==f'  assign {PC}self_flow_valid = 1\'b1;\n'
 shared=branch
 for n in ['applyIt_chunksMask','applyIt_chunksTakenOh','applyIt_needIt','applyIt_doIt','pcPort_valid','pcPort_payload_pc',
           'applyIt_history_layers_0_history','applyIt_history_layersLogic_0_doIt','applyIt_history_layersLogic_0_shifted',
           'applyIt_history_layers_1_history','historyPort_valid','historyPort_payload_history']:shared+=assign(BT+n)
 for i in range(3):
  shared+=assign(H+f'ports_{i}_valid')+assign(H+f'ports_{i}_payload_history')
 shared+=assign(PC+'self_flow_valid')+assign(PC+'self_flow_payload_pc')
 for i in range(4):
  line=assign(PC+f'aggregator_sortedByPriority_{i}_laneValid');assert "= 1'b1;" in line;shared+=line
  shared+=assign(PC+f'aggregator_valids_{i}')
 for suffix in ['', '_1','_2','_3']:shared+=assign('_zz_'+PC+'aggregator_oh'+suffix)
 shared+=always('_zz_'+PC+'aggregator_oh_4[0]')+assign(PC+'aggregator_oh')
 for suffix in ['', '_1','_2','_3']:shared+=assign('_zz_'+PC+'aggregator_target'+suffix)
 shared+=assign(PC+'aggregator_target')+always(PC+'output_payload_pc =')
 shared+=assign('fetch_logic_ctrls_0_up_Fetch_WORD_PC')+assign('fetch_logic_ctrls_0_down_Fetch_WORD_PC')
 shared+=assign('_zz_'+Y)
 for i in range(1,4):shared+=assign('_zz_'+Y+f'_{i}')
 history=always(H+'valueNext =')
 # Controls intentionally include illegal/unowned combinations: TP and TH
 # independent, sent/valid arbitrary, taken independent of isBranch/HIT.
 ids={TP+'pcPort_valid':'c[0]',TP+'historyPort_valid':'c[1]',BP+'jumpLogic_doIt':'c[2]',
      'fetch_logic_ctrls_2_up_isValid':'c[3]',BT+'applyIt_correctionSent':'c[4]',FB+'hitCalc_HIT':'c[5]',
      FB+'predict_TAKEN':'c[6]',FB+'readRsp_ENTRY_isBranch':'c[7]',
      TP+'pcPort_payload_pc':'pt',BP+'pcPort_payload_pc':'pb',PC+'self_pc':'ps',BT+'applyIt_pcTarget':'bt',
      TP+'historyPort_payload_history':'ht',BP+'historyPort_payload_history':'hb',H+'value':'hs',
      'fetch_logic_ctrls_2_down_Prediction_BRANCH_HISTORY':'hf',Y:'y'}
 ports='input [7:0] c,input [31:0] pt,pb,ps,input [30:0] bt,input [11:0] ht,hb,hs,hf,output [12:0] y'
 def short(body):
  for a in sorted(ids,key=len,reverse=True):body=re.sub(r'\b'+re.escape(a)+r'\b',lambda m:ids[a],body)
  return body
 def module(kind,body):
  full=shared+body
  locals=set(re.findall(r'\b(?:wire|reg)\s*(?:\[[^]]+\]\s*)?(\w+)',full))
  decl=[]
  for name in sorted(set(re.findall(r'\b[A-Za-z_]\w*\b',full))):
   if name in ids or name in locals:continue
   found=re.findall(r'^  (?:wire|reg)\s+(?:signed\s+)?(?:\[[^]]+\]\s+)?'+re.escape(name)+r';\n',s,re.M)
   if found:assert len(found)==1;decl.append(found[0])
  return f'module cone_{kind}({ports});\n'+short(''.join(decl)+full)+'endmodule\n'
 # Mutants catch untaken-history suppression and false trap PC/history aliasing.
 bad_event=new.replace(H+'ports_0_valid ?',BT+'pcPort_valid ?')
 bad_trap=new.replace(H+'ports_2_valid ?',TP+'pcPort_valid ?')
 probe=''.join(module(k,b) for k,b in [('old',old),('new',new),('bad_event',bad_event),('bad_trap',bad_trap)])
 probe+='module proof('+ports.rsplit(',output',1)[0]+',output [12:0] old_y,new_y,bad_event_y,bad_trap_y);\n'
 for k in ['old','new','bad_event','bad_trap']:probe+=f'cone_{k} {k}_inst(c,pt,pb,ps,bt,ht,hb,hs,hf,{k}_y);\n'
 probe+='endmodule\n'
 O.mkdir();shutil.copy2(B,O/'baseline.v');shutil.copy2(__file__,O/'recipe.py');(O/'VexiiRiscv.v').write_text(candidate)
 put(O/'hunks.json',[dict(old=old,new=new)])
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),candidate.splitlines(True),fromfile='3bbe/VexiiRiscv.v',tofile='btb-prehash/VexiiRiscv.v')))
 put(O/'source-binding.json',dict(branch_alias=branch,literal_shared_cone=shared,original_history_priority=history,input_binding=ids,
  proof_scope='All 256 controls, all 175 raw payload bits and complements; arbitrary trap selectors and invalid groups. Actual original PC onehot/default and history/BTB equations extracted literally.',
  area_timing='Unmeasured. Two kept 13-bit hash planes replace prior two planes; local PC/history mux logic may add area.'))
 d=O/'literal';d.mkdir();(d/'proof.v').write_text(probe)
 (d/'main.cpp').write_text(r'''#include "Vproof.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);Vproof t;uint64_t checks=0,negEvent=0,negTrap=0;uint32_t rng=0x6519fe3d;
 auto random=[&](){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;};
 auto check=[&](){t.eval();checks++;negEvent+=(t.old_y!=t.bad_event_y);negTrap+=(t.old_y!=t.bad_trap_y);if(t.old_y!=t.new_y){std::fprintf(stderr,"mismatch c=%x check=%llu old=%x new=%x\n",t.c,(unsigned long long)checks,t.old_y,t.new_y);return false;}return true;};
 const unsigned widths[8]={32,32,32,31,12,12,12,12};
 for(unsigned c=0;c<256;c++)for(unsigned basis=0;basis<=175;basis++)for(unsigned inv=0;inv<2;inv++){
  uint32_t data[8];unsigned offset=0;
  for(unsigned j=0;j<8;j++){uint32_t m=widths[j]==32?0xffffffffu:((1u<<widths[j])-1);data[j]=basis>=offset&&basis<offset+widths[j]?(1u<<(basis-offset)):0;if(inv)data[j]^=m;offset+=widths[j];}
  t.c=c;t.pt=data[0];t.pb=data[1];t.ps=data[2];t.bt=data[3];t.ht=data[4];t.hb=data[5];t.hs=data[6];t.hf=data[7];if(!check())return 1;
 }
 for(unsigned i=0;i<262144;i++){t.c=random()&255;t.pt=random();t.pb=random();t.ps=random();t.bt=random()&0x7fffffff;t.ht=random()&4095;t.hb=random()&4095;t.hs=random()&4095;t.hf=random()&4095;if(!check())return 2;}
 if(!negEvent||!negTrap)return 3;
 std::printf("{\"passed\":true,\"literal_checks\":%llu,\"control_combinations\":256,\"raw_payload_bits\":175,\"payload_basis_and_complement_checks\":90112,\"random_checks\":262144,\"untaken_history_mutant_failures\":%llu,\"trap_alias_mutant_failures\":%llu,\"two_state_only\":true}\n",(unsigned long long)checks,(unsigned long long)negEvent,(unsigned long long)negTrap);
}
''')
 put(O/'prepared.json',dict(parent_sha256=PARENT,candidate_sha256=sha(O/'VexiiRiscv.v'),recipe_sha256=sha(Path(__file__)),new_state_bits=0,new_cycles=0,all_sequential_lines_exact=True,native_and_physical_pending=True,production_modified=False))
 print('SOURCE',sha(O/'VexiiRiscv.v'))
def prove():
 p=json.loads((O/'prepared.json').read_text());assert sha(O/'VexiiRiscv.v')==p['candidate_sha256'] and sha(Path(__file__))==p['recipe_sha256'];d=O/'literal'
 cmds=dict(lint=['verilator','--lint-only','--Wno-fatal','--top-module','VexiiRiscv',str(O/'VexiiRiscv.v')],build=['verilator','--cc','--exe','--build','-j','4','--Wno-fatal','--top-module','proof','--Mdir',str(d/'obj'),str(d/'proof.v'),str(d/'main.cpp')])
 put(O/'commands.json',cmds)
 for n,c in cmds.items():
  with (O/(n+'.log')).open('x') as f:subprocess.run(c,stdout=f,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run([str(d/'obj/Vproof')],capture_output=True,text=True,check=True);(O/'proof.log').write_text(r.stdout+r.stderr);data=json.loads(r.stdout)
 assert data['passed'] and data['literal_checks']==352256
 put(O/'result.json',data)
 files=[Path(__file__),P/'cpu-parallel-control-v1/manifest.json',*list(O.iterdir()),d/'proof.v',d/'main.cpp'];files=[x for x in files if x.is_file()]
 put(O/'manifest.json',dict(passed=True,new_state_bits=0,new_cycles=0,native_and_physical_pending=True,evidence_sha256={str(x.relative_to(R)):sha(x) for x in files}));print(r.stdout.strip());print('MANIFEST',sha(O/'manifest.json'))
if __name__=='__main__':assert sys.argv[1] in ('prepare','prove');globals()[sys.argv[1]]()
