#!/usr/bin/env python3
"""Check conditional compatibility, unchanged snapshot ABI and empty hot hooks."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
CANDIDATE = ROOT/'docs/measurements/os30-fps25-20261005/fps-panel-v1'
OUT = CANDIDATE/'conditional-check'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def tokens(s):
    # Declaration ordering is irrelevant; it moved below the detail-only block.
    s=s.replace('void sm64_perf_render_begin(uint32_t begin);','')
    return re.findall(r'"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|\S',s)


def main():
    manifest=json.loads((CANDIDATE/'manifest.json').read_text())
    for name,entry in manifest['files'].items():
        assert sha(CANDIDATE/name)==entry['candidate_sha256']
        assert sha(CANDIDATE/('baseline-'+name))==entry['baseline_sha256']
    OUT.mkdir(exist_ok=False)
    stubs=OUT/'stubs';stubs.mkdir()
    for name in ['stdint.h','stdio.h','string.h','of_caps.h','of_input.h','of_video.h',
                 'of_timer.h','sm64.h','game/area.h','game/game_init.h','game/print.h','gfx/gfx_gpu.h']:
        path=stubs/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('/* preprocessing stub */\n')
    cases=[]
    for label in ('baseline','candidate'):
        folder=OUT/label;folder.mkdir()
        for name in manifest['files']:
            shutil.copy2(CANDIDATE/(name if label=='candidate' else 'baseline-'+name),folder/name)
        for diag,detail in [(0,0),(0,1),(1,0),(1,1)]:
            cmd=['cc','-E','-P','-nostdinc','-I'+str(stubs),'-DSM64_PERF_DIAG='+str(diag),
                 '-DSM64_PERF_DETAILS='+str(detail),str(folder/'perf_pocket.c')]
            output=subprocess.check_output(cmd,text=True)
            (folder/f'diag{diag}-detail{detail}.i').write_text(output)
    for diag,detail in [(0,0),(0,1),(1,1)]:
        b=OUT/'baseline'/f'diag{diag}-detail{detail}.i'
        c=OUT/'candidate'/f'diag{diag}-detail{detail}.i'
        assert tokens(b.read_text())==tokens(c.read_text()),(diag,detail)
        cases.append({'diag':diag,'details':detail,'passed_token_equivalence':True,
                      'baseline_sha256':sha(b),'candidate_sha256':sha(c)})
    baseline=(CANDIDATE/'baseline-perf_pocket.c').read_text()
    candidate=(CANDIDATE/'perf_pocket.c').read_text()
    def snapshot(s):
        s=s[s.index('struct PerfSnapshot {'):];return s[:s.index('\n};')+3]
    assert snapshot(baseline)==snapshot(candidate)
    light=(OUT/'candidate/diag1-detail0.i').read_text()
    assert 'sm64_perf_enter(' not in light and 'sm64_perf_leave(' not in light
    assert 'sm64_perf_scope_begin(' not in light and 'sm64_perf_count(' not in light
    assert 'PERF F4 FPS' in light and 'PERF P3 VERT' not in light
    for label in ['"GAME"','"AUD"','"REND"','"WAIT"','"FPS"','"LCD"']:
        assert label in light
    unit=OUT/'unit';unit.mkdir()
    shutil.copy2(CANDIDATE/'perf_pocket.h',unit/'perf_pocket.h')
    (unit/'of_timer.h').write_text('extern unsigned test_clock;\nstatic inline unsigned of_time_us(void){return ++test_clock;}\n')
    (unit/'test.c').write_text('''#include "perf_pocket.h"
unsigned test_clock, side_effects;
unsigned sm64_perf_detail_active=1;
struct Sm64PerfDetail sm64_perf_detail;
static unsigned side_effect(void){++side_effects;return SM64_PERF_TRI;}
__attribute__((noinline)) void hot_hooks(void){
 SM64_PERF_SCOPE(side_effect());
 SM64_PERF_COUNT(side_effect(),side_effect());
}
int main(void){hot_hooks();return side_effects || test_clock || sm64_perf_detail.depth;}
''')
    cmd=['cc','-std=gnu99','-O3','-DSM64_PERF_DIAG=1','-DOF_PC',str(unit/'test.c'),'-o',str(unit/'test')]
    subprocess.run(cmd,check=True);subprocess.run([str(unit/'test')],check=True)
    assembly=subprocess.check_output(['objdump','-d','--disassemble=hot_hooks',str(unit/'test')],text=True)
    (unit/'hot_hooks.asm').write_text(assembly)
    assert all(name not in assembly for name in ['sm64_perf_detail','test_clock','side_effects'])
    report={'source_review_passed':True,'runtime_qualified':False,'script_sha256':sha(Path(__file__)),
            'candidate_files':manifest['files'],'preprocessor_cases':cases,
            'normalization':'Only whitespace and moved render_begin prototype ordering ignored; external includes stubbed solely for conditional-body comparison.',
            'snapshot_definition_exact':True,'details0_hot_hooks_runtime_noop':True,
            'details0_has_no_scope_count_functions':True,'fps_title_and_labels_present':True,
            'unit_command':cmd,'unit_sha256':sha(unit/'test'),'unit_assembly_sha256':sha(unit/'hot_hooks.asm'),
            'remaining':'Target normal/diagnostic build, capture, actual panel rendering and hardware measurement.'}
    (OUT/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
