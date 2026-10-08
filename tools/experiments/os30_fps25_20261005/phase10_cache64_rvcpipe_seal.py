#!/usr/bin/env python3
"""Seal actual single-issue RVC pipeline generation and literal helper evidence."""
import json,re,shutil
from pathlib import Path
import phase10_cache64_rvcpipe as m
R,P,O,B=m.R,m.P,m.O,m.B
sha,put=m.sha,m.put
d=m.guard();g=json.loads((O/'generation.json').read_text());h=json.loads((O/'helper-audit/compatibility.json').read_text());proof=json.loads((O/'decompression-proof/result.json').read_text());assert g['returncode']==0 and h['passed'] and proof['passed']
x=O/'helper-audit/candidate-matched-helpers.v';s=x.read_text();old=(B/'VexiiRiscv.v').read_text();assert sha(x)==h['candidate_sha256']=='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
ports=lambda text:re.search(r'module VexiiRiscv \((.*?)\n\);',text,re.S).group(1)
assert ports(s)==ports(old)
assert 'execute_lane1_' not in s
required=[
 '      decode_ctrls_1_up_Decode_INSTRUCTION_RAW_0 <= decode_ctrls_0_down_Decode_INSTRUCTION_RAW_0;',
 '      decode_ctrls_2_up_Decode_INSTRUCTION_0 <= decode_ctrls_1_down_Decode_INSTRUCTION_0;',
 '      decode_ctrls_2_up_Decode_DECOMPRESSION_FAULT_0 <= decode_ctrls_1_down_Decode_DECOMPRESSION_FAULT_0;',
 '  assign DecoderPlugin_logic_laneLogic_0_trapPort_payload_tval = decode_ctrls_2_down_Decode_INSTRUCTION_RAW_0;',
 '  assign DispatchPlugin_logic_candidates_0_ctx_valid = ((decode_ctrls_3_up_isValid && decode_ctrls_3_up_LANE_SEL_0) && (! DispatchPlugin_logic_feeds_0_sent));',
 '  assign when_AlignerPlugin_l192 = (decode_ctrls_1_up_Decode_INSTRUCTION_RAW_0[1 : 0] != 2\'b11);',
]
for t in required:assert s.count(t)==1,t
assert not re.search(r'\bdecode_ctrls_1_up_Decode_INSTRUCTION_0\s*<=',s)
assert not re.search(r'\bdecode_ctrls_1_up_Decode_DECOMPRESSION_FAULT_0\s*<=',s)
flags=re.search(r'^\[info\] running \(fork\) vexiiriscv.Generate (.*)$',(O/'generate.log').read_text(),re.M).group(1).split();assert flags==d['flags']
shutil.copy2(x,O/'VexiiRiscv.v')
put(O/'result.json',dict(passed_generation_and_literal_contract=True,candidate_sha256=sha(x),top_interface_exact=True,single_issue=True,raw_capture_stage=1,rvc_expansion_stage=1,expanded_capture_and_isa_decode_stage=2,dispatch_stage=3,added_frontend_cycles=1,peak_issue_width=1,whole_cpu_pipeline_validation_pending=True,native_performance_pending=True,original_four_helpers_passed=True,preserved_exact_overlays=['FP RS3','fetch-ready early allow','INTWB data','FP RS2'],omitted_inapplicable_overlays=['stage0 RVC bit planes15/16/18/26/28','dispatch2 ready factor','bit19 (not in plain cache64 parent)'],checked_structural_fragments=required,decompression_proof=proof,production_modified=False,map_fit_performed=False,deployment_ready=False))
files=[p for p in O.iterdir() if p.is_file()]+[O/'source'/n for n in d['candidate_scala']]+[O/'helpers'/n for n in d['helper_sha256']]
for dirname in ['helper-audit','decompression-proof']:files.extend(p for p in (O/dirname).iterdir() if p.is_file())
files.extend([Path(__file__),Path(m.__file__),R/'tools/experiments/os30_fps25_20261005/phase10_cache64_rvcpipe_helpers.py',R/'tools/experiments/os30_fps25_20261005/phase10_cache64_rvcpipe_decompress.py',B/'manifest.json'])
put(O/'manifest.json',dict(scope='Author source/generation/literal-output contract only; independent source and whole-CPU native checks required',evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files))}));print('MANIFEST',sha(O/'manifest.json'))
