#!/usr/bin/env python3
"""Factor only the current os30 decode2-to3 ready link; literal cone proof."""
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
O = P / 'cpu-decode-ready-v1'
PARENT = '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
D = 'DispatchPlugin_logic_'
C = D + 'candidates_0_'
H = [D + f'rsHazardChecker_0_onLl_0_onRs_{i}_hazard' for i in range(3)]
Y = 'decode_ctrls_2_down_ready'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, d):
    with p.open('x') as f:
        json.dump(d, f, indent=2, sort_keys=True); f.write('\n')


def prepare():
    assert sha(B) == PARENT
    s = B.read_text()
    old = ('  always @(*) begin\n    decode_ctrls_2_down_ready = decode_ctrls_3_up_ready;\n'
           "    if(when_StageLink_l71_4) begin\n      decode_ctrls_2_down_ready = 1'b1;\n    end\n  end\n")
    new = ('  // Same stage2-to3 ready condition, with RS3 hazard entering last.\n'
        '  (* keep *) wire os30_decode2_ready_free = !decode_ctrls_3_up_isValid ||\n'
        '      !decode_ctrls_3_up_LANE_SEL_0 || DispatchPlugin_logic_feeds_0_sent;\n'
        f'  (* keep *) wire os30_decode2_ready_allow = !{C}flushHazards &&\n'
        f'      !{C}fenceOlderHazards && {C}ctx_laneLayerHits[0] &&\n'
        f'      !{C}reservationHazards[0] && !execute_freeze_valid && !DispatchPlugin_api_haltDispatch &&\n'
        f'      !{H[0]} && !{H[1]};\n'
        f'  always @(*) begin\n    {Y} = os30_decode2_ready_free ||\n'
        f'        (os30_decode2_ready_allow && !{H[2]});\n  end\n')
    assert s.count(old) == 1
    candidate = s.replace(old, new, 1)
    decls = lambda t: re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$', t, re.M)
    assert decls(candidate) == decls(s)
    O.mkdir(exist_ok=False)
    shutil.copyfile(B, O/'baseline.v'); shutil.copyfile(__file__, O/'recipe.py')
    (O/'VexiiRiscv.v').write_text(candidate)
    save(O/'hunk.json',dict(old=old,new=new))
    (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),candidate.splitlines(True),fromfile='82b7/VexiiRiscv.v',tofile='decode-ready/VexiiRiscv.v')))
    # Stop at independent hazard/qualification inputs; preserve the original
    # generated scheduler, fire, stall and stage-link logic in the literal cone.
    primary = ['decode_ctrls_3_up_isValid','decode_ctrls_3_up_LANE_SEL_0',D+'feeds_0_sent',
        C+'flushHazards',C+'fenceOlderHazards',C+'ctx_laneLayerHits',C+'reservationHazards',
        'execute_freeze_valid','DispatchPlugin_api_haltDispatch'] + H
    nodes = [C+'rsHazards[0]',C+'ctx_valid','when_DispatchPlugin_l373',
        D+'scheduler_eusFree_0',D+'scheduler_hartFree_0',D+'scheduler_arbiters_0_candHazard',
        D+'scheduler_arbiters_0_layersHits',D+'scheduler_arbiters_0_layersHits_bools_0',
        '_zz_'+D+'scheduler_arbiters_0_layerOh[0]',D+'scheduler_arbiters_0_layerOh',
        D+'scheduler_arbiters_0_doIt',C+'fire','decode_ctrls_3_up_ready',
        'decode_ctrls_3_down_isReady','when_StageLink_l71_4']
    lines = [re.search(r'^  assign '+re.escape(n)+r' = .*;\n',s,re.M).group() for n in nodes]
    ctrl3 = re.search(r'  always @\(\*\) begin\n    decode_ctrls_3_down_ready = .*?\n  end\n',s,re.S).group()
    common = ''.join(lines) + ctrl3
    for name, value in [(D+'scheduler_eusFree_0',"1'b1"),(D+'scheduler_hartFree_0',"1'b1"),(D+'scheduler_arbiters_0_candHazard',"1'b0")]:
        assert f'  assign {name} = {value};\n' in common
    save(O/'cone.json',dict(primary_inputs=primary,source_assignments=lines,source_ready3_block=ctrl3,
        arbitrary_input_count=len(primary),lemma='R2 = !V || !G || (V && G && E && !H0 && !H1 && !H2) = (!V || !G) || ((E && !H0 && !H1) && !H2); G=lane&&!sent',
        unchanged='Original fire/issue/insert/hazard/cancel/ready3 and every sequential block'))
    declarations = re.findall(r'^  (?:wire|reg)\s+[^;]+;\n',s,re.M)
    used = common + old + new + ' '.join(primary)
    selected = [line for line in declarations if re.search(r'\b'+re.escape(re.search(r'(\w+);',line).group(1))+r'\b',used)]
    d=O/'literal';d.mkdir(); modules=[]
    for kind, body in [('old',old),('new',new),('bad',new.replace('!'+H[2],"1'b1"))]:
        modules.append(f'module ready_{kind}(input [11:0] controls,output result);\n'+''.join(selected)+
            ''.join(f'  assign {name} = controls[{i}];\n' for i,name in enumerate(primary))+
            common+body+f'  assign result = {Y};\nendmodule\n')
    modules.append('module proof(input [11:0] controls,output old_y,new_y,bad_y);\nready_old o(controls,old_y);ready_new n(controls,new_y);ready_bad b(controls,bad_y);\nendmodule\n')
    (d/'proof.v').write_text(''.join(modules))
    (d/'main.cpp').write_text(r'''#include "Vproof.h"
#include "verilated.h"
#include <cstdio>
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);Vproof t;unsigned checks=0,negative=0;
 for(unsigned c=0;c<4096;c++){t.controls=c;t.eval();if(t.old_y!=t.new_y){std::fprintf(stderr,"mismatch %u\n",c);return 1;}negative+=(t.old_y!=t.bad_y);checks++;}
 if(!negative)return 2;
 std::printf("{\"passed\":true,\"actual_extracted_RTL\":true,\"two_state_only\":true,\"checks\":%u,\"negative_control_failures\":%u}\n",checks,negative);
}
''')
    save(O/'prepared.json',dict(parent_sha256=PARENT,candidate_sha256=sha(O/'VexiiRiscv.v'),recipe_sha256=sha(Path(__file__)),
        new_state=False,new_cycles=False,scope='Single stage2-to3 ready block; exact RS3-late Boolean factor. No original scheduler, hazard, fire or control update changes.',
        native_integration_pending=True,physical_timing_unmeasured=True,production_modified=False))
    print('SOURCE',sha(O/'VexiiRiscv.v'))


def prove():
    prep=json.loads((O/'prepared.json').read_text());assert sha(O/'VexiiRiscv.v')==prep['candidate_sha256'] and sha(Path(__file__))==prep['recipe_sha256']
    d=O/'literal'
    commands=dict(lint=['verilator','--lint-only','--Wno-fatal','--top-module','VexiiRiscv',str(O/'VexiiRiscv.v')],
        build=['verilator','--cc','--exe','--build','-j','4','--Wno-fatal','--top-module','proof','--Mdir',str(d/'obj'),str(d/'proof.v'),str(d/'main.cpp')])
    save(O/'commands.json',commands)
    for label,cmd in commands.items():
        with (O/(label+'.log')).open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    run=subprocess.run([str(d/'obj/Vproof')],text=True,capture_output=True,check=True)
    with (O/'proof.log').open('x') as f:f.write(run.stdout+run.stderr)
    data=json.loads(run.stdout);assert data['passed'] and data['checks']==4096 and data['negative_control_failures']>0
    save(O/'result.json',data)
    files=[Path(__file__)]+[p for p in O.iterdir() if p.is_file()]+[d/'proof.v',d/'main.cpp']
    save(O/'manifest.json',dict(passed=True,native_and_physical_pending=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}))
    print(run.stdout.strip());print('MANIFEST',sha(O/'manifest.json'))


if __name__=='__main__':
    assert len(sys.argv)==2 and sys.argv[1] in ['prepare','prove']
    globals()[sys.argv[1]]()
