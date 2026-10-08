#!/usr/bin/env python3
"""Freeze/prove the private texture-cache early offer address selection."""
import argparse, difflib, hashlib, json, re, shutil, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
GPU=BASE/'gpu-chain-final-v1/gpu_core.v'
CACHE=ROOT/'src/fpga/common/gpu_tex_cache.v'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a));return s.replace(a,b,1)
def run(cmd,log):
    with log.open('w') as f:subprocess.run(list(map(str,cmd)),check=True,stdout=f,stderr=subprocess.STDOUT)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True,type=Path);args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    g=GPU.read_text();c=CACHE.read_text();changes_g=[];changes_c=[]
    def change(kind,a,b):
        nonlocal g,c
        if kind=='g':g=once(g,a,b);changes_g.append((a,b))
        else:c=once(c,a,b);changes_c.append((a,b))
    change('g','wire        tex_req_valid;','wire        tex_req_valid;\nwire        tex_req_offer_a;')
    change('g','    .SET_BITS(GPU_TEX_CACHE_SET_BITS)\n) tex_cache (','    .SET_BITS(GPU_TEX_CACHE_SET_BITS),\n    .EARLY_REQ_OFFER(1)\n) tex_cache (')
    change('g','    .req_valid(tex_req_valid),','    .req_valid(tex_req_valid),\n    .req_offer_a(tex_req_offer_a),')
    stream='    assign tex_req_valid = (state == S_FRAG_PIPE) && (req_count != 0)\n                        && (res_reserved < 3\'d4)\n                        && (!lookup_pending || tex_resp_valid);'
    assert g.count(stream)==2
    offer='    assign tex_req_offer_a = (state == S_FRAG_PIPE) && (req_count != 0)\n                            && (res_reserved < 3\'d4);'
    g=g.replace(stream,stream+'\n'+offer);changes_g.append((stream,stream+'\n'+offer))
    single='    assign tex_req_valid = (state == S_FRAG_PIPE) && p0_valid && !p1_valid;'
    change('g',single,single+'\n    assign tex_req_offer_a = (state == S_FRAG_PIPE) && p0_valid && !p1_valid;')
    change('c','    parameter SET_BITS = 10   //','    parameter EARLY_REQ_OFFER = 0, // Legacy callers use req_valid for prime selection.\n    parameter SET_BITS = 10   //')
    change('c','    input  wire         req_valid,','    input  wire         req_valid,\n    input  wire         req_offer_a,')
    oldprime="wire prime_a = state_fillout_prime && (lat_port == 1'b0)\n            && !flush_block\n            && !(req_valid && req_ready);"
    newprime="""// During a port-A fill response, the GPU's qualified request equals its
// response-independent offer. Only this address selection uses the offer;
// the original acceptance handshake remains authoritative everywhere else.
wire prime_a = state_fillout_prime && (lat_port == 1'b0)
            && !flush_block
            && !(EARLY_REQ_OFFER ? req_offer_a : req_valid);"""
    change('c',oldprime,newprime)
    for text,changes,original in [(g,changes_g,GPU.read_text()),(c,changes_c,CACHE.read_text())]:
        for a,b in reversed(changes):text=text.replace(b,a)
        assert text==original
    (out/'gpu_core.v').write_text(g);(out/'gpu_tex_cache.v').write_text(c)
    for name,orig,new in [('gpu_core.v',GPU.read_text(),g),('gpu_tex_cache.v',CACHE.read_text(),c)]:
        (out/(name+'.patch')).write_text(''.join(difflib.unified_diff(orig.splitlines(True),new.splitlines(True),fromfile='a/'+name,tofile='b/'+name)))
    shutil.copy2(__file__,out/'texture_offer.py')
    # Actual complete original FSM is retained. A proof-only loading arm seeds
    # every state/port/flush-pending combination. Only the two miss predicates
    # are replaced by unrestricted cutpoints, covering every possible branch.
    proof=CACHE.read_text()
    proof=once(proof,'module gpu_tex_cache #(','module texture_offer_proof #(')
    ports='''    input wire proof_load,
    input wire [2:0] proof_state,
    input wire proof_port, proof_pending, proof_init_last,
    input wire proof_miss_a, proof_miss_b,
    input wire proof_in_frag, proof_lookup, proof_p0, proof_p1,
    input wire [2:0] proof_req_count, proof_res_reserved,
    output wire [2:0] proof_next_state,
    output wire proof_next_port, proof_fill_a, proof_fill_b,
    output wire proof_prime, proof_default, proof_early,
    output wire proof_offer_stream, proof_valid_stream, proof_offer_single, proof_valid_single,
'''
    proof=once(proof,'    input wire clk,',ports+'    input wire clk,')
    for port in 'ab':
        original=re.search(r'wire pipe_miss_'+port+r'\s*=.*?;',proof,re.S)[0]
        proof=once(proof,original,'wire pipe_miss_'+port+' = proof_miss_'+port+';')
    original_main=CACHE.read_text().split('// ---- Main FSM ----\n',1)[1]
    load='''    if (proof_load) begin
        state <= proof_state;
        lat_port <= proof_port;
        fill_resp_valid_a <= (proof_state == S_FILL_OUT) && !proof_port;
        fill_resp_valid_b <= (proof_state == S_FILL_OUT) && proof_port;
        flush_pending <= proof_pending;
        init_counter <= proof_init_last ? {SET_BITS{1'b1}} : {SET_BITS{1'b0}};
    end else if (!reset_n) begin'''
    proof=once(proof,'    if (!reset_n) begin',load)
    assert proof.split('// ---- Main FSM ----\n',1)[1].replace(load,'    if (!reset_n) begin',1)==original_main
    # Bind producer expressions to both actual GPU stream branches and the
    # single-slot branch; only identifiers are mapped to proof input names.
    stream_expr=stream.split('=',1)[1].strip().rstrip(';')
    offer_expr=offer.split('=',1)[1].strip().rstrip(';')
    single_expr=single.split('=',1)[1].strip().rstrip(';')
    def mapped(e):
        e=e.replace('(state == S_FRAG_PIPE)','proof_in_frag')
        for a,b in [('req_count','proof_req_count'),('res_reserved','proof_res_reserved'),('lookup_pending','proof_lookup'),('tex_resp_valid','resp_valid'),('p0_valid','proof_p0'),('p1_valid','proof_p1')]:e=re.sub(r'\b'+a+r'\b',b,e)
        return e
    probes=f'''
assign proof_next_state=state;
assign proof_next_port=lat_port;
assign proof_fill_a=fill_resp_valid_a;
assign proof_fill_b=fill_resp_valid_b;
assign proof_prime=prime_a;
assign proof_default=state_fillout_prime && !lat_port && !flush_block && !req_valid;
assign proof_early=state_fillout_prime && !lat_port && !flush_block && !proof_offer_stream;
assign proof_offer_stream={mapped(offer_expr)};
assign proof_valid_stream={mapped(stream_expr)};
assign proof_offer_single={mapped(single_expr)};
assign proof_valid_single={mapped(single_expr)};
'''
    proof=once(proof,'endmodule',probes+'endmodule')
    (out/'proof.v').write_text(proof)
    cpp=r'''#include "Vtexture_offer_proof.h"
#include <verilated.h>
#include <cstdio>
#include <cstdlib>
static void tick(Vtexture_offer_proof &m){m.clk=0;m.eval();m.clk=1;m.eval();m.clk=0;m.eval();}
static void require(bool yes,const char *s){if(!yes){std::fprintf(stderr,"FAIL %s\n",s);std::abort();}}
int main(int argc,char **argv){Verilated::commandArgs(argc,argv);Vtexture_offer_proof m;
 unsigned long long transitions=0,defaults=0,producer=0,prefix=0;
 m.reset_n=0;tick(m);require(m.proof_next_state==4&&!m.proof_fill_a&&!m.proof_fill_b,"reset base");
 for(unsigned old=0;old<8;++old)for(unsigned port=0;port<2;++port)for(unsigned x=0;x<(1u<<13);++x){
  m.proof_load=1;m.proof_state=old;m.proof_port=port;m.proof_pending=(x>>2)&1;m.proof_init_last=(x>>5)&1;tick(m);
  m.proof_load=0;m.reset_n=(x>>0)&1;m.flush=(x>>1)&1;m.proof_miss_a=(x>>3)&1;m.proof_miss_b=(x>>4)&1;
  m.axi_arready=(x>>6)&1;m.axi_rvalid=(x>>7)&1;m.axi_rlast=(x>>8)&1;m.req_valid=(x>>9)&1;m.req_valid_b=(x>>10)&1;m.resp_pop_b=(x>>11)&1;m.resp_flush_b=(x>>12)&1;m.eval();
  require(m.proof_prime==m.proof_default,"legacy prime all control states");++defaults;
  tick(m);require(bool(m.proof_fill_a)==(m.proof_next_state==3&&!m.proof_next_port),"A invariant step");
  require(bool(m.proof_fill_b)==(m.proof_next_state==3&&m.proof_next_port),"B invariant step");++transitions;
 }
 // Actual producer equations: arbitrary counters, flags, invalid/reserved
 // values, no offers, all cache states and both ports under the proven invariant.
 for(unsigned old=0;old<8;++old)for(unsigned port=0;port<2;++port)for(unsigned x=0;x<(1u<<12);++x){
  m.proof_load=1;m.proof_state=old;m.proof_port=port;m.proof_pending=(x>>11)&1;m.proof_init_last=0;tick(m);m.proof_load=0;
  m.reset_n=1;m.flush=(x>>10)&1;m.proof_in_frag=(x>>0)&1;m.proof_lookup=(x>>1)&1;m.proof_p0=(x>>2)&1;m.proof_p1=(x>>3)&1;
  m.proof_req_count=(x>>4)&7;m.proof_res_reserved=(x>>7)&7;m.eval();
  require(m.proof_offer_single==m.proof_valid_single,"single-slot producer");
  m.req_valid=m.proof_valid_stream;m.eval();
  require(m.proof_prime==m.proof_early,"enabled early prime matches original");
  if(old==3&&!port){require(m.resp_valid,"fill prefix guarantees response");require(m.req_valid==m.proof_offer_stream,"stream offer contract");++prefix;}
  ++producer;
 }
 std::printf("PASS actual FSM induction=%llu legacy-prime=%llu producer=%llu fill-prefix=%llu; two actual stream branches share the extracted equation; single-slot exact.\n",transitions,defaults,producer,prefix);}
'''
    (out/'proof.cpp').write_text(cpp)
    run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','texture_offer_proof','-GSET_BITS=4','--Mdir',out/'proof-obj',out/'proof.v',out/'proof.cpp'],out/'proof-build.log')
    run([out/'proof-obj/Vtexture_offer_proof'],out/'proof.log')
    assert 'PASS actual FSM' in (out/'proof.log').read_text()
    manifest=dict(reference_gpu=str(GPU),reference_gpu_sha256=sha(GPU),reference_cache=str(CACHE),reference_cache_sha256=sha(CACHE),
        gpu_sha256=sha(out/'gpu_core.v'),cache_sha256=sha(out/'gpu_tex_cache.v'),extra_registers=0,extra_cycles=0,
        exact_reverse_source_identity=True,actual_main_fsm_preserved_in_proof=True,induction_transitions=131072,
        original_default_prime_cases=131072,actual_producer_cases=65536,all_three_gpu_modes=True,
        legacy_default='EARLY_REQ_OFFER=0 uses original req_valid; no caller must supply the new input unless enabled.',
        interface_scope='Only prime-A address selection uses the early offer. Accepted/presented handshakes, RAM enables/writes, all state and prime-B remain unchanged.',
        runtime_qualification='Pending paired retained cache and GPU rendering suites.',
        files_sha256={p.name:sha(p) for p in out.iterdir() if p.is_file()})
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:manifest[k] for k in ['gpu_sha256','cache_sha256','induction_transitions','actual_producer_cases']},indent=2))
if __name__=='__main__':main()
