#!/usr/bin/env python3
"""Check the ramp pipeline against the original mixer, including PCM cycles."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'docs/measurements/os30-frontend-20261004/mixer-checks'
STAGE = OUT.parent/'rtl/audio_mixer.v'
TEST = ROOT/'src/fpga/test'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def run(command, log, **kwargs):
    with log.open('w') as f:
        subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True, **kwargs)
    return log.read_text()


def main():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',default='mixer-checks-serial-v2')
    args=parser.parse_args()
    assert re.fullmatch(r'[a-z0-9_-]+',args.label)
    OUT=OUT.parent/args.label
    OUT.mkdir(exist_ok=False)
    traces, throughput, sources = {}, {}, {}
    for label, source, flags in [
        ('original', ROOT/'src/fpga/common/audio_mixer.v', []),
        ('disabled', STAGE, []),
        ('pipeline', STAGE, ['+define+INCLUDE_MIXER_RAMP_PIPE']),
    ]:
        dest = OUT/label
        dest.mkdir()
        files = [source, TEST/'tb_audio_mixer.v', TEST/'tb_audio_mixer_main.cpp',
                 ROOT/'src/fpga/common/sync_fifo.v',
                 ROOT/'src/fpga/targets/mister/mister_audio_output.v']
        for p in files:
            shutil.copy2(p, dest/p.name)
            sources[str(dest/p.name)] = sha(p)
        command = ['verilator', '--cc', '--exe', '--build', '-j', '3',
                   '-Wno-fatal', '--top-module', 'tb_audio_mixer',
                   '--Mdir', str(dest/'obj'), '-CFLAGS', '-std=c++17 -O2']
        command += flags + [str(dest/p.name) for p in files]
        run(command, dest/'build.log')
        exe = str(dest/'obj/Vtb_audio_mixer')
        output = run([exe], dest/'run.log', env=dict(os.environ, AUDIO_PERF_REPORT='1'))
        trace = re.search(r'PERF audio samples \d+ trace [0-9a-f]+', output)
        assert trace and re.search(r'Results: \d+ passed, 0 failed', output), label
        traces[label] = trace.group()
        print(label, traces[label], flush=True)
        if label != 'disabled':
            throughput[label] = run([exe, '--throughput'], dest/'throughput.csv')
    assert len(set(traces.values())) == 1, traces
    assert throughput['original'] == throughput['pipeline']
    function = re.search(r'function \[7:0\] ramp_clamp;.*?endfunction', STAGE.read_text(), re.S).group()
    top = OUT/'ramp_oracle.v'
    top.write_text('module ramp_oracle(input [7:0] cur, tgt, step, output [7:0] result);\n'
                   + function + '\nwire [8:0] up = {1\'b0,cur} + {1\'b0,step};\n'
                   'wire [8:0] dn = {1\'b0,cur} - {1\'b0,step};\n'
                   'assign result = ramp_clamp(cur,tgt,step,up,dn);\nendmodule\n')
    cpp = OUT/'ramp_oracle.cpp'
    cpp.write_text('''#include "Vramp_oracle.h"
#include <algorithm>
#include <cassert>
#include <cstdio>
int main() {
  Vramp_oracle dut;
  for (int cur=0;cur<256;++cur) for (int tgt=0;tgt<256;++tgt)
    for (int step=0;step<256;++step) {
      dut.cur=cur; dut.tgt=tgt; dut.step=step; dut.eval();
      int want=step==0?tgt:cur+((tgt>cur)?std::min(tgt-cur,step):-std::min(cur-tgt,step));
      assert(dut.result==want);
    }
  puts("16,777,216 ramp inputs match the independent saturating-distance oracle");
}
''')
    run(['verilator', '--cc', '--exe', '--build', '-j', '3', '-Wno-fatal',
         '--top-module', 'ramp_oracle', '--Mdir', str(OUT/'oracle'),
         '-CFLAGS', '-std=c++17 -O2', str(top), str(cpp)], OUT/'oracle-build.log')
    oracle = run([str(OUT/'oracle/Vramp_oracle')], OUT/'oracle.log')
    result = dict(passed=True, traces=traces, exact_pcm_and_cycle_match=True,
                  throughput_36_cases_identical=True, ramp_inputs=16777216,
                  sources=sources, candidate_sha256=sha(STAGE))
    (OUT/'results.json').write_text(json.dumps(result, indent=2)+'\n')
    print(oracle.strip(), flush=True)


if __name__ == '__main__': main()
