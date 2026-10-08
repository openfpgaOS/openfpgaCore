#!/usr/bin/env python3
"""Private isolated running-step enable; literal proof and replay shadow utility."""
import difflib,hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'gpu-parallel-control-v1/gpu_core.v';O=P/'gpu-span-step-enable-v1'
BASE_SHA='aae8ad7d90b58d0b8aa703411a010e3d366eb4d33f493018258fa530f52d1f0b'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def once(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b,1)

EARLY='''// A separate low-fanout qualification for the running perspective step.
// Active non-final segments make the scalar tail condition true. The
// pending-Z consumer is pending&&!flush, so its availability reduces to
// !flush. Keep src_done at the final select instead of the shared load_p0a.
(* keep = "true" *) wire span_step_advance_early =
       (!p0a_valid || (GPU_STREAM_PIPE ? stream_launch : !p0_valid))
    && (sp_count != 16'd0) && !persp_swap_pending
    && persp_seg_a_ready && persp_active && (sp_seg_left != 4'd0)
    && (!(sp_z_write_enable && !sp_z_test_enable) || !z_flush_valid)
    && (g_zwarm == 2'd0);
'''
OLD_WRITES='''                        sp_sstep    <= sp_sstep + sp_scc;
                        sp_tstep    <= sp_tstep + sp_tcc;
'''
NEW_WRITES='''            // Same edge and writer priority as the original load_p0a arm.
            if (span_step_advance_early && !src_done) begin
                sp_sstep <= sp_sstep + sp_scc;
                sp_tstep <= sp_tstep + sp_tcc;
            end

'''
SWAP='''            // Apply a perspective segment swap one cycle after the boundary
'''

def transform(old):
 new=once(old, 'wire persp_issue_stall = persp_active && !persp_seg_a_ready;\n',
     'wire persp_issue_stall = persp_active && !persp_seg_a_ready;\n\n'+EARLY)
 new=once(new,OLD_WRITES,'')
 return once(new,SWAP,NEW_WRITES+SWAP)

def prepare():
 assert sha(B)==BASE_SHA
 old=B.read_text();new=transform(old)
 assert old.count('z_src_pending_consume = z_src_pending_valid && !z_flush_valid;')==1
 assert len(re.findall(r'\bz_src_pending_consume\s*=',old))==1
 between=old[old.index(OLD_WRITES)+len(OLD_WRITES):old.index(SWAP)]
 assert not re.search(r'\bsp_[st]step\s*<=',between)
 O.mkdir(exist_ok=False);(O/'baseline.v').write_text(old);(O/'gpu_core.v').write_text(new)
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='gpuaae8/gpu_core.v',tofile='span-step-enable/gpu_core.v')))
 shutil.copy2(__file__,O/'recipe.py')
 put(O/'prepared.json',dict(parent_sha256=BASE_SHA,source_sha256=sha(O/'gpu_core.v'),recipe_sha256=sha(Path(__file__)),added_cycles=0,added_registers=0,kept_combinational_qualifier=True,production_modified=False,quartus_invoked=False,pending=['Literal predicate proof','Full-writer shadow in parent replay','Native composition','Physical qualification']))
 (O/'replay-monitor.vh').write_text(MONITOR)
 print('SOURCE',sha(O/'gpu_core.v'))

def instrument(source):
 """Return simulation-only original full writer shadow; does not drive DUT."""
 assert source.count(NEW_WRITES)==1 and source.count(EARLY)==1
 assert source.count('reg signed [31:0] sp_sstep, sp_tstep;')==1
 # Mirror every unchanged task/decoder/swap/PSS writer at its original site.
 def mirror(m):
  reg,rhs=m.groups()
  if rhs.strip() in ('sp_sstep + sp_scc','sp_tstep + sp_tcc'):return m.group(0)
  return m.group(0)+' proof_old_'+reg+' <= '+rhs+';'
 result=re.sub(r'\b(sp_[st]step)\s*<=\s*([^;]+);',mirror,source)
 result=once(result,'reg signed [31:0] sp_sstep, sp_tstep;',
     'reg signed [31:0] sp_sstep, sp_tstep;\nreg signed [31:0] proof_old_sp_sstep, proof_old_sp_tstep;')
 # Restore only the two removed writes under the literal original nesting.
 anchor='                        sp_seg_left <= sp_seg_left - 4\'d1;\n'
 restored='''                        proof_old_sp_sstep <= proof_old_sp_sstep + sp_scc;
                        proof_old_sp_tstep <= proof_old_sp_tstep + sp_tcc;
'''
 result=once(result,anchor,restored+anchor)
 assert len(re.findall(r'\bproof_old_sp_sstep\s*<=',result))==len(re.findall(r'\bsp_sstep\s*<=',source))
 assert len(re.findall(r'\bproof_old_sp_tstep\s*<=',result))==len(re.findall(r'\bsp_tstep\s*<=',source))
 return result

MONITOR='''reg step_checked=0;
reg[63:0] step_cycles=0,step_advances=0,step_swaps=0,step_src_done=0;
always @(posedge clk)begin
 step_checked<=1;step_cycles<=step_cycles+1;
 if(reset_n && !gpu.soft_reset && gpu.state==gpu.S_FRAG_PIPE)begin
  if(gpu.span_step_advance_early && !gpu.src_done)step_advances<=step_advances+1;
  if(gpu.persp_swap_pending)step_swaps<=step_swaps+1;
  if(gpu.src_done)step_src_done<=step_src_done+1;
  if((gpu.span_step_advance_early && !gpu.src_done)
     !==(gpu.frag_pipe_blk.load_p0a && gpu.persp_active && (gpu.sp_seg_left!=0)))
   $fatal(1,"running-step original/new enables differ");
 end
end
always @(negedge clk)if(step_checked)begin
 if(gpu.sp_sstep!==gpu.proof_old_sp_sstep || gpu.sp_tstep!==gpu.proof_old_sp_tstep)
  $fatal(1,"running-step full original writer shadow differs");
end
final $display("SPAN_STEP_AUDIT cycles=%0d advances=%0d swaps=%0d src_done=%0d",step_cycles,step_advances,step_swaps,step_src_done);
'''

def literal():
 assert sha(B)==BASE_SHA
 d=O/'literal';d.mkdir(exist_ok=False)
 src=(O/'gpu_core.v').read_text();assert transform(B.read_text())==src
 # Exact source assignments, with no hand-written substitute old predicate.
 old=B.read_text();body=old[old.index('            p0a_to_p0 = GPU_STREAM_PIPE'):old.index('            source_fb_addr = sp_fb_addr;',old.index('            p0a_to_p0 = GPU_STREAM_PIPE'))]
 # Source coordinate/Z payload assignments do not participate in the enable.
 start=body.index('            source_z_word_addr =');end=body.index('            source_z_stage_ready =')
 body=body[:start]+body[end:]
 body=body.replace('            source_z_advance_active =\n                sp_z_write_enable || sp_z_test_enable;\n','')
 declarations='''reg p0a_to_p0,p0a_free_after,scalar_source_pixel_available,source_pixel_available;
reg scalar_span_last_issue,source_z_write_only_active,source_z_stage_ready,source_z_ready,load_p0a,load_p0a_z;
wire persp_issue_stall=persp_active && !persp_seg_a_ready;
wire z_src_pending_consume=z_src_pending_valid && !z_flush_valid;
'''
 sv='''module probe(input p0a_valid,stream_launch,p0_valid,GPU_STREAM_PIPE,src_done,persp_swap_pending,persp_active,persp_seg_a_ready,persp_seg_b_ready,sp_z_write_enable,sp_z_test_enable,z_flush_valid,z_src_pending_valid,
input[15:0] sp_count,input[3:0] sp_seg_left,input[1:0] g_zwarm,output old_fire,new_fire);
'''+declarations+EARLY+'\nalways @*begin\n'+body+'''\nend
assign old_fire=load_p0a && persp_active && (sp_seg_left!=4'd0);
assign new_fire=span_step_advance_early && !src_done;
endmodule
'''
 (d/'probe.v').write_text(sv)
 cpp='''#include "Vprobe.h"
#include <cstdio>
#include <cstdint>
int main(){Vprobe t;uint64_t n=0,on=0;unsigned counts[]={0,1,2,65535};
for(unsigned b=0;b<8192;b++)for(unsigned count:counts)for(unsigned seg=0;seg<16;seg++)for(unsigned warm=0;warm<4;warm++){
 t.p0a_valid=b&1;t.stream_launch=(b>>1)&1;t.p0_valid=(b>>2)&1;t.GPU_STREAM_PIPE=(b>>3)&1;
 t.src_done=(b>>4)&1;t.persp_swap_pending=(b>>5)&1;t.persp_active=(b>>6)&1;t.persp_seg_a_ready=(b>>7)&1;t.persp_seg_b_ready=(b>>8)&1;
 t.sp_z_write_enable=(b>>9)&1;t.sp_z_test_enable=(b>>10)&1;t.z_flush_valid=(b>>11)&1;t.z_src_pending_valid=(b>>12)&1;
 t.sp_count=count;t.sp_seg_left=seg;t.g_zwarm=warm;t.eval();
 if(t.old_fire!=t.new_fire){std::fprintf(stderr,"FAIL b=%u count=%u seg=%u warm=%u\\n",b,count,seg,warm);return 1;}n++;on+=t.new_fire;
}std::printf("PASS cases=%llu advances=%llu\\n",(unsigned long long)n,(unsigned long long)on);}
'''
 (d/'main.cpp').write_text(cpp)
 cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),'-CFLAGS','-std=c++17 -O2',str(d/'probe.v'),str(d/'main.cpp')]
 put(d/'command.json',cmd)
 with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 with (d/'run.log').open('x') as f:subprocess.run([str(d/'obj/Vprobe')],stdout=f,stderr=subprocess.STDOUT,check=True)
 log=(d/'run.log').read_text();assert 'PASS cases=2097152' in log
 put(d/'result.json',dict(passed=True,cases=2097152,literal_original_assignments=True,all_boolean_inputs_exhausted=True,segment_and_warm_domains_exhausted=True,count_representatives=[0,1,2,65535],count_equivalence_classes='zero,one,other; only ==0 and ==1 appear in original qualification',cycles_added=0))
 (O/'instrumented-reference-shadow.v').write_text(instrument(src))
 print(log.strip())

if __name__=='__main__':
 for action in sys.argv[1:]:globals()[action]()
