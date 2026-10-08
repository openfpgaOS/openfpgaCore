#!/usr/bin/env python3
"""Independent frontend endpoint source/evidence review; no build or simulation."""
from pathlib import Path
import hashlib,itertools,json,re,shutil
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
S=P/'cpu-frontend-endpoints-v2';O=P/'cpu-frontend-endpoints-source-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def main():
 assert not O.exists();assert sha(S/'manifest.json')=='d4aa7a6cfa0c2d5f963b5e983e11861949c45d2742a9e6dec41ac2e2c8e38b18'
 m=json.loads((S/'manifest.json').read_text());n=0
 for p,h in m['evidence_sha256'].items():assert sha(R/p)==h,p;n+=1
 b=(S/'baseline.v').read_text();c=(S/'VexiiRiscv.v').read_text();hs=json.loads((S/'hunks.json').read_text());assert len(hs)==3
 assert sha(S/'baseline.v')=='3bbe3886be4edceb73b4e338314ee22c6f0777b9197689ba184d5c14d85cba96'
 assert sha(S/'VexiiRiscv.v')=='a63f36d0613aceb2ccdb424135c2cc107f2b959f781827d23460044be6acf023'
 for order in itertools.permutations(hs):
  t=b
  for h in order:assert t.count(h['old'])==1;t=t.replace(h['old'],h['new'],1)
  assert t==c
  for h in reversed(order):assert t.count(h['new'])==1;t=t.replace(h['new'],h['old'],1)
  assert t==b
 decl=lambda x:re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$',x,re.M)
 assert decl(b)==decl(c)
 assert sorted(l.strip() for l in b.splitlines() if '<=' in l)==sorted(l.strip() for l in c.splitlines() if '<=' in l)
 aliases={'fetch_logic_ctrls_3_down_isCancel':"1'b0",'decode_ctrls_0_up_isCancel':"1'b0",
  'fetch_logic_ctrls_3_down_valid':'fetch_logic_ctrls_3_up_valid','fetch_logic_ctrls_3_down_isValid':'fetch_logic_ctrls_3_down_valid',
  'fetch_logic_ctrls_3_down_isReady':'fetch_logic_ctrls_3_down_ready','decode_ctrls_0_up_isReady':'decode_ctrls_0_up_ready',
  'decode_ctrls_0_up_ready':'decode_ctrls_0_down_isReady','decode_ctrls_0_down_isReady':'decode_ctrls_0_down_ready',
  'decode_ctrls_1_up_ready':'decode_ctrls_1_down_isReady','decode_ctrls_1_down_isReady':'decode_ctrls_1_down_ready',
  'decode_ctrls_2_up_ready':'decode_ctrls_2_down_isReady','decode_ctrls_2_down_isReady':'decode_ctrls_2_down_ready',
  'AlignerPlugin_logic_buffer_downFire':'(decode_ctrls_0_up_isReady || decode_ctrls_0_up_isCancel)'}
 for k,v in aliases.items():
  line=f'  assign {k} = {v};';assert b.count(line)==1 and c.count(line)==1
 # All reset assignments, late flush priority, and every other capture payload
 # remain literal. Only mask/last grouping changes their outer enable spelling.
 for reg,val in [('mask',"2'b00"),('last',"2'b00"),('trap',"1'b0")]:
  line=f'      AlignerPlugin_logic_buffer_{reg} <= {val};';assert line in b and line in c
 flush="      if(AlignerPlugin_logic_buffer_flushIt) begin\n        AlignerPlugin_logic_buffer_mask <= 2'b00;\n        AlignerPlugin_logic_buffer_last <= 2'b00;\n      end"
 assert b.count(flush)==c.count(flush)==1
 assert c.index(flush)>c.index(hs[2]['new'])
 assert hs[2]['new'].index('if(when_AlignerPlugin_l256)')<hs[2]['new'].index('end else begin')
 for name in ['decode_ctrls_1_down_ready','decode_ctrls_2_down_ready']:
  pat=r'  always @\(\*\) begin\n    '+name+r' = .*?\n  end\n'
  assert re.findall(pat,b,re.S)==re.findall(pat,c,re.S)
 result=json.loads((S/'result.json').read_text());assert result['passed'] and result['full_CPU_lint_passed']
 assert result['literal']['control']==dict(checks=32768,missing_RS1_negative=240,passed=True)
 assert result['literal']['next-state']==dict(checks=1048576,passed=True)
 state=(S/'next-state/proof.v').read_text();assert state.count("if(reset)begin next_mask=2'b00;next_last=2'b00;next_trap=1'b0;end")==2
 # Rebinding to v2 must not hide a candidate-source change.
 assert sha(S/'VexiiRiscv.v')==sha(P/'cpu-frontend-endpoints-v1/VexiiRiscv.v')
 O.mkdir();shutil.copy2(__file__,O/'recipe.py')
 put(O/'review.json',dict(passed=True,blocking_findings=[],source_sha256=sha(S/'VexiiRiscv.v'),evidence_rehashed=n,exact_hunks=3,all6_forward_reverse_orders=True,no_new_state_or_cycles=True,
  control_lemma='Let F=ready2_free, A=ready2_allow, Q=!H0&&!H1&&!H2, D=!valid1||!valid2||F||A*Q, E=empty saved mask. Literal StageLink aliases give old downFire=D. Old capture=V*allow*(E||D), because both cancellation inputs are constant0. The new capture distributes its free and A*Q terms exactly.',
  update_lemma='Old outer ownership downFire||capture = D || V*allow*E = D || V*!halt*E, since empty saved mask makes allow=!halt. New mask/last update is this expression; capture still overrides consumption. The later flush and top reset overrides remain literal.',
  payload_ownership='All other capture payload RHS/conditions are unchanged apart from the equivalent capture wire. Decode1 RAW/slice/prediction writes retain their original RHS and depend on the proven equivalent decode0 ready. No fire, issue, hazard or scheduler equation changed.',
  proof_scope='Author literal32768 controls include arbitrary free/allow/hazard/scanner/width/mask inputs. The complete1048576-state projection binds every mask,last,used,incoming,trap/reset/flush value; corrected v2 includes the actual trap reset. Native and physical qualification remain pending; no tests repeated by reviewer.',
  preserved_failure='V1 source was already identical and passing, but its next-state fixture held trap on reset. V2 corrects only that fixture and re-runs the full component checks; no source discrepancy.'))
 files=[Path(__file__),S/'manifest.json',S/'VexiiRiscv.v',S/'hunks.json',S/'result.json',S/'control/proof.v',S/'next-state/proof.v',O/'recipe.py',O/'review.json']
 put(O/'manifest.json',dict(passed=True,evidence_sha256={str(x.relative_to(R)):sha(x) for x in files}));print(sha(O/'manifest.json'))
if __name__=='__main__':main()
