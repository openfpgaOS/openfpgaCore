#!/usr/bin/env python3
"""Independent source/provenance review only; does not compile or simulate."""
from pathlib import Path
import hashlib,itertools,json,re,shutil
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';S=P/'cpu-gshare-control-v1';O=P/'cpu-gshare-control-source-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def main():
 assert not O.exists();m=json.loads((S/'manifest.json').read_text());assert m['passed'];n=0
 for p,h in m['evidence_sha256'].items():assert sha(R/p)==h,p;n+=1
 b=(S/'baseline.v').read_text();c=(S/'VexiiRiscv.v').read_text();hs=json.loads((S/'hunks.json').read_text());assert len(hs)==2
 assert sha(S/'baseline.v')=='878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e'
 for order in itertools.permutations(hs):
  v=b
  for h in order:assert v.count(h['old'])==1;v=v.replace(h['old'],h['new'],1)
  assert v==c
  for h in reversed(order):assert v.count(h['new'])==1;v=v.replace(h['new'],h['old'],1)
  assert v==b
 declarations=lambda x:re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$',x,re.M)
 assert declarations(b)==declarations(c)
 assert [l for l in b.splitlines() if '<=' in l]==[l for l in c.splitlines() if '<=' in l]
 A='fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH';H='HistoryPlugin_logic_onFetch_'
 bindings=[f'  assign _zz_{A}_4 = {{1\'d0, _zz_{A}_5}};',f'  assign _zz_{A}_5 = fetch_logic_ctrls_0_down_Prediction_BRANCH_HISTORY;',f'  assign fetch_logic_ctrls_0_down_Prediction_BRANCH_HISTORY = {H}valueNext;']
 for line in bindings:assert b.count(line)==1 and c.count(line)==1
 sb=json.loads((S/'source-binding.json').read_text())
 for k in ['arbiter_module','instance','learn','history']:assert b.count(sb[k])==1 and c.count(sb[k])==1
 for a,d in sb['valid_aliases']:
  line=f'  assign {a} = {d};';assert b.count(line)==1 and c.count(line)==1
 assert 'assign io_output_valid = io_inputs_0_valid;' in sb['arbiter_module'] and not re.search(r'\balways\b',sb['arbiter_module'])
 assert re.search(r'\.io_inputs_0_valid\s*\(LearnPlugin_logic_buffered_0_valid\s*\)',sb['instance'])
 assert re.search(r'\.io_output_valid\s*\(streamArbiter_3_io_output_valid\s*\)',sb['instance'])
 expected_learn='  assign early0_BranchPlugin_logic_jumpLogic_learn_valid = (((execute_ctrl3_up_LANE_SEL_lane0 && execute_ctrl3_down_isReady) && (! execute_lane0_ctrls_3_upIsCancel)) && (|execute_ctrl3_down_early0_BranchPlugin_SEL_lane0));\n'
 assert sb['learn']==expected_learn
 assert re.search(r'wire\s+execute_ctrl3_down_early0_BranchPlugin_SEL_lane0;',b)
 expected_history='  always @(*) begin\n    '+H+'valueNext = '+H+'value;\n'
 for i in range(3):expected_history+=f'    if({H}ports_{i}_valid) begin\n      {H}valueNext = {H}ports_{i}_payload_history;\n    end\n'
 expected_history+='  end\n';assert sb['history']==expected_history
 assert hs[0]['old'].startswith('  assign '+A+' = ')
 lhs=hs[0]['old'].split(' = ',1)[1][1:].rsplit(' ^ ',1)[0]
 expected_new=('  // Precompute the two priority groups before the late branch redirect.\n'+f'  (* keep *) wire [12:0] os30_gshare_hash_low = {lhs} ^\n'+f'      {{1\'b0, ({H}ports_0_valid ? {H}ports_0_payload_history : {H}value)}};\n'+f'  (* keep *) wire [12:0] os30_gshare_hash_high = {lhs} ^\n'+f'      {{1\'b0, ({H}ports_2_valid ? {H}ports_2_payload_history : {H}ports_1_payload_history)}};\n'+f'  assign {A} = ({H}ports_2_valid || {H}ports_1_valid) ?\n      os30_gshare_hash_high : os30_gshare_hash_low;\n')
 assert hs[0]['new']==expected_new
 proof=json.loads((S/'result.json').read_text());assert proof['passed'] and proof['checks']==325632 and proof['hash_negative_failures']>0 and proof['cancel_negative_failures']>0
 O.mkdir();shutil.copy2(__file__,O/'recipe.py')
 report=dict(passed=True,blocking_findings=[],source_sha256=sha(S/'VexiiRiscv.v'),evidence_rehashed=n,exact_hunks=2,all_hunk_orders_and_reversals=True,sequential_and_decl_exact=True,
 read_equivalence='History priority remains port2 > port1 > port0 > saved for all8 arbitrary selector combinations. Identical PC permutation is XORed with zero-extended12-bit history; moving XOR before group selection is exact bitwise.',
 write_equivalence='Single-input combinational StreamArbiter_2 forwards learn-valid unconditionally. Its actual instance and four aliases are exact; original valid is lane && ready && !cancel && branchSEL. Moving !cancel last preserves all six qualifiers for arbitrary inputs.',
 proof_scope='Author literal RTL exhausts8 selectors x64 qualifiers x zero/61 data bases and complements, plus262144 random vectors. For each selector output is linear bitwise data; bases cover full13PC+48history bits. This review repeats no tests.',
 limitations=['Two-state source/component gate only; no new native or physical qualification.'])
 put(O/'review.json',report)
 files=[Path(__file__),S/'manifest.json',S/'prepared.json',S/'VexiiRiscv.v',S/'source-binding.json',S/'hunks.json',S/'result.json',O/'recipe.py',O/'review.json']
 put(O/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}));print(sha(O/'manifest.json'))
if __name__=='__main__':main()
