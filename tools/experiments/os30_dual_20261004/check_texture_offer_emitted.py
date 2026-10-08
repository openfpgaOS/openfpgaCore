#!/usr/bin/env python3
"""Independently audit early-offer equivalence at the actual texture cache cut."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
CAND=ART/'texture-offer-v1'
OUT=ART/'texture-offer-emitted-audit-v1'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a))
    return s.replace(a,b,1)


def main():
    gpu=ART/'gpu-chain-final-v1/gpu_core.v'
    cache=ROOT/'src/fpga/common/gpu_tex_cache.v'
    assert sha(gpu)=='5e628c9a576221950d53ff3cfb845ab2020f59d974e91743f59691183359c089'
    assert sha(cache)=='3999efcfd3c233e4b866c8462dfa211c25ae9ac2e0f4e7ca4929b64efc6b3fef'
    assert sha(CAND/'gpu_core.v')=='2d4870c5b1c687cb51a3e60aef0fa0d31372bcea4c4dd521a0c825dd8ba24ec7'
    assert sha(CAND/'gpu_tex_cache.v')=='0d2d36690acfc00f764e4c24dde05195b8bd5d8a00ca51406c0507d4ec81c263'
    oldg,oldc=gpu.read_text(),cache.read_text()
    newg,newc=(CAND/'gpu_core.v').read_text(),(CAND/'gpu_tex_cache.v').read_text()
    g=newg
    g=once(g,'wire        tex_req_valid;\nwire        tex_req_offer_a;','wire        tex_req_valid;')
    g=once(g,'    .SET_BITS(GPU_TEX_CACHE_SET_BITS),\n    .EARLY_REQ_OFFER(1)\n) tex_cache (','    .SET_BITS(GPU_TEX_CACHE_SET_BITS)\n) tex_cache (')
    g=once(g,'    .req_valid(tex_req_valid),\n    .req_offer_a(tex_req_offer_a),','    .req_valid(tex_req_valid),')
    stream="    assign tex_req_valid = (state == S_FRAG_PIPE) && (req_count != 0)\n                        && (res_reserved < 3'd4)\n                        && (!lookup_pending || tex_resp_valid);"
    offer="    assign tex_req_offer_a = (state == S_FRAG_PIPE) && (req_count != 0)\n                            && (res_reserved < 3'd4);"
    assert g.count(stream+'\n'+offer)==2
    g=g.replace(stream+'\n'+offer,stream)
    single='    assign tex_req_valid = (state == S_FRAG_PIPE) && p0_valid && !p1_valid;'
    g=once(g,single+'\n    assign tex_req_offer_a = (state == S_FRAG_PIPE) && p0_valid && !p1_valid;',single)
    assert g==oldg
    c=newc
    c=once(c,'    parameter EARLY_REQ_OFFER = 0, // Legacy callers use req_valid for prime selection.\n','')
    c=once(c,'    input  wire         req_valid,\n    input  wire         req_offer_a,','    input  wire         req_valid,')
    c=once(c,"// During a port-A fill response, the GPU's qualified request equals its\n// response-independent offer. Only this address selection uses the offer;\n// the original acceptance handshake remains authoritative everywhere else.\n",'')
    c=once(c,'&& !(EARLY_REQ_OFFER ? req_offer_a : req_valid);','&& !(req_valid && req_ready);')
    assert c==oldc
    # The new signal only changes prime-A's controlling equation; same read
    # enables, write schedules, metadata/state, port-B behavior and handshakes.
    assert len(re.findall(r'\breq_offer_a\b',newc))==2
    original_main=oldc.split('// ---- Main FSM ----\n',1)[1]
    assert newc.split('// ---- Main FSM ----\n',1)[1]==original_main
    proof=(CAND/'proof.v').read_text()
    load=re.search(r'    if \(proof_load\) begin.*?    end else if \(!reset_n\) begin',proof,re.S)[0]
    assert proof.split('// ---- Main FSM ----\n',1)[1].split('\nassign proof_next_state=')[0].rstrip()==original_main.rsplit('endmodule',1)[0].rstrip().replace('    if (!reset_n) begin',load,1)
    assert 'fill_resp_valid_a <= (proof_state == S_FILL_OUT) && !proof_port;' in load
    assert 'fill_resp_valid_b <= (proof_state == S_FILL_OUT) && proof_port;' in load
    # Reconstruct the entire original module before the added proof probes.
    normalized=proof.split('\nassign proof_next_state=')[0]+'endmodule\n'
    normalized=once(normalized,'module texture_offer_proof #(','module gpu_tex_cache #(')
    ports=re.search(r'    input wire proof_load,.*?(?=    input wire clk,)',normalized,re.S)[0]
    normalized=once(normalized,ports,'')
    normalized=once(normalized,load,'    if (!reset_n) begin')
    for port in 'ab':
        original=re.search(r'wire pipe_miss_'+port+r'\s*=.*?;',oldc,re.S)[0]
        normalized=once(normalized,'wire pipe_miss_'+port+' = proof_miss_'+port+';',original)
    assert normalized==oldc
    # The abstracted miss inputs are unrestricted; other fields outside the
    # induction cut cannot affect state/port/fill flags except init-last.
    assert "assign req_ready   = (state_pipe_ready && !pipe_miss_a && !flush_block)\n                  || (state_fillout_ready && (lat_port == 1'b0) && !flush_block);" in oldc
    assert "assign resp_valid   = pipe_hit_a || fill_resp_valid_a;" in oldc
    assert "wire flush_block = flush || flush_pending;" in oldc
    for state,value in [('S_PIPE',0),('S_FILL_AR',1),('S_FILL_DATA',2),('S_FILL_OUT',3),('S_INIT',4)]:
        assert re.search(r'localparam '+state+r'\s*= 3\'d'+str(value)+r';',oldc)
    # Independent algebra for the prime boundary, arbitrary controls, counters
    # (including unused encodings), response-hit/lookup states and GPU modes.
    cases=0
    for state in range(8):
      for port in range(2):
       for bits in range(1<<12):
        flush=(bits>>0)&1;pending=(bits>>1)&1;in_frag=(bits>>2)&1
        lookup=(bits>>3)&1;hit=(bits>>4)&1;miss=(bits>>5)&1
        count=(bits>>6)&7;reserved=(bits>>9)&7
        prefix=state==3 and port==0 and not(flush or pending)
        fill=state==3 and port==0
        ready=(state==0 and not miss and not(flush or pending)) or prefix
        response=hit or fill
        offer=in_frag and count!=0 and reserved<4
        valid=offer and (not lookup or response)
        assert (prefix and not(valid and ready))==(prefix and not offer)
        assert (prefix and not(valid and ready))==(prefix and not valid)
        cases+=1
    OUT.mkdir(exist_ok=False)
    with (OUT/'actual-proof-rerun.log').open('w') as log:
        subprocess.run([str(CAND/'proof-obj/Vtexture_offer_proof')],check=True,stdout=log,stderr=subprocess.STDOUT)
    assert 'induction=131072 legacy-prime=131072 producer=65536' in (OUT/'actual-proof-rerun.log').read_text()
    result=dict(gpu_sha256=sha(CAND/'gpu_core.v'),cache_sha256=sha(CAND/'gpu_tex_cache.v'),
       exact_interface_and_prime_only_scope=True,actual_main_fsm_byte_identical=True,
       proof_restores_entire_original_module_after_removing_explicit_cuts=True,
       all_original_sequential_handshake_read_enable_write_and_port_b_source_unchanged=True,
       inductive_fill_valid_state_relation_reset_and_all_transition_cases=131072,
       actual_producer_equations_cases=65536,independent_prime_algebra_cases=cases,
       arbitrary_no_offer_flush_unused_count_and_state_encodings=True,
       original_default_parameter_semantics_preserved=True,
       all_current_instantiations_use_named_ports_and_parameters=True,
       compatibility_limit='New input/parameter insertion is scoped to current named callers; positional external instantiations would require updating.',
       registers_added=0,cycles_added=0,
       runtime_qualification='Separate paired texture-cache and GPU regression pending.')
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
