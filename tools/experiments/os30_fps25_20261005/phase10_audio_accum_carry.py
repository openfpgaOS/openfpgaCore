#!/usr/bin/env python3
"""Private exact signed-sample accumulator carry split. No mapper or fitter."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
CORE = P / 'compact-lod5-core-v11'
BASE = CORE / 'frozen/src/fpga/common/audio_mixer.v'
OUT = P / 'audio-accum-carry-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

FUNCTION = '''// Sign extension contributes only -sample[15] to the upper five bits.
// Select an early +/-1 upper result after the low 16-bit carry is known,
// instead of propagating the late volume-product carry through all 21 bits.
function [ACCUM_W-1:0] accum_add_s16;
    input [ACCUM_W-1:0] accumulator;
    input [15:0] sample;
    (* keep *) reg [16:0] low_sum;
    (* keep *) reg [4:0] upper_plus, upper_minus;
    reg [4:0] upper_result;
    begin
        low_sum = {1'b0, accumulator[15:0]} + {1'b0, sample};
        upper_plus = accumulator[20:16] + 5'd1;
        upper_minus = accumulator[20:16] - 5'd1;
        case ({sample[15], low_sum[16]})
            2'b01: upper_result = upper_plus;
            2'b10: upper_result = upper_minus;
            default: upper_result = accumulator[20:16];
        endcase
        accum_add_s16 = {upper_result, low_sum[15:0]};
    end
endfunction

'''


def save(path, data):
    with path.open('x') as f:
        json.dump(data, f, indent=2)
        f.write('\n')


def run(cmd, log, env=None):
    with log.open('x') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True, env=env)


def prepare():
    old = BASE.read_text()
    anchor = 'function [7:0] ramp_step;'
    assert old.count(anchor) == 1 and 'localparam ACCUM_W = 21;' in old
    new = old.replace(anchor, FUNCTION + anchor)
    for channel in ('l', 'r'):
        a = f'accum_{channel} <= accum_{channel} + $signed({{{{(ACCUM_W-16){{samp_{channel}_tmp[15]}}}}, samp_{channel}_tmp}});'
        b = f'accum_{channel} <= accum_add_s16(accum_{channel}, samp_{channel}_tmp);'
        assert new.count(a) == 1
        new = new.replace(a, b)
    OUT.mkdir(exist_ok=False)
    shutil.copy2(BASE, OUT / 'baseline.v')
    (OUT / 'audio_mixer.v').write_text(new)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile='compact-v11/audio_mixer.v',
        tofile='audio-accum-carry-v1/audio_mixer.v')))
    shutil.copy2(__file__, OUT / 'recipe.py')
    save(OUT / 'prepared.json', dict(parent_sha256=sha(BASE), candidate_sha256=sha(OUT / 'audio_mixer.v'),
        parent_final_manifest_sha256=sha(CORE / 'final-manifest.json'),
        timing_manifest_sha256=sha(P / 'compact-timing-audit-v11/manifest.json'),
        accumulator_bits=21, sample_bits=16, low_carry_bits=16, upper_bits=5,
        added_registers=0, added_cycles=0, voices=32, saturation_and_volume_unchanged=True,
        no_map=True, no_fit=True))
    print(sha(OUT / 'audio_mixer.v'))


def literal():
    d = OUT / 'literal'
    d.mkdir(exist_ok=False)
    source = (OUT / 'audio_mixer.v').read_text()
    function = source[source.index('function [ACCUM_W-1:0] accum_add_s16;'):source.index('endfunction', source.index('function [ACCUM_W-1:0] accum_add_s16;')) + len('endfunction')]
    assert function in FUNCTION
    (d / 'probe.v').write_text('''module probe(input [20:0] accumulator,input [15:0] sample,
output [20:0] old_value,new_value,bad_value);
localparam ACCUM_W=21;
''' + function + '''
assign old_value=$signed(accumulator)+$signed({{5{sample[15]}},sample});
assign new_value=accum_add_s16(accumulator,sample);
assign bad_value=accumulator+{5'd0,sample};
endmodule
''')
    (d / 'main.cpp').write_text(r'''#include "Vprobe.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
static uint32_t r=0x7321468d;static uint32_t rng(){r^=r<<13;r^=r>>17;r^=r<<5;return r;}
int main(){Vprobe d;uint64_t checks=0,negative=0;
auto check=[&](){d.eval();checks++;uint32_t ref=(d.accumulator+uint32_t(int32_t(int16_t(d.sample))))&0x1fffff;
if(d.old_value!=ref||d.new_value!=ref){printf("FAIL %u %u %u %u %u\n",d.accumulator,d.sample,d.old_value,d.new_value,ref);exit(1);}negative+=d.bad_value!=ref;};
for(unsigned s=0;s<65536;s++)for(unsigned hi=0;hi<32;hi++){
unsigned lows[]={0,1,65535,s,s^65535,(-s)&65535,(-s-1)&65535,(-s+1)&65535};
for(unsigned lo:lows){d.accumulator=(hi<<16)|lo;d.sample=s;check();}}
for(unsigned i=0;i<1000000;i++){d.accumulator=rng()&0x1fffff;d.sample=rng();check();}
unsigned upper_cases=0;for(unsigned h=0;h<32;h++)for(unsigned sign=0;sign<2;sign++)for(unsigned carry=0;carry<2;carry++){
unsigned selected=carry==sign?h:carry?(h+1)&31:(h-1)&31;
if(selected!=((h+carry-sign)&31))return 2;upper_cases++;}
if(!negative)return 3;
printf("{\"passed\":true,\"literal_cases\":%llu,\"upper_control_cases\":%u,\"negative_unsigned_sample_mismatches\":%llu}\n",(unsigned long long)checks,upper_cases,(unsigned long long)negative);
}
''')
    cmd = ['verilator', '--cc', '--exe', '--build', '--assert', '-j', '3', '-Wno-fatal', '-Wno-WIDTH',
           '--top-module', 'probe', '--Mdir', str(d / 'obj'), str(d / 'probe.v'), str(d / 'main.cpp')]
    save(d / 'command.json', cmd)
    run(cmd, d / 'build.log')
    run([str(d / 'obj/Vprobe')], d / 'run.log')
    result = json.loads((d / 'run.log').read_text())
    assert result['passed'] and result['upper_control_cases'] == 128
    save(d / 'result.json', result)
    print(result)


def native():
    d = OUT / 'native'
    d.mkdir(exist_ok=False)
    f = d / 'frozen'
    f.mkdir()
    inputs = {}
    for name, origin in {
        'audio_mixer.v': OUT / 'audio_mixer.v',
        'sync_fifo.v': CORE / 'frozen/src/fpga/common/sync_fifo.v',
        'mister_audio_output.v': ROOT / 'src/fpga/targets/mister/mister_audio_output.v',
        'tb_audio_mixer_main.cpp': ROOT / 'src/fpga/test/tb_audio_mixer_main.cpp',
    }.items():
        shutil.copy2(origin, f / name)
        inputs[str(origin.relative_to(ROOT))] = sha(origin)
    old = (OUT / 'baseline.v').read_text()
    assert old.count('module audio_mixer (') == 1
    (f / 'audio_mixer_reference.v').write_text(old.replace('module audio_mixer (', 'module audio_mixer_reference ('))
    tb_source = ROOT / 'src/fpga/test/tb_audio_mixer.v'
    inputs[str(tb_source.relative_to(ROOT))] = sha(tb_source)
    tb = tb_source.read_text()
    begin = tb.index('    audio_mixer dut (')
    end = tb.index('\n    );', begin) + len('\n    );')
    ref = tb[begin:end].replace('audio_mixer dut (', 'audio_mixer_reference reference (')
    outputs = dict(voice_wr_ready=1, m_arvalid=1, m_araddr=32, m_arlen=8, m_rready=1,
                   sample_wr=1, sample_data=32, pos_readback=22, voice_end_pending=32,
                   voice_active_mask=32)
    declarations = ''
    checks = ''
    for name, bits in outputs.items():
        assert len(re.findall(r'\.' + name + r'\s*\(' + name + r'\)', ref)) == 1
        ref = re.sub(r'(\.' + name + r'\s*\()' + name + r'\)', r'\1reference_' + name + ')', ref)
        declarations += f' wire [{bits-1}:0] reference_{name};\n'
        checks += f'   assert({name} == reference_{name});\n'
    checks += '''   assert(dut.state==reference.state);
   assert(dut.cur_voice==reference.cur_voice);
   assert(dut.accum_l==reference.accum_l && dut.accum_r==reference.accum_r);
   assert(dut.voice_end_irq==reference.voice_end_irq);
'''
    monitor = '''
 reg [63:0] equivalence_cycles=0;
 always @(negedge clk) if(reset_n) begin
   equivalence_cycles<=equivalence_cycles+1;
''' + checks + ''' end
 final $display("MIXER_EQUIVALENCE_CYCLES %0d",equivalence_cycles);
'''
    assert tb.count('\nendmodule') == 1
    (f / 'tb_audio_mixer.v').write_text(tb.replace('\nendmodule', '\n' + declarations + ref + monitor + '\nendmodule'))
    sources = [f / name for name in ['tb_audio_mixer.v', 'audio_mixer.v', 'audio_mixer_reference.v', 'sync_fifo.v', 'mister_audio_output.v']]
    cmd = ['verilator', '--cc', '--exe', '--build', '--assert', '-j', '4', '-Wno-fatal', '-Wno-WIDTH',
           '-Wno-UNOPTFLAT', '--top-module', 'tb_audio_mixer', '--Mdir', str(d / 'obj'),
           '-CFLAGS', '-O2'] + list(map(str, sources)) + [str(f / 'tb_audio_mixer_main.cpp')]
    save(d / 'command.json', cmd)
    save(d / 'inputs.json', inputs)
    run(cmd, d / 'build.log')
    env = dict(os.environ, AUDIO_PERF_REPORT='1')
    run([str(d / 'obj/Vtb_audio_mixer')], d / 'run.log', env)
    run([str(d / 'obj/Vtb_audio_mixer'), '--throughput'], d / 'throughput.log', env)
    text = (d / 'run.log').read_text()
    assert '0 failed' in text
    count = re.search(r'=== Results: (\d+) passed, 0 failed', text)
    trace = re.search(r'PERF audio samples (\d+) trace ([0-9a-f]+)', text)
    assert count and trace
    save(d / 'result.json', dict(passed=True, assertions_passed=int(count[1]),
        exact_live_outputs_and_accumulators=True, samples=int(trace[1]), cycle_data_trace=trace[2],
        throughput_passed=True, scope='Full actual mixer RTL miter under existing Pocket/MMIO/stream/reset/ramp/adversarial and throughput stimuli.'))
    print((d / 'result.json').read_text())


def seal():
    literal_result = json.loads((OUT / 'literal/result.json').read_text())
    native_result = json.loads((OUT / 'native/result.json').read_text())
    assert literal_result['passed'] and native_result['passed']
    save(OUT / 'result.json', dict(passed=True, candidate_sha256=sha(OUT / 'audio_mixer.v'),
        added_registers=0, added_cycles=0, voices=32, arithmetic_modulus_bits=21,
        literal=literal_result, native=native_result, timing_gain_not_measured=True))
    (OUT / 'REPORT.txt').write_text('''Exact 21-bit audio accumulator carry split: private proof

Only the two saturated-sample accumulation expressions change, through one
combinational helper. The low 16 bits use an unsigned 17-bit sum. The upper
five bits select an early accumulator-upper +1, unchanged or -1 result from
{sample sign, low carry}. Multiplies, interpolation, saturation, all registers,
32 voices, reset, scheduling, AXI and sample emission are unchanged.

For a=H*65536+L and 16-bit sample bits S with sign s, the signed contribution
is S-s*65536. Let L+S=c*65536+R. The old sum modulo2^21 is
((H+c-s) modulo32)*65536+R. The helper's four sign/carry cases implement
exactly that identity, including accumulator wrap, for every binary input.
This does not rely on a reachable signal-range or audio-content assumption.

The literal extracted RTL is checked on every16-bit sample, all32 accumulator
upper values and8 lower-boundary patterns, plus1,000,000 random full inputs.
All128 upper/sign/carry cases are independently exhaustive. A negative control
that zero-extends negative samples is detected. These bounded RTL checks
complement the full-domain algebra; they are not a2^37 brute-force claim.

The actual candidate and original mixers run side by side under the existing
MMIO, volume/ramp, IRQ, stereo, stream starvation/resume, mutable voice state,
reset/rearm, SDRAM-stall and MiSTer pacing test stimuli. Every live output,
AXI address/length/handshake, FSM/voice and accumulator compares each cycle.
The32-voice throughput campaign also runs the same live miter. PCM values and
their exact emission cycles remain unchanged; no new native CPU game claim.

The measured prior path is DSP4.169ns +route3.228ns then a21-bit carry chain.
The intended structural change shortens the late carry to16 bits and selects
precomputed upper values. Synthesis/routing may choose differently: no timing
gain, area gain, mapper, fitter or configured-clock closure is claimed here.
Only private files were created; no production, sibling or SD write occurred.
''')
    refs = [CORE / 'prepared.json', CORE / 'final-manifest.json',
            P / 'compact-timing-audit-v11/manifest.json', BASE, Path(__file__)]
    save(OUT / 'manifest.json', dict(
        files={str(x.relative_to(OUT)): sha(x) for x in OUT.rglob('*') if x.is_file() and 'obj' not in x.parts},
        evidence_sha256={str(x.relative_to(ROOT)): sha(x) for x in refs}))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    globals()[sys.argv[1]]()
