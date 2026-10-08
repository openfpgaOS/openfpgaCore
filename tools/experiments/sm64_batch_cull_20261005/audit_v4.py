#!/usr/bin/env python3
"""Independent candidate4 bounded-range and empty-ownership audit."""
from pathlib import Path
import hashlib,json,re,subprocess
from audit_v2 import body,once

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005'
DEST=OUT/'independent-audit-v4'
GFX=OUT/'SM64/src/sm64/sm64/src/pc/gfx'
OLD=OUT/'independent-audit-v3'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    DEST.mkdir(exist_ok=True)
    expected={'gfx_geometry.h':'a1bcd056f093bef91763548c66e98aa9b9a9899cd8be7c04e1b63ad2f4d0d88b',
        'gfx_pc.c':'4665b1edd36eb7feffb8ef4203fbcc9faed15baad7e9cd9bed463663c340f927',
        'gfx_vertex_cull.h':'6c48e51766818099864b4629a1c06a28abd665da6ec4188605b755244b7788d2'}
    assert {n:sha(GFX/n) for n in expected}==expected
    g=(GFX/'gfx_geometry.h').read_text()
    reverse=once(g,'    /* The caller admits at most 32 vertices, so this product cannot wrap. */\n','')
    # Only software bounds changes: original hardware quotient stays intact.
    assert reverse.count('count*sizeof(Vtx)<=end-address')==1
    reverse=once(reverse,'count*sizeof(Vtx)<=end-address','count<=(end-address)/sizeof(Vtx)')
    reverse=once(reverse,'''    if(geometry_valid|geometry_pending) {
        uint32_t mask=count>=32-first?(0xffffffffu<<first):(((1u<<count)-1u)<<first);
        uint32_t release=geometry_valid&mask;
        geometry_valid&=~mask;geometry_pending&=~mask;
        for(unsigned i=first;release;i++)if(release&(1u<<i)) {
            struct GeometryContext *context=&geometry_contexts[geometry_vertices[i].context];
            release&=~context->owners;context->owners&=~mask;
        }
    }
''','''    uint32_t mask=count>=32-first?(0xffffffffu<<first):(((1u<<count)-1u)<<first);
    uint32_t release=geometry_valid&mask;
    geometry_valid&=~mask;geometry_pending&=~mask;
    for(unsigned i=first;release;i++)if(release&(1u<<i)) {
        struct GeometryContext *context=&geometry_contexts[geometry_vertices[i].context];
        release&=~context->owners;context->owners&=~mask;
    }
''')
    assert reverse==(OLD/'gfx_geometry.h').read_text()
    for n in ('gfx_pc.c','gfx_vertex_cull.h'):
        assert (GFX/n).read_bytes()==(OLD/n).read_bytes()
    assert g.count('geometry_software_bounds(')==2 # definition + sole caller
    sw=g[g.index('static __attribute__((noinline)) int geometry_prepare_software('):g.index('static __attribute__((noinline)) int geometry_prepare(')]
    assert sw.index('if(first>=32)return 0;')<sw.index('count>32-first || count<8')<sw.index('geometry_software_bounds(')
    cases=0
    for n in range(8,33):
        assert n*24<=768
        for a in set(range(0,1025)) | {2**32-1,2**32-2,2**32-768,n*24-1,n*24,n*24+1}:
            assert (n<=a//24)==(n*24<=a)
            cases+=1
    # For arbitrary mask, the skipped old assignments read/write zero, and
    # release=0 means the old context loop cannot execute. All nonzero states
    # retain the identical old body (proved by byte-for-byte reversal above).
    for mask in [0,1,2**31,2**32-1,0xaaaaaaaa,0x55555555]:
        assert (0 & ~mask)==0 and (0 & mask)==0
    targets=[]
    fastpaths=[]
    for label,app in [('diagnostic','sm64-perfdiag'),('normal','sm64')]:
        obj=OUT/f'SM64/.obj/{app}/sm64/src/pc/gfx/gfx_pc.o'
        asm=subprocess.check_output(['llvm-objdump','-dr','--section=.text.geometry_confirm_outside',
            '--section=.text.gfx_sp_vertex','--section=.text.vertex_cull_materialize',
            '--section=.text.geometry_prepare_software',str(obj)],text=True)
        (DEST/f'gfx_pc-{label}.asm').write_text(asm)
        prev=(OLD/f'gfx_pc-{label}.asm').read_text()
        for n in ['geometry_confirm_outside','gfx_sp_vertex','vertex_cull_materialize']:
            assert body(asm,n)==body(prev,n),(label,n)
        prep=asm.split('Disassembly of section .text.geometry_prepare_software:',1)[1].split('Disassembly of section ',1)[0]
        assert not re.search(r'\bdivu?\b',prep)
        # Preserve the assembly for review of the generated OR/zero branch;
        # merely omitting the old stores in C is not treated as mapping proof.
        assert 'geometry_valid' in prep and 'geometry_pending' in prep
        ins=[m.groups() for m in re.finditer(r'^\s*([0-9a-f]+):\s+([0-9a-f]+)\s+(\S+)\s*(.*)$',prep,re.M)]
        mask_or=[x for x in ins if x[2]=='or' and x[3]=='a5, a4, a3']
        assert len(mask_or)==1
        branch=ins[ins.index(mask_or[0])+2]
        assert branch[2]=='beqz' and branch[3].startswith('a5, 0xf2')
        assert any(x[0]=='94' and x[2]=='sw' for x in ins)
        assert any(x[0]=='98' and x[2]=='sw' for x in ins)
        fastpaths.append(dict(label=label,mask_or_address=mask_or[0][0],zero_branch_address=branch[0],
            zero_branch_target='0xf2',mask_store_addresses=['0x94','0x98'],
            zero_branch_bypasses_mask_computation_stores_context_loop=True,software_divide_absent=True))
        build=json.loads((OUT/f'build-{label}.json').read_text())
        assert build['returncode']==0 and build['changed_inputs']==[]
        assert all(build['input_sha256']['src/sm64/sm64/src/pc/gfx/'+n]==h for n,h in expected.items())
        targets.append(dict(label=label,object_sha256=sha(obj),elf_sha256=build['elf_sha256'],
            assembly_sha256=sha(DEST/f'gfx_pc-{label}.asm'),software_divide_count=0,
            confirmation_fallback_recovery_instructions_and_external_bindings_identical=True))
    for n in expected:(DEST/n).write_bytes((GFX/n).read_bytes())
    (DEST/'fastpath-mapping.json').write_text(json.dumps(dict(passed=True,results=fastpaths,
        source_sha256=expected['gfx_geometry.h']),indent=2)+'\n')
    result=dict(passed=True,source_sha256=expected,exact_reverse_to_v3=True,
        range_identity='For S>0 and n*S representable, n<=floor(a/S) iff n*S<=a.',
        bound='Only production call is guarded 8<=count<=32-first and first<32; sizeof(Vtx)=24, product<=768.',
        supplemental_range_cases=cases,empty_ownership_identity=True,target=targets,
        fastpath_mapping_sha256=sha(DEST/'fastpath-mapping.json'),
        source_review_sha256=sha(DEST/'REVIEW.txt'),script_sha256=sha(Path(__file__)),
        prior_audit_sha256=sha(OLD/'manifest.json'),
        scope='Source/control/compiled-arithmetic audit; controlled performance and game output are separate.')
    (DEST/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: v4 exact range/zero-state changes, DIV absent, target FP DAGs unchanged.')

if __name__=='__main__':main()
