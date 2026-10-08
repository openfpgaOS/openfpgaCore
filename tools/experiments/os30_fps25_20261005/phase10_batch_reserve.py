#!/usr/bin/env python3
"""Prepare exact-order bounded indexed-run reservation, private F11 input."""
import difflib
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase9/checks-state-f11'
OUT=CAMP/'phase10/batch-reserve-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    build=json.loads((BASE/'build-normal.json').read_text())
    inputs={n:(BASE/n).read_text() for n in build['inputs']}
    for n,i in build['inputs'].items():assert sha(BASE/n)==i['sha256']
    original=dict(inputs)
    s=inputs['gfx_pc.c']
    old='''    unsigned count = 0, consumed = 0;
    unsigned slot[6], reject[2], n = 0, checked = 0;'''
    new='''    unsigned count = 0, consumed = 0, packet_words = 0;
    unsigned slot[6], reject[2], n = 0, checked = 0;'''
    assert s.count(old)==1;s=s.replace(old,new)
    old='''            jobs[count++] = s[0] | (s[1] << 5) | (s[2] << 10) | (dirty << 15);'''
    new='''            /* Prefix length lets the emitter reserve any contiguous job
             * segment without a second scan or per-triangle SDK checks.
             * 64 jobs / 32 live slots fit below 4096 words even if a dirty
             * triangle repeats a slot. Bits18..19 stay free for cull flags. */
            packet_words += 2u + 9u * ((dirty & 1u) + ((dirty >> 1) & 1u) + ((dirty >> 2) & 1u));
            jobs[count++] = s[0] | (s[1] << 5) | (s[2] << 10) | (dirty << 15)
                          | (packet_words << 20);'''
    assert s.count(old)==1;s=s.replace(old,new);inputs['gfx_pc.c']=s
    s=inputs['gfx_gpu.c']
    begin=s.index('    /* No state mutation occurs inside this bounded CPU-only run. */',s.index('void gfx_gpu_vtx_cache_batch('))
    end=s.index('\n}\n\n/* Raw geometry path.',begin)
    old=s[begin:end]
    new='''#ifndef OF_PC
    /* Each segment ends at the same 32-triangle explicit publication point.
     * Earlier buffer-capacity publication may move to its segment boundary;
     * command contents, order, ownership and texture epochs stay unchanged. */
    unsigned first = 0, previous_words = 0, total_loads = 0;
    while (first < count) {
        unsigned end = first + (32u - g_vc_kick);
        if (end > count) end = count;
        const unsigned prefix_words = jobs[end - 1] >> 20;
        const unsigned words = prefix_words - previous_words;
        _gpu_stream_reserve_words(words);
        uint32_t *restrict packet = _gpu_ring_claim();
        for (unsigned tri = first; tri < end; ++tri) {
            const uint32_t job = jobs[tri];
            unsigned dirty = (job >> 15) & 7u;
            uint32_t indices = job;
            while (dirty) {
                if (dirty & 1u) {
                    const unsigned slot = indices & 31u;
                    const struct gfx_vc_prepared *restrict vtx = &vertices[slot];
                    packet[0] = ((uint32_t)GPU_CMD_LOAD_VERT_CLIP << 24) | 8u;
                    packet[1] = slot;
                    packet[2] = (uint32_t)vtx->x;
                    packet[3] = (uint32_t)vtx->y;
                    packet[4] = (uint32_t)vtx->w;
                    packet[5] = (uint32_t)vtx->u;
                    packet[6] = (uint32_t)vtx->v;
                    packet[7] = vtx->rgb;
                    packet[8] = vtx->depth;
                    packet += 9;
                }
                dirty >>= 1;
                indices >>= 5;
            }
            *packet++ = ((uint32_t)GPU_CMD_DRAW_INDEXED_TRI << 24) | 1u;
            *packet++ = job & 0x7fffu;
        }
        _gpu_ring_commit(words);
        total_loads += (words - 2u * (end - first)) / 9u;
        g_vc_kick += end - first;
        if (g_vc_kick == 32u) {
            g_vc_kick = 0;
            of_gpu_kick();
        }
        first = end;
        previous_words = prefix_words;
    }
    g_vc_words += previous_words;
    g_vc_loads += total_loads;
    g_vc_tris_cached += count;
#else
''' + old + '''
#endif'''
    s=s[:begin]+new+s[end:];inputs['gfx_gpu.c']=s
    OUT.mkdir(exist_ok=False)
    patch=''
    for name,contents in inputs.items():
        (OUT/name).write_text(contents)
        if contents!=original[name]:patch+=''.join(difflib.unified_diff(original[name].splitlines(True),contents.splitlines(True),fromfile='F11/'+name,tofile='batch-reserve-v1/'+name))
    (OUT/'candidate.patch').write_text(patch)
    (OUT/'source.json').write_text(json.dumps(dict(parent='phase9/checks-state-f11',source_sha256={n:sha(OUT/n) for n in inputs},recipe_sha256=sha(Path(__file__)),qualified=False,contract='Same ordered GPU commands;32-triangle explicit kick positions retained. Automatic capacity publication may move to earlier segment boundary.'),indent=2)+'\n')
    print(OUT)

if __name__=='__main__':main()
