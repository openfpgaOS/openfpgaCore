#!/usr/bin/env python3
"""Read-only source gate for a native-first registered RVC experiment."""
import hashlib,json,pathlib,re
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';B=P/'cpu-cache64-rvcpipe-v1';O=P/'cpu-cache64-rvcpipe-source-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
put=lambda p,x:p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
def main():
 O.mkdir(exist_ok=False);d=json.loads((B/'prepared.json').read_text());c=json.loads((B/'helper-audit/compatibility.json').read_text());g=json.loads((B/'generation.json').read_text())
 assert g['returncode']==0 and g['actual_scala'] and sha(B/'raw-emitted.v')==g['raw_sha256']
 assert set(d['base_flags'])-set(d['flags'])=={'--dispatcher-at=2'}
 assert set(d['flags'])-set(d['base_flags'])=={'--dispatcher-at=3','--decoder-at=2','--rvc-decompress-at=1'}
 changed=[n for n,h in d['original_scala'].items()if d['candidate_scala'][n]!=h]
 assert sorted(changed)==['src/main/scala/vexiiriscv/Param.scala','src/main/scala/vexiiriscv/decode/AlignerPlugin.scala']
 for n,h in d['candidate_scala'].items():assert sha(B/'source'/n)==h,n
 for n,h in d['helper_sha256'].items():assert sha(B/'helpers'/n)==h,n
 for n,h in d['production_guard'].items():assert sha(R/n)==h,n
 h=c['qualified_literal_hunks'];assert [x['old_token']for x in h if x['selected_for_preservation']]==['OLD','OLD2','INTWBOLD','RS2OLD']
 assert all(x['literal_matches']==0 for x in h if not x['selected_for_preservation'])
 state=(B/'helper-audit/05-normalized.v').read_text()
 for x in h:
  if not x['selected_for_preservation']:continue
  source=(B/'helpers'/x['helper']).read_text()
  blocks=re.findall(r"my \$old=<<'([^']+)';\n(.*?)\n\1\n\s*my \$new=<<'([^']+)';\n(.*?)\n\3\n",source,re.S)
  ot,old,nt,new=next(b for b in blocks if b[0]==x['old_token'])
  if 'rs3' not in x['helper']:old+='\n';new+='\n'
  assert hashlib.sha256(state.encode()).hexdigest()==x['input_sha256'] and state.count(old)==1
  state=state.replace(old,new);assert hashlib.sha256(state.encode()).hexdigest()==x['output_sha256']
 candidate=B/'helper-audit/candidate-matched-helpers.v';assert state==candidate.read_text() and sha(candidate)==c['candidate_sha256']
 for fragment in ['decode_ctrls_1_up_Decode_INSTRUCTION_RAW_0 <= decode_ctrls_0_down_Decode_INSTRUCTION_RAW_0;', 'decode_ctrls_2_up_Decode_INSTRUCTION_RAW_0 <= decode_ctrls_1_down_Decode_INSTRUCTION_RAW_0;', 'decode_ctrls_2_up_Decode_INSTRUCTION_0 <= decode_ctrls_1_down_Decode_INSTRUCTION_0;', 'decode_ctrls_2_up_Decode_DECOMPRESSION_FAULT_0 <= decode_ctrls_1_down_Decode_DECOMPRESSION_FAULT_0;', 'decode_ctrls_0_up_Decode_INSTRUCTION_RAW_0[31 : 16] = 16\'h0;', 'assign lane0_IntFormatPlugin_logic_stages_2_wb_valid = (execute_ctrl4_up_LANE_SEL_lane0 && (|lane0_IntFormatPlugin_logic_stages_2_hits));', 'assign lane0_integer_WriteBackPlugin_logic_stages_2_hits = lane0_IntFormatPlugin_logic_stages_2_wb_valid;']:
  assert state.count(fragment)==1,fragment
 for rs in ['RS2','RS3']:
  assert f'execute_lane0_bypasser_float_{rs}_bypassEnables[9] = 1\'b1;' in state
  assert f'_zz_execute_lane0_bypasser_float_{rs}_sel[0]' in state
 assert 'rvc_instruction_bit19' not in state
 result=dict(passed=True,passed_for_native_screen=True,passed_for_resource_fit=False,deployment_ready=False,source_only=True,candidate_sha256=sha(candidate),actual_scala_generation=True,changed_scala=changed,added_flags=sorted(set(d['flags'])-set(d['base_flags'])),single_issue_unchanged=True,retained_literal_hunks=['FP_RS3','scanner consume/early ready','integer zero-formatter gating','FP_RS2'],old_stage0_rvc_and_dispatch2_hunks_omitted=True,findings=[],rationale=['Compressed raw upper16 zeroing stays at extraction; stage1 raw feeds decompression and stage2 captures raw/instruction/fault/PC under the same stage movement.', 'DecoderPlugin reads INSTRUCTION_RAW as trap tval at decodeAt2; DecodePipelinePlugin creates StageLinks and age-based cancellation for all stages through dispatcher3.', 'No instruction extraction, slice count, PC/prediction metadata or aligner-buffer capture/reset policy is rewritten by the Scala relocation.', 'Retained fetch-ready helper proves the existing 0/1/2 ready chain; stage2 readiness is now itself backed by stage3 occupancy, so no dispatch2 semantic assumption is introduced.', 'FP forwarding is still first enabled execute-stage value with the same10 selectors; integer formatter returns0 for raw0 and keeps architectural write-valid/control unchanged.'],limits=['This adds pipeline state/cycles and changes latency; no cycle equivalence to the old pipeline is claimed.','Native branch/trap/compressed instruction and renderer behavior remains pending.','No map, fitter, timing, or production qualification is provided.'])
 put(O/'review.json',result)
 files=[B/'prepared.json',B/'generation.json',B/'generator.patch',B/'helper-audit/compatibility.json',candidate,pathlib.Path(__file__),O/'review.json']+[B/'source'/n for n in changed]+[B/'source/src/main/scala/vexiiriscv/decode'/n for n in ['DecodePipelinePlugin.scala','DecoderPlugin.scala']]+[B/'helpers'/n for n in ['retime_fetch_reads.pl','retime_aligner_pc.pl','factor_fetch_ready.pl','retime_dcache_writes.pl','factor_os30_fp_rs3.pl','factor_os30_frontend.pl']]
 put(O/'manifest.json',dict(passed=True,passed_for_native_screen=True,passed_for_resource_fit=False,deployment_ready=False,evidence_sha256={str(p.relative_to(R)):sha(p)for p in files}))
 print(sha(O/'manifest.json'))
if __name__=='__main__':main()
