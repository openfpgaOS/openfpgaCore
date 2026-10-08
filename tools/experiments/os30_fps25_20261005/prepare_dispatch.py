#!/usr/bin/env python3
"""Move CPU-only material invalidation into the state-changing DL cases."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PARENT = ROOT / 'docs/measurements/os30-fps25-20261005/baseline/gfx_pc.c'
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/dispatch-v1'
EXPECTED = '9226056cd448aa70a7ee0b558bf6b351a5e1bb0c7ce3be918ab2b089744338d0'
CASES = [
    'G_MOVEMEM', '(uint8_t)G_MOVEWORD', '(uint8_t)G_TEXTURE',
    'G_GEOMETRYMODE', '(uint8_t)G_SETGEOMETRYMODE', '(uint8_t)G_CLEARGEOMETRYMODE',
    '(uint8_t)G_SETOTHERMODE_L', '(uint8_t)G_SETOTHERMODE_H',
    'G_SETTIMG', 'G_LOADBLOCK', 'G_LOADTILE', 'G_SETTILE', 'G_SETTILESIZE',
    'G_LOADTLUT', 'G_SETENVCOLOR', 'G_SETPRIMCOLOR', 'G_SETFOGCOLOR',
    'G_SETFILLCOLOR', 'G_SETCOMBINE', 'G_FILLRECT',
    'G_SETSCISSOR', 'G_SETZIMG', 'G_SETCIMG',
]
HELPER = '''/* Ordinary state commands cannot prepare a new triangle material. Only
 * rectangles need a second invalidation after their internal triangles.
 * The optional worker keeps its original outer classification/dispatch. */
static inline __attribute__((always_inline)) void gfx_dl_invalidate_material(void) {
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
    triangle_material_ready = false;
#endif
}

'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(original):
    assert sha(original.encode()) == EXPECTED
    start = original.index('static void gfx_run_dl(Gfx* cmd) {')
    end = original.index('\nstatic void gfx_sp_reset()', start)
    old = original[start:end]
    body = old
    edits = []

    def replace(a, b):
        nonlocal body
        assert body.count(a) == 1, a
        body = body.replace(a, b, 1)
        edits.append((a, b))

    replace('''#ifdef TARGET_OPENFPGA
        const bool triangle_opcode''', '''#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP
        const bool triangle_opcode''')
    replace('''#ifdef TARGET_OPENFPGA
        // Rectangles restore temporary combiner state''', '''#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP
        // Rectangles restore temporary combiner state''')
    for case in CASES:
        anchor = f'            case {case}:\n'
        replace(anchor, anchor + '                gfx_dl_invalidate_material();\n')
    rect = '''            case G_TEXRECT:
            case G_TEXRECTFLIP:
            {
'''
    replace(rect, rect + '                gfx_dl_invalidate_material();\n')
    for call in [
        '                gfx_dp_texture_rectangle(ulx, uly, lrx, lry, tile, uls, ult, dsdx, dtdy, opcode == G_TEXRECTFLIP);\n',
        '                gfx_dp_fill_rectangle(ulx, uly, lrx, lry);\n',
        '                gfx_dp_fill_rectangle(C1(12, 12), C1(0, 12), C0(12, 12), C0(0, 12));\n',
    ]:
        replace(call, call + '                gfx_dl_invalidate_material();\n')
    tail = '''                gfx_dp_set_color_image(C0(21, 3), C0(19, 2), C0(0, 11), seg_addr(cmd->words.w1));
                break;
        }
'''
    replacement = tail[:-len('        }\n')] + '''            default:
                /* Unknown/no-op commands used to invalidate the memo too.
                 * GBI2 calls TRI2 a triangle in the old classifier even
                 * though this switch does not implement that opcode. */
#ifdef F3DEX_GBI_2
                if (opcode != (uint8_t)G_TRI2)
#endif
                    gfx_dl_invalidate_material();
                break;
        }
'''
    replace(tail, replacement)
    restored = body
    for a, b in reversed(edits):
        assert restored.count(b) == 1
        restored = restored.replace(b, a, 1)
    assert restored == old
    return original[:start] + HELPER + body + original[end:]


def main():
    original = PARENT.read_text()
    candidate = transform(original)
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / 'gfx_pc.c'
    assert not dest.exists() or dest.read_text() == candidate
    dest.write_text(candidate)
    (OUT / 'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='a/gfx_pc.c', tofile='b/gfx_pc.c')))
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    manifest = {
        'parent_sha256': sha(original.encode()),
        'candidate_sha256': sha(candidate.encode()),
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'qualified': False,
        'reverse_source_exact': True,
        'scope': 'CPU-only display-list material invalidation at individual state-changing cases; no per-command material classifier or live material-opcode boolean across switch.',
        'invalidate_before': CASES + ['G_TEXRECT', 'G_TEXRECTFLIP'],
        'invalidate_after': ['G_TEXRECT', 'G_TEXRECTFLIP', 'G_FILLRECT'],
        'preserve': ['G_MTX', 'G_POPMTX', 'G_VTX', 'G_DL', 'G_ENDDL', 'G_TRI1', 'G_TRI2'],
        'unknown': 'Invalidate once for no-op/unknown commands, except unimplemented F3DEX_GBI_2 TRI2, which the original classifier explicitly preserves.',
        'worker_override': 'APP=1 retains original classifier, worker dispatch and both outer invalidations; inserted helper calls compile to empty. Non-OpenFPGA behavior unchanged.',
        'proof': 'Each ordinary state handler contains no triangle-material preparation, so a second false assignment after it is redundant. Rectangles retain both invalidations because internal triangles can make the memo ready before temporary state is restored. All handlers and command/triangle decoding are otherwise literal.',
        'remaining': 'Independent handler closure/source review, emitted code audit, paired scene capture and exact GPU replay. No performance claim.'
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
