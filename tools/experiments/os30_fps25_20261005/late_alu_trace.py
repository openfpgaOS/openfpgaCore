#!/usr/bin/env python3
"""Observe unchanged RTL objects to diagnose the stride-address regression."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/late-alu-v1/validation/stride-trace'
OLD=ROOT/'docs/measurements/os30-dual-20261004/validation/baseline128'
NEW=OUT.parent/'candidate'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
OUT.mkdir(exist_ok=False)


def run(label, origin):
    out=OUT/label;out.mkdir();obj=origin/'obj'
    text=(origin/'main.cpp').read_text()
    needle='        tick_half(false);\n        const bool aw_take'
    assert text.count(needle)==1
    layer='cpu.__PVT__execute_ctrl1_up_lane0_LAYER_SEL_lane0' if label=='late-alu' else '0'
    observe='''        tick_half(false);
        auto &cpu = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu;
        static unsigned observed_loops=0, observed_steps=0;
        if(cpu.__PVT__execute_ctrl1_up_LANE_SEL_lane0 &&
           !cpu.__PVT___zz___05Fzz_WhiteboxerPlugin_logic_perf_executeFreezedCounter_1_1) {
            unsigned pc = cpu.__PVT__execute_ctrl1_up_PC_lane0;
            if(pc == 0x103201e8) observed_loops++;
            if(observed_loops >= 5 && observed_steps < 96 && pc >= 0x103201e8 && pc <= 0x103201fc) {
                std::printf("STRIDE cycle=%llu pc=%08x layer=%u\\n", (unsigned long long)cpu_cycles, pc, (unsigned)LAYER);
                observed_steps++;
            }
        }
        const bool aw_take'''.replace('LAYER',layer)
    text=text.replace(needle,observe);(out/'main.cpp').write_text(text)
    objects=[obj/n for n in ['Vtb_system__ALL.a','verilated.o','verilated_dpi.o','verilated_threads.o']]
    command=['g++','-std=c++17','-O2','-DSYSTEM_NO_REGFILE_PEEK','-I'+str(obj),
        '-I/usr/share/verilator/include','-I/usr/share/verilator/include/vltstd',str(out/'main.cpp'),
        *map(str,objects),'-pthread','-lpthread','-latomic','-o',str(out/'Vtb_system')]
    with (out/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
    manifest=dict(command=command,unchanged_rtl_objects={str(p):sha(p)for p in objects},
        simulator_sha256=sha(out/'Vtb_system'),observer_only=True)
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    env=dict(os.environ,SCAN_ENABLE='0',SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000')
    with (out/'run.log').open('w')as f:subprocess.run([str(out/'Vtb_system'),'kernels.bin','40000000'],
        cwd=OUT.parent/'firmware',env=env,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
    text=(out/'run.log').read_text();assert 'KERNEL PASS HAL init' in text
    assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
    old=(origin/'kernels-quiet.log').read_text()
    getuart=lambda s:s.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
    assert getuart(text)==getuart(old), 'Observer altered cycle measurements or hashes'
    rows=[line for line in text.splitlines()if line.startswith('STRIDE ')];assert len(rows)==96
    (out/'trace.txt').write_text('\n'.join(rows)+'\n')
    manifest['uart_and_component_cycles_exact_unobserved']=True
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(label,'trace complete',flush=True)


with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:list(pool.map(lambda t:run(*t), [('A',OLD),('late-alu',NEW)]))
