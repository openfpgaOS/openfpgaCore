#!/usr/bin/env python3
"""Prepare a conservative, current-framebuffer-only duplicate clear elision."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-comprehensive-20261008'
B = P / 'work/base'
W = P / 'work/clear'
C = P / 'components/clear-v1'
GL = 'src/quake2/openfpga/of_gl1.c'
BASE = '29818e61c88a358f30cde8a4282676c6904a71b12b77c98934844233ea84fbdc'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new, 1)

def apply(text):
    text = replace_once(text, 'static uint8_t *ofgl_framebuffer;', '''static uint8_t *ofgl_framebuffer;

/* Only a byte-uniform GPU clear can establish this state. The pointer is
 * invalidated by every framebuffer rebind and CPU/GPU colour drawing path.
 * Keep the eager post-flip clear: it overlaps client work and serves 2D-only
 * frames. This merely avoids clearing its untouched result again in R_Clear. */
static struct
{
\tuint16_t *fb;
\tint width, height, stride;
\tuint16_t color;
} ofgl_fb_clear;''')
    text = replace_once(text, '\tuint8_t *fb = of_video_surface();', '\tuint8_t *fb = of_video_surface();\n\tofgl_fb_clear.fb = NULL;')
    text = replace_once(text, '''\t\tOFQ2_ClearRect((uint8_t *)fb16, ofgl_fb_stride, ofgl_fb_width * 2,
\t\t\tofgl_fb_height, (uint8_t)(ofgl_clear565 & 0xFF));
\t\tofgl_gpu_pending = 1;
\t\treturn;
\t}

#if OFGL_DBG_GRADIENT''', '''\t\tif (ofgl_fb_clear.fb == fb16 &&
\t\t\tofgl_fb_clear.color == ofgl_clear565 &&
\t\t\tofgl_fb_clear.width == ofgl_fb_width &&
\t\t\tofgl_fb_clear.height == ofgl_fb_height &&
\t\t\tofgl_fb_clear.stride == ofgl_fb_stride)
\t\t{
\t\t\t/* The earlier clear may still be queued: preserve pending state. */
\t\t\treturn;
\t\t}
\t\tOFQ2_ClearRect((uint8_t *)fb16, ofgl_fb_stride, ofgl_fb_width * 2,
\t\t\tofgl_fb_height, (uint8_t)(ofgl_clear565 & 0xFF));
\t\tofgl_gpu_pending = 1;
\t\tofgl_fb_clear.fb = fb16;
\t\tofgl_fb_clear.color = ofgl_clear565;
\t\tofgl_fb_clear.width = ofgl_fb_width;
\t\tofgl_fb_clear.height = ofgl_fb_height;
\t\tofgl_fb_clear.stride = ofgl_fb_stride;
\t\treturn;
\t}

\t/* CPU fallback/diagnostic fills do not establish reusable GPU state. */
\tofgl_fb_clear.fb = NULL;
#if OFGL_DBG_GRADIENT''')
    text = replace_once(text, '''\tif (!textured)
\t{
\t\t/* solid fill.''', '''\t/* Both solid/alpha fills and textured HUD blits write colour here. */
\tofgl_fb_clear.fb = NULL;
\tif (!textured)
\t{
\t\t/* solid fill.''')
    text = replace_once(text, '''\tofgl_ensure_flat_tex();

\t/* Is this a lightmapped world batch?''', '''\t/* Every CPU-prepared, indexed and raw-world GPU draw enters here. */
\tofgl_fb_clear.fb = NULL;
\tofgl_ensure_flat_tex();

\t/* Is this a lightmapped world batch?''')
    text = replace_once(text, '''\t(void)win;\t/* no SDL_GL context; we bind our own gl* table */''', '''\tofgl_fb_clear.fb = NULL;
\t(void)win;\t/* no SDL_GL context; we bind our own gl* table */''')
    text = replace_once(text, '''\tofgl_framebuffer = NULL;
\tfor (int i''', '''\tofgl_framebuffer = NULL;
\tofgl_fb_clear.fb = NULL;
\tfor (int i''')
    return text

def prepare():
    assert sha(B / GL) == BASE and not W.exists() and not C.exists()
    C.mkdir(parents=True)
    shutil.copytree(B, W, ignore=shutil.ignore_patterns('.obj', 'build', '__pycache__'))
    old = (B / GL).read_text()
    new = apply(old)
    (W / GL).write_text(new)
    (C / 'of_gl1.c').write_text(new)
    (C / 'source.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile='a/' + GL, tofile='b/' + GL)))
    shutil.copy2(__file__, C / 'recipe.py')
    (C / 'prepared.json').write_text(json.dumps(dict(
        parent_sha256=BASE, source_sha256=sha(W / GL), worktree=str(W),
        scope='Elide only a byte-uniform full colour clear with identical current framebuffer, colour, dimensions and stride and no intervening colour writer. Keep depth clear, eager clear, publish, fence, viewport and HUD behavior.'), indent=2) + '\n')
    print((C / 'prepared.json').read_text())

if __name__ == '__main__':
    prepare()
