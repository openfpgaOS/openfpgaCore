#!/usr/bin/env python3
"""Private clip-write default factoring; preserve all owned RAM writes and cycles."""
import difflib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P = CAMP / 'phase10'
CORE = P / 'compact-lod5-core-v9'
BASE = CORE / 'frozen/src/fpga/common/geometry_raw_load.v'
FIXTURE = P / 'unified-load-components-v3'
OUT = P / 'geometry-clip-write-v1'


def block(text):
    start = text.index(' always @*begin\n  attribute_write_address=0;')
    end = text.index('`ifdef VERILATOR', start)
    return text[start:end]


def rename(text, prefix):
    for name in ('address', 'data', 'bytes'):
        text = text.replace('attribute_write_' + name, prefix + '_' + name)
    return text


def prepare():
    old = BASE.read_text()
    a = 'attribute_write_address=0;attribute_write_data=0;attribute_write_bytes=0;'
    b = 'attribute_write_data={26\'b0,classification};attribute_write_bytes=4\'b1111;'
    assert old.count(a) == old.count(b) == 1
    new = old.replace(a, 'attribute_write_address=0;attribute_write_data={26\'b0,classification};attribute_write_bytes=0;')
    new = new.replace(b, 'attribute_write_bytes=4\'b1111;')
    assert old[:old.index(block(old))] == new[:new.index(block(new))]
    assert old[old.index('`ifdef VERILATOR', old.index(block(old))):] == new[new.index('`ifdef VERILATOR', new.index(block(new))):]
    OUT.mkdir(exist_ok=False)
    shutil.copy2(BASE, OUT / 'baseline.v')
    (OUT / 'geometry_raw_load.v').write_text(new)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile='compact-v9/geometry_raw_load.v',
        tofile='geometry-clip-write-v1/geometry_raw_load.v')))
    shutil.copytree(FIXTURE / 'frozen', OUT / 'frozen')
    closure = {}
    for file in sorted((OUT / 'frozen').glob('*.v')):
        parent = CORE / 'frozen/src/fpga/common' / file.name
        if parent.exists():
            assert sha(parent) == sha(file), file.name
            closure[file.name] = sha(parent)
    assert len(closure) == 11
    shutil.copy2(OUT / 'geometry_raw_load.v', OUT / 'frozen/geometry_raw_load.v')
    write(OUT / 'prepared.json', dict(
        parent_prepared_sha256=sha(CORE / 'prepared.json'),
        parent_final_manifest_sha256=sha(CORE / 'final-manifest.json'),
        parent_source_sha256=sha(BASE), candidate_sha256=sha(OUT / 'geometry_raw_load.v'),
        fixture_manifest_sha256=sha(FIXTURE / 'manifest.json'),
        selected_parent_rtl_sha256=closure, only_changed_rtl=['geometry_raw_load.v'],
        added_state=0, added_cycles=0, arithmetic_unchanged=True, ABI_unchanged=True,
        difference_only_when_attribute_write_bytes_zero=True, no_fit=True))
    shutil.copy2(__file__, OUT / 'recipe.py')
    print(sha(OUT / 'geometry_raw_load.v'))


def proof():
    d = OUT / 'proof'
    d.mkdir(exist_ok=False)
    old, new = (OUT / 'baseline.v').read_text(), (OUT / 'geometry_raw_load.v').read_text()
    hdr = '''module probe(input word_valid,input[2:0]raw_word,input[31:0]word_data,
 input[31:0]u_product,v_product,input[4:0]global_raw_vertex,input[15:0]normal_xy,
 input attribute_rgb_write,input[4:0]attribute_rgb_addr,input[7:0]attribute_rgb_data,
 input[1:0]attribute_rgb_channel,input[2:0]state,input read_valid,input[1:0]read_row,
 input[4:0]job_dest,read_vertex,input[5:0]classification,
 output reg[6:0]old_address,new_address,bad_address,
 output reg[31:0]old_data,new_data,bad_data,
 output reg[3:0]old_bytes,new_bytes,bad_bytes);
 localparam SCAN=3'd3;
'''
    bad = block(new).replace('attribute_write_data={4{attribute_rgb_data}};',
                             'attribute_write_data=0;')
    assert bad != block(new)
    (d / 'probe.sv').write_text(hdr + rename(block(old), 'old') +
                               rename(block(new), 'new') + rename(bad, 'bad') + 'endmodule\n')
    (d / 'main.cpp').write_text(r'''#include "Vprobe.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
static uint32_t seed=0xc1205a31;
static uint32_t rnd(){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
int main(){Vprobe d;uint64_t checks=0,owned=0,idle_difference=0,negative=0;
 auto test=[&](){d.eval();checks++;uint32_t mask=0;
  for(int b=0;b<4;b++)if(d.old_bytes&(1u<<b))mask|=0xffu<<(b*8);
  if(d.old_bytes!=d.new_bytes||d.old_address!=d.new_address||((d.old_data^d.new_data)&mask)){
   printf("FAIL %llu\n",(unsigned long long)checks);std::exit(1);}
  owned+=!!mask;idle_difference+=!mask&&d.old_data!=d.new_data;
  negative+=!!((d.old_data^d.bad_data)&mask);
 };
 auto payload=[&](){d.word_data=rnd();d.u_product=rnd();d.v_product=rnd();
  d.global_raw_vertex=rnd()&31;d.normal_xy=rnd();d.attribute_rgb_addr=rnd()&31;
  d.attribute_rgb_data=rnd();d.job_dest=rnd()&31;d.read_vertex=rnd()&31;};
 for(unsigned b=0;b<(1u<<19);b++){
  d.word_valid=b&1;d.raw_word=(b>>1)&7;d.attribute_rgb_write=(b>>4)&1;
  d.state=(b>>5)&7;d.read_valid=(b>>8)&1;d.read_row=(b>>9)&3;
  d.attribute_rgb_channel=(b>>11)&3;d.classification=(b>>13)&63;
  payload();test();
 }
 for(unsigned i=0;i<1000000;i++){
  unsigned b=rnd();d.word_valid=b&1;d.raw_word=(b>>1)&7;d.attribute_rgb_write=(b>>4)&1;
  d.state=(b>>5)&7;d.read_valid=(b>>8)&1;d.read_row=(b>>9)&3;
  d.attribute_rgb_channel=(b>>11)&3;d.classification=(b>>13)&63;
  payload();test();
 }
 if(!idle_difference||!negative)return 2;
 printf("{\"passed\":true,\"checks\":%llu,\"all_control_and_classification_cases\":524288,\"owned_writes\":%llu,\"unowned_data_differences\":%llu,\"negative_missing_lighting_override\":%llu}\n",
  (unsigned long long)checks,(unsigned long long)owned,
  (unsigned long long)idle_difference,(unsigned long long)negative);
}
''')
    cmd = ['verilator', '--cc', '--exe', '--build', '--assert', '-j', '3',
           '-Wno-fatal', '-Wno-WIDTH', '--top-module', 'probe', '--Mdir', str(d / 'obj'),
           str(d / 'probe.sv'), str(d / 'main.cpp')]
    write(d / 'build-command.json', cmd)
    with (d / 'build.log').open('x') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
    run = subprocess.run([str(d / 'obj/Vprobe')], capture_output=True, text=True, check=True)
    (d / 'run.log').write_text(run.stdout + run.stderr)
    result = json.loads(run.stdout)
    assert result['passed'] and result['negative_missing_lighting_override'] > 0
    write(d / 'result.json', result)
    print(run.stdout)


def wrapper():
    f = OUT / 'frozen'
    d = OUT / 'wrapper'
    d.mkdir(exist_ok=False)
    new = (f / 'geometry_raw_load.v').read_text()
    old_block = rename(block((OUT / 'baseline.v').read_text()), 'reference_attribute_write')
    monitor = '''
 reg [6:0] reference_attribute_write_address;
 reg [31:0] reference_attribute_write_data;
 reg [3:0] reference_attribute_write_bytes;
''' + old_block + '''
 always @(posedge clk) if(!reset)begin
  assert(attribute_write_address==reference_attribute_write_address);
  assert(attribute_write_bytes==reference_attribute_write_bytes);
  if(attribute_write_bytes[0])assert(attribute_write_data[7:0]==reference_attribute_write_data[7:0]);
  if(attribute_write_bytes[1])assert(attribute_write_data[15:8]==reference_attribute_write_data[15:8]);
  if(attribute_write_bytes[2])assert(attribute_write_data[23:16]==reference_attribute_write_data[23:16]);
  if(attribute_write_bytes[3])assert(attribute_write_data[31:24]==reference_attribute_write_data[31:24]);
 end
'''
    pos = new.rindex('endmodule')
    instrumented = d / 'geometry_raw_load.v'
    instrumented.write_text(new[:pos] + monitor + new[pos:])
    sources = [instrumented if x.name == 'geometry_raw_load.v' else x for x in sorted(f.glob('*.v'))]
    cmd = ['verilator', '--cc', '--exe', '--build', '--assert', '+define+GEOMETRY_SCAN_CHECKS',
           '-j', '4', '-Wno-fatal', '-Wno-WIDTH', '--top-module', 'tb_load_alias',
           '--Mdir', str(d / 'obj'), '-CFLAGS', '-O2 -std=c++17'] + [str(x) for x in sources] + [str(f / 'phase10_load_alias_test.cpp')]
    write(d / 'build-command.json', cmd)
    with (d / 'build.log').open('x') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    with (d / 'run.log').open('x') as log:
        subprocess.run([str(d / 'obj/Vtb_load_alias'), str(f / 'native.bin')],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    result = json.loads((d / 'run.log').read_text().splitlines()[-1])
    parent = json.loads((FIXTURE / 'wrapper/run.log').read_text().splitlines()[-1])
    assert result == parent
    write(d / 'result.json', dict(**result, exact_parent_cycles_and_outputs=True,
                                 live_old_new_enabled_write_assertions=True))
    print(json.dumps(result))


def seal():
    proof = json.loads((OUT / 'proof/result.json').read_text())
    native = json.loads((OUT / 'wrapper/result.json').read_text())
    assert proof['passed'] and native['passed'] and native['exact_parent_cycles_and_outputs']
    prep = json.loads((OUT / 'prepared.json').read_text())
    assert prep['candidate_sha256'] == sha(OUT / 'geometry_raw_load.v')
    assert sha(OUT / 'geometry_raw_load.v') == sha(OUT / 'frozen/geometry_raw_load.v')
    result = dict(passed=True, candidate_sha256=prep['candidate_sha256'],
                  only_changed_rtl=['geometry_raw_load.v'], added_state=0, added_cycles=0,
                  clip_arithmetic_unchanged=True, all_owned_RAM_writes_exact=True,
                  literal_block_proof=proof, native_wrapper=native,
                  timing_gain_not_yet_measured=True, no_map=True, no_fit=True,
                  deployment_ready=False)
    write(OUT / 'result.json', result)
    (OUT / 'REPORT.txt').write_text('''Clip RAM write-data default factoring: private component proof

Only the default value of attribute_write_data changes, from zero to the
existing {26'b0,classification}. The SCAN arm's redundant data assignment is
removed. Its address and byte enables are unchanged. UV, raw RGBA and lighting
branches still fully override data with original priority. Every enabled byte
write is identical. Changed values occur only when all write enables are zero,
so the physical byte-enabled attribute RAM cannot observe them.

The floating comparison function, clip_pair/classification, W-first scan order,
all read/write ownership, final-write/done edge, reset, ABI and source range
remain byte-identical. There is no numerical change or new state/cycle.

Literal old/new combinational blocks pass all 524,288 combinations of producer
controls and all 64 classifications, plus one million random payload cases.
Address, byte enables and every enabled data byte compare exactly. A missing
lighting override negative control fails, and unowned data differences are
observed, establishing that the test distinguishes the changed don't-care.

The unchanged native wrapper fixture passes 20 jobs/230 native vertices (145
independently qualified RGBs), plus 528 synthetic vertices covering counts
1..32, source/error ranges, alias backpressure, start/read ownership, CPU and
fabric reset, rejected writes, and normal passthrough. All 17,248 alias words
and total 58,942 cycles match the exact selected-source parent fixture. Live
old/new write-address, byte-enable and owned-data assertions are enabled.

This removes a redundant last-stage SCAN data selector on the measured acc RAM
to clip-byte RAM input path. It establishes behavior, not routed timing gain.
No mapper, fitter, clock/settings change or production edit was performed.
''')
    shutil.copy2(__file__, OUT / 'final-recipe.py')
    evidence = [CORE / 'final-manifest.json', CORE / 'prepared.json',
                P / 'compact-timing-audit-v9/manifest.json', FIXTURE / 'manifest.json',
                FIXTURE / 'wrapper/run.log', Path(__file__)]
    write(OUT / 'manifest.json', dict(
        files={str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*'))
               if p.is_file() and 'obj' not in p.relative_to(OUT).parts and p.name != 'manifest.json'},
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence}))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    assert sys.argv[1] in ('prepare', 'proof', 'wrapper', 'seal')
    globals()[sys.argv[1]]()
