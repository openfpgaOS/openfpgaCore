#!/usr/bin/env python3
"""Build actual-function CPU fixture from immutable renderer sources/captures."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import *
import re,struct,shutil,subprocess
out=OUT/'cpu-kernel';old=ROOT/'docs/measurements/os30-dual-20261004/validation/baseline128';fw=out/'firmware';fw.mkdir(exist_ok=True)
for name in ['start.S','link.ld','firmware.mif']:shutil.copy2(old.parent/'firmware'/name,fw/name)
# Verify observer noninterference before accepting captured application values.
a=BASELINES['baseline-normal'];b=OUT/'capture-kernel-inputs-normal';check_capture(b)
for name in ['frames.csv','scene.csv','geometry.csv','diagnostic.csv','culling.csv']:
 assert (a/name).exists()==(b/name).exists(),name
 if (a/name).exists():assert (a/name).read_bytes()==(b/name).read_bytes(),name
for p in (a/'gpuvec').glob('*'):assert sha(p)==sha(b/'gpuvec'/p.name)
data=(b/'clip-inputs.bin').read_bytes();assert len(data)%116==0;records=[]
for pos in range(0,len(data),116):
 frame,unused=struct.unpack_from('<II',data,pos);words=struct.unpack_from('<27I',data,pos+8);records.append((frame,words))
assert len(records)>=128,len(records)
(fw/'captured.h').write_text('#define JOBS '+str(len(records))+'u\nstatic const uint32_t input_words[JOBS][27] __attribute__((aligned(64)))={\n'+''.join('{'+','.join('0x%08xu'%v for v in words)+'},\n' for _,words in records)+'};\n')
parent=(OUT.parent/'baseline/gfx_pc.c').read_text();flags=(OUT.parent/'vertex-flags/gfx_pc.c').read_text();fixture=OUT.parent/'clip-exact/function-checks/functions.c'
src=fixture.read_text();src=src[src.index('typedef float rspv_t;'):src.index('static uint32_t rng=')]
pre='''#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
volatile unsigned traps,irq_count;
static void fail(void);
#define assert(x) do {if(!(x))fail();}while(0)
#include <string.h>
'''
mask_values=re.findall(r'\b(CLIP_(?:NEAR|FAR|TOP|BOTTOM|RIGHT|LEFT))\s*=\s*(\d+)',parent)
assert len(mask_values)==6
mask_constants='\n'.join('#define '+k+' '+v for k,v in mask_values)
masks='\n'+mask_constants+'\n'
for label,text in [('reference',parent),('candidate',flags)]:
 part=text.split('        // trivial clip rejection\n',1)[1].split('\n        d->x = x;',1)[0]
 masks+='static __attribute__((noinline)) void '+label+'_mask(struct LoadedVertex *d) {float x=d->x,y=d->y,z=d->z,w=d->w;\n'+part+'\n}\n'
tail=Path(__file__).with_name('main-tail.c').read_text()
(fw/'kernel.c').write_text(pre+src+masks+'\n#include "captured.h"\n'+tail)
# Preserve the exact generated CPU/fabric object model; only the host observer is
# rebuilt. The observed freeze alias is Verilator's generated execute_freeze.
rtl=(FIT/'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v').read_text()
contracts=['assign PrivilegedPlugin_logic_harts_0_commitMask = (((execute_ctrl5_down_LANE_SEL_lane0 && execute_ctrl5_down_isReady) && (! execute_lane0_ctrls_5_downIsCancel)) && execute_ctrl5_down_COMMIT_lane0);','assign execute_lane0_ctrls_5_downIsCancel = 1\'b0;','assign execute_ctrl5_down_LANE_SEL_lane0 = execute_ctrl5_up_LANE_SEL_lane0;','assign execute_ctrl5_down_COMMIT_lane0 = execute_ctrl5_up_COMMIT_lane0;','assign execute_ctrl12_down_ready = (! execute_freeze_valid);']
for i in range(5,12):contracts += [f'assign execute_ctrl{i}_down_ready = execute_ctrl{i+1}_up_ready;']
for i in range(5,13):contracts += [f'assign execute_ctrl{i}_up_ready = execute_ctrl{i}_down_isReady;',f'assign execute_ctrl{i}_down_isReady = execute_ctrl{i}_down_ready;']
for line in contracts:assert line in rtl,line
cpp=(old/'main.cpp').read_text();cpp=cpp.replace('static uint64_t cpu_cycles = 0;','''static uint64_t cpu_cycles = 0;
static uint64_t retired=0, mark_retired=0, mark_cycle=0, mark_freeze=0, mark_hazard=0;
static bool measuring=false;''')
needle='        const bool aw_take = tb->dbg_aw_valid && tb->dbg_aw_ready;'
assert cpp.count(needle)==1
cpp=cpp.replace(needle,'''        auto &cpu = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu;
        if (cpu.__PVT__execute_ctrl5_up_LANE_SEL_lane0 && cpu.__PVT__execute_ctrl5_up_COMMIT_lane0 &&
            !cpu.__PVT___zz___05Fzz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1_1) retired++;
'''+needle)
needle='            uart_buf.push_back(ch);';assert cpp.count(needle)==1
cpp=cpp.replace(needle,needle+'''
            if(ch=='@') {
              if(measuring)std::abort();measuring=true;mark_retired=retired;mark_cycle=cpu_cycles;
              mark_freeze=cpu.__PVT___zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_2;
              mark_hazard=cpu.__PVT___zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_2;
            }
            if(ch=='#') {
              if(!measuring)std::abort();measuring=false;
              std::printf("\\nHOST_KERNEL retired=%llu cycles=%llu freeze=%llu hazards=%llu\\n",
                (unsigned long long)(retired-mark_retired),(unsigned long long)(cpu_cycles-mark_cycle),
                (unsigned long long)(cpu.__PVT___zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_2-mark_freeze),
                (unsigned long long)(cpu.__PVT___zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_2-mark_hazard));
            }''')
# Cache-resident compute legitimately has no SDRAM transactions for >1.5M cycles.
assert cpp.count('if (c - last_m1_activity > 1500000)')==1
cpp=cpp.replace('if (c - last_m1_activity > 1500000)','if (c - last_m1_activity > max_cycles)')
(out/'main.cpp').write_text(cpp)
obj=old/'obj';libs=[obj/'Vtb_system__ALL.a',*[obj/n for n in ['verilated.o','verilated_dpi.o','verilated_threads.o']]]
cmd=['g++','-std=c++17','-O2','-DSYSTEM_NO_REGFILE_PEEK','-I'+str(obj),'-I/usr/share/verilator/include','-I/usr/share/verilator/include/vltstd',str(out/'main.cpp'),*map(str,libs),'-pthread','-lpthread','-latomic','-o',str(out/'Vtb_system')]
with (out/'host-build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
manifest={'cpu_sha256':sha(FIT/'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'),'original_simulator_sha256':sha(obj/'Vtb_system'),'simulator_sha256':sha(out/'Vtb_system'),'unchanged_rtl_object_sha256':{str(p.relative_to(ROOT)):sha(p) for p in libs},'host_build_command':cmd,'commit_counter_contracts':contracts,'observer_noninterference':True,'captured_triangles':len(records),'frame_counts':{str(f):sum(r[0]==f for r in records) for f in set(r[0] for r in records)},'sources_sha256':{str(p.relative_to(ROOT)):sha(p) for p in [fixture,OUT.parent/'baseline/gfx_pc.c',OUT.parent/'vertex-flags/gfx_pc.c',b/'clip-inputs.bin',fw/'kernel.c',fw/'captured.h',out/'main.cpp',Path(__file__),Path(__file__).with_name('main-tail.c')]},'scope':'Actual extracted clipper/mask component on captured triangles. SameA CPU/fabric/SDRAM compiled object model. Host retired markers include common UART/timer harness overhead; timer deltas bracket function only. Not whole-game FPS.'}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print('CPU KERNEL PREPARED',len(records))
