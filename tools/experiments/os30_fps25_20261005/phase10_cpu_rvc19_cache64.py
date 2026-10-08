#!/usr/bin/env python3
"""Private direct bit19 decoder plane for the measured I64/D64 timing path."""
import difflib,hashlib,importlib.util,json,re,shutil,sys
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
C=Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
B=P/'cpu-cache64-resource-v1/frozen'/C;O=P/'cpu-cache64-rvc19-v1'
I='AlignerPlugin_logic_extractors_0_ctx_instruction';F='AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst';E='AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal'
EQ='''  // Direct existing RVC rs1[4]/immediate bit. No added cycle or state.
  (* keep *) wire rvc19_cr_zero_rs2_rd4 =
    ({AlignerPlugin_logic_extractors_0_ctx_instruction[6:2],
      !AlignerPlugin_logic_extractors_0_ctx_instruction[11]} == 6'b0);
  wire rvc_instruction_bit19 =
    (((switch_Rvc_l55==5'h08)||(switch_Rvc_l55==5'h10)) &&
      AlignerPlugin_logic_extractors_0_ctx_instruction[11]) |
    ((switch_Rvc_l55==5'h14) && rvc19_cr_zero_rs2_rd4) |
    ((switch_Rvc_l55==5'h14) && AlignerPlugin_logic_extractors_0_ctx_instruction[12] &&
      AlignerPlugin_logic_extractors_0_ctx_instruction[11]) |
    (((switch_Rvc_l55==5'h09)||(switch_Rvc_l55==5'h0d)) &&
      AlignerPlugin_logic_extractors_0_ctx_instruction[12]) |
    ((switch_Rvc_l55==5'h0b) &&
      (AlignerPlugin_logic_extractors_0_ctx_instruction[11:7]!=5'd2) &&
      AlignerPlugin_logic_extractors_0_ctx_instruction[12]);
'''
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def mod():
 spec=importlib.util.spec_from_file_location('proof26',R/'tools/experiments/os30_fps25_20261005/phase10_cpu_rvc_bit26.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def prepare():
 assert sha(B)=='ab3205dff8e38211a223234e237a4ee083b55ecd0a02cb915c58850a34ce322e'
 O.mkdir(exist_ok=False);s=B.read_text();shutil.copyfile(B,O/'baseline.v')
 mark='  assign AlignerPlugin_logic_feeder_lanes_0_isRvc =';assert s.count(mark)==1
 t=s.replace(mark,EQ+'\n'+mark)
 mark='      decode_ctrls_0_up_Decode_INSTRUCTION_0[18] = rvc_instruction_bit18;';assert t.count(mark)==1
 t=t.replace(mark,mark+'\n      decode_ctrls_0_up_Decode_INSTRUCTION_0[19] = rvc_instruction_bit19;')
 (O/'VexiiRiscv.v').write_text(t);(O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),t.splitlines(True),fromfile='cache64',tofile='cache64-rvc19')))
 names=set();decl=[]
 for line in s.splitlines():
  if not re.match(r'^  (wire|reg)\s+',line):continue
  name=line.strip().rstrip(';').split()[-1]
  if F in name or name in (E,'switch_Rvc_l55') or name.startswith('when_Rvc_'):decl.append(line);names.add(name)
 assignments=[]
 for line in s.splitlines():
  m=re.match(r'^  assign (\w+) = ',line)
  if m and m[1] in names:assignments.append(line)
 blocks=[]
 for b in mod().blocks(s):
  targets=set(re.findall(r'\b(\w+)(?:\[[^\]]+\])? =(?!=) ',b))
  if targets&names:assert targets<=names;blocks.append(b)
 assert len(blocks)==10
 text='module rvc_bit26_probe(input wire[31:0] raw,output wire[31:0] old_instruction,new_instruction,output wire illegal);\n'
 text+='wire[31:0] '+I+'=raw;\n'+'\n'.join(decl+assignments+blocks)+'\n'+EQ
 text+="assign old_instruction=raw[1:0]==2'b11 ? raw : "+F+';\n'
 text+="assign new_instruction=raw[1:0]==2'b11 ? raw : {"+F+'[31:20],rvc_instruction_bit19,'+F+'[18:0]};\n'
 text+="assign illegal=(raw[1:0]!=2'b11)&&"+E+';\nendmodule\n';(O/'probe.v').write_text(text)
 save(O/'prepared.json',dict(parent_cpu_sha256=sha(B),candidate_sha256=sha(O/'VexiiRiscv.v'),added_state=0,added_cycles=0,scope='Only compressed decoder bit19 data plane changes; illegal/raw/control/other bits exact. Literal original nested decoder extracted for exhaustive low16 equivalence.'))
 shutil.copyfile(__file__,O/'recipe.py')
def prove():
 m=mod();m.O=O;m.prove()
def seal():
 assert json.loads((O/'proof/result.json').read_text())['passed']
 save(O/'manifest.json',dict(candidate_sha256=sha(O/'VexiiRiscv.v'),native_qualification_pending=True,fit_performed=False,files={str(p.relative_to(O)):sha(p) for p in O.rglob('*') if p.is_file() and 'obj' not in p.parts and p.name!='manifest.json'}))
 print(sha(O/'manifest.json'),sha(O/'VexiiRiscv.v'))
if __name__=='__main__':globals()[sys.argv[1]]()
