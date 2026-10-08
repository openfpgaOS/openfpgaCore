#!/usr/bin/env python3
"""Decode adjacent CPU triangles in one loop without crossing state commands."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PARENT = ROOT / 'docs/measurements/os30-fps25-20261005/baseline/gfx_pc.c'
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/triangle-run-v1'
EXPECTED = '9226056cd448aa70a7ee0b558bf6b351a5e1bb0c7ce3be918ab2b089744338d0'
HELPER = '''
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
#define GFX_CPU_TRIANGLE_RUN 1
/* Triangle commands preserve RSP/material state. Keep their original decode
 * and draw order, stopping before the first command that can change state. */
static __attribute__((noinline)) Gfx *gfx_cpu_triangle_run(Gfx *cmd) {
    for (;;) {
        uint32_t opcode = cmd->words.w0 >> 24;
        if (opcode == (uint8_t)G_TRI1) {
#ifdef F3DEX_GBI_2
            gfx_sp_tri1(C0(16, 8) / 2, C0(8, 8) / 2, C0(0, 8) / 2);
#elif defined(F3DEX_GBI) || defined(F3DLP_GBI)
            gfx_sp_tri1(C1(16, 8) / 2, C1(8, 8) / 2, C1(0, 8) / 2);
#else
            gfx_sp_tri1(C1(16, 8) / 10, C1(8, 8) / 10, C1(0, 8) / 10);
#endif
#if defined(F3DEX_GBI) || defined(F3DLP_GBI)
        } else if (opcode == (uint8_t)G_TRI2) {
            gfx_sp_tri1(C0(16, 8) / 2, C0(8, 8) / 2, C0(0, 8) / 2);
            gfx_sp_tri1(C1(16, 8) / 2, C1(8, 8) / 2, C1(0, 8) / 2);
#endif
        } else {
            return cmd;
        }
        ++cmd;
    }
}
#else
#define GFX_CPU_TRIANGLE_RUN 0
#endif
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(original):
    assert sha(original.encode()) == EXPECTED
    anchor = '#define C1(pos, width) ((cmd->words.w1 >> (pos)) & ((1U << width) - 1))\n'
    assert original.count(anchor) == 1
    candidate = original.replace(anchor, anchor + HELPER, 1)
    start = candidate.index('            case (uint8_t)G_TRI1:\n', candidate.index('static void gfx_run_dl'))
    end = candidate.index('            case (uint8_t)G_SETOTHERMODE_L:', start)
    before = candidate[start:end]
    first_start = '            case (uint8_t)G_TRI1:\n'
    first_end = '                break;\n#if defined(F3DEX_GBI) || defined(F3DLP_GBI)\n'
    second_start = '            case (uint8_t)G_TRI2:\n'
    second_end = '                break;\n#endif\n'
    after = before
    for old, new in [
        (first_start, first_start + '''#if GFX_CPU_TRIANGLE_RUN
                cmd = gfx_cpu_triangle_run(cmd);
                continue;
#else
'''),
        (first_end, '                break;\n#endif /* GFX_CPU_TRIANGLE_RUN */\n#if defined(F3DEX_GBI) || defined(F3DLP_GBI)\n'),
        (second_start, second_start + '''#if GFX_CPU_TRIANGLE_RUN
                cmd = gfx_cpu_triangle_run(cmd);
                continue;
#else
'''),
        (second_end, '                break;\n#endif /* GFX_CPU_TRIANGLE_RUN */\n#endif\n'),
    ]:
        assert after.count(old) == 1, old
        after = after.replace(old, new, 1)
    candidate = candidate[:start] + after + candidate[end:]
    restored = candidate.replace(anchor + HELPER, anchor, 1).replace(after, before, 1)
    assert restored == original
    return candidate


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
        'scope': 'CPU-only contiguous TRI1/TRI2 command loop invoking unchanged gfx_sp_tri1 with original bitfields and divisions.',
        'preserved': [
            'Same triangle order, including the first and second triangles in each TRI2 opcode.',
            'Stops before every non-triangle command; outer loop receives that exact command pointer and does not increment it before dispatch.',
            'No triangle decode support added for unsupported GBI2 TRI2; it is returned to the original switch unchanged.',
            'Triangle opcodes preserve the material memo, so skipping the outer post-switch false material_opcode check is equivalent.',
            'Worker-enabled and non-OpenFPGA builds retain their original outer dispatch and handlers.',
        ],
        'remaining': 'Independent control-flow/decode review, target codegen, paired capture/cycles and exact GPU replay. No speedup claimed.'
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
