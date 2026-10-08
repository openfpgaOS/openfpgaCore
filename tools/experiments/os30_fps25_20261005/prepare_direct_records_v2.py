#!/usr/bin/env python3
"""Keep representation dispatch outside the display-list interpreter."""
import difflib
import json
from pathlib import Path

from prepare_direct_records import BASE, DEST as V1, HASHES, ROOT, once, sha

DEST = ROOT / 'docs/measurements/os30-fps25-20261005/direct-record-v2'


def main():
    manifest = json.loads((V1 / 'manifest.json').read_text())
    sources = {name: (V1 / name).read_bytes() for name in HASHES}
    for name, data in sources.items():
        assert sha(data) == manifest['files'][name]['candidate_sha256'], name
    pc = sources['gfx_pc.c'].decode()
    gpu = sources['gfx_gpu.c'].decode()
    pc = once(pc, '''static bool buf_vbo_direct;
extern void gfx_gpu_draw_direct_records(const struct gfx_vc_vtx *vertices,
                                        size_t num_tris);''', '''/* The representation selector is consumed synchronously by draw_triangles.
 * Keep dispatch in that backend so every inlined gfx_flush remains small. */
bool gfx_gpu_direct_batch;
#define buf_vbo_direct gfx_gpu_direct_batch''')
    pc = once(pc, '''#ifdef TARGET_OPENFPGA
        if (buf_vbo_direct)
            gfx_gpu_draw_direct_records(buf_vbo_storage.direct, buf_vbo_num_tris);
        else
#endif
            gfx_rapi->draw_triangles(buf_vbo, buf_vbo_len, buf_vbo_num_tris);''', '''        gfx_rapi->draw_triangles(buf_vbo, buf_vbo_len, buf_vbo_num_tris);''')
    gpu = once(gpu, '''static void gpu_draw_triangles(float buf_vbo[], size_t buf_vbo_len,
                               size_t buf_vbo_num_tris) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);''', '''extern bool gfx_gpu_direct_batch;
static void gpu_draw_triangles(float buf_vbo[], size_t buf_vbo_len,
                               size_t buf_vbo_num_tris) {
    /* The caller's union contains live gfx_vc_vtx objects in direct mode.
     * The float pointer carries their aligned address; no float is read. */
    if (gfx_gpu_direct_batch) {
        gfx_gpu_draw_direct_records((const struct gfx_vc_vtx *)(const void *)buf_vbo,
                                    buf_vbo_num_tris);
        return;
    }
    SM64_PERF_SCOPE(SM64_PERF_SEND);''')
    outputs = {'gfx_pc.c': pc.encode(), 'gfx_gpu.c': gpu.encode()}
    DEST.mkdir(exist_ok=False)
    for name, data in outputs.items():
        base = (BASE / name).read_bytes()
        assert sha(base) == HASHES[name], name
        (DEST / name).write_bytes(data)
        (DEST / (name + '.patch')).write_text(''.join(difflib.unified_diff(
            base.decode().splitlines(True), data.decode().splitlines(True),
            fromfile='a/' + name, tofile='b/' + name)))
        manifest['files'][name]['candidate_sha256'] = sha(data)
    manifest['candidate'] = DEST.name
    manifest['parent_candidate'] = V1.name
    manifest['parent_manifest_sha256'] = sha((V1 / 'manifest.json').read_bytes())
    manifest['recipe_sha256'] = sha(Path(__file__).read_bytes())
    manifest['scope'] += ' Representation dispatch moved out of inlined gfx_flush.'
    manifest['qualified'] = False
    (DEST / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (DEST / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    (DEST / 'prepare_direct_records.py').write_bytes(Path(__file__).with_name('prepare_direct_records.py').read_bytes())
    print(json.dumps(manifest['files'], indent=2))


if __name__ == '__main__':
    main()
