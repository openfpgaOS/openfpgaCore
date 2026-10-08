#!/usr/bin/env python3
"""Outline packet writer work; only private source artifacts are written."""
from pathlib import Path
import hashlib, json, difflib
ROOT=Path(__file__).resolve().parents[3]
C=ROOT/'docs/measurements/os30-simple-20261005'
OUT=C/'vc-packet-v2'
BASE=C/'baseline/gfx_gpu.c'
V1=C/'vc-packet-v1/gfx_gpu.c'
sha=lambda b:hashlib.sha256(b).hexdigest()
assert sha(BASE.read_bytes())=='71d7e1fb8afaf414c746a487c5cc9fa1eba22f685a4173c772743a662210bada'
assert sha(V1.read_bytes())=='325bc7efaef07bd1cfb82a556d0afed37197578623f95ad6a045bac22630bfec'
def func(s,name):
 st=s.index('void '+name+'('); p=s.index('{',st)+1;d=1
 while d:
  d+=(s[p]=='{')-(s[p]=='}');p+=1
 return s[st:p]
src=BASE.read_text();old=func(src,'gfx_gpu_vtx_cache_tri');prev=func(V1.read_text(),'gfx_gpu_vtx_cache_tri')
vp_start=prev.index('        of_gpu_object_state_t os;')
vp_end=prev.index('\n    }',vp_start)
vp='\n'.join(line[4:] for line in prev[vp_start:vp_end].splitlines())
assert vp.count('of_gpu_set_object_state(&os);')==1
# Derive the complete vertex arithmetic from v1, retaining all expression text.
conv_start=prev.index('        const uint16_t rgb =')
conv_end=prev.index('\n#ifndef OF_PC',conv_start)
conv=prev[conv_start:conv_end]
write_start=prev.index('            *packet++ = ((uint32_t)GPU_CMD_LOAD_VERT_CLIP')
write_end=prev.index('\n        }',write_start)
writes=prev[write_start:write_end]
conv='\n'.join(x[4:] for x in conv.splitlines()).replace('v[k].','v->')
writes='\n'.join(x[8:] for x in writes.splitlines()).replace('v[k].','v->').replace('slot[k]','slot')
helpers='''#ifndef OF_PC
/* Keep SDK transport slow paths and upload-only arithmetic outside the common
 * resident-slot draw. One shared quantizer replaces three unrolled copies. */
static __attribute__((noinline)) void gfx_vc_packet_viewport(void) {
'''+vp+'''
}

static __attribute__((noinline)) uint32_t *gfx_vc_packet_reserve(unsigned words) {
    _gpu_stream_reserve_words(words);
    return _gpu_ring_claim();
}

static __attribute__((noinline)) void gfx_vc_packet_kick(void) {
    of_gpu_kick();
}

static __attribute__((noinline)) uint32_t *gfx_vc_packet_vertex(
        uint32_t *packet, const struct gfx_vc_vtx *v, uint8_t slot) {
'''+conv+'\n'+writes+'''
    return packet;
}

'''
intro=prev[:prev.index('    /* X projection')]
new=intro+'''    if (!g_vc_vp_valid || g_vc_vp[0] != g_cx || g_vc_vp[1] != g_cy ||
        g_vc_vp[2] != g_hw || g_vc_vp[3] != g_hh)
        gfx_vc_packet_viewport();

    const uint32_t features = of_get_caps()->hw_features;
    const unsigned load_clip = (features & OF_HW_GPU_VERT_TRI) &&
                               (features & OF_HW_GPU_CLIP_LOAD);
    const unsigned draw_indexed = (features & OF_HW_GPU_VERT_TRI) &&
        (features & (OF_HW_GPU_XFORM_RGB | OF_HW_GPU_CLIP_LOAD));
    const unsigned dirty_count = (dirty_mask & 1u) + ((dirty_mask >> 1) & 1u)
                               + ((dirty_mask >> 2) & 1u);
    const unsigned packet_words = 9u * dirty_count * load_clip + 2u * draw_indexed;
    uint32_t *packet = packet_words ? gfx_vc_packet_reserve(packet_words) : NULL;
    if (load_clip && dirty_count) {
        for (unsigned k = 0; k < 3; ++k)
            if (dirty_mask & (1u << k))
                packet = gfx_vc_packet_vertex(packet, &v[k], slot[k]);
    }
    g_vc_words += 9u * dirty_count;
    g_vc_loads += dirty_count;
    if (draw_indexed) {
        *packet++ = ((uint32_t)GPU_CMD_DRAW_INDEXED_TRI << 24) | 1u;
        *packet++ = ((uint32_t)(slot[2] & 0x1fu) << 10)
                  | ((uint32_t)(slot[1] & 0x1fu) << 5)
                  | (uint32_t)(slot[0] & 0x1fu);
    }
    if (packet_words) _gpu_ring_commit(packet_words);
    g_vc_words += 2;
    g_vc_tris_cached++;
    if (++g_vc_kick >= 32u) {
        g_vc_kick = 0;
        gfx_vc_packet_kick();
    }
}'''
# Keep desktop implementation byte-identical to baseline.
replacement=helpers+new+'\n#else\n'+old+'\n#endif'
candidate=src.replace(old,replacement)
assert candidate.replace(replacement,old)==src
assert new.count('gfx_vc_packet_vertex(')==1
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'gfx_gpu.c').write_text(candidate)
(OUT/'reference-function.c').write_text(old+'\n')
(OUT/'candidate-function.c').write_text(helpers+new+'\n#endif\n')
(OUT/'change.patch').write_text(''.join(difflib.unified_diff(src.splitlines(True),candidate.splitlines(True),fromfile='a/gfx_gpu.c',tofile='b/gfx_gpu.c')))
(OUT/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
meta={'candidate_sha256':sha(candidate.encode()),'parent_sha256':sha(BASE.read_bytes()),'packet_v1_sha256':sha(V1.read_bytes()),'recipe_sha256':sha(Path(__file__).read_bytes()),'scope':'One public function replaced by identical API and four private noinline helpers; desktop branch exact baseline. No other source changes.','arithmetic_derived_exactly_from_v1':True,'inverse_whole_source_exact':True,'publication_timing':'Same full-packet reservation as v1; differs from baseline only in staging publication boundaries, not ordered command stream.','qualification':'pending focused actual-function transcript comparison and target/game replay','expected_structural_effect':'Remove upload FP state/viewport aggregate/transport slow code from resident draw; one shared vertex quantizer instead of three compiler-unrolled copies.'}
(OUT/'manifest.json').write_text(json.dumps(meta,indent=2)+'\n')
print(json.dumps(meta,indent=2))
