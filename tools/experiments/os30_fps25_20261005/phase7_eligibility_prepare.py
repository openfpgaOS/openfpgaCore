#!/usr/bin/env python3
"""Memoize the complete CPU indexed-run material predicate in the F8 source."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase6/candidate/gfx_pc.c'
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase7/eligibility-v1'
EXPECTED = '1e059331b48b6e975691e35ab4a0497719fb9711789c86f120ac98e6b1ce31af'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(source):
    assert sha(source.encode()) == EXPECTED
    replacements = []

    def replace(before, after, count=1):
        nonlocal source
        assert source.count(before) == count, before
        source = source.replace(before, after)
        replacements.append((before, after, count))

    before = 'static bool triangle_vc_texture_ready;\n'
    replace(before, before + '''#if !GFX_GPU_GEOMETRY_WORKER_APP
/* Indexed runs require one alpha for the complete run and no per-triangle
 * LOD inputs. Cache this full predicate with the material, so known fallback
 * materials never repeat speculative clip/cull work at each TRI command. */
static bool triangle_cpu_indexed_elig;
static inline __attribute__((always_inline)) bool gfx_cpu_indexed_material_eligible(
        const struct ColorCombiner *comb, bool use_fog) {
    const int rgb_in = triangle_vc_rgb_input, alpha_in = triangle_vc_alpha_input;
    if (!triangle_vc_elig ||
        (rgb_in >= 0 && comb->shader_input_mapping[0][rgb_in] == CC_LOD))
        return false;
    if (alpha_in >= 0) {
        const uint8_t source = comb->shader_input_mapping[1][alpha_in];
        if (source == CC_LOD || (source == CC_SHADE && !use_fog)) return false;
    }
    return true;
}
#endif
''')
    before = '''        triangle_vc_elig = gfx_gpu_vtx_cache_begin(&triangle_vc_rgb_input,
                                                &triangle_vc_alpha_input);
'''
    replace(before, before + '''#if !GFX_GPU_GEOMETRY_WORKER_APP
        triangle_cpu_indexed_elig = gfx_cpu_indexed_material_eligible(comb, use_fog);
#endif
''')
    replace('''            if (!elig || (rgb_in >= 0 && comb->shader_input_mapping[0][rgb_in] == CC_LOD))
                goto submit;
''', '''            if (!triangle_cpu_indexed_elig) goto submit;
''')
    replace('''                if (source == CC_LOD || (source == CC_SHADE && !use_fog)) goto submit;
''', '')
    replace('''                if (!triangle_material_ready || triangle_vc_elig) {
''', '''                if (!triangle_material_ready || triangle_cpu_indexed_elig) {
''', 2)
    return source, replacements


def main():
    original = BASE.read_text()
    candidate, replacements = transform(original)
    destination = OUT / 'gfx_pc.c'
    assert destination.read_text() in (original, candidate)
    destination.write_text(candidate)
    (OUT / 'eligibility.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='F8/gfx_pc.c', tofile='eligibility-v1/gfx_pc.c')))
    record = dict(parent_sha256=sha(original.encode()),
        candidate_sha256=sha(candidate.encode()), recipe_sha256=sha(Path(__file__).read_bytes()),
        changes='Complete indexed-run material eligibility memo only; original lazy preparation and whole-command acceptance retained.',
        qualified=False)
    (OUT / 'eligibility-source.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
