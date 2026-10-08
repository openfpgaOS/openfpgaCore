#!/usr/bin/env python3
"""Prepare a strided dirty-lightmap update; keep ordinary uploads unchanged."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-comprehensive-20261008'
FILES = ['src/quake2/openfpga/of_gl1.c', 'src/quake2/openfpga/of_world.h',
         'src/quake2/src/client/refresh/gl1/gl1_surf.c']


def apply_renderer(text):
    old = 'ofgl_lm_store(int block, int x0, int y0, int w, int h, const uint32_t *src)'
    new = 'ofgl_lm_store_stride(int block, int x0, int y0, int w, int h,\n\tconst uint32_t *src, int row_pixels)'
    assert text.count(old) == 1
    text = text.replace(old, new)
    old = 'src + (size_t)(first_row + row) * w + first_col'
    assert text.count(old) == 1
    text = text.replace(old, 'src + (size_t)(first_row + row) * row_pixels + first_col')
    anchor = 'static inline uint16_t\nofgl_lm_sample('
    assert text.count(anchor) == 1
    wrappers = '''static void
ofgl_lm_store(int block, int x0, int y0, int w, int h, const uint32_t *src)
{
\tofgl_lm_store_stride(block, x0, y0, w, h, src, w);
}

/* Dynamic lightmaps already track a dirty rectangle in their full RGBA page.
 * Consume that rectangle directly, without repacking or converting its rows'
 * unchanged columns. Ordinary GL uploads keep their tightly packed stride. */
void
OFGL_UpdateLightmapRect(int texture, int x, int y, int w, int h,
\tint row_pixels, const void *pixels)
{
\tif (!pixels || row_pixels <= 0 || row_pixels < w) return;
\tofgl_lm_store_stride(ofgl_lm_index(texture), x, y, w, h,
\t\t(const uint32_t *)pixels, row_pixels);
}

'''
    return text.replace(anchor, wrappers + anchor)


def apply_header(text):
    anchor = '/* Conservative model-space bounds for the next explicit submission.'
    assert text.count(anchor) == 1
    return text.replace(anchor, '''/* Update an RGBA8888 dirty lightmap rectangle from a larger source page.
 * row_pixels is the source pitch in pixels; it must be positive and >= w.
 * The source pointer addresses the rectangle's first pixel. */
void OFGL_UpdateLightmapRect(int texture, int x, int y, int w, int h,
    int row_pixels, const void *pixels);

''' + anchor)


def apply_surface(text):
    anchor = '#ifdef YQ2_GL1_GLES\n\t\tbase += (best.top * BLOCK_WIDTH) * LIGHTMAP_BYTES;'
    assert text.count(anchor) == 1
    return text.replace(anchor, '''#if defined(YQ2_OPENFPGA) && defined(YQ2_GL1_GLES)
\t\tbase += (best.top * BLOCK_WIDTH + best.left) * LIGHTMAP_BYTES;

\t\tR_Bind(gl_state.lightmap_textures + i + lmtex);
\t\tOFGL_UpdateLightmapRect(gl_state.lightmap_textures + i + lmtex,
\t\t\tbest.left, best.top, best.right - best.left, best.bottom - best.top,
\t\t\tBLOCK_WIDTH, base);
#elif defined(YQ2_GL1_GLES)
\t\tbase += (best.top * BLOCK_WIDTH) * LIGHTMAP_BYTES;''')


def main():
    base = OUT / 'work/base'
    work = OUT / 'work/lightmap_rect'
    component = OUT / 'components/lightmap-rect-v1'
    assert not work.exists() and not component.exists()
    shutil.copytree(base, work)
    component.mkdir(parents=True)
    patch = []
    hashes = {}
    for rel, apply in zip(FILES, [apply_renderer, apply_header, apply_surface]):
        before = (base / rel).read_text()
        after = apply(before)
        (work / rel).write_text(after)
        patch.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        fromfile='a/' + rel, tofile='b/' + rel))
        hashes[rel] = hashlib.sha256(after.encode()).hexdigest()
    (component / 'source.patch').write_text(''.join(patch))
    (component / 'prepared.json').write_text(json.dumps(dict(
        candidate=str(work), files_sha256=hashes,
        scope='Convert the exact dirty rectangle with the original full-page source pitch.'),
        indent=2) + '\n')
    print((component / 'prepared.json').read_text())


if __name__ == '__main__':
    main()
