#!/usr/bin/env python3
"""Remove diagnostic-only backend writes in a private all-off renderer build."""
import argparse, collections, difflib, json, re, shutil, subprocess
from pathlib import Path
from phase10_experiment import CAMP, ROOT, INPUTS, sha, write

P = CAMP / 'phase10'
PARENT = P / 'load-submit-overlap-v1'
OUT = P / 'backend-wire-counters-v1'
PAT = r'g_vc_(?:words|loads|tris_cached|tris_legacy)\s*(?:\+\+|\+=\s*[^;]+);'
COND = '(defined(SM64_PROFILE) && SM64_PROFILE) || (defined(SM64_PERF_DIAG) && SM64_PERF_DIAG)'

def parts(text):
    declarations = re.search(r'static uint32_t g_vc_words;[^\n]*\n(?:static uint32_t g_vc_[^\n]+\n){3}', text).group()
    getter = re.search(r'void gpu_vc_prof_get\([^}]+\}', text).group()
    return declarations, getter

def prepare():
    OUT.mkdir(exist_ok=False)
    for name in INPUTS:
        shutil.copy2(PARENT / name, OUT / name)
    old = (PARENT / 'gfx_gpu.c').read_text()
    declarations, getter = parts(old)
    newdecl = ('#if ' + COND + '\n#define GPU_VC_PROF(statement) do { statement; } while (0)\n' +
               declarations + '#else\n#define GPU_VC_PROF(statement) do { } while (0)\n#endif\n')
    newgetter = getter[:-1] + '#else\n    *words = *ctris = *ltris = *loads = 0;\n#endif\n}'
    newgetter = newgetter.replace('unsigned *loads) {\n', 'unsigned *loads) {\n#if ' + COND + '\n')
    assert '#if ' in newgetter
    updates = re.findall(PAT, old)
    assert len(updates) == 28, len(updates)
    new = re.sub(PAT, lambda m: 'GPU_VC_PROF(' + m[0][:-1] + ');', old)
    new = new.replace(declarations, newdecl).replace(getter, newgetter)
    # Structural reverse proof: no functional statement, kick, ordering, or
    # parent build option changed. Every original update remains when enabled.
    reversed_source = new.replace(newdecl, declarations).replace(newgetter, getter)
    reversed_source = re.sub(r'GPU_VC_PROF\((g_vc_[^;]+)\);', r'\1;', reversed_source)
    assert reversed_source == old
    assert new.count('GPU_VC_PROF(') == len(updates) + 2
    (OUT / 'gfx_gpu.c').write_text(new)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile='parent/gfx_gpu.c', tofile='candidate/gfx_gpu.c')))
    shutil.copy2(Path(__file__), OUT / 'recipe.py')
    write(OUT / 'source.json', dict(parent_elf_sha256=sha(PARENT / 'app.elf'),
        sources={n:sha(OUT/n) for n in INPUTS}, parent_sources={n:sha(PARENT/n) for n in INPUTS},
        only_changed_input='gfx_gpu.c', updates=updates, update_count=len(updates),
        per_counter=dict(collections.Counter(re.match(r'g_vc_\w+', s)[0] for s in updates)),
        reverse_reconstruction_byte_exact=True, helper_sha256=sha(Path(__file__)),
        gate=COND, kick_and_render_state_unchanged=True, no_lto=True))

def proof():
    dest = OUT / 'counter-proof'
    dest.mkdir(exist_ok=False)
    old = (PARENT / 'gfx_gpu.c').read_text()
    new = (OUT / 'gfx_gpu.c').read_text()
    declarations, getter = parts(old)
    gate = new[new.index('#if '+COND):new.index('/* Pre-scale on the 0x56')]
    gate = gate[:gate.rfind('#endif')+len('#endif')]
    newgetter = parts(new)[1]
    updates = re.findall(PAT, old)
    wrapped = re.findall(r'GPU_VC_PROF\((g_vc_[^;]+)\);', new)
    assert [s+';' for s in wrapped] == updates
    # Compile exact extracted declarations/update expressions/getter, with the
    # original expressions as oracle. All unsigned-overflow behavior is kept.
    pre = '#include <stdint.h>\n#include <stdio.h>\n#include <assert.h>\n'
    body = '''
static uint32_t seed=0xdeadbeefu;
static uint32_t rnd(void) {seed=seed*1664525u+1013904223u;return seed;}
int main(void) {
 unsigned words,ctris,ltris,loads,ow,oc,ol,ou;
 for(unsigned i=0;i<100000;i++) {
 uint32_t n=rnd(),count=rnd(),triangle_count=rnd();
 int num_tris=(int)rnd(),buf_vbo_num_tris=(int)rnd(),cd_on=rnd()&1;
 words=rnd();
 UPDATE;
 gpu_vc_prof_get(&ow,&oc,&ol,&ou);
 ORACLE;
 original_get(&words,&ctris,&ltris,&loads);
 if(SM64_PROFILE||SM64_PERF_DIAG)assert(ow==words&&oc==ctris&&ol==ltris&&ou==loads);
 else assert(!(ow|oc|ol|ou));
 gpu_vc_prof_get(&ow,&oc,&ol,&ou);assert(!(ow|oc|ol|ou));
 }
 puts("100000 update/reset iterations pass");return 0;
}
'''
    # Use a distinct output word variable because original update expressions
    # consume the local `words` value before the getter overwrites it.
    original = declarations + getter
    for name in ('words','loads','tris_cached','tris_legacy'):
        original = original.replace('g_vc_'+name, 'original_'+name)
    original = original.replace('gpu_vc_prof_get','original_get')
    oracle='\n'.join(updates)
    for name in ('words','loads','tris_cached','tris_legacy'):
        oracle=oracle.replace('g_vc_'+name,'original_'+name)
    source = pre + gate + '\n' + newgetter + '\n' + original + body.replace('UPDATE;', '\n'.join('GPU_VC_PROF('+s+');' for s in wrapped)).replace('ORACLE;', oracle)
    (dest/'counter_test.c').write_text(source)
    results=[]
    for profile,diag in ((0,0),(1,0),(0,1),(1,1)):
        exe=dest/f'counter-{profile}{diag}'
        cmd=['cc','-std=c99','-O2','-fsanitize=undefined','-fno-sanitize-recover=all',f'-DSM64_PROFILE={profile}',f'-DSM64_PERF_DIAG={diag}',str(dest/'counter_test.c'),'-o',str(exe)]
        subprocess.run(cmd,check=True)
        output=subprocess.check_output([str(exe)],text=True)
        results.append(dict(profile=profile,diagnostic=diag,iterations=100000,output=output.strip(),binary_sha256=sha(exe)))
    write(dest/'result.json',dict(passed=True,configurations=results,source_sha256=sha(dest/'counter_test.c'),scope='Exact extracted counter declarations, all28 update expressions and getter; no renderer replacement.'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','proof']);a=p.parse_args();globals()[a.action]()
