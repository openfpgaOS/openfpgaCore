#!/usr/bin/env python3
"""Remove fog deferral's triangle tax by resolving at a fog-mode transition."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/baseline/gfx_pc.c'
PARENT = ROOT / 'docs/measurements/os30-fps25-20261005/deferred-fog-v1/gfx_pc.c'
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/deferred-fog-v2'
EXPECTED = '7d829cde2e0310c7e272aa01c0880207e5f4728b1bc401011b57f553bbebb324'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(original):
    assert sha(original.encode()) == EXPECTED
    candidate = original
    edits = []

    def replace(a, b):
        nonlocal candidate
        assert candidate.count(a) == 1, a
        candidate = candidate.replace(a, b, 1)
        edits.append((a, b))

    replace('    const bool defer_fog = fog_active && dest_index < 32 && n_vertices <= 32 - dest_index;',
            '''    const bool defer_fog = fog_active && (rdp.other_mode_l >> 30) == G_BL_CLR_FOG
        && dest_index < 32 && n_vertices <= 32 - dest_index;''')
    start = candidate.index('#if GFX_CPU_DEFERRED_FOG\n    /* A non-fog draw can consume shade alpha')
    end = candidate.index('#ifdef GFX_MANUAL_CLIPPING\n    if (!gfx_clip_triangle', start)
    triangle_insert = candidate[start:end]
    replace(triangle_insert, '')
    replace('''    om = (om & ~mask) | mode;
    rdp.other_mode_l = (uint32_t)om;''', '''    om = (om & ~mask) | mode;
#if GFX_CPU_DEFERRED_FOG
    /* The only assignment to other_mode_l: resolve the saved alpha before
     * any later draw can leave the backend's fog/opaque-shade convention. */
    if (cpu_fog_pending && ((uint32_t)om >> 30) != G_BL_CLR_FOG)
        gfx_cpu_flush_fog(0xffffffffu);
#endif
    rdp.other_mode_l = (uint32_t)om;''')
    frame = '    { SM64_PERF_SCOPE(SM64_PERF_DL); gfx_run_dl(commands); }\n'
    replace(frame, frame + '''#if GFX_CPU_DEFERRED_FOG
    /* Fog configuration can change between frames. Finish the last pending
     * batch now; earlier overwritten batches avoided the unused alpha work. */
    gfx_cpu_flush_fog(0xffffffffu);
#endif
''')
    # The sole removal is proved by reinserting at its unchanged next anchor.
    restored = candidate
    for a, b in reversed(edits):
        if b:
            assert restored.count(b) == 1
            restored = restored.replace(b, a, 1)
        else:
            anchor = '#ifdef GFX_MANUAL_CLIPPING\n    if (!gfx_clip_triangle'
            assert restored.count(anchor) == 1
            restored = restored.replace(anchor, a + anchor, 1)
    assert restored == original
    assert 'A non-fog draw can consume shade alpha' not in candidate
    return candidate


def main():
    original = PARENT.read_text()
    candidate = transform(original)
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / 'gfx_pc.c'
    assert not dest.exists() or dest.read_text() == candidate
    dest.write_text(candidate)
    (OUT / 'change.patch').write_text(''.join(difflib.unified_diff(
        BASE.read_text().splitlines(True), candidate.splitlines(True),
        fromfile='a/gfx_pc.c', tofile='b/gfx_pc.c')))
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    manifest = {
        'parent_sha256': sha(original.encode()),
        'baseline_sha256': sha(BASE.read_bytes()),
        'candidate_sha256': sha(candidate.encode()),
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'qualified': False,
        'reverse_source_exact': True,
        'scope': 'Defer only otherwise-unused fog alpha while both vertex and render fog are active; resolve at fog disable rather than checking every triangle.',
        'lifetime': [
            'All v1 pending-batch overwrite and signed coefficient snapshot handling is unchanged.',
            'Only a load with configEnableFog, G_FOG and RDP G_BL_CLR_FOG active may create pending fog alpha.',
            'gfx_sp_set_other_mode contains the only assignment to other_mode_l and resolves pending alpha before installing any non-fog value.',
            'Frame-end resolves pending alpha before configEnableFog may change externally. Source contains no added per-triangle checks.',
            'Rectangle helpers temporarily change only other_mode_h and geometry_mode, use scratch slots64..67, and restore those fields. They do not consume or overwrite pending loaded slots.',
            'Every non-fog draw therefore sees materialized source alpha before clipping; every still-fogged draw ignores intensity and forces opaque shade alpha in both backend routes.',
        ],
        'remaining': 'Independent mode-transition review, target codegen, paired capture/cycles and exact GPU replay. No performance claim.'
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
