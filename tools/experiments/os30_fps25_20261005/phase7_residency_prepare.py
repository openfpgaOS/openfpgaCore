#!/usr/bin/env python3
"""Two independent private candidates over the pinned F8 renderer."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase7'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def save(name, filename, old, new):
    assert old != new
    dest = OUT / name
    (dest / filename).write_text(new)
    (dest / 'change.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True),
        fromfile='phase6/candidate/' + filename, tofile=name + '/' + filename)))
    baseline = json.loads((OUT / 'baseline.json').read_text())
    (dest / 'source-manifest.json').write_text(json.dumps({
        'candidate': name, 'parent': 'phase6/candidate/F8',
        'qualified': False, 'source_files': {n: sha(dest / n) for n in baseline['inputs']},
        'changed_file': filename, 'generator_sha256': sha(Path(__file__)),
    }, indent=2) + '\n')

def main():
    path = OUT / 'invalidation-v1/gfx_pc.c'
    old = path.read_text()
    new = old
    replacements = [
        ('    const uint8_t *addr;          /* imported texture (re-import/animation) */\n', ''),
        ('    uint8_t  cms, cmt;            /* clamp/mirror wrap mode */\n', ''),
        ('        const uint8_t *taddr = textured ? rdp.loaded_texture[0].addr : NULL;\n', ''),
        ('        if (taddr != vc_tex_sig.addr ||\n            comb->cc_id != vc_tex_sig.cc_id ||',
         '        if (comb->cc_id != vc_tex_sig.cc_id ||'),
        ('            tbias != vc_tex_sig.bias ||\n            rdp.texture_tile.cms != vc_tex_sig.cms ||\n            rdp.texture_tile.cmt != vc_tex_sig.cmt) {',
         '            tbias != vc_tex_sig.bias) {'),
        ('            vc_tex_sig.addr     = taddr;\n', ''),
        ('            vc_tex_sig.cms      = rdp.texture_tile.cms;\n', ''),
        ('            vc_tex_sig.cmt      = rdp.texture_tile.cmt;\n', ''),
    ]
    for a, b in replacements:
        assert new.count(a) == 1, a
        new = new.replace(a, b)
    marker = 'static struct {\n    uint32_t cc_id;'
    assert new.count(marker) == 1
    new = new.replace(marker, '''/* Texture address and wrap/clamp modes belong to SET_TRI_STATE. They do
 * not change clip coordinates, raw tile-relative s/t, shade or explicit
 * depth in LOAD_VERT_CLIP. Keep those slots resident while the shared
 * material emitter updates the surface. Retain the existing G_TEXTURE
 * scale dependency conservatively, including optional worker builds. */
static struct {
    uint32_t cc_id;''')
    save('invalidation-v1', 'gfx_pc.c', old, new)

    path = OUT / 'resident-v1/gfx_gpu.c'
    old = path.read_text()
    start = old.index('void gfx_gpu_vtx_cache_batch(')
    end = old.index('\n/* Raw geometry path.', start)
    body = old[start:end]
    marker = '        const uint8_t slot[3] = {job & 31u, (job >> 5) & 31u, (job >> 10) & 31u};'
    assert body.count(marker) == 1
    fast = '''#ifndef OF_PC
        /* Resident slots need only the existing indexed-draw packet. Keep
         * each reservation and the exact 32-draw publication cadence. */
        if ((job & (7u << 15)) == 0) {
            _gpu_stream_reserve_words(2u);
            uint32_t *packet = _gpu_ring_claim();
            packet[0] = ((uint32_t)GPU_CMD_DRAW_INDEXED_TRI << 24) | 1u;
            packet[1] = job & 0x7fffu;
            _gpu_ring_commit(2u);
            g_vc_words += 2u;
            g_vc_tris_cached++;
            if (++g_vc_kick >= 32u) {
                g_vc_kick = 0;
                of_gpu_kick();
            }
            continue;
        }
#endif
'''
    body = body.replace(marker, fast + marker)
    new = old[:start] + body + old[end:]
    save('resident-v1', 'gfx_gpu.c', old, new)
    print('Prepared independent invalidation-v1 and resident-v1 candidates.')

if __name__ == '__main__':
    main()
