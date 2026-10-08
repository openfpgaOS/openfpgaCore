#!/usr/bin/env python3
"""Use the existing ready-implies-head invariant to simplify one valid FF."""
from pathlib import Path
import argparse,difflib,hashlib,json,re,shutil,subprocess
ROOT=Path(__file__).resolve().parents[4]
ART=ROOT/'docs/measurements/os30-dual-20261004'
PARENT=ART/'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
SHA='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
H='decode_ctrls_1_up_valid';R='decode_skid_gate_down_isReady';V='decode_skid_gate_down_isValid'
OLD=f'''      if({R}) begin
        {H} <= {V};
      end'''
NEW=f"      {H} <= 1'b1;\n"+OLD
RESET=f"      {H} <= 1'b0;"
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def run(args,log,**kw):
 with log.open('w') as f:subprocess.run(list(map(str,args)),check=True,stdout=f,stderr=subprocess.STDOUT,**kw)

def ready_cone(src):
 start=src.index('  wire decode_load_pending0;')
 end=src.index('\n  assign when_StageLink_l71_2',start)
 block=src[start:end]
 assert block.count('!decode_ctrls_1_up_valid')==1
 assert 'assign decode_load_without_lane0 = !decode_ctrls_1_up_valid ||' in block
 assert 'decode_skid_gate_down_ready = decode_load_without_lane0 || (dispatch_available0[1] && decode_load_needs_lane0);' in block
 assert src.count(f'  assign {R} = decode_skid_gate_down_ready;')==1
 assert src.count(f'  assign {V} = decode_skid_gate_down_valid;')==1
 inputs=[H,'decode_ctrls_2_up_valid','decode_ctrls_3_up_valid','decode_ctrls_3_up_LANE_SEL_0','decode_ctrls_3_up_LANE_SEL_1','DispatchPlugin_logic_feeds_0_sent','DispatchPlugin_logic_feeds_1_sent','execute_freeze_valid','DispatchPlugin_api_haltDispatch','DispatchPlugin_logic_candidates_1_flushHazards','DispatchPlugin_logic_candidates_1_fenceOlderHazards','DispatchPlugin_logic_scheduler_arbiters_1_candHazard','dispatch_first_allowed','dispatch_available0','dispatch_available1']
 for n in inputs:
  width='[1:0]' if n.startswith('dispatch_available') else ''
  assert re.search(r'^  (?:\(\*[^\n]*\*\)\s+)?(?:reg|wire)\s+'+(re.escape(width)+r'\s+' if width else '')+n+r'(?: /\*[^\n]*\*/\s*)?;$',src,re.M),n
 module='module ready_cone('+','.join('input wire '+('[1:0] ' if n.startswith('dispatch_available') else '')+n for n in inputs)+',output reg decode_skid_gate_down_ready);\n'+block+'endmodule\n'
 return module,inputs,block

def state(src):
 cap=NEW if NEW in src else OLD
 assert src.count(cap)==1 and src.count(RESET)==1
 assert len(re.findall(r'\b'+H+r'\s*<=',src))==(3 if cap==NEW else 2)
 for piece in [cap,RESET]:
  before=src[:src.index(piece)]
  assert before.rfind('always @(posedge clk or posedge reset) begin')>before.rfind('always @(posedge clk) begin')
 return f'  always @(posedge clk or posedge reset) begin\n    if(reset) begin\n{RESET}\n    end else begin\n{cap}\n    end\n  end\n'

def proof(old,new,out):
 cone,inputs,block=ready_cone(old)
 assert ready_cone(new)==(cone,inputs,block)
 (out/'actual-ready.v').write_text(cone)
 modules=cone
 for label,text in [('reference',old),('candidate',new)]:
  s=state(text);(out/(label+'-state.v')).write_text(s)
  modules+=f'module {label}(input clk,reset,{R},{V},output reg {H});\n'+s+'endmodule\n'
 tb='module tb;\n'
 for n in inputs:tb+='reg '+('[1:0] ' if n.startswith('dispatch_available') else '')+n+';\n'
 tb+='wire ready;ready_cone rc('+','.join('.'+n+'('+n+')' for n in inputs)+',.decode_skid_gate_down_ready(ready));\n'
 tb+='reg clk=0,reset=0,r=0,v=0;wire a,b;integer count=0,states=0;\n'
 tb+=f'reference ref_dut(clk,reset,r,v,a);candidate new_dut(clk,reset,r,v,b);\n'
 tb+='task step;begin #1;clk=1;#1;clk=0;#1;end endtask\ninitial begin\n'
 bits=sum(2 if n.startswith('dispatch_available') else 1 for n in inputs)
 tb+=f'for(integer i=0;i<(1<<{bits});i++)begin\n'
 bit=0
 for n in inputs:
  w=2 if n.startswith('dispatch_available') else 1
  tb+=f'{n}=i[{bit+w-1}:{bit}];\n';bit+=w
 tb+=f'#1;if(!ready && !{H})$fatal(1,"ready contract");count++;end\n'
 tb+='''for(integer q=0;q<2;q++)for(integer rr=0;rr<2;rr++)for(integer vv=0;vv<2;vv++)for(integer rst=0;rst<2;rst++)begin
 if(q || rr)begin
 reset=1;r=0;v=0;#1;if(a!==0 || b!==0)$fatal(1,"async reset");reset=0;
 r=1;v=q[0];step();if(a!==q[0] || b!==q[0])$fatal(1,"restore via actual capture");
 r=rr[0];v=vv[0];reset=rst[0];#1;
 if(rst && (a!==0 || b!==0))$fatal(1,"off-clock reset");
 step();if(a!==b || a!==(rst?1'b0:(rr?vv[0]:q[0])))$fatal(1,"transition");states++;
 end end
 reset=0;r=1;v=1;step();reset=1;#1;if(a!==0 || b!==0)$fatal(1,"final async reset");
 $display("PASS actual ready cone %0d patterns; actual state blocks %0d constrained transitions; async reset",count,states);$finish;end endmodule
'''
 (out/'proof.sv').write_text(modules+tb)
 run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal','--Mdir',out/'obj','-j','2',out/'proof.sv'],out/'proof-build.log')
 assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'proof-build.log').read_text())
 run([out/'obj/Vtb'],out/'proof.log')
 assert f'patterns; actual state blocks 12 constrained transitions' in (out/'proof.log').read_text()
 # Literal procedural-if four-state semantics: only 1 takes the branch.
 # Prove known H even with X/Z in R or V, using actual !H OR ... ready.
 vals=['0','1','x','z'];checks=0
 for h in ['0','1']:
  for rest in vals:
   r='1' if h=='0' or rest=='1' else ('0' if rest=='0' else 'x')
   for v in vals:
    for rst in vals:
     a='0' if rst=='1' else (v if r=='1' else h)
     b='0' if rst=='1' else (v if r=='1' else '1')
     assert a==b;checks+=1
 # Binary reachable-state closure is inductive from async reset with binary
 # control inputs. Undefined captured valid can make H unknown; do not conceal
 # the resulting unrestricted-X counterexample.
 four=dict(passed=True,known_head_four_state_checks=checks,
  exact_procedural_if_semantics=True,known_head_scope='H is binary; ready is actual !H OR other terms; R,V,reset may include X/Z.',
  binary_reset_induction='reset forces H=0; every capture/hold with binary valid inputs yields binary H; actual ready forces R=1 when H=0.',
  unrestricted_unknown_head_equivalence=False,
  counterexample={'H':'x','R':'x','V':'0','reset':'0','old_next':'x','new_next':'1'},
  limitation='An artificially unknown/uninitialized valid FF outside the binary reset-initialized execution model is not claimed equivalent.')
 (out/'four-state-scope.json').write_text(json.dumps(four,indent=2)+'\n')
 return bits,checks

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=ART/'decode-head-valid-direct-v3/immediate');args=ap.parse_args()
 assert sha(PARENT)==SHA;old=PARENT.read_text();assert old.count(OLD)==1
 new=old.replace(OLD,NEW,1);assert new.replace(NEW,OLD,1)==old
 ready_cone(old);state(old);state(new)
 out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
 target=out/'VexiiRiscv.v';target.write_text(new)
 bits,checks=proof(old,new,out)
 patch=out/'head-valid-direct.patch';patch.write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
 replay=out/'patch-replay';replay.mkdir();(replay/'VexiiRiscv.v').write_text(new)
 for label,flags,expected in [('reverse',['-R'],old),('replay',[],new)]:
  run(['patch','--batch','--fuzz=0',*flags,'-p1','-i',patch],out/(label+'.log'),cwd=replay)
  assert (replay/'VexiiRiscv.v').read_text()==expected
 run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',target],out/'lint.log')
 assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
 shutil.copy2(__file__,out/Path(__file__).name)
 m=dict(passed=True,parent_sha256=SHA,candidate_sha256=sha(target),changed_scope='Only add constant-one default before the existing head-valid ready capture; same reset and all other source bytes.',
  actual_ready_input_patterns=1<<bits,actual_state_transitions=12,known_head_four_state_checks=checks,
  ready_implies_invalid_head_accepts=True,binary_reachable_state_equivalence=True,unrestricted_unknown_head_equivalence=False,
  new_registers=0,new_cycles=0,all_lane_masks_and_flush_equations_unchanged=True,
  full_lint_reverse_replay_passed=True,whole_cpu_validation='pending',physical_status='Not fitted.',script_sha256=sha(Path(__file__)),patch_sha256=sha(patch))
 (out/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
 (out/'recipe.json').write_text(json.dumps(dict(script=str(Path(__file__).resolve()),script_sha256=sha(Path(__file__)),parent=str(PARENT),parent_sha256=SHA,candidate_sha256=sha(target)),indent=2)+'\n')
 print(json.dumps(m,indent=2))
if __name__=='__main__':main()
