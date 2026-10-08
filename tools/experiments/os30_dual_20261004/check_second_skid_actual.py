#!/usr/bin/env python3
"""Independently bind the decoded-buffer sequential harness to frozen RTL."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
SRC=ART/'decode-second-skid-draft-v1/immediate'
PROOF=ART/'decode-second-skid-actual-proof-v1'
OUT=ART/'decode-second-skid-actual-emitted-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    assert sha(SRC/'VexiiRiscv.v')=='f703a709e272a70084ee567ee59719da1e4ff351462fd64f4355ae9e61e47fc5'
    s=(SRC/'VexiiRiscv.v').read_text();h=(PROOF/'actual.v').read_text()
    changes=json.loads((SRC/'changed-source-blocks.json').read_text())
    fields=json.loads((SRC/'payload-map.json').read_text())
    block=next(b for a,b in changes if a=='  wire decode_load_pending0;').removesuffix('\n  wire decode_load_pending0;')
    assert s.count(block)==h.count(block)==1
    control=next(b for a,b in changes if 'decode_ctrls_3_up_valid <=' in a)
    cancel='''      if(when_DecodePipelinePlugin_l95_4) begin
        decode_ctrls_3_up_LANE_SEL_0 <= 1'b0;
      end
      if(when_DecodePipelinePlugin_l95_5) begin
        decode_ctrls_3_up_LANE_SEL_1 <= 1'b0;
      end'''
    assert (control+'\n'+cancel) in s and (control+'\n'+cancel) in h
    assert len(fields)==90 and sum(x['bits'] for x in fields)==380
    for f in fields:
        sl=str(f['lsb']) if f['bits']==1 else f"{f['msb']}:{f['lsb']}"
        assert f"wire [{f['bits']-1}:0] {f['input']}=incoming_data[{sl}];" in h
        assert f"  assign {f['head']} = decode_dispatch_head[{sl}];" in h
        assert len(re.findall(r'\b'+f['head']+r'\s*=',h))==1
    assert 'assign field_output={'+','.join(f['head'] for f in reversed(fields))+'};' in h
    for i,n in enumerate(['early0_EnvPlugin_logic_flushPort_valid','CsrAccessPlugin_logic_flushPort_valid','early0_BranchPlugin_logic_flushPort_valid','LsuPlugin_logic_flushPort_valid']):
        assert f'wire {n}=flushes[{i}];' in h
    for lane in range(2):
        assert f'wire when_DecodePipelinePlugin_l95_{lane+4}=(!decode_ctrls_3_up_isReady)&&decode_dispatch_flush;' in h
        # Actual per-lane cancel is the same OR of all four older sources.
        alias=f'decode_ctrls_3_lane{lane}_upIsCancel'
        assert f'  assign when_DecodePipelinePlugin_l95_{lane+4} = ((! decode_ctrls_3_up_isReady) && {alias});' in s
    cpp=(PROOF/'proof.cpp').read_text()
    assert 'nh[i]=r?(s?td[i]:d.incoming_data[i]):hd[i]' in cpp
    assert 'sn=!r&&!f&&(s||d.incoming_valid)' in cpp
    assert 'if(!d.dispatch_ready&&f)nm=0;' in cpp
    assert 'if(data_known)req(d.head[i]==hd[i]' in cpp
    assert 'if(s)req(d.tail[i]==td[i]' in cpp
    assert 'observe(!d.reset)' in cpp and 'd.reset=1;d.eval();h=0;s=0;hm=0;' in cpp
    OUT.mkdir(exist_ok=False)
    with (OUT/'proof-rerun.log').open('w') as log:
        subprocess.run([str(PROOF/'obj/Vdecoded_buffer')],stdout=log,stderr=subprocess.STDOUT,check=True)
    result=(OUT/'proof-rerun.log').read_text()
    assert 'cycles=1340480' in result and 'asynchronous_resets=146892' in result
    report=dict(passed=True,candidate_sha256=sha(SRC/'VexiiRiscv.v'),
        source_scope_review=str(ART/'decode-second-skid-emitted-audit-v1/manifest.json'),
        exact_payload_bank_control_and_head_cancel_blocks_bound=True,
        all_90_field_inputs_and_380_output_positions_checked=True,
        four_flush_inputs_and_reset_domain_exact=True,
        independent_reference_is_explicit_s2m_plus_stage_link=True,
        checks_all_head_payload_after_each_nonreset_edge_and_tail_when_full=True,
        actual_source_runtime_cycles=1340480,off_clock_reset_events=146892,
        proof_rerun_pass=True,
        qualification_limit='This refines the newly specified two-slot boundary, not the original one-slot timing. Whole-CPU architectural and cycle/performance qualification remains necessary.')
    (OUT/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
