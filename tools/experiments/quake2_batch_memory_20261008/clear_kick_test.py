#!/usr/bin/env python3
"""Actual publication-owner/SDK-leaf and renderer boundary checks."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import clear_kick as recipe

R,P,B,W,O,GL,EM=recipe.R,recipe.P,recipe.B,recipe.W,recipe.O,recipe.GL,recipe.EM
put=recipe.put
C=O/'checks';C.mkdir(exist_ok=False)
env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
commands=[]
def run(cmd,name):
    commands.append(dict(name=name,command=cmd))
    with (C/(name+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,check=True)

sdk=(W/'src/sdk/include/of_gpu.h').read_text();em=(W/EM).read_text()
assert '#define OF_GPU_KICK_MIN_WORDS 512u' in sdk
leaves='\n'.join(recipe.function(sdk,sig) for sig in ('static inline void of_gpu_kick(void)','static inline void of_gpu_kick_now(void)'))
owners='\n'.join(recipe.function(em,sig) for sig in ('void\nOFQ2_Kick(void)','void\nOFQ2_KickNow(void)'))
owner=r'''#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#define OF_GPU_KICK_MIN_WORDS 512u
static int ofq2_ready,ofq2_pending;
static unsigned _gpu_cmd_words,_gpu_unflushed_sync;
static uint32_t storage[1024],*batch,submitted[2048];
static unsigned flush_calls,publishes,submitted_words;
static jmp_buf trap;
/* Recording only the already-owned SDK flush boundary. Its empty/cold guards
 * and successful staged-state reset match the real SDK. DMA/MMIO mechanics
 * are unchanged by this patch and are checked by qsim/RTL separately. */
static void _gpu_flush_cmd_stream(void){
 flush_calls++;
 if(!_gpu_cmd_words)return;
 if(!batch)longjmp(trap,1);
 assert(submitted_words+_gpu_cmd_words<2048);
 memcpy(submitted+submitted_words,batch,_gpu_cmd_words*4);
 submitted_words+=_gpu_cmd_words;publishes++;
 _gpu_cmd_words=0;_gpu_unflushed_sync=0;
}
'''+leaves+'\n'+owners+r'''
static void reset(unsigned words,unsigned sync){
 _gpu_cmd_words=words;_gpu_unflushed_sync=sync;batch=storage;
 flush_calls=publishes=submitted_words=0;memset(submitted,0,sizeof submitted);
 for(unsigned i=0;i<1024;i++)storage[i]=0x12000000+i;
}
int main(void){
 const unsigned sizes[]={0,1,4,17,511,512,513};unsigned cases=0;
 for(unsigned ready=0;ready<2;ready++)for(unsigned pending=0;pending<2;pending++)
 for(unsigned sync=0;sync<2;sync++)for(unsigned i=0;i<sizeof sizes/sizeof *sizes;i++)
 for(unsigned immediate=0;immediate<2;immediate++){
  unsigned n=sizes[i];reset(n,sync);ofq2_ready=ready;ofq2_pending=pending;
  unsigned flush=ready&&(immediate||n>=512||sync),publish=flush&&n;
  if(immediate)OFQ2_KickNow();else OFQ2_Kick();
  assert(flush_calls==flush && publishes==publish && submitted_words==(publish?n:0));
  assert(_gpu_cmd_words==(publish?0:n) && _gpu_unflushed_sync==(publish?0:sync));
  assert(ofq2_pending==(int)pending);
  if(publish)assert(!memcmp(submitted,storage,n*4));cases++;
 }
 /* Cold/unready must not access even a nonexistent staging allocation. */
 reset(4,0);batch=NULL;ofq2_ready=0;ofq2_pending=1;
 OFQ2_KickNow();OFQ2_Kick();assert(!flush_calls&&_gpu_cmd_words==4&&ofq2_pending);
 /* Ready with an empty stream is harmless even without storage. */
 reset(0,0);batch=NULL;ofq2_ready=1;OFQ2_KickNow();assert(!publishes);
 /* Preserve the SDK's existing invalid nonempty/cold-stream guard. */
 reset(4,0);batch=NULL;ofq2_ready=1;
 if(!setjmp(trap)){OFQ2_KickNow();assert(!"expected cold stream trap");}
 /* Clear publishes now; a later short ordinary batch stays advisory. */
 reset(4,0);ofq2_ready=1;ofq2_pending=1;OFQ2_KickNow();assert(publishes==1&&submitted_words==4);
 _gpu_cmd_words=5;OFQ2_Kick();assert(publishes==1&&_gpu_cmd_words==5);
 _gpu_unflushed_sync=1;OFQ2_Kick();assert(publishes==2&&submitted_words==9&&ofq2_pending);
 printf("PASS owner_sdk_leaf_cases=%u cold_guards=3 ordered_boundary_sequence=1\n",cases);
 return 0;
}
'''
(C/'owner.c').write_text(owner)
basic=['cc','-std=gnu11','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
run(basic+[str(C/'owner.c'),'-o',str(C/'owner')],'owner-build');run([str(C/'owner')],'owner-run')
G=W/'src/quake2'
flags=basic+['-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-ffunction-sections','-fdata-sections','-DYQ2OSTYPE="test"','-DYQ2ARCH="host"']
flags+=['-I'+str(p) for p in (G,G/'src/common/header',G/'src/client/refresh/gl1/glad-gles1/include',W/'src/sdk/include')]
run(flags+[str(W/recipe.TEST),'-Wl,--gc-sections','-lm','-o',str(C/'renderer')],'renderer-build');run([str(C/'renderer')],'renderer-run')
old=R/'docs/measurements/quake2-comprehensive-20261008/clear-interaction-combined6-v1'
lifecycle=(old/'lifecycle.c').read_text()
assert lifecycle.count('void OFQ2_Kick(void) { }')==1
lifecycle=lifecycle.replace('void OFQ2_Kick(void) { }','''static unsigned advisory_kicks, immediate_kicks;
void OFQ2_Kick(void) { advisory_kicks++; }
void OFQ2_KickNow(void) { immediate_kicks++; }''')
lifecycle=lifecycle.replace('''  unsigned c=color_clears;
  RI_EndFrame(); assert(color_clears==c+1);''','''  unsigned c=color_clears, kicks=immediate_kicks, advisory=advisory_kicks;
  RI_EndFrame(); assert(color_clears==c+1);
  assert(immediate_kicks==kicks+1 && advisory_kicks==advisory && ofgl_gpu_pending);''')
assert 'kicks=immediate_kicks' in lifecycle
lifecycle=lifecycle.replace('''  gpu_write(route); snapshot("gpu-write");''','''  unsigned kicks=immediate_kicks, advisory=advisory_kicks;
  gpu_write(route); assert(immediate_kicks==kicks && advisory_kicks==advisory+1);
  snapshot("gpu-write");''')
anchor=''' RI_ShutdownContext();
 fprintf(stderr,"PASS lifecycle'''
assert lifecycle.count(anchor)==1
lifecycle=lifecycle.replace(anchor,''' /* CPU fallback or absent surfaces must not issue a forced publish. */
 unsigned kicks=immediate_kicks, advisory=advisory_kicks;
 ofgl_clear565=0x1234; RI_EndFrame();
 assert(immediate_kicks==kicks && advisory_kicks==advisory && !ofgl_gpu_pending);
 ofgl_clear565=0; missing_surface=1; RI_EndFrame(); missing_surface=0;
 assert(immediate_kicks==kicks && !ofgl_gpu_pending);
 ofgl_gpu_ok=0; RI_EndFrame();
 assert(immediate_kicks==kicks && !ofgl_gpu_pending); ofgl_gpu_ok=1;
 RI_ShutdownContext();
 fprintf(stderr,"PASS lifecycle''',1)
(C/'lifecycle.c').write_text(lifecycle)
run(flags+['-DOFGL_SOURCE="'+str(W/GL)+'"','-DEXPECT_REUSE=1',str(C/'lifecycle.c'),'-Wl,--gc-sections','-lm','-o',str(C/'lifecycle')],'lifecycle-build')
run([str(C/'lifecycle')],'lifecycle-run')
def snapshots(path):return [v for v in path.read_text().splitlines() if v and v[0].isdigit()]
assert snapshots(C/'lifecycle-run.log')==snapshots(old/'run.log')
assert recipe.function((W/GL).read_text(),'ofgl_end3d(void)')==recipe.function((B/GL).read_text(),'ofgl_end3d(void)')
assert (W/'src/sdk/include/of_gpu.h').read_bytes()==(B/'src/sdk/include/of_gpu.h').read_bytes()
put(C/'commands.json',commands);shutil.copy2(__file__,O/'test-recipe.py')
put(O/'tests.json',dict(passed=True,actual_renderer_sanitizers=True,owner_sdk_leaf_cases=112,
    lifecycle_snapshots_exact=53,normal_3d_kicks_advisory=True,post_flip_gpu_clear_forced=True,
    cpu_fallback_and_missing_surface_no_forced_publish=True,pending_state_and_existing_waits_preserved=True,
    sdk_unchanged_sha256=recipe.sha(W/'src/sdk/include/of_gpu.h'),
    limitations='Publication-owner test compiles literal owner and SDK kick leaves with a recording flush boundary. Existing SDK MMIO/DMA internals unchanged; full target qsim and GPU replay are separate gates.'))
put(O/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):recipe.sha(p) for p in O.rglob('*') if p.is_file()}))
print('PASS owner publication guards, actual renderer,53 lifecycle snapshots and EndFrame-specific publish checks')
