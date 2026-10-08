#!/usr/bin/env python3
"""Balance only the measured os30 branch-target equality tree; private proof."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil
import subprocess
import sys

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
B = P / 'cpu-load-forward-v1/VexiiRiscv.v'
O = P / 'cpu-btb-balanced-v2'
PARENT = '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
A = 'execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0'
C = 'execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0'
Y = 'execute_ctrl2_down_early0_BranchPlugin_logic_alu_btb_BAD_TARGET_lane0'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, d):
    with p.open('x') as f:
        json.dump(d, f, indent=2, sort_keys=True)
        f.write('\n')


def prepare():
    assert sha(B) == PARENT
    s = B.read_text()
    old = f'  assign {Y} = ({A} != {C});\n'
    assert s.count(old) == 1
    new = ('  // Bound the equality reduction depth; target calculation and pipeline stay unchanged.\n'
           '  (* keep *) wire [7:0] os30_btb_target_eq4;\n'
           '  (* keep *) wire [1:0] os30_btb_target_eq16;\n')
    for i in range(8):
        new += f'  assign os30_btb_target_eq4[{i}] = ({A}[{4*i+3}:{4*i}] == {C}[{4*i+3}:{4*i}]);\n'
    new += ('  assign os30_btb_target_eq16[0] = &os30_btb_target_eq4[3:0];\n'
            '  assign os30_btb_target_eq16[1] = &os30_btb_target_eq4[7:4];\n'
            f'  assign {Y} = !(&os30_btb_target_eq16);\n')
    candidate = s.replace(old, new, 1)
    O.mkdir(exist_ok=False)
    (O / 'VexiiRiscv.v').write_text(candidate)
    shutil.copyfile(B, O / 'baseline.v')
    shutil.copyfile(__file__, O / 'recipe.py')
    save(O / 'hunk.json', {'old': old, 'new': new})
    (O / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        s.splitlines(True), candidate.splitlines(True), fromfile='82b7/VexiiRiscv.v', tofile='balanced/VexiiRiscv.v')))
    save(O / 'prepared.json', dict(parent_sha256=PARENT, candidate_sha256=sha(O/'VexiiRiscv.v'),
        recipe_sha256=sha(Path(__file__)), new_state=False, new_cycles=False,
        scope='Only branch BAD_TARGET equality: eight kept 4-bit comparisons, two kept four-way reductions, final two-way reduction. PC addition, branch control, pipeline and prediction state unchanged.',
        source_path_evidence='rvcpipe-gpu32-forward-texture-fit-v1 slow_85_setup: SRC1[1] through short target-adder leg then Equal237 chain to BAD_TARGET; 12 total logic levels.',
        physical_timing_unmeasured=True, production_modified=False))
    d = O / 'literal'; d.mkdir()
    modules = []
    for kind, body in [('old', old), ('new', new), ('bad', new.replace(f'{A}[31:28] == {C}[31:28]', "1'b1"))]:
        body = body.replace(A, 'a').replace(C, 'b').replace(Y, 'y')
        modules.append(f'module btb_{kind}(input [31:0] a,b,output y);\n{body}endmodule\n')
    modules.append('module proof(input [31:0] a,b,output old_y,new_y,bad_y);\nbtb_old o(a,b,old_y);btb_new n(a,b,new_y);btb_bad m(a,b,bad_y);\nendmodule\n')
    (d / 'proof.v').write_text(''.join(modules))
    (d / 'main.cpp').write_text(r'''#include "Vproof.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
int main(int argc,char**argv){
 Verilated::commandArgs(argc,argv);Vproof t;uint64_t checks=0,negative=0;uint32_t state=0x91837abc;
 auto random=[&](){state^=state<<13;state^=state>>17;state^=state<<5;return state;};
 auto check=[&](uint32_t a,uint32_t b){t.a=a;t.b=b;t.eval();
  if(t.old_y!=t.new_y || t.new_y!=(a!=b)){std::fprintf(stderr,"mismatch %08x %08x\n",a,b);std::exit(1);}
  negative+=(t.old_y!=t.bad_y);checks++;
 };
 // Exhaust every local 4-bit input pair at every position and every
 // combination of matching/mismatching chunks through the reduction tree.
 for(unsigned pos=0;pos<8;pos++)for(unsigned a=0;a<16;a++)for(unsigned b=0;b<16;b++)
  for(uint32_t base: {uint32_t(0),UINT32_MAX}){
   uint32_t mask=15u<<(pos*4);check((base&~mask)|(a<<(pos*4)),(base&~mask)|(b<<(pos*4)));
  }
 for(unsigned pattern=0;pattern<256;pattern++)for(unsigned bit=0;bit<4;bit++){
  uint32_t b=0;for(unsigned pos=0;pos<8;pos++)if(pattern&(1u<<pos))b|=1u<<(pos*4+bit);check(0,b);check(UINT32_MAX,~b);
 }
 for(unsigned i=0;i<262144;i++){uint32_t a=random(),b=random();check(a,b);check(a,a);check(a,a^(1u<<(b&31)));}
 if(!negative)return 2;
 std::printf("{\"passed\":true,\"actual_extracted_RTL\":true,\"two_state_only\":true,\"checks\":%llu,\"negative_control_failures\":%llu}\n",(unsigned long long)checks,(unsigned long long)negative);
}
'''.replace('#include <cstdlib>', '#include <cstdlib>\n#include <initializer_list>'))
    print('SOURCE', sha(O/'VexiiRiscv.v'))


def prove():
    prep = json.loads((O/'prepared.json').read_text())
    assert sha(O/'VexiiRiscv.v') == prep['candidate_sha256'] and sha(Path(__file__)) == prep['recipe_sha256']
    d = O/'literal'
    commands = [
        ['verilator','--lint-only','--Wno-fatal','--top-module','VexiiRiscv',str(O/'VexiiRiscv.v')],
        ['verilator','--cc','--exe','--build','-j','4','--Wno-fatal','--top-module','proof','--Mdir',str(d/'obj'),str(d/'proof.v'),str(d/'main.cpp')],
    ]
    for label, cmd in zip(['lint','build'], commands):
        with (O/(label+'.log')).open('x') as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
    result = subprocess.run([str(d/'obj/Vproof')], text=True, capture_output=True, check=True)
    (O/'proof.log').write_text(result.stdout+result.stderr)
    data = json.loads(result.stdout); assert data['passed'] and data['negative_control_failures'] > 0
    save(O/'result.json', data)
    files = [Path(__file__), O/'prepared.json', O/'VexiiRiscv.v', O/'baseline.v', O/'recipe.py', O/'hunk.json', O/'candidate.patch', O/'result.json', O/'lint.log', O/'build.log', O/'proof.log', d/'proof.v', d/'main.cpp']
    save(O/'manifest.json', dict(passed=True, native_and_physical_pending=True,
        evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}))
    print(result.stdout.strip()); print('MANIFEST', sha(O/'manifest.json'))


if __name__ == '__main__':
    assert len(sys.argv) == 2 and sys.argv[1] in ['prepare','prove']
    globals()[sys.argv[1]]()
