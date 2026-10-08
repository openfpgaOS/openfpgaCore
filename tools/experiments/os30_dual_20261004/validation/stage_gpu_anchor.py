#!/usr/bin/env python3
"""Privately stage only the sorted slot0 anchor in existing DDS registers."""
import argparse
from collections import deque
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'docs/measurements/os30-dual-20261004'
SOURCE = BASE / 'texture-offer-v1/gpu_core.v'
SHA = '2d4870c5b1c687cb51a3e60aef0fa0d31372bcea4c4dd521a0c825dd8ba24ec7'
NAMES = ['dd1x', 'dd2x', 'dd1y', 'dd2y', 'dd_x0', 'dd_y0']
CAPTURE = '''                    // Reuse the not-yet-live anchor registers as coordinate staging.
                    // Sorting is complete; raw vertices/order hold until DRV_DELTA.
                    dd_x0 <= dvx[dv_ord[0]];
                    dd_y0 <= dvy[dv_ord[0]];
'''
OLD_READ = '''                    sx0 = dvx[dv_ord[0]]; sx1 = dvx[dv_ord[1]]; sx2 = dvx[dv_ord[2]];
                    sy0 = dvy[dv_ord[0]]; sy1 = dvy[dv_ord[1]]; sy2 = dvy[dv_ord[2]];'''
NEW_READ = '''                    sx0 = dd_x0; sx1 = dvx[dv_ord[1]]; sx2 = dvx[dv_ord[2]];
                    sy0 = dd_y0; sy1 = dvy[dv_ord[1]]; sy2 = dvy[dv_ord[2]];'''

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p, value): p.write_text(json.dumps(value, indent=2) + '\n')
def run(args, log, **kwargs):
    with log.open('w') as f:
        subprocess.run(list(map(str, args)), stdout=f, stderr=subprocess.STDOUT, check=True, **kwargs)
def comments(s):
    s = re.sub(r'/\*.*?\*/', lambda m: '\n' * m.group().count('\n'), s, flags=re.S)
    return re.sub(r'//[^\n]*', '', s)
def derive(s): return s[s.index('            case (dstate)'):s.index('            end // INCLUDE_VERT_TRI')]
def blocks(s):
    body = derive(s)
    matches = list(re.finditer(r'^                (DRV_\w+|default):', body, re.M))
    return {m[1]: body[m.start():matches[i+1].start() if i+1 < len(matches) else body.rindex('            endcase')].rstrip() for i,m in enumerate(matches)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',required=True,type=Path); ap.add_argument('--resume',action='store_true'); args=ap.parse_args()
    out=args.output.resolve(); assert not out.exists() or (args.resume and not (out/'manifest.json').exists()); assert sha(SOURCE)==SHA
    source=SOURCE.read_text(); clean=comments(source); old=blocks(source)
    anchor='                DRV_PROD_L0: begin\n'
    assert source.count(anchor)==source.count(OLD_READ)==1
    candidate=source.replace(anchor,anchor+CAPTURE).replace(OLD_READ,NEW_READ)
    assert candidate.replace(anchor+CAPTURE,anchor).replace(NEW_READ,OLD_READ)==source
    new=blocks(candidate)
    assert new['DRV_PROD_L0'].replace(CAPTURE,'')==old['DRV_PROD_L0']
    assert new['DRV_DELTA'].replace(NEW_READ,OLD_READ)==old['DRV_DELTA']
    for k in old:
        if k not in ('DRV_PROD_L0','DRV_DELTA'): assert old[k]==new[k],k
    # Exact whole-identifier census: no unqualified or concurrent DDS observer.
    refs=[]
    for n,line in enumerate(clean.splitlines(),1):
        if any(re.search(r'\b'+name+r'\b',line) for name in NAMES):refs.append(dict(line=n,text=source.splitlines()[n-1]))
    assert [x['line'] for x in refs]==[3597,3600,7995,7996,7997,8126,8128,8131,8133,8293,8295]
    assert 'reg signed [16:0] dd1x, dd2x, dd1y, dd2y;' in source
    assert 'reg signed [15:0] dd_x0, dd_y0;' in source
    assert re.findall(r'dstate\s*<=\s*DRV_SORT_A\s*;\s*state\s*<=\s*S_TRI_DERIVE\s*;',clean).__len__()==3
    assert len(re.findall(r'state\s*<=\s*S_TRI_DERIVE\s*;',clean))==3
    derive_clean=comments(derive(source))
    assert not re.search(r'tri_v[012]_[xy]\s*<=',derive_clean)
    assert set(re.findall(r'\bdv_ord\[[^\]]+\]\s*<=',derive_clean))=={'dv_ord[0] <=','dv_ord[1] <=','dv_ord[2] <='}
    for k,b in old.items():
        if k not in ('DRV_SORT_A','DRV_SORT_B','DRV_SORT_C'): assert not re.search(r'\bdv_ord\[[^\]]+\]\s*<=',comments(b))
    # All actual dstate writers are captured by this graph or the three entries.
    outside=clean.replace(derive_clean,'')
    assert re.findall(r'dstate\s*<=\s*(\w+)',outside)==['DRV_SORT_A']*3
    graph={k:sorted(set(re.findall(r'dstate\s*<=\s*(DRV_\w+)',comments(b))) | {k}) for k,b in old.items()}
    assert graph['DRV_SORT_C']==['DRV_PROD_L0','DRV_SORT_C']
    # Overapproximate all conditional successors and holds. Reset/abort returns
    # to an unobserved outside state, whose only re-entry is SORT_A/phase0.
    # phase0=uninitialized scratch, phase1=coordinates, phase2=equal old deltas.
    seen=set();queue=deque([('DRV_SORT_A',0)]); checked=0
    while queue:
        state,phase=queue.popleft()
        if (state,phase) in seen:continue
        seen.add((state,phase));checked+=1
        if state=='DRV_DELTA':assert phase==1
        if state in ('DRV_PLANE_NUM','DRV_PLANE_ORG'):assert phase==2
        # A DELTA hold is impossible: it writes a successor unconditionally.
        # Also L0 cannot repeat after an abort without entry initialization.
        successors=graph.get(state,['default'])
        if state in ('DRV_DELTA','DRV_PROD_L0'):successors=[x for x in successors if x!=state]
        after=1 if state=='DRV_PROD_L0' else 2 if state=='DRV_DELTA' else phase
        for dest in successors:
            if dest in old:queue.append((dest,after))
            else:
                assert dest=='DRV_DONE';queue.append(('default',after))
    assert ('DRV_DELTA',1) in seen and ('DRV_PLANE_NUM',2) in seen and ('DRV_PLANE_ORG',2) in seen
    # Algebraic proof boundary: staging only copies a selected signed 16-bit
    # source. Projection of sign-extension restores all original bits. The
    # unchanged declarations/remaining DELTA statements then denote literally
    # identical 17-bit arithmetic and 32-bit DSP extensions for all 96 inputs.
    assert '''reg signed [16:0] e1x, e2x, e1y, e2y;
                    reg signed [15:0] sx0, sx1, sx2, sy0, sy1, sy2;''' in old['DRV_DELTA']
    for name in ['e1x','e2x','e1y','e2y']:
        assert len(re.findall(r'\b'+name+r' = \{s[xy][12]\[15\], s[xy][12]\} - \{s[xy]0\[15\], s[xy]0\};',old['DRV_DELTA']))==1
    normalized_delta=new['DRV_DELTA'].replace(NEW_READ,OLD_READ)
    assert normalized_delta==old['DRV_DELTA']
    out.mkdir(parents=True,exist_ok=args.resume)
    (out/'gpu_core.v').write_text(candidate)
    write(out/'source-contracts.json',dict(dds_census=refs,state_graph=graph,reachable_phase_states=sorted(seen),phase_states_checked=checked,
        entry_count=3,register_bits=100,new_register_bits=0,new_cycles=0,
        all_state_transitions_sorting_raw_attributes_and_other_source_unchanged=True,
        original_reads_only_after_delta=True,coordinates_and_order_held_from_capture_to_delta=True,
        reset_aborts_before_any_later_read=True,arithmetic_normalization_exact=True,
        proof_scope='Reachable-state reuse from normal reset/entry; invalid stale DDS payload is intentionally different before DELTA.'))
    # Copy actual capture and arithmetic blocks into a paired sequential harness.
    # This supports the universal structural proof with full 16-bit domain runs
    # against boundary operands and an independent 64-bit numerical oracle.
    params='\n'.join(re.findall(r'^localparam DRV_.*?;',source,re.M))
    assert 'DRV_PROD_L0' in params and 'DRV_DELTA' in params
    head='''module SIDE(input wire clk,reset_n,soft_reset,input wire [5:0] phase,
 input wire [1:0] o0,o1,o2,input wire [15:0] x0,x1,x2,y0,y1,y2,
 output wire [99:0] bank,output wire [127:0] operands);
 wire signed [15:0] dvx[0:2]; wire signed [15:0] dvy[0:2];
 assign dvx[0]=x0;assign dvx[1]=x1;assign dvx[2]=x2;
 assign dvy[0]=y0;assign dvy[1]=y1;assign dvy[2]=y2;
 wire [1:0] dv_ord[0:2];assign dv_ord[0]=o0;assign dv_ord[1]=o1;assign dv_ord[2]=o2;
 reg signed [16:0] dd1x,dd2x,dd1y,dd2y;
 reg signed [15:0] dd_x0,dd_y0;
 reg signed [31:0] dsp_a,dsp_b,dsp2_a,dsp2_b;
 wire signed [31:0] dv_szi[0:2],dv_tzi[0:2],vt_zi[0:2];
 assign dv_szi[0]=32'h12345678;assign vt_zi[0]=32'h23456789;assign dv_tzi[0]=32'h3456789a;
 wire signed [31:0] da1_sat=32'h92345678,da2_sat=32'h6abcdef0,dv_du=32'h96543210,dv_dv=32'h12345678;
 wire dv_doing_dv=phase==6'd63;
 reg [5:0] dstate;
 assign bank={dd1x,dd2x,dd1y,dd2y,dd_x0,dd_y0};
 assign operands={dsp_a,dsp_b,dsp2_a,dsp2_b};
PARAMS
 always @(posedge clk) begin
  if(!reset_n) begin dsp_a<=0;dsp_b<=0;dsp2_a<=0;dsp2_b<=0;end
  else if(!soft_reset) begin
   case(phase)
BLOCKS
   endcase
  end
 end
endmodule
'''
    modules=[]
    for label,bs in [('original',old),('candidate',new)]:
        chosen=[bs['DRV_PROD_L0'],bs['DRV_DELTA'],bs['DRV_PLANE_NUM'].replace('DRV_PLANE_NUM:',"6'd63, DRV_PLANE_NUM:"),bs['DRV_PLANE_ORG']]
        modules.append(head.replace('SIDE',label).replace('PARAMS',params).replace('BLOCKS','\n'.join(chosen)))
    top='''module coordinate_proof(input wire clk,reset_n,soft_reset,input wire [5:0] phase,
 input wire [1:0] o0,o1,o2,input wire [15:0] x0,x1,x2,y0,y1,y2,
 output wire [99:0] old_bank,new_bank,output wire [127:0] old_operands,new_operands);
 original a(.*,.bank(old_bank),.operands(old_operands));
 candidate b(.*,.bank(new_bank),.operands(new_operands));
endmodule
'''
    (out/'proof.sv').write_text('\n'.join(modules)+top)
    cpp=r'''#include "Vcoordinate_proof.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
static uint64_t cases=0,resets=0,holds=0;
static void step(Vcoordinate_proof&d){d.clk=0;d.eval();d.clk=1;d.eval();}
static int32_t s16(uint32_t x){return int16_t(x);}
static void equal(Vcoordinate_proof&d){for(int i=0;i<4;++i)if(d.old_bank[i]!=d.new_bank[i]||d.old_operands[i]!=d.new_operands[i]){std::printf("FAIL bank/operands at case %llu\n",(unsigned long long)cases);std::exit(1);}}
static void one(Vcoordinate_proof&d,unsigned n){
 d.reset_n=1;d.soft_reset=0;d.phase=23;step(d); // actual DRV_PROD_L0
 if((n&255)==0){d.phase=62;for(int k=0;k<17;++k){step(d);++holds;}}
 d.phase=3;step(d);equal(d); // DRV_DELTA
 int32_t xs[3]={s16(d.x0),s16(d.x1),s16(d.x2)},ys[3]={s16(d.y0),s16(d.y1),s16(d.y2)};
 int32_t a=xs[d.o1]-xs[d.o0],b=ys[d.o2]-ys[d.o0],c=xs[d.o2]-xs[d.o0],e=ys[d.o1]-ys[d.o0];
 uint32_t expect[4]={(uint32_t)e,(uint32_t)c,(uint32_t)b,(uint32_t)a};
 for(int i=0;i<4;++i)if(d.new_operands[i]!=expect[i]){std::printf("FAIL signed oracle %llu i%d got%08x expected%08x\n",(unsigned long long)cases,i,d.new_operands[i],expect[i]);std::exit(2);}
 d.phase=7;step(d);equal(d);d.phase=63;step(d);equal(d);d.phase=14;step(d);equal(d);++cases;
 if((n&511)==0){
  d.phase=23;step(d);d.reset_n=0;step(d);d.reset_n=1;d.phase=62;step(d);++resets;
  d.phase=23;step(d);d.soft_reset=1;step(d);d.soft_reset=0;d.phase=62;step(d);++resets;
 }
}
int main(){Vcoordinate_proof d;d.clk=0;d.reset_n=0;d.soft_reset=0;step(d);
 const unsigned perms[6][3]={{0,1,2},{0,2,1},{1,0,2},{1,2,0},{2,0,1},{2,1,0}};
 const unsigned boundary[8]={0,1,0x7ffe,0x7fff,0x8000,0x8001,0xfffe,0xffff};
 for(auto &p:perms){d.o0=p[0];d.o1=p[1];d.o2=p[2];
  for(unsigned b:boundary)for(unsigned v=0;v<65536;++v){
   d.x0=b;d.x1=v;d.x2=(uint16_t)~v;d.y0=v;d.y1=b;d.y2=(uint16_t)(v^0x8000);one(d,v);
  }
 }
 std::printf("PASS %llu complete16-bit-domain/boundary/permutation cases; %llu hold edges, %llu resets; actual extracted capture/delta/consumers plus signed64 oracle\n",(unsigned long long)cases,(unsigned long long)holds,(unsigned long long)resets);
}
'''
    assert re.search(r"localparam DRV_PROD_L0\s*= 5'd23",source)
    assert re.search(r"localparam DRV_PLANE_NUM\s*= 5'd7",source)
    assert re.search(r"localparam DRV_PLANE_ORG\s*= 5'd14",source)
    (out/'proof.cpp').write_text(cpp)
    run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','coordinate_proof','--Mdir',out/'obj','-CFLAGS','-std=c++17 -O2',out/'proof.sv',out/'proof.cpp'],out/'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|LATCH|MULTIDRIVEN)',(out/'proof-build.log').read_text())
    run([out/'obj/Vcoordinate_proof'],out/'proof.log')
    patch=out/'gpu-anchor-stage.patch'
    patch.write_text(''.join(difflib.unified_diff(source.splitlines(True),candidate.splitlines(True),fromfile='a/gpu_core.v',tofile='b/gpu_core.v')))
    replay=out/'patch-replay';replay.mkdir();(replay/'gpu_core.v').write_text(candidate)
    for label,extra,expected in [('reverse',['-R'],source),('replay',[],candidate)]:
        run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',patch],out/(label+'.log'),cwd=replay)
        assert (replay/'gpu_core.v').read_text()==expected
    # Full module lint using the same immutable helper modules/defines as the
    # retained selected-os30 renderer, with only its MLAB texture hint removed.
    frozen=BASE/'validation/gpu-count-nonzero/rendering'
    config=json.loads((frozen/'manifest.json').read_text());common=frozen/'rtl'
    if not common.exists():common=frozen/'common'
    assert common.exists(),common
    defs=[x for x in config['variant']['defs'].split() if x!='INCLUDE_TEX_QUEUE_MLAB']
    sources=[out/'gpu_core.v',common/'gpu_geometry_worker.v',common/'gpu_edge_walker.v',BASE/'texture-offer-v1/gpu_tex_cache.v']
    run(['verilator','--lint-only','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','gpu_core','-I'+str(common),*['+define+'+x for x in defs],*sources],out/'lint.log')
    shutil.copyfile(__file__,out/Path(__file__).name)
    manifest=dict(reference_sha256=SHA,candidate_sha256=sha(out/'gpu_core.v'),source_scope='Two anchor captures in DRV_PROD_L0 and two DELTA input assignments; all other source bytes unchanged.',
        new_register_bits=0,new_cycles=0,existing_bank_bits=100,staged_anchor_bits=32,arithmetic_widths_and_signed_extensions_unchanged=True,
        reachable_phase_closure=True,all_actual_dds_consumers_closed=True,exact_universal_operand_substitution=True,
        compiled_arithmetic_check_cases=3145728,compiled_check_is_supporting_not_exhaustive_96bit_arithmetic=True,
        original_raw_vertices_order_attributes_sorting_and_state_transitions_unchanged=True,
        reset_scope='Same reset/soft-reset branches; pre-DELTA DDS payload may differ but has no observed consumer.',
        exact_reverse_replay=True,full_module_lint=True,whole_gpu_qualification='Pending independent review and retained38x3 suite; no fitted timing claim.')
    write(out/'manifest.json',manifest)
    evidence=['source-contracts.json','proof.sv','proof.cpp','proof.log','gpu-anchor-stage.patch','lint.log']
    write(out/'recipe.json',dict(script_sha256=sha(out/Path(__file__).name),reference_sha256=SHA,candidate_sha256=sha(out/'gpu_core.v'),evidence={n:sha(out/n) for n in evidence}))
    print((out/'proof.log').read_text(),end='');print(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
