#!/usr/bin/env python3
"""Exact reciprocal register write-data factoring over selected v7 GPU."""
import difflib, hashlib, json, re, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v7/frozen/src/fpga/common/gpu_core.v'
OUT=P/'gpu-recip-write-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)

DATA='''// Default the reciprocal write DATA to the LUT seed. The existing write
// enable owns SHIFT, refined-NR capture, and the optional Q29 cache hit.
// Thus SHIFT need not qualify the seed again inside the data-selection mux.
wire [5:0] recip_q16_seed_shift = pss_use_q29 ? pss_q29_recip_shift
                                              : {1'b0, persp_clz};
wire [44:0] recip_q16_seed_funnel = {29'd0, recip_rd_data} << recip_q16_seed_shift;
wire recip_q16_write_enable = (persp_pss == PSS_RECIP_SHIFT)
                           || (persp_pss == PSS_NR_CAPTURE)
                           || ((persp_pss == PSS_TOP8) && pss_use_q29
                               && pss_recip_cache_hit);
reg signed [31:0] recip_q16_write_data;
reg signed [63:0] recip_q16_nr_shifted;
always @* begin
    recip_q16_write_data = $signed(recip_q16_seed_funnel[44:13]);
    recip_q16_nr_shifted = dsp_p >>> 16;
    if (persp_pss == PSS_TOP8)
        recip_q16_write_data = $signed(pss_recip_cache_value);
    if (persp_pss == PSS_NR_CAPTURE) begin
        if (pss_use_q29)
            recip_q16_write_data = recip_q16_nr_shifted[31:0];
        else
            recip_q16_write_data = dsp_p[47:16]
                                + ((sp_zinv_step_zero
                                 && sp_zinv != 32'sh00010000) ? 32'sd1
                                                              : 32'sd0);
    end
end
'''

def pieces(s):
    a=s.index('                    recip_shamt  = pss_use_q29 ?')
    z=s.index('                    persp_pss <= PSS_NR_MUL_X;',a)
    seed=s[a:z]
    a=s.index('                    if (pss_use_q29) begin\n                        recip_shifted = dsp_p >>> 16;')
    z=s.index('                    persp_pss <= PSS_MUL;',a)
    return seed,s[a:z]

def prepare():
    old=BASE.read_text();assert sha(BASE)=='3c171d36bce942735040d3cdc2d96a5f9959b46b2b5c4896455d1c4763ffa4d1'
    seed,nr=pieces(old);new=old
    anchor='reg signed [31:0] recip_q16_r;       // Q16, or Q(16+PSS_Q29_RECIP_EXTRA) for Q29\n'
    new=once(new,anchor,anchor+DATA)
    new=once(new,'            if (!persp_active) begin\n                persp_pss <= PSS_IDLE;',
        '''            if (persp_active && recip_q16_write_enable)
                recip_q16_r <= recip_q16_write_data;
            if (!persp_active) begin
                persp_pss <= PSS_IDLE;''')
    new=once(new,'                        recip_q16_r <= $signed(pss_recip_cache_value);\n','')
    new=once(new,'                    reg [5:0]  recip_shamt;\n                    reg [44:0] recip_funnel;\n','')
    new=once(new,seed,'')
    new=once(new,'                    reg signed [63:0] recip_shifted;\n','')
    new=once(new,nr,'')
    assert len(re.findall(r'\brecip_q16_r\s*<=',old))==4
    assert len(re.findall(r'\brecip_q16_r\s*<=',new))==1
    OUT.mkdir(exist_ok=False);(OUT/'baseline.v').write_text(old);(OUT/'gpu_core.v').write_text(new)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='v7/gpu_core.v',tofile='gpu-recip-write-v1/gpu_core.v')))
    shutil.copyfile(__file__,OUT/'recipe.py')
    save(OUT/'prepared.json',dict(parent_sha256=sha(BASE),candidate_sha256=sha(OUT/'gpu_core.v'),added_registers=0,added_cycles=0,scope='Only reciprocal write-data/enable factoring. Three mutually exclusive original write states, identical original arithmetic and reset/soft-reset hold behavior.'))
    print(sha(OUT/'gpu_core.v'))

def reference_body(s, target):
    seed,nr=pieces(s)
    seed=seed.replace('recip_q16_r <=',target+' =')
    nr=nr.replace('recip_q16_r <=',target+' =')
    return '''case (persp_pss)
 PSS_TOP8: if(pss_use_q29 && pss_recip_cache_hit) begin
  OLD_WE=1; OLD_TARGET=$signed(pss_recip_cache_value);
 end
 PSS_RECIP_SHIFT: begin
  OLD_WE=1;
'''.replace('OLD_TARGET',target)+seed+''' end
 PSS_NR_CAPTURE:begin
  OLD_WE=1;
'''+nr+''' end
 default:begin end
endcase
'''

def literal():
    d=OUT/'literal';d.mkdir(exist_ok=False);old=(OUT/'baseline.v').read_text()
    shift_state=int(re.search(r"localparam PSS_RECIP_SHIFT\s*=\s*6'd(\d+)",old)[1])
    constants='\n'.join(re.findall(r'^localparam PSS_(?:TOP8|RECIP_SHIFT|NR_CAPTURE)\s*=.*?;',old,re.M))
    assert constants.count('localparam')==3
    body=reference_body(old,'old_value').replace('OLD_WE','old_we')
    text='''module probe(input [31:0] held, input [15:0] recip_rd_data,
 input [4:0] persp_clz,input pss_use_q29,pss_recip_cache_hit,
 input [31:0] pss_recip_cache_value,sp_zinv,input sp_zinv_step_zero,
 input signed [63:0] dsp_p,input [5:0] persp_pss,
 input reset_n,soft_reset,fragment,persp_active,
 output reg [31:0] old_value,output [31:0] new_value,
 output reg old_we,output new_we);
'''+constants+'''
wire [5:0] pss_q29_recip_shift={1'b0,persp_clz}+6'd4;
'''+DATA+'''
reg[5:0]recip_shamt;reg[44:0]recip_funnel;reg signed[63:0]recip_shifted;
always @*begin
 old_value=held;old_we=0;recip_shamt=0;recip_funnel=0;recip_shifted=0;
 if(reset_n&&!soft_reset&&fragment&&persp_active)begin
'''+body+'''end
end
assign new_we=reset_n&&!soft_reset&&fragment&&persp_active&&recip_q16_write_enable;
assign new_value=new_we?recip_q16_write_data:held;
endmodule
'''
    (d/'probe.v').write_text(text)
    (d/'main.cpp').write_text('''#include "Vprobe.h"
#include <cstdio>
#include <cstdint>
static uint64_t count=0;static uint32_t rng=0x68cabf10;
static uint32_t next(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static void check(Vprobe&t){t.eval();++count;if(t.old_we!=t.new_we||t.old_value!=t.new_value){std::printf("FAIL case=%llu state=%u\\n",(unsigned long long)count,t.persp_pss);std::exit(1);}}
int main(){Vprobe t;t.reset_n=1;t.fragment=1;t.persp_active=1;t.soft_reset=0;
t.held=0xdeadbeef;t.pss_recip_cache_value=0xa55a0123;t.dsp_p=0xaabbccddfeffffffULL;
t.persp_pss=7; // replaced by literal selected PSS_RECIP_SHIFT below
for(unsigned q=0;q<2;q++)for(unsigned c=0;c<32;c++)for(unsigned v=0;v<65536;v++){
t.pss_use_q29=q;t.persp_clz=c;t.recip_rd_data=v;check(t);}
for(unsigned ctrl=0;ctrl<32768;ctrl++){
t.persp_pss=ctrl&63;t.reset_n=(ctrl>>6)&1;t.soft_reset=(ctrl>>7)&1;t.fragment=(ctrl>>8)&1;
t.persp_active=(ctrl>>9)&1;t.pss_use_q29=(ctrl>>10)&1;t.pss_recip_cache_hit=(ctrl>>11)&1;
t.sp_zinv_step_zero=(ctrl>>12)&1;t.sp_zinv=(ctrl>>13)==0?0x10000:((ctrl>>13)==1?0:((ctrl>>13)==2?0xffffffff:0x80000000));
for(unsigned bit=0;bit<64;bit++){t.dsp_p=1ULL<<bit;t.pss_recip_cache_value=1u<<(bit&31);t.held=~t.pss_recip_cache_value;
t.recip_rd_data=next();t.persp_clz=next()&31;check(t);}}
for(unsigned i=0;i<1000000;i++){t.persp_pss=next()&63;t.reset_n=next()&1;t.soft_reset=next()&1;t.fragment=next()&1;t.persp_active=next()&1;
t.pss_use_q29=next()&1;t.pss_recip_cache_hit=next()&1;t.sp_zinv_step_zero=next()&1;t.sp_zinv=next();
t.dsp_p=(uint64_t(next())<<32)|next();t.pss_recip_cache_value=next();t.held=next();t.recip_rd_data=next();t.persp_clz=next()&31;check(t);}
std::printf("RECIP_LITERAL cases=%llu PASS\\n",(unsigned long long)count);}
'''.replace('#include <cstdint>','#include <cstdint>\n#include <cstdlib>').replace('t.persp_pss=7;',f't.persp_pss={shift_state};'))
    cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),'-CFLAGS','-O2',str(d/'probe.v'),str(d/'main.cpp')]
    save(d/'command.json',cmd)
    for name,command in [('build',cmd),('run',[str(d/'obj/Vprobe')])]:
        with (d/(name+'.log')).open('x') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
    m=re.search(r'RECIP_LITERAL cases=(\d+) PASS',(d/'run.log').read_text());assert m
    save(d/'result.json',dict(passed=True,checks=int(m[1]),all_lut_values_and_clz_and_q29=True,all_32768_control_combinations_with_64_payload_bases=True,random_full_inputs=1000000,scope='Literal original expressions versus new combinational data/enable, including hold/reset/soft-reset, Q29 cache hit and legacy constant-Z bias.'))


if __name__=='__main__':globals()[sys.argv[1]]()
