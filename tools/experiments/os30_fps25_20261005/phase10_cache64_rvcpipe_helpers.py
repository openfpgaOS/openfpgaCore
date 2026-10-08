#!/usr/bin/env python3
"""Inventory literal helper compatibility on new RAW/RVC/ISA CPU, fail closed."""
import hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
import phase10_cache64_rvcpipe as m
R,P,O,B=m.R,m.P,m.O,m.B
sha,put=m.sha,m.put
m.guard();assert json.loads((O/'generation.json').read_text())['returncode']==0
D=O/'helper-audit';D.mkdir(exist_ok=False);state=(O/'raw-emitted.v').read_text();steps=[]
# Replay the exact existing shell annotations; these do not change CPU behavior.
needle='  wire                execute_freeze_valid;';assert state.count(needle)==1;state=state.replace(needle,'  (* maxfan = 16 *) wire                execute_freeze_valid;')
assert '$urandom' not in state
needle='module VexiiRiscv (';assert state.count(needle)==1;state=state.replace(needle,'(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)\n'+needle)
x=D/'00-shell.v';x.write_text(state);steps.append(dict(name='existing shell annotations',sha256=sha(x),no_urandom=True))
for i,name in enumerate(['retime_fetch_reads.pl','retime_aligner_pc.pl','factor_fetch_ready.pl','retime_dcache_writes.pl'],1):
 y=D/(f'{i:02}-'+name+'.v');shutil.copy2(x,y);before=sha(y);cmd=['perl',str(O/'helpers'/name),str(y)];r=subprocess.run(cmd,capture_output=True,text=True);(D/(name+'.log')).write_text(r.stdout+r.stderr)
 steps.append(dict(name=name,helper_sha256=sha(O/'helpers'/name),returncode=r.returncode,before_sha256=before,after_sha256=sha(y),changed=before!=sha(y)))
 if r.returncode:
  put(D/'compatibility.json',dict(passed=False,steps=steps,reason='Original production helper failed; do not continue blindly.'));raise SystemExit(1)
 x=y
state=x.read_text();h=(O/'helpers/factor_os30_fp_rs3.pl').read_text();header,body=state.split('\n\n',1);assert re.fullmatch(r'// Generator : [^\n]+\n// Component : VexiiRiscv(?:\n// Git hash  : [^\n]+)?',header)
canonical=re.search(r"my \$qualified_header=<<'HEADER';\n(.*?)\nHEADER",h,re.S).group(1);state=canonical+'\n\n'+body
(D/'05-normalized.v').write_text(state)
# Merely inventory the exact literal transformations, in original chain order.
# The old RVC and dispatch2 hunks are deliberately not retargeted here.
hunks=[]
for helper in ['factor_os30_fp_rs3.pl','factor_os30_frontend.pl']:
 text=(O/'helpers'/helper).read_text();blocks=re.findall(r"my \$old=<<'([^']+)';\n(.*?)\n\1\n\s*my \$new=<<'([^']+)';\n(.*?)\n\3\n",text,re.S)
 assert len(blocks)==(1 if 'rs3' in helper else 8)
 for ot,old,nt,new in blocks:
  if 'rs3' not in helper:old+='\n';new+='\n'
  count=state.count(old);selected=helper=='factor_os30_fp_rs3.pl' or ot in ['OLD2','INTWBOLD','RS2OLD']
  row=dict(helper=helper,old_token=ot,old_sha256=hashlib.sha256(old.encode()).hexdigest(),new_sha256=hashlib.sha256(new.encode()).hexdigest(),literal_matches=count,selected_for_preservation=selected)
  if selected:
   assert count==1,(helper,ot,count);row['input_sha256']=hashlib.sha256(state.encode()).hexdigest();state=state.replace(old,new);row['output_sha256']=hashlib.sha256(state.encode()).hexdigest()
  else:row['reason']='RVC output belongs to registered RAW stage1 now, or dispatch topology moved to stage3. No textual retargeting/bit19 overlay.'
  hunks.append(row)
(D/'candidate-matched-helpers.v').write_text(state)
assert 'rvc_instruction_bit19' not in state
put(D/'compatibility.json',dict(passed=True,original_production_helpers_passed=True,steps=steps,qualified_literal_hunks=hunks,candidate_sha256=sha(D/'candidate-matched-helpers.v'),old_rvc_bitplanes_omitted=True,old_dispatch2_ready_factor_omitted=True,bit19_overlay=False,map_fit_performed=False,native_validation_pending=True,source_review_pending=True))
shutil.copy2(__file__,D/'recipe.py');print(json.dumps(json.loads((D/'compatibility.json').read_text()),indent=2))
