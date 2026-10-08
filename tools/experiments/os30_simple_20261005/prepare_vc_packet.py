#!/usr/bin/env python3
"""Private one-reservation clip-cache packet candidate; no staged-tree edits."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / 'docs/measurements/os30-simple-20261005'
OUT = CAMPAIGN / 'vc-packet-v1'
BASE = CAMPAIGN / 'baseline/gfx_gpu.c'
EXPECTED = '71d7e1fb8afaf414c746a487c5cc9fa1eba22f685a4173c772743a662210bada'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def function(text, name):
    start = text.index('void ' + name + '(')
    brace = text.index('{', start)
    depth = 1
    pos = brace + 1
    while depth:
        depth += (text[pos] == '{') - (text[pos] == '}')
        pos += 1
    return text[start:pos]

source = BASE.read_text()
assert sha(BASE.read_bytes()) == EXPECTED
old = function(source, 'gfx_gpu_vtx_cache_tri')
new = old
anchor = '    /* w PRE-SCALE (hardware-verified fix for misplaced distant triangles):'
setup = '''#ifndef OF_PC
    /* One reservation preserves the same ordered 0x56 loads and 0x54 draw.
     * Material and viewport commands above have already been staged. */
    const uint32_t features = of_get_caps()->hw_features;
    const unsigned load_clip = (features & OF_HW_GPU_VERT_TRI) &&
                               (features & OF_HW_GPU_CLIP_LOAD);
    const unsigned draw_indexed = (features & OF_HW_GPU_VERT_TRI) &&
        (features & (OF_HW_GPU_XFORM_RGB | OF_HW_GPU_CLIP_LOAD));
    const unsigned dirty_count = (dirty_mask & 1u) + ((dirty_mask >> 1) & 1u)
                               + ((dirty_mask >> 2) & 1u);
    const unsigned packet_words = 9u * dirty_count * load_clip + 2u * draw_indexed;
    if (packet_words) _gpu_stream_reserve_words(packet_words);
    uint32_t *packet = packet_words ? _gpu_ring_claim() : NULL;
#endif

'''
assert new.count(anchor) == 1
new = new.replace(anchor, setup + anchor)
load = '''        of_gpu_load_vert_clip(slot[k],
                              (int32_t)lrintf(v[k].cx * (65536.0f * VC_W_PRESCALE)),
                              (int32_t)lrintf(v[k].cy * (65536.0f * VC_W_PRESCALE)),
                              (int32_t)lrintf(v[k].cw * (65536.0f * VC_W_PRESCALE)),
                              (int32_t)lrintf(v[k].u * 65536.0f),
                              (int32_t)lrintf(v[k].v * 65536.0f),
                              rgb,
                              v[k].use_exact_depth ? v[k].exact_depth : (uint32_t)lrintf(df));'''
packed_load = '''#ifndef OF_PC
        if (load_clip) {
            *packet++ = ((uint32_t)GPU_CMD_LOAD_VERT_CLIP << 24) | 8u;
            *packet++ = (uint32_t)(slot[k] & 0x1fu);
            *packet++ = (uint32_t)(int32_t)lrintf(v[k].cx * (65536.0f * VC_W_PRESCALE));
            *packet++ = (uint32_t)(int32_t)lrintf(v[k].cy * (65536.0f * VC_W_PRESCALE));
            *packet++ = (uint32_t)(int32_t)lrintf(v[k].cw * (65536.0f * VC_W_PRESCALE));
            *packet++ = (uint32_t)(int32_t)lrintf(v[k].u * 65536.0f);
            *packet++ = (uint32_t)(int32_t)lrintf(v[k].v * 65536.0f);
            *packet++ = (uint32_t)rgb;
            *packet++ = v[k].use_exact_depth ? v[k].exact_depth : (uint32_t)lrintf(df);
        }
#else
''' + load + '\n#endif'
assert new.count(load) == 1
new = new.replace(load, packed_load)
draw = '    of_gpu_draw_indexed_tri(slot[0], slot[1], slot[2]);'
packed_draw = '''#ifndef OF_PC
    if (draw_indexed) {
        *packet++ = ((uint32_t)GPU_CMD_DRAW_INDEXED_TRI << 24) | 1u;
        *packet++ = ((uint32_t)(slot[2] & 0x1fu) << 10)
                  | ((uint32_t)(slot[1] & 0x1fu) << 5)
                  | (uint32_t)(slot[0] & 0x1fu);
    }
    if (packet_words) _gpu_ring_commit(packet_words);
#else
''' + draw + '\n#endif'
assert new.count(draw) == 1
new = new.replace(draw, packed_draw)
candidate = source.replace(old, new)
assert candidate.count(new) == 1
assert candidate.replace(new, old) == source
OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'gfx_gpu.c').write_text(candidate)
(OUT / 'reference-function.c').write_text(old + '\n')
(OUT / 'candidate-function.c').write_text(new + '\n')
(OUT / 'change.patch').write_text(''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True), fromfile='a/gfx_gpu.c', tofile='b/gfx_gpu.c')))
(OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
manifest = {
    'source': str(BASE.relative_to(ROOT)), 'parent_sha256': EXPECTED,
    'candidate_sha256': sha(candidate.encode()), 'recipe_sha256': sha(Path(__file__).read_bytes()),
    'scope': 'gfx_gpu_vtx_cache_tri only: combine dirty clip-load and indexed-draw reservations/commits; all quantization, state handling, viewport, diagnostic counters and advisory kick logic retained',
    'reverse_exact': True, 'full_runtime_qualification': 'pending',
    'packet_words_range': [0, 29],
    'preserved_capability_guards': ['VERT_TRI && CLIP_LOAD for each load', 'VERT_TRI && (XFORM_RGB || CLIP_LOAD) for draw'],
    'publication_timing_note': 'A full-triangle reservation may publish existing buffered words sooner than per-command reservations; flattened command words/order are intended identical, publication timing is not claimed identical.',
}
(OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
