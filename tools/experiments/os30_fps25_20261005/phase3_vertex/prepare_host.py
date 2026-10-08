#!/usr/bin/env python3
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase3/vertex-cpu';OLD=ROOT/'docs/measurements/os30-simple-20261005/validation/cpu-kernel'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 old=json.loads((OLD/'manifest.json').read_text())
 for p,h in old['unchanged_rtl_object_sha256'].items():assert sha(ROOT/p)==h
 s=(OLD/'main.cpp').read_text();assert s.count('tb->rootp->tb_system->cram0_mem__DOT__mem[w] = v;')==1
 s=s.replace('tb->rootp->tb_system->cram0_mem__DOT__mem[w] = v;','if(w < 4194304u) tb->rootp->tb_system->cram0_mem__DOT__mem[w] = v;')
 s=s.replace('#include <vector>','#include <vector>\n#include <map>')
 s=s.replace('static bool measuring=false;','static bool measuring=false;\nstatic uint64_t fdiv_freeze=0, intdiv_freeze=0;\nstatic std::map<uint32_t,uint64_t> fdiv_pc;')
 needle='        const bool aw_take = tb->dbg_aw_valid && tb->dbg_aw_ready;'
 s=s.replace(needle,'''        if(measuring && cpu.__PVT__early0_DivPlugin_logic_processing_request &&
           !cpu.__PVT__early0_DivPlugin_logic_processing_div__DOT__done &&
           !cpu.__PVT__early0_DivPlugin_logic_processing_unscheduleRequest) {
          if(cpu.__PVT__execute_ctrl2_up_FpuDivPlugin_SEL_lane0) {
            fdiv_freeze++;fdiv_pc[cpu.__PVT__execute_ctrl2_up_PC_lane0]++;
          } else intdiv_freeze++;
        }
'''+needle)
 s=s.replace('mark_freeze=cpu.__PVT___zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_2;','fdiv_freeze=intdiv_freeze=0;fdiv_pc.clear();\n              mark_freeze=cpu.__PVT___zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_2;')
 needle='(unsigned long long)(cpu.__PVT___zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter_2-mark_hazard));'
 s=s.replace(needle,needle+'''\n              std::printf("DIV_FREEZE fdiv=%llu intdiv=%llu",(unsigned long long)fdiv_freeze,(unsigned long long)intdiv_freeze);
              for(auto &p:fdiv_pc)std::printf(" pc_%08x=%llu",p.first,(unsigned long long)p.second);
              std::printf("\\n");''')
 (OUT/'main.cpp').write_text(s)
 command=[str(OUT/'main.cpp') if x==str(OLD/'main.cpp') else str(OUT/'Vtb_system') if x==str(OLD/'Vtb_system') else x for x in old['host_build_command']]
 with (OUT/'host-build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
 record={'command':command,'simulator_sha256':sha(OUT/'Vtb_system'),'source_sha256':sha(OUT/'main.cpp'),'original_simulator_sha256':old['simulator_sha256'],'unchanged_rtl_object_sha256':old['unchanged_rtl_object_sha256'],'cpu_sha256':old['cpu_sha256'],'changes':['Bound CRAM mirror to original4194304words for a larger sparse image; SDRAM backdoor receives all words.','Observe divider request&&!done&&!unschedule during existing UART markers, classify FPU select and accumulate its stage2 PC.'],'signal_contract':'RTL connects DivPlugin rsp_valid to divider io_rsp_valid; divider assign io_rsp_valid=done. execute_freeze_valid directly includes request&&!rsp_valid&&!unschedule. FPU_SEL stage2 retained up/down alias is identical. These are overlapping freeze-attribution counters, not additive with dispatch hazards.','script_sha256':sha(Path(__file__))}
 (OUT/'host-manifest.json').write_text(json.dumps(record,indent=2)+'\n');print('HOST READY',record['simulator_sha256'])
if __name__=='__main__':main()
