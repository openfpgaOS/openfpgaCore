#!/usr/bin/env python3
"""Private exact ramp-step flattening; no map, fitter or production edits."""
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
P = ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT = P/'audio-ramp-flat-v1'
CORE = P/'compact-lod5-core-v7'
BASE = CORE/'frozen/src/fpga/common/audio_mixer.v'
INHERITED = P/'audio-accum-carry-v1/recipe.py'

NEW = '''function [7:0] ramp_step;
    input [7:0] cur;
    input [7:0] tgt;
    input [7:0] step;
    reg [8:0] up, dn;
    reg take_up, take_dn;
    begin
        up = {1'b0, cur} + {1'b0, step};
        dn = {1'b0, cur} - {1'b0, step};
        // A non-wrapping up<tgt already implies cur<tgt. Likewise,
        // a non-borrowing dn>tgt implies cur>tgt. These choices cannot
        // both be true; all overshoot/equality/step-zero cases yield tgt.
        take_up = (step != 8'd0) && (up < {1'b0, tgt});
        take_dn = (step != 8'd0) && !dn[8] && (dn[7:0] > tgt);
        ramp_step = take_up ? up[7:0] : take_dn ? dn[7:0] : tgt;
    end
endfunction'''


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as f: json.dump(v,f,indent=2);f.write('\n')
def function(s):
    start=s.index('function [7:0] ramp_step;')
    return s[start:s.index('endfunction',start)+len('endfunction')]
def run(cmd,log):
    with log.open('x') as f: subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)


def prepare():
    s=BASE.read_text(); old=function(s)
    assert sha(BASE)=='b530d3c7bba3c1e0e809008131bdaade03af8f2ecf9b5375822d6bb273d779f3'
    new=s.replace(old,NEW)
    assert new.count(NEW)==1
    OUT.mkdir(exist_ok=False)
    (OUT/'baseline.v').write_text(s);(OUT/'audio_mixer.v').write_text(new)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),new.splitlines(True),fromfile='v7/audio_mixer.v',tofile='audio-ramp-flat-v1/audio_mixer.v')))
    shutil.copyfile(__file__,OUT/'recipe.py')
    save(OUT/'prepared.json',dict(parent_sha256=sha(BASE),candidate_sha256=sha(OUT/'audio_mixer.v'),added_registers=0,added_cycles=0,voice_count=32,scope='One ramp_step function only; unchanged shared operands, FSM, reset, writes and sample timing.',native_recipe_sha256=sha(INHERITED)))
    print(sha(OUT/'audio_mixer.v'))


def literal():
    d=OUT/'literal';d.mkdir(exist_ok=False)
    old=function((OUT/'baseline.v').read_text()).replace('ramp_step','old_ramp_step')
    new=function((OUT/'audio_mixer.v').read_text())
    (d/'probe.v').write_text('module probe(input[7:0] cur,tgt,step,output[7:0] old_value,new_value,bad_value);\n'+old+'\n'+new+'''\nassign old_value=old_ramp_step(cur,tgt,step);
assign new_value=ramp_step(cur,tgt,step);
assign bad_value=step==0?cur:new_value;
endmodule
''')
    (d/'main.cpp').write_text('''#include "Vprobe.h"
#include <cstdio>
int main(){Vprobe t; unsigned mismatch=0,negative=0;
for(unsigned k=0;k<16777216;k++){t.cur=k;t.tgt=k>>8;t.step=k>>16;t.eval();
if(t.old_value!=t.new_value){if(mismatch++<4)std::printf("mismatch %u\\n",k);}
negative+=t.old_value!=t.bad_value;}
std::printf("cases=16777216 mismatches=%u negative=%u\\n",mismatch,negative);
return mismatch||negative!=65280;}
''')
    cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),'-CFLAGS','-O2',str(d/'probe.v'),str(d/'main.cpp')]
    save(d/'command.json',cmd);run(cmd,d/'build.log');run([str(d/'obj/Vprobe')],d/'run.log')
    assert 'cases=16777216 mismatches=0 negative=65280' in (d/'run.log').read_text()
    save(d/'result.json',dict(passed=True,exhaustive_binary_input_cases=16777216,mismatches=0,step_zero_negative_control_mismatches=65280,literal_source_extraction=True))


def native():
    manifest=json.loads((P/'audio-accum-carry-v1/manifest.json').read_text())
    assert sha(INHERITED)==manifest['files']['recipe.py']
    spec=importlib.util.spec_from_file_location('audio_native_inherited',INHERITED)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.ROOT=ROOT;m.P=P;m.OUT=OUT;m.CORE=CORE;m.BASE=BASE
    m.native()


def seal():
    a=json.loads((OUT/'literal/result.json').read_text());b=json.loads((OUT/'native/result.json').read_text())
    assert a['passed'] and b['passed']
    assert (OUT/'baseline.v').read_text().replace(function((OUT/'baseline.v').read_text()),NEW)==(OUT/'audio_mixer.v').read_text()
    save(OUT/'result.json',dict(passed=True,candidate_sha256=sha(OUT/'audio_mixer.v'),literal=a,native=b,added_registers=0,added_cycles=0,voice_count=32,fit_attempted=False,timing_gain_not_measured=True))
    (OUT/'REPORT.txt').write_text('''Exact shared audio ramp flattening: PASS

Only ramp_step changes. U=cur+step and D=cur-step are unsigned9-bit values.
For nonzero step, U<tgt implies cur<tgt without overflow; !D[8]&&D[7:0]>tgt implies cur>tgt without borrow. The two predicates are disjoint. If neither holds, the original saturating move reaches tgt, including cur==tgt. Step0 still jumps directly to tgt.
All2^24 cur/tgt/step combinations compare the literal extracted old/new RTL exactly. A step-zero negative control is detected65280 times. The inherited full actual-mixer miter compares live outputs, AXI, FSM/voice and accumulators under MMIO/ramp/reset/stalls/stream and32-voice throughput stimuli; no output cycle changes.
The measured cache64 hot path is state.S_WR_VOL_RAMP → shared operand mux → direction comparison → serial ramp result muxes → nxt_l_r[4],7levels,10.924ns data,7.459ns route. The new function removes the redundant direction selection; mapping/timing benefit is not claimed until separately measured. No state, registers, voices or schedule change; no fitter or production edit.
''')
    shutil.copyfile(__file__,OUT/'recipe-final.py')
    refs=[BASE,INHERITED,CORE/'prepared.json',P/'cache64-v7-fit-v1-retry1/fit-seed2/job/src/fpga/targets/pocket/timing/slow_85_setup.rpt']
    save(OUT/'manifest.json',dict(passed=True,files={str(f.relative_to(OUT)):sha(f) for f in OUT.rglob('*') if f.is_file() and 'obj' not in f.parts},evidence_sha256={str(f.relative_to(ROOT)):sha(f) for f in refs}))
    print(sha(OUT/'manifest.json'))


if __name__=='__main__': globals()[sys.argv[1]]()
