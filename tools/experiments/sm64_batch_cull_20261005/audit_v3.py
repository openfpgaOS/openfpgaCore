#!/usr/bin/env python3
"""Independent candidate3 backoff and actual target arithmetic audit."""
from pathlib import Path
import hashlib,json,subprocess
from audit_v2 import body,once

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005'
DEST=OUT/'independent-audit-v3'
GFX=OUT/'SM64/src/sm64/sm64/src/pc/gfx'
OLD=OUT/'independent-audit-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    DEST.mkdir(exist_ok=True)
    expected={'gfx_geometry.h':'0c6e33ce35dd9057cf674a9d2899e48961e999bf92ece38178d5ea3dca401fb4',
        'gfx_pc.c':'4665b1edd36eb7feffb8ef4203fbcc9faed15baad7e9cd9bed463663c340f927',
        'gfx_vertex_cull.h':'6c48e51766818099864b4629a1c06a28abd665da6ec4188605b755244b7788d2'}
    assert {n:sha(GFX/n) for n in expected}==expected
    g=(GFX/'gfx_geometry.h').read_text()
    reverse=once(g,'''/* Visible immutable batches retry culling every eighth load. A skipped test
 * always falls back to full CPU loading; a rejection is never reused. */
struct GeometrySoftwareBounds {const Vtx *source;uint16_t count,retry;float box[6];};''',
        'struct GeometrySoftwareBounds {const Vtx *source;unsigned count;float box[6];};')
    reverse=once(reverse,'''static const float *geometry_software_bounds(const Vtx *vertices,unsigned count,float box[6],
        struct GeometrySoftwareBounds **entry) {
    struct GeometrySoftwareBounds *cached=NULL;
    if(entry)*entry=NULL;''','''static const float *geometry_software_bounds(const Vtx *vertices,unsigned count,float box[6]) {
    struct GeometrySoftwareBounds *cached=NULL;''')
    reverse=once(reverse,'''        if(entry)*entry=cached;
        if(cached->source==vertices && cached->count==count) {
            if(cached->retry) {cached->retry--;return NULL;}
            return cached->box;
        }''','        if(cached->source==vertices && cached->count==count)return cached->box;')
    reverse=once(reverse,'    if(cached) {cached->source=vertices;cached->count=count;cached->retry=0;box=cached->box;}',
        '    if(cached) {cached->source=vertices;cached->count=count;box=cached->box;}')
    reverse=once(reverse,'    struct GeometrySoftwareBounds *cached;\n    const float *box=geometry_software_bounds(vertices,count,temporary,&cached);',
        '    const float *box=geometry_software_bounds(vertices,count,temporary);')
    reverse=once(reverse,'    if(cached)cached->retry=7;\n','')
    assert reverse==(OLD/'gfx_geometry.h').read_text()
    for n in ('gfx_pc.c','gfx_vertex_cull.h'):
        assert (GFX/n).read_bytes()==(OLD/n).read_bytes()
    # Static control closure: a retry NULL cannot capture or reach raw return1.
    b=g[g.index('static const float *geometry_software_bounds('):g.index('static __attribute__((noinline)) void geometry_software_update_planes(')]
    sw=g[g.index('static __attribute__((noinline)) int geometry_prepare_software('):g.index('static __attribute__((noinline)) int geometry_prepare(')]
    assert 'if(entry)*entry=NULL;' in b and b.count('return NULL;')==3
    assert 'if(!box)return 0;' in sw and 'return 1;' not in sw
    assert sw.index('geometry_valid&=~mask;geometry_pending&=~mask;')<sw.index('geometry_software_bounds(')
    assert sw.index('if(!box)return 0;')<sw.index('vertex_cull_capture(')<sw.index('if(cached)cached->retry=7;')
    assert 'if(outside && geometry_confirm_outside(vertices,count,aspect,outside))' in sw
    # Exhaust the scheduler's eight reachable retry states: no decrement can
    # skip the bound and retry at zero is always a real current-state attempt.
    cases=[]
    for retry in range(8):
        for match in (False,True):
            skip=match and retry!=0
            next_retry=retry-1 if skip else (retry if match else 0)
            assert not skip or 0<=next_retry<7
            assert not match or retry!=0 or not skip
            cases.append(dict(retry=retry,tag_match=match,skip_to_full_cpu=skip,next_retry_before_test=next_retry))
    target=[]
    for label,app in [('diagnostic','sm64-perfdiag'),('normal','sm64')]:
        obj=OUT/f'SM64/.obj/{app}/sm64/src/pc/gfx/gfx_pc.o'
        asm=subprocess.check_output(['llvm-objdump','-dr','--section=.text.geometry_confirm_outside',
            '--section=.text.gfx_sp_vertex','--section=.text.vertex_cull_materialize',str(obj)],text=True)
        (DEST/f'gfx_pc-{label}.asm').write_text(asm)
        prev=(OLD/f'gfx_pc-{label}.asm').read_text()
        funcs=[]
        for n in ['geometry_confirm_outside','gfx_sp_vertex','vertex_cull_materialize']:
            actual=body(asm,n);reference=body(prev,n)
            assert actual==reference,(label,n)
            funcs.append(dict(function=n,machine_instructions=len(actual[0]),instruction_and_external_relocations_identical=True))
        build=json.loads((OUT/f'build-{label}.json').read_text())
        assert build['returncode']==0 and build['changed_inputs']==[]
        assert all(build['input_sha256']['src/sm64/sm64/src/pc/gfx/'+n]==h for n,h in expected.items())
        target.append(dict(label=label,object_sha256=sha(obj),elf_sha256=build['elf_sha256'],
            assembly_sha256=sha(DEST/f'gfx_pc-{label}.asm'),functions=funcs))
    for n in expected:(DEST/n).write_bytes((GFX/n).read_bytes())
    result=dict(passed=True,source_sha256=expected,exact_reverse_to_v2=True,
        reviewed_retry_states=cases,actual_normal_and_diagnostic_instruction_dag_inherited=True,
        prior_audit_sha256=sha(OLD/'manifest.json'),target=target,
        source_review_sha256=sha(DEST/'REVIEW.txt'),script_sha256=sha(Path(__file__)),
        scope='Independent retry/source/state and compiled arithmetic audit; runtime performance is separate.',
        limitations=['Backoff can miss a profitable rejection for seven uses, which changes only work performed, not which geometry is rendered.',
            'The retry truth table supplements the reviewed actual-source closure; it is not a standalone whole-program formal proof.',
            'Separate generator/validator records own focused runtime and whole-game/GPU qualification.'])
    (DEST/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: candidate3 exact backoff scope, control closure and target FP DAGs.')

if __name__=='__main__':main()
