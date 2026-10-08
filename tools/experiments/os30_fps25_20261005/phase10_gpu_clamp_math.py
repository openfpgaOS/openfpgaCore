#!/usr/bin/env python3
"""Literal RTL arithmetic checks and all-domain derivation for GPU clamp v12."""
import json,shutil,subprocess
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';OUT=P/'gpu-clamp-review-v12';CAND=P/'gpu-clamp-v12/gpu_core.v';OLD=P/'gpu-paths-v11/gpu_core.v'
def main():
 OUT.mkdir(exist_ok=False);s=CAND.read_text()
 assert sha(CAND)=='e7ddaa402f17a4c621afd8f2277d714a56a6df9d5a5734a090b1286b36797ff2'
 block=s[s.index('reg        [29:0] pss_zinv_shrink_limit_r;'):s.index('reg [4:0] pss_slope_divisor;')]
 split=block.index('wire [31:0] pss_zinv_ones_magnitude')
 block=block[:split].replace('sp_zinv','old_zi')+block[split:].replace('sp_zinv','new_zi')
 block=block.replace('reg        [29:0] pss_zinv_shrink_limit_r;','wire [29:0] pss_zinv_shrink_limit_r = pss_zinv_shrink_limit;')
 text='''module clamp_probe(input wire signed [31:0] old_zi,new_zi,
 output wire [29:0] threshold_limit,output wire shrink_new,shrink_old,
 output wire q16_new,q16_old,q29_new,q29_old);
'''+block+'''
wire [31:0] old_abs=old_zi[31]?-old_zi:old_zi;
wire [31:0] new_abs=new_zi[31]?-new_zi:new_zi;
wire crossing=(new_zi[31]^old_zi[31])&&(old_zi!=0)&&(new_zi!=0);
assign threshold_limit=pss_zinv_shrink_limit;
assign shrink_new=pss_zinv_shrunk;
assign shrink_old=(new_abs<(old_abs>>2));
assign q16_new=crossing||shrink_new;assign q16_old=crossing||shrink_old;
assign q29_new=(new_zi==0)||crossing;assign q29_old=(new_abs==0)||crossing;
endmodule
'''
 (OUT/'clamp_probe.v').write_text(text)
 cpp=r'''#include "Vclamp_probe.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
static Vclamp_probe m;static uint64_t checks;
static uint32_t magnitude(uint32_t v){return v>>31?0u-v:v;}
static void check(uint32_t a,uint32_t b){
 m.old_zi=a;m.new_zi=b;m.eval();uint32_t t=magnitude(a)>>2;
 if(m.threshold_limit!=((t-1)&0x3fffffff)||m.shrink_new!=m.shrink_old||m.q16_new!=m.q16_old||m.q29_new!=m.q29_old){
  fprintf(stderr,"mismatch old=%08x new=%08x limit=%08x T=%08x\n",a,b,m.threshold_limit,t);abort();}
 checks++;
}
static void boundary(uint32_t a){uint32_t t=magnitude(a)>>2;
 uint32_t b[]={0,1,2,3,0xffffffff,0xfffffffe,0xfffffffd,0x80000000,0x7fffffff,
 t-1,t,t+1,0u-(t-1),0u-t,0u-(t+1)};for(uint32_t v:b)check(a,v);}
int main(int argc,char **argv){Verilated::commandArgs(argc,argv);
 for(uint32_t a=0;a<(1u<<20);a++){boundary(a);boundary(0u-a);}
 for(uint32_t a=0;a<65536;a++){boundary(0x80000000u+a);boundary(0x80000000u-a);}
 for(unsigned i=0;i<32;i++)for(int delta=-8;delta<=8;delta++){
  uint32_t a=(1u<<i)+delta;boundary(a);boundary(0u-a);}
 for(unsigned i=0;i<32;i++)for(unsigned j=0;j<32;j++){
  check(1u<<i,1u<<j);check(0u-(1u<<i),1u<<j);check(1u<<i,0u-(1u<<j));check(0u-(1u<<i),0u-(1u<<j));}
 uint32_t rng=0x9610037;auto next=[&](){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;};
 for(unsigned i=0;i<2000000;i++){uint32_t a=next(),b=next();check(a,b);if(!(i&15))boundary(a);}
 m.final();printf("PASS literal_clamp_checks=%llu\n",(unsigned long long)checks);
}
'''
 (OUT/'main.cpp').write_text(cpp)
 cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','clamp_probe','--Mdir',str(OUT/'obj'),'-CFLAGS','-O3 -std=c++17',str(OUT/'clamp_probe.v'),str(OUT/'main.cpp')]
 write(OUT/'command.json',cmd)
 with(OUT/'build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 with(OUT/'run.log').open('x')as f:subprocess.run([str(OUT/'obj/Vclamp_probe')],stdout=f,stderr=subprocess.STDOUT,check=True)
 log=(OUT/'run.log').read_text();assert log.startswith('PASS literal_clamp_checks=')
 (OUT/'DERIVATION.txt').write_text('''Let A be the unsigned two-complement magnitude of old_zi, including
INT_MIN -> 2^31. T=floor(A/4) lies in [0,2^29]. The old test is B<T,
where B is the same unsigned magnitude of new_zi.

For nonnegative old_zi, base=old_zi[31:2]=T and decrement=1.
For negative old_zi, complement(old_zi[31:2])=ceil(A/4)-1.
If low2=0 this is T-1 and decrement=0; otherwise it is T and decrement=1.
Thus the stored 30-bit result L is always T-1 modulo2^30. T=0 produces
all ones (bit29=1), rejecting every shrink. For T>0, L is0..2^29-1.

For nonnegative new_zi, M=(new_zi XOR signmask)=B, and B<T iff M<=L.
For negative new_zi, M=B-1, and B<T iff M<L. Since L<=2^29-1,
any set upper M[31:29] bit makes either comparison false. The candidate
uses exactly these range and signedness cases. This proves every pair
of32-bit inputs, including INT_MIN, zero and magnitudes1..3, threshold
ties, and the maximal T=2^29 case. Two-complement has only one zero;
abs(new)==0 iff new==0, proving the Q29 zero replacement as well.

Reset/lifetime: old reset threshold represented T=0, whereas candidate
reset L=0 represents T=1. These unowned reset values differ. The only
CLAMP predecessors are full ADV_ISSUE or tail ADV_TAIL_COMMIT, both
reachable only after PSS_ADV captured the threshold. Reset/soft-reset
and inactive perspective lead to PSS_IDLE; anchor uses RECIP_NA and
sets clamp false without reading the threshold. Therefore no candidate
revision is needed. Combined full-GPU proof must assert capture-before-
CLAMP and compare the owned captured value on every path.

No hardware state, pipeline cycles or arithmetic source was changed by
this unit test. The candidate comparison wires are extracted literally;
the reference uses unsigned two-complement magnitude and original <.
''')
 write(OUT/'result.json',dict(passed=True,literal_RTL_checks=int(log.strip().split('=')[1]),all_domain_mathematical_derivation=True,reset_value_equivalent=False,reset_difference_unowned_until_PSS_ADV=True,full_GPU_lifetime_proof_pending=True,candidate_sha256=sha(CAND),deployment_ready=False))
 shutil.copy2(Path(__file__),OUT/'recipe.py')
 evidence={str(p.relative_to(ROOT)):sha(p)for p in OUT.rglob('*')if p.is_file()and'obj'not in p.parts}
 for p in [CAND,OLD,OUT/'obj/Vclamp_probe',Path(__file__)]:evidence[str(p.relative_to(ROOT))]=sha(p)
 write(OUT/'manifest.json',dict(passed=True,arithmetic_only=True,evidence_sha256=evidence));print(log.strip(),sha(OUT/'manifest.json'))
if __name__=='__main__':main()
