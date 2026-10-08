#!/usr/bin/env python3
"""Prove a cycle-neutral, shared audio volume-ramp arithmetic rewrite."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT/'docs/measurements/os30-dual-20261004'
OUT = ART/'audio-ramp-balance-v1'
REF = ART/'fit-rvc-skid-pingpong-v1/frozen/src/fpga/common/audio_mixer.v'
SHA = 'b530d3c7bba3c1e0e809008131bdaade03af8f2ecf9b5375822d6bb273d779f3'
TEST = ROOT/'src/fpga/test'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(args, log, **kwargs):
    with log.open('w') as handle:
        subprocess.run(args, stdout=handle, stderr=subprocess.STDOUT, check=True, **kwargs)
    return log.read_text()


def main():
    src = REF.read_text()
    assert sha(src.encode()) == SHA
    old = '''function [7:0] ramp_step;
    input [7:0] cur;
    input [7:0] tgt;
    input [7:0] step;
    reg   [8:0] up, dn;
    begin
        up = {1'b0, cur} + {1'b0, step};
        dn = {1'b0, cur} - {1'b0, step};
        if (step == 8'd0)
            ramp_step = tgt;
        else if (cur < tgt)
            ramp_step = (up[8] || up[7:0] >= tgt) ? tgt : up[7:0];
        else if (cur > tgt)
            ramp_step = (dn[8] || dn[7:0] <= tgt) ? tgt : dn[7:0];
        else
            ramp_step = cur;
    end
endfunction'''
    assert src.count(old) == 1
    new = '''function [7:0] ramp_step;
    input [7:0] cur;
    input [7:0] tgt;
    input [7:0] step;
    reg   [8:0] up, dn;
    reg         take_up, take_dn;
    begin
        up = {1'b0, cur} + {1'b0, step};
        dn = {1'b0, cur} - {1'b0, step};
        // Nonoverflowed up<tgt implies cur<tgt; nonborrowed dn>tgt
        // implies cur>tgt. These two choices are mutually exclusive.
        // Zero step still snaps directly to the target.
        take_up = (step != 8'd0) && !up[8] && (up[7:0] < tgt);
        take_dn = (step != 8'd0) && !dn[8] && (dn[7:0] > tgt);
        ramp_step = ({8{take_up}} & up[7:0]) |
                    ({8{take_dn}} & dn[7:0]) |
                    ({8{!(take_up || take_dn)}} & tgt);
    end
endfunction'''
    candidate = src.replace(old, new, 1)
    assert candidate.replace(new, old, 1) == src
    OUT.mkdir(exist_ok=False)
    (OUT/'original-function.v').write_text(old+'\n')
    (OUT/'candidate-function.v').write_text(new+'\n')
    design = ''
    for label, function in [('reference', old), ('candidate', new)]:
        design += f'module {label}(input [7:0] cur,tgt,step,output [7:0] result);\n'
        design += function+'\nassign result=ramp_step(cur,tgt,step);\nendmodule\n'
    design += '''module ramp_proof(input [7:0] cur,tgt,step,output [7:0] old_result,new_result);
reference a(cur,tgt,step,old_result);
candidate b(cur,tgt,step,new_result);
endmodule
'''
    (OUT/'ramp_proof.v').write_text(design)
    cpp = '''#include "Vramp_proof.h"
#include <algorithm>
#include <cassert>
#include <cstdio>
int main() {
 Vramp_proof dut;
 for(unsigned cur=0;cur<256;++cur) for(unsigned tgt=0;tgt<256;++tgt)
  for(unsigned step=0;step<256;++step) {
   dut.cur=cur;dut.tgt=tgt;dut.step=step;dut.eval();
   int c=int(cur),t=int(tgt),s=int(step);
   int want=s==0?t:(t>c?c+std::min(t-c,s):c-std::min(c-t,s));
   if(dut.old_result!=dut.new_result || dut.new_result!=want) {
    std::printf("FAIL cur=%u tgt=%u step=%u old=%u new=%u oracle=%d\\n",cur,tgt,step,dut.old_result,dut.new_result,want);
    return 1;
   }
  }
 std::puts("PASS all16,777,216 triples: extracted original/new functions and independent saturating-distance oracle agree");
}
'''
    (OUT/'ramp_proof.cpp').write_text(cpp)
    run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal',
         '--top-module','ramp_proof','--Mdir',str(OUT/'proof-obj'),
         '-CFLAGS','-std=c++17 -O2',str(OUT/'ramp_proof.v'),str(OUT/'ramp_proof.cpp')],OUT/'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (OUT/'proof-build.log').read_text())
    print(run([str(OUT/'proof-obj/Vramp_proof')],OUT/'proof.log'),end='',flush=True)
    (OUT/'rtl').mkdir()
    (OUT/'rtl/audio_mixer.v').write_text(candidate)
    patch = ''.join(difflib.unified_diff(src.splitlines(True),candidate.splitlines(True),
                    fromfile='a/audio_mixer.v',tofile='b/audio_mixer.v'))
    (OUT/'ramp-balance.patch').write_text(patch)
    replay=OUT/'patch-replay'
    replay.mkdir()
    (replay/'audio_mixer.v').write_text(candidate)
    run(['patch','--batch','--fuzz=0','-R','-p1','-i',str(OUT/'ramp-balance.patch')],OUT/'reverse.log',cwd=replay)
    assert (replay/'audio_mixer.v').read_text()==src
    run(['patch','--batch','--fuzz=0','-p1','-i',str(OUT/'ramp-balance.patch')],OUT/'replay.log',cwd=replay)
    assert (replay/'audio_mixer.v').read_text()==candidate

    def regress(label, source):
        dest=OUT/label
        dest.mkdir()
        (dest/'audio_mixer.v').write_text(source)
        files=[dest/'audio_mixer.v']
        for p in [TEST/'tb_audio_mixer.v',TEST/'tb_audio_mixer_main.cpp',
                  ROOT/'src/fpga/common/sync_fifo.v',ROOT/'src/fpga/targets/mister/mister_audio_output.v']:
            shutil.copy2(p,dest/p.name)
            files.append(dest/p.name)
        command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal',
                 '--top-module','tb_audio_mixer','--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2']
        run(command+[str(p) for p in files],dest/'build.log')
        log=run([str(dest/'obj/Vtb_audio_mixer')],dest/'run.log',env=dict(os.environ,AUDIO_PERF_REPORT='1'))
        assert re.search(r'Results: \d+ passed, 0 failed',log),label
        trace=re.findall(r'PERF audio samples \d+ trace [0-9a-f]+',log)
        assert len(trace)==1
        throughput=run([str(dest/'obj/Vtb_audio_mixer'),'--throughput'],dest/'throughput.csv')
        assert len(throughput.splitlines())==37
        print(label,trace[0],flush=True)
        return dict(label=label,log=log,trace=trace[0],throughput=throughput,
                    sources={p.name:sha(p.read_bytes()) for p in files})
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(regress,label,text) for label,text in [('reference',src),('candidate',candidate)]]
        results=[job.result() for job in jobs]
    assert results[0]['log']==results[1]['log']
    assert results[0]['throughput']==results[1]['throughput']
    manifest=dict(reference=str(REF.relative_to(ROOT)),reference_sha256=SHA,
                  candidate_sha256=sha(candidate.encode()),exhaustive_inputs=16777216,
                  source_scope='Only ramp_step function body and its two combinational temporaries',
                  shared_ramp_instance_unchanged=True,states_and_registers_unchanged=True,
                  extra_registers=0,extra_cycles=0,feature_or_voice_count_changes=False,
                  existing_audio_suite_identical=True,pcm_and_timestamp_trace=results[0]['trace'],
                  throughput_cases=36,throughput_exactly_identical=True,
                  exact_reverse_and_replay=True,
                  sources={r['label']:r['sources'] for r in results})
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (OUT/'audio_ramp_balance.py').write_text(Path(__file__).read_text())
    print('READY',OUT/'rtl/audio_mixer.v','SHA256',manifest['candidate_sha256'],flush=True)


if __name__=='__main__':
    main()
