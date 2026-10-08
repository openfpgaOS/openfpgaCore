#!/usr/bin/env python3
"""Independent source, cache and inherited target-DAG audit for candidate2."""
from pathlib import Path
import hashlib,json,re,subprocess

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005'
DEST=OUT/'independent-audit-v2'
GFX=OUT/'SM64/src/sm64/sm64/src/pc/gfx'
BASE=OUT/'source-baseline'
OLD=OUT/'independent-audit'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def once(s,a,b):
    assert s.count(a)==1
    return s.replace(a,b)

def body(asm,name):
    s=asm.split('Disassembly of section .text.'+name+':',1)[1].split('Disassembly of section ',1)[0]
    # Control-flow labels are renumbered when the translation unit grows;
    # the actual machine instructions and linked symbol references must agree.
    ins=[m.groups() for m in re.finditer(r'^\s*([0-9a-f]+):\s+([0-9a-f]+)\s+\S+.*$',s,re.M)]
    # Local branch and constant labels are translation-unit numbered. Their
    # unmodified source is separately pinned; this compares external bindings.
    rel=[m.group(1) for m in re.finditer(r'^\s*[0-9a-f]+:\s+(R_RISCV_.*)$',s,re.M)
         if not re.search(r'\s\.L',m.group(1))]
    return ins,rel

def main():
    DEST.mkdir(exist_ok=True)
    expected={'gfx_geometry.h':'cc942c5b4516bcb87989b503d5cb7edfd3b5156207984bc42e88d287efc97a8d',
        'gfx_pc.c':'4665b1edd36eb7feffb8ef4203fbcc9faed15baad7e9cd9bed463663c340f927',
        'gfx_vertex_cull.h':'6c48e51766818099864b4629a1c06a28abd665da6ec4188605b755244b7788d2'}
    assert {n:sha(GFX/n) for n in expected}==expected
    g=(GFX/'gfx_geometry.h').read_text()
    a=g.index('/* With software transforms, confirm only a prospective reject plane')
    b=g.index('/* GEO0 uses only cached bounds and frustum planes.',a)
    c=g.index('static __attribute__((noinline)) int geometry_prepare(',b)
    oldg=(OLD/'candidate1-inputs/gfx_geometry.h').read_text()
    olda=oldg.index('/* With software transforms, confirm only a prospective reject plane')
    oldb=oldg.index('static __attribute__((noinline)) int geometry_prepare(',olda)
    assert g[a:b]==oldg[olda:oldb]
    reverse=g[:a]+g[c:]
    reverse=once(reverse,'    if (!gfx_gpu_geometry_enabled())return geometry_prepare_software(vertices,count,first,aspect);\n    if (first >= 32)\n',
        '    if (!gfx_gpu_geometry_enabled() || first >= 32)\n')
    assert reverse==(BASE/'gfx_geometry.h').read_text()
    p=(GFX/'gfx_pc.c').read_text()
    p=once(p,'''    if(culled_pending) {
        if(v1->clip_rej&v2->clip_rej&v3->clip_rej)return;
        /* A triangle may mix deferred low slots with ordinary slots 32+. */
        if(vtx1_idx<32 && (culled_pending&(1u<<vtx1_idx)))vertex_cull_materialize(vtx1_idx);
        if(vtx2_idx<32 && (culled_pending&(1u<<vtx2_idx)))vertex_cull_materialize(vtx2_idx);
        if(vtx3_idx<32 && (culled_pending&(1u<<vtx3_idx)))vertex_cull_materialize(vtx3_idx);
    }
''','''    if((vtx1_idx|vtx2_idx|vtx3_idx)<32 && culled_pending) {
        if(v1->clip_rej&v2->clip_rej&v3->clip_rej)return;
        vertex_cull_materialize(vtx1_idx);vertex_cull_materialize(vtx2_idx);vertex_cull_materialize(vtx3_idx);
    }
''')
    assert p==(BASE/'gfx_pc.c').read_text()
    assert (GFX/'gfx_vertex_cull.h').read_bytes()==(BASE/'gfx_vertex_cull.h').read_bytes()
    target=[]
    for label,app in [('diagnostic','sm64-perfdiag'),('normal','sm64')]:
        obj=OUT/f'SM64/.obj/{app}/sm64/src/pc/gfx/gfx_pc.o'
        asm=subprocess.check_output(['llvm-objdump','-dr','--section=.text.geometry_confirm_outside',
            '--section=.text.gfx_sp_vertex','--section=.text.vertex_cull_materialize',str(obj)],text=True)
        (DEST/f'gfx_pc-{label}.asm').write_text(asm)
        previous=(OLD/f'gfx_pc-{label}.asm').read_text()
        funcs=[]
        for n in ['geometry_confirm_outside','gfx_sp_vertex','vertex_cull_materialize']:
            actual=body(asm,n);reference=body(previous,n)
            assert actual==reference,(label,n)
            funcs.append(dict(function=n,machine_instructions=len(actual[0]),instruction_and_external_relocations_identical=True))
        build=json.loads((OUT/f'build-{label}.json').read_text())
        assert build['returncode']==0 and build['changed_inputs']==[]
        assert all(build['input_sha256']['src/sm64/sm64/src/pc/gfx/'+n]==h for n,h in expected.items())
        target.append(dict(label=label,object_sha256=sha(obj),elf_sha256=build['elf_sha256'],
            assembly_sha256=sha(DEST/f'gfx_pc-{label}.asm'),functions=funcs))
    for n in expected:
        (DEST/n).write_bytes((GFX/n).read_bytes())
    result=dict(passed=True,source_sha256=expected,exact_reverse_scope=True,
        actual_normal_and_diagnostic_instruction_dag_inherited=True,
        prior_codegen_proof_sha256=sha(OLD/'codegen-manifest.json'),target=target,
        source_review_sha256=sha(DEST/'REVIEW.txt'),script_sha256=sha(Path(__file__)),
        scope='Independent exact emitted source/cache/state and actual target arithmetic review; not a performance or whole-GPU qualification.',
        software_bss_bytes_rv32=32944,
        runtime_qualification='Separate generator focused suites and validator controlled game/GPU replay; not asserted by this audit.')
    (DEST/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: candidate2 source/cache/state and actual normal/diagnostic FP DAGs.')

if __name__=='__main__':main()
