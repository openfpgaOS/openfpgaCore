#!/usr/bin/env python3
"""Read-only candidate audit; write only a private independent-review artifact."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
PHASE=ROOT/'docs/measurements/os30-fps25-20261005/phase2'
FILES=('gfx_pc.c','gfx_gpu.c','gfx_gpu.h','gfx_geometry.h','perf_pocket.h','perf_pocket.c','Makefile.sm64')
LEAVES=('indexed-batch-v2','viewport-dirty-v2','lighting-word-copy-v1','reciprocal-bit-v1','clip-distance-v2')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def reverse_leaf(text, leaf, name):
    base=(PHASE/'baseline'/name).read_text().splitlines(True)
    changed=(PHASE/leaf/name).read_text().splitlines(True)
    groups=list(difflib.SequenceMatcher(None,base,changed,autojunk=False).get_grouped_opcodes(3))
    for group in reversed(groups):
        old=''.join(base[group[0][1]:group[-1][2]])
        new=''.join(changed[group[0][3]:group[-1][4]])
        assert text.count(new)==1,(leaf,name,'non-unique or missing exact reverse hunk')
        text=text.replace(new,old,1)
    return text,len(groups)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('candidate',default='exact-v1',nargs='?');args=parser.parse_args()
    assert '/' not in args.candidate
    dest=PHASE/args.candidate
    manifest=json.loads((dest/'manifest.json').read_text())
    assert tuple(manifest['components'])==LEAVES
    assert not manifest['guardband_included']
    source={n:(dest/n).read_text() for n in FILES}
    assert manifest['source_sha256']=={n:sha(dest/n) for n in FILES}
    assert manifest['generator_sha256']==sha(ROOT/'tools/experiments/os30_fps25_20261005/prepare_phase2_exact.py')
    for leaf in LEAVES:
        assert manifest['component_source_sha256'][leaf]=={n:sha(PHASE/leaf/n) for n in FILES}
    reverse={n:source[n] for n in FILES}; hunks=[]
    # Undo the one documented composition adapter before subtracting whole leaf hunks.
    gpu=reverse['gfx_gpu.c'];start=gpu.index('void gfx_gpu_vtx_cache_batch(');end=gpu.index('\n/* Raw geometry path.',start)
    block=gpu[start:end]
    new='    if (!g_vc_vp_valid) {'
    old=('    if (!g_vc_vp_valid || g_vc_vp[0] != g_cx || g_vc_vp[1] != g_cy ||\n'
         '        g_vc_vp[2] != g_hw || g_vc_vp[3] != g_hh) {')
    assert block.count(new)==1
    reverse['gfx_gpu.c']=gpu[:start]+block.replace(new,old)+gpu[end:]
    for name in FILES:
        for leaf in reversed(LEAVES):
            reverse[name],count=reverse_leaf(reverse[name],leaf,name)
            if count:hunks.append({'file':name,'leaf':leaf,'hunks':count})
    reverse['perf_pocket.c']=reverse['perf_pocket.c'].replace('PERF F5 FPS','PERF F4 FPS').replace('MAP %u CPU %u M  ','LVL %u CPU %u MHZ  ')
    assert all(reverse[n]==(PHASE/'baseline'/n).read_text() for n in FILES)
    assert 'Private guardband trial' not in source['gfx_pc.c']
    assert source['gfx_pc.c'].count('if (!triangle_material_ready || triangle_vc_elig) {')==2
    def generic_tri(s):
        a=s.index('static void gfx_sp_tri1('); b=s.index('\nstatic ',a+1);return s[a:b]
    assert generic_tri(source['gfx_pc.c'])==generic_tri((PHASE/'baseline/gfx_pc.c').read_text())
    assert source['gfx_pc.c'].count('d->color = memo->color;')==1
    assert source['gfx_gpu.c'].count('gpu_clip_reciprocal(den)')==1
    evidence_paths=[PHASE/'indexed-batch-v1/independent-review/source-review.json',
                    PHASE/'clip-distance-v2/independent-review/source-review.json',
                    PHASE/'clip-distance-v2/function-checks/manifest.json',
                    PHASE/'clip-distance-v2/codegen/manifest.json']
    audit={
      'candidate':args.candidate,'passed':True,'source_review_passed':True,'runtime_qualified':False,
      'source_sha256':{n:sha(dest/n) for n in FILES},'selected_components':list(LEAVES),
      'composition':{'passed':True,'all_seven_files_reconstruct_exact_baseline':True,'method':'Independently subtract each frozen leaf as exact unique contextual text hunks; undo only the batch viewport predicate adapter and two panel strings. Does not rerun composition merge recipe.','reverse_hunks':hunks,'generator_sha256':sha(ROOT/'tools/experiments/os30_fps25_20261005/prepare_phase2_exact.py'),'manifest_sha256':sha(dest/'manifest.json')},
      'guardband_included':False,
      'components':{
        'batch_guard':{'passed':True,'source_component':'indexed-batch-v2','known_ineligible_dispatch_gate':'Only both TRI dispatch sites are guarded; cold or eligible material still attempts the same reviewed batch. Ready/ineligible goes to the original generic handler.','lifetime':'Run stops at every non-triangle command. No vertex/material/viewport mutation inside accepted run. Both TRI2 triangles are classified before committing the command. All-culled commands prepare no material.','order':'Buffered generic draws flush before submitted run. Every original per-corner dirty bit, duplicate-slot upload, draw order, and per-32-draw kick is retained.','depth':'Worker-disabled CPU path always uses use_exact_depth=0, same as scalar CPU path. Positive bounded nonuniform w and zero clip flags required; scalar fallback handles everything else. Backface and trivial-reject math is the original expression.','alpha':'Non-fog SHADE alpha and LOD alpha/RGB fall back; accepted alpha is constant over run. Fog SHADE alpha uses255 as original. No new alpha interpolation or clamp.'},
        'viewport_dirty':{'passed':True,'source_component':'viewport-dirty-v2','closure':'gpu_set_viewport is sole post-initialization writer of g_cx/g_cy/g_hw/g_hh and invalidates whenever saved values differ. Both cached scalar and batch consumers check shared valid flag. Frame begin invalidates; geometry object-state writers update saved values and validity together. The one batch predicate adaptation mirrors scalar exactly.'},
        'lighting_word':{'passed':True,'source_component':'lighting-word-copy-v1','closure':'Only memo-hit RGB-byte copy becomes complete RGBA assignment. LightingMemo is static zero-initialized storage and the alpha byte is never uninitialized. Every ordinary vertex loop reaches its unconditional fog/source alpha assignment before return or any draw. Geometry-worker early returns precede this loop and are unchanged. Texture generation reads normals/state only. RGB lighting arithmetic, keys, epochs, and misses are unchanged.'},
        'reciprocal_bit':{'passed':True,'source_component':'reciprocal-bit-v1','domain':'Sole caller gpu_project_clip clamps den to256 or greater before the call; all reachable den are nonzero uint32_t.','proof':'Let UINT32_MAX=q*den+r. Old correction r==den-1 means den divides UINT32_MAX+1=2^32. For nonzero uint32_t den, those divisors are exactly powers of two, characterized by (den&(den-1))==0. Products in old remainder do not overflow because q*den<=UINT32_MAX. Quotient, correction, projection shifts, clamps and signed quantization are unchanged.'},
        'clip_distance':{'passed':True,'source_component':'clip-distance-v2','closure':'Only selected plane distance loads/expressions change. Every plane mask/order, >0 classification, crossing ratio, interpolation, w reciprocal, scratch buffer and fan order remains. Actual x/y/z float member pointer is formed via aligned offsetof byte displacement, never float-array indexing across members.','proof':'Strict, fast-math, ASan/UBSan extracted function runs each pass32172finite/signed-zero/all64mask cases with43508fans/1044192payload words identical. SDK emits direct FLW and hoists sign choice once per plane. Finite RNE output scope; exceptional FP patterns are outside this sampled proof.'}
      },
      'normal_fps_source_closure':{'passed':True,'render_changes_independent_of_perf_diag':True,'explanation':'The rendering leaves introduce no SM64_PERF_DIAG conditionals. Existing scope/count macros and TARGET_POCKET counters are unchanged. New batch is guarded only by TARGET_OPENFPGA && !GFX_GPU_GEOMETRY_WORKER_APP, in both modes; API declaration/definition is available. Both string edits are within existing PERF_DIAG panel implementation, with F5/MAP only in the existing non-details branch. Makefile and perf header equal baseline.'},
      'invariants':{'original_generic_triangle_handler_unchanged':True,'no_guardband_or_clip_rejection_relaxation':True,'no_hardware_changes':True,'no_fog_removal_or_alpha_shortcut':True,'selected_leaf_only_outside_two_panel_strings_and_batch_viewport_adapter':True},
      'evidence':{str(p.relative_to(ROOT)):sha(p) for p in evidence_paths},
      'limitations':['This is a frozen-source and local code-generation review, not hardware performance or whole-scene qualification.','Paired normal/FPS full-scene state and exact retained-A color/depth replay remain separate runtime gates; no 25FPS claim.','The validation agent independently closes all6747 stage sources, compiler dependency paths, SDK inputs, build logs and ELF hashes. Those build bindings are deliberately not inferred by this source audit.'],
      'audit_script_sha256':sha(Path(__file__))}
    out=dest/'independent-review';out.mkdir(exist_ok=True);p=out/'final-source-audit.json';p.write_text(json.dumps(audit,indent=2)+'\n');print(p,sha(p))

if __name__=='__main__':main()
