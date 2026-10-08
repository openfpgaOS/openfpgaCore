#!/usr/bin/env python3
"""Private exact split of the measured ctrl9 FP RS3 tag comparator; no CAD."""
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
B = P / 'cpu-load-forward-v1/VexiiRiscv.v'
O = P / 'cpu-fp-rs3-tag-v1'
PARENT = '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
E = 'execute_lane0_bypasser_float_RS3_bypassEnables'
RD = 'execute_ctrl9_down_RD_PHYS_lane0'
RS = 'execute_ctrl1_down_RS3_PHYS_lane0'
RF = 'execute_ctrl9_down_RD_RFID_lane0'
SF = 'execute_ctrl1_down_RS3_RFID_lane0'
LANE = 'execute_ctrl9_up_LANE_SEL_lane0'
ENABLE = 'execute_ctrl9_up_RD_ENABLE_lane0'
LO = 'os30_fp_rs3_ctrl9_tag_lo_eq'
HI = 'os30_fp_rs3_ctrl9_tag_hi_eq'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, d):
    with p.open('x') as f:
        json.dump(d, f, indent=2, sort_keys=True)
        f.write('\n')


def prepare():
    assert sha(B) == PARENT
    source = B.read_text()
    old = f'    {E}[5] = ((({LANE} && {ENABLE}) && ({RD} == {RS})) && ({RF} == {SF}));\n'
    new = f'    {E}[5] = (({LANE} && {ENABLE}) && {LO} && {HI});\n'
    anchor = '  always @(*) begin\n' + f'    {E}[0]'
    wires = ('  // Exact six-bit tag match; retain all ownership and forwarding priority.\n'
        f'  (* keep *) wire {LO} = ({RD}[2:0] == {RS}[2:0]);\n'
        f'  (* keep *) wire {HI} = ({{{RF}, {RD}[4:3]}} == {{{SF}, {RS}[4:3]}});\n')
    assert source.count(old) == source.count(anchor) == 1
    candidate = source.replace(anchor, wires + anchor, 1).replace(old, new, 1)
    declarations = lambda s: re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$', s, re.M)
    assert declarations(source) == declarations(candidate)
    O.mkdir(exist_ok=False)
    shutil.copyfile(B, O / 'baseline.v')
    shutil.copyfile(__file__, O / 'recipe.py')
    (O / 'VexiiRiscv.v').write_text(candidate)
    save(O / 'hunks.json', dict(wires=dict(old=anchor, new=wires+anchor), enable=dict(old=old, new=new)))
    (O / 'candidate.patch').write_text(''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True),
        fromfile='82b7/VexiiRiscv.v', tofile='rs3-tag/VexiiRiscv.v')))
    save(O / 'prepared.json', dict(parent_sha256=PARENT, candidate_sha256=sha(O/'VexiiRiscv.v'),
        recipe_sha256=sha(Path(__file__)), new_state=False, new_cycles=False,
        scope='Only ctrl9 float RS3 match: kept3-bit equality of low physical tags and kept3-bit equality of high physical tags plus register-file ID. Lane/RD ownership, all other enables and every payload/priority expression unchanged.',
        measured_path='cold ctrl9 RD_PHYS[3] to ctrl2 float_RS3[26],86.723MHz,8logic,4cells before53-fanout match-derived control',
        native_integration_pending=True, physical_timing_unmeasured=True, production_modified=False))
    d = O / 'literal'; d.mkdir()
    modules = []
    for name, body in [('old', old), ('new', wires + new), ('bad', wires.replace(f'{{{RF}, {RD}[4:3]}} == {{{SF}, {RS}[4:3]}}', f'{RD}[4:3] == {RS}[4:3]') + new)]:
        for a, b in [(E+'[5]', 'y'), (RD,'rd'), (RS,'rs'), (RF,'rd_rf'), (SF,'rs_rf'), (LANE,'lane'), (ENABLE,'en')]:
            body = body.replace(a, b)
        # Convert just the literal combinational procedural assignment to an assign.
        body = body.replace('    y = ', '  assign y = ')
        modules.append(f'module tag_{name}(input [4:0] rd,rs,input rd_rf,rs_rf,lane,en,output y);\n{body}endmodule\n')
    modules.append('module proof(input [4:0] rd,rs,input rd_rf,rs_rf,lane,en,output old_y,new_y,bad_y);\n'
        'tag_old o(rd,rs,rd_rf,rs_rf,lane,en,old_y);tag_new n(rd,rs,rd_rf,rs_rf,lane,en,new_y);tag_bad b(rd,rs,rd_rf,rs_rf,lane,en,bad_y);\nendmodule\n')
    (d / 'proof.v').write_text(''.join(modules))
    (d / 'main.cpp').write_text(r'''#include "Vproof.h"
#include "verilated.h"
#include <cstdio>
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);Vproof t;unsigned checks=0,negative=0;
 for(unsigned v=0;v<(1u<<14);v++){
  t.rd=v&31;t.rs=(v>>5)&31;t.rd_rf=(v>>10)&1;t.rs_rf=(v>>11)&1;t.lane=(v>>12)&1;t.en=(v>>13)&1;t.eval();
  bool ref=t.lane&&t.en&&(t.rd==t.rs)&&(t.rd_rf==t.rs_rf);
  if(t.old_y!=t.new_y || t.new_y!=ref){std::fprintf(stderr,"mismatch %u\n",v);return 1;}
  negative+=(t.old_y!=t.bad_y);checks++;
 }
 if(negative!=64)return 2;
 std::printf("{\"passed\":true,\"actual_extracted_RTL\":true,\"two_state_only\":true,\"checks\":%u,\"negative_control_failures\":%u}\n",checks,negative);
}
''')
    print('SOURCE', sha(O/'VexiiRiscv.v'))


def prove():
    prep = json.loads((O/'prepared.json').read_text())
    assert sha(O/'VexiiRiscv.v') == prep['candidate_sha256'] and sha(Path(__file__)) == prep['recipe_sha256']
    d = O/'literal'
    commands = dict(lint=['verilator','--lint-only','--Wno-fatal','--top-module','VexiiRiscv',str(O/'VexiiRiscv.v')],
        build=['verilator','--cc','--exe','--build','-j','4','--Wno-fatal','--top-module','proof','--Mdir',str(d/'obj'),str(d/'proof.v'),str(d/'main.cpp')])
    save(O/'commands.json', commands)
    for label, cmd in commands.items():
        with (O/(label+'.log')).open('x') as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
    run = subprocess.run([str(d/'obj/Vproof')], text=True, capture_output=True, check=True)
    with (O/'proof.log').open('x') as f: f.write(run.stdout + run.stderr)
    data = json.loads(run.stdout)
    assert data['passed'] and data['checks'] == 16384 and data['negative_control_failures'] == 64
    save(O/'result.json', data)
    files = [Path(__file__)] + [p for p in O.iterdir() if p.is_file()] + [d/'proof.v', d/'main.cpp']
    save(O/'manifest.json', dict(passed=True, native_and_physical_pending=True,
        evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}))
    print(run.stdout.strip()); print('MANIFEST', sha(O/'manifest.json'))


if __name__ == '__main__':
    assert len(sys.argv) == 2 and sys.argv[1] in ['prepare','prove']
    globals()[sys.argv[1]]()
