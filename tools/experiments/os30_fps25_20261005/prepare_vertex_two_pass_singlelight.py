#!/usr/bin/env python3
"""Prepare a private batch-specialized eager single-light attribute pass."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p/'src/fpga').is_dir() and (p/'docs/measurements').is_dir())
CAMPAIGN = ROOT/'docs/measurements/os30-fps25-20261005/phase2'
PARENT = CAMPAIGN/'vertex-two-pass-v1'
NAME = 'vertex-two-pass-singlelight-v1'
FILES = ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h', 'gfx_geometry.h',
         'perf_pocket.h', 'perf_pocket.c', 'Makefile.sm64')
PARENT_PC = 'e87e304cc9a49d53589e50e208411f7c2a41f9e4e4152e286769ad8b42ebdd60'
sha = lambda data: hashlib.sha256(data).hexdigest()

SPECIALIZED = '''#if defined(TARGET_OPENFPGA) && !defined(OF_DBG_FACE)
    /* Most batches use one diffuse light plus ambient without texgen.
     * Select once, after the transform pass, so lighting/UV scalars need
     * not compete with the sixteen matrix values for FP registers.
     * Keep the generic loop below for all other modes and debug probes. */
    if ((rsp.geometry_mode & (G_LIGHTING | G_TEXTURE_GEN)) == G_LIGHTING &&
        rsp.current_num_lights == 2) {
        const uint16_t scale_s = rsp.texture_scaling_factor.s;
        const uint16_t scale_t = rsp.texture_scaling_factor.t;
        const float coeff0 = RSPV_TO_FLOAT(rsp.current_lights_coeffs[0][0]);
        const float coeff1 = RSPV_TO_FLOAT(rsp.current_lights_coeffs[0][1]);
        const float coeff2 = RSPV_TO_FLOAT(rsp.current_lights_coeffs[0][2]);
        const int ambient_r = rsp.current_lights[1].col[0];
        const int ambient_g = rsp.current_lights[1].col[1];
        const int ambient_b = rsp.current_lights[1].col[2];
        const float diffuse_r = rsp.current_lights[0].col[0];
        const float diffuse_g = rsp.current_lights[0].col[1];
        const float diffuse_b = rsp.current_lights[0].col[2];
        for (size_t i = 0; i < n_vertices; i++) {
            const Vtx_t *v = &vertices[i].v;
            const Vtx_tn *vn = &vertices[i].n;
            struct LoadedVertex *d = &rsp.loaded_vertices[attribute_dest_index + i];
            short U = v->tc[0] * scale_s >> 16;
            short V = v->tc[1] * scale_t >> 16;

            const uint32_t normal = gfx_lighting_normal_key(v->cn);
            struct LightingMemo *memo = &lighting_memo[(normal ^ (normal >> 11)) & 255u];
            if (memo->epoch == lighting_epoch && memo->normal == normal) {
                d->color.r = memo->color.r;
                d->color.g = memo->color.g;
                d->color.b = memo->color.b;
            } else {
                int r = ambient_r;
                int g = ambient_g;
                int b = ambient_b;
                float intensity = 0;
                intensity += vn->n[0] * coeff0;
                intensity += vn->n[1] * coeff1;
                intensity += vn->n[2] * coeff2;
                intensity /= 127.0f;
                if (intensity > 0.0f) {
                    r += intensity * diffuse_r;
                    g += intensity * diffuse_g;
                    b += intensity * diffuse_b;
                }
                d->color.r = r < 0 ? 0 : r > 255 ? 255 : r;
                d->color.g = g < 0 ? 0 : g > 255 ? 255 : g;
                d->color.b = b < 0 ? 0 : b > 255 ? 255 : b;
                memo->epoch = lighting_epoch;
                memo->normal = normal;
                memo->color.r = d->color.r;
                memo->color.g = d->color.g;
                memo->color.b = d->color.b;
            }
            d->u = RSPV_FROM_INT(U);
            d->v = RSPV_FROM_INT(V);
        }
    } else
#endif
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=CAMPAIGN/NAME,
                        help='New private output folder; existing folders are never overwritten.')
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists(), output
    source = (PARENT/'gfx_pc.c').read_text()
    assert sha(source.encode()) == PARENT_PC
    anchor = '    /* Finish the eager UV/color work after the matrix registers die. */\n'
    assert source.count(anchor) == 1
    candidate = source.replace(anchor, anchor + SPECIALIZED)
    assert candidate.replace(anchor + SPECIALIZED, anchor) == source
    begin = source.index(anchor) + len(anchor)
    end = source.index('    }\n#ifdef TARGET_POCKET\n    prof_vtx', begin)
    original_loop = source[begin:end]
    assert candidate.count(original_loop) == 1
    assert 'd->color.a' not in SPECIALIZED and 'd->clip_rej' not in SPECIALIZED
    assert SPECIALIZED.count('for (') == 1
    assert 'intensity /= 127.0f;' in SPECIALIZED
    assert not any(token in SPECIALIZED for token in ['needed', 'pending', 'gfx_materialize'])
    output.mkdir(parents=True)
    parent_hashes = {}
    for name in FILES:
        parent_hashes[name] = sha((PARENT/name).read_bytes())
        shutil.copy2(PARENT/name, output/name)
    (output/'gfx_pc.c').write_text(candidate)
    source_hashes = {name:sha((output/name).read_bytes()) for name in FILES}
    changed = [name for name in FILES if source_hashes[name] != parent_hashes[name]]
    assert changed == ['gfx_pc.c']
    script = Path(__file__).read_bytes()
    (output/'prepare.py').write_bytes(script)
    (output/'change.patch').write_text(''.join(difflib.unified_diff(
        source.splitlines(True), candidate.splitlines(True),
        fromfile='vertex-two-pass-v1/gfx_pc.c', tofile=NAME+'/gfx_pc.c')))
    review = dict(source_review_passed=True, runtime_qualified=False,
        source_sha256=source_hashes['gfx_pc.c'], parent_sha256=PARENT_PC,
        original_generic_loop_sha256=sha(original_loop.encode()),
        proof={
            'parent_plus_single_insertion_exact': True,
            'generic_second_loop_byte_exact': True,
            'position_clip_reciprocal_alpha_fog_and_preamble_unchanged': True,
            'other_six_files_byte_exact': True,
            'memo_lookup_key_hash_epoch_collision_and_write_order_preserved': True,
            'per_vertex_intensity_addition_order_and_div127_preserved': True,
            'integer_compound_assignments_and_clamps_preserved': True,
            'UV_integer_multiply_shift_short_and_float_conversion_preserved': True,
            'no_per_triangle_checks_or_new_pending_state': True,
            'debug_face_and_non_openfpga_use_original_loop': True},
        review=[
            'The fast path requires lighting, no G_TEXTURE_GEN, and exactly two current lights including ambient. All other modes retain the exact original loop.',
            'The unchanged preamble prepares light coefficients and increments the lighting epoch before the batch. All seven source files are copied from the frozen two-pass parent.',
            'Hoisted uint16 UV scales preserve integer promotions and signed right-shift behavior. RSPV_FROM_INT still receives the original short intermediate.',
            'Hoisted diffuse RGB bytes convert exactly to float (0..255), matching their implicit conversion in the original intensity products. Ambient starts remain int.',
            'Intensity still starts at zero, accumulates normal x/y/z in that order, divides by 127.0f, then uses the same positive check, int compound assignments and clamps. No source reciprocal folding or reassociation was introduced.',
            'Memo hits still copy only RGB. Misses store the same epoch, full normal key and RGB in the same vertex order, so collisions and repeated normals behave identically. Alpha and fog state remain untouched.',
            'The source array and private loaded_vertices storage are distinct for supported G_VTX callers. No callback or RSP state mutation occurs inside the specialized loop; hoisted state is stable for its duration.',
            'OF_DBG_FACE retains the original loop and all diagnostic side effects. No extra helper call, per-triangle mask or deferred attribute state is introduced.',
            'The compiler can change contraction/register allocation under existing fast-math flags. Exact produced-code and replay qualification remain required; source review is not a runtime equivalence claim.'],
        remaining=['Build/code-generation inspection by root', 'Paired normal/FPS model capture',
                   'Exact gameplay and A GPU color/depth comparisons if performance passes'])
    (output/'source-review.json').write_text(json.dumps(review, indent=2)+'\n')
    manifest = dict(parent='vertex-two-pass-v1', candidate=NAME, qualified=False,
        source_sha256=source_hashes['gfx_pc.c'], parent_sha256=PARENT_PC,
        files=source_hashes, parent_files=parent_hashes, recipe_sha256=sha(script),
        changed_files=changed, original_generic_loop_sha256=sha(original_loop.encode()),
        change='Batch-level single diffuse plus ambient specialization of the second eager UV/RGB loop; original arithmetic and memo retained; generic fallback untouched.',
        specialization='TARGET_OPENFPGA, !OF_DBG_FACE, lighting enabled, texgen disabled, current_num_lights==2',
        source_review_sha256=sha((output/'source-review.json').read_bytes()),
        builds_run=False, stage_modified=False, siblings_modified=False,
        required_checks=review['remaining'])
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__': main()
