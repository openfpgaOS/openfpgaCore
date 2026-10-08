#!/usr/bin/env python3
"""Prepare exact early Q16.16 scaling for stable cached vertices only."""
import ctypes
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import struct

from prepare_lighting_layout import ROOT, OUT, BASE, HASHES

DEST = OUT / 'cached-uv-scale-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def once(source, before, after):
    assert source.count(before) == 1, (source.count(before), before[:100])
    return source.replace(before, after, 1)


def range_proof():
    # The full uint16 storage range is stronger than actual12-bit G_SETTILESIZE.
    # All U-tile*8 differences form a contiguous integer interval.
    lower, upper = -32768 - 65535 * 8, 32767
    pack = lambda x: struct.pack('<f', x)
    f32 = lambda x: struct.unpack('<f', pack(x))[0]
    cases = 0
    minimum, maximum = 0, 0
    for difference in range(lower, upper + 1):
        for bias in (0.0, 0.5):
            old_texels = f32(f32(difference / 32.0) + bias)
            old = f32(old_texels * 65536.0)
            new = f32(f32(difference * 2048.0) + f32(bias * 65536.0))
            # Binary64 exactly represents these products/sums, so one final
            # f32 rounding also checks both contracted multiply-add forms.
            old_contracted = f32(f32(difference / 32.0 + bias) * 65536.0)
            new_contracted = f32(difference * 2048.0 + bias * 65536.0)
            exact = (difference + (16 if bias else 0)) * 2048
            assert -(1 << 31) <= exact < (1 << 31)
            assert old == new == old_contracted == new_contracted == exact
            assert pack(old) == pack(new) == pack(old_contracted) == pack(new_contracted)
            minimum, maximum = min(minimum, exact), max(maximum, exact)
            cases += 1
    return {'passed': True, 'cases': cases, 'U_range': [-32768, 32767],
            'tile_storage_range': [0, 65535], 'difference_range': [lower, upper],
            'bias_values': [0, 0.5], 'q16_range': [minimum, maximum],
            'ordinary_and_contracted_binary32_chains_equal': True,
            'proof': 'All operations are exact: integer difference and difference+16 need at most20 significant binary digits. Scaling by powers of two changes only the exponent. Division by32, addition of0/.5, multiplication by65536 and the rewritten multiplication by2048/addition of0/32768 therefore have no rounding error, with or without FMA. Final values are integral and fit int32, so lrintf rounding mode cannot change their integer result. A signed-zero difference also converts to the same integer0.',
            'scope': 'Exhaustive effective integer difference domain; no C compiler, target CPU, gameplay or timing claim.'}


def layout_proof():
    f, b, w = ctypes.c_float, ctypes.c_uint8, ctypes.c_uint32
    class Old(ctypes.Structure):
        _fields_ = [(n, f) for n in ('cx', 'cy', 'cw', 'w_inv', 'u', 'v')] + \
                   [(n, b) for n in ('r', 'g', 'b', 'a')] + [('exact_depth', w), ('use_exact_depth', b)]
    class U(ctypes.Union):
        _fields_ = [('u', f), ('u_q16', f)]
    class V(ctypes.Union):
        _fields_ = [('v', f), ('v_q16', f)]
    class New(ctypes.Structure):
        _anonymous_ = ('U', 'V')
        _fields_ = [(n, f) for n in ('cx', 'cy', 'cw', 'w_inv')] + [('U', U), ('V', V)] + \
                   [(n, b) for n in ('r', 'g', 'b', 'a')] + [('exact_depth', w), ('use_exact_depth', b)]
    assert ctypes.sizeof(f) == ctypes.sizeof(w) == 4 and ctypes.sizeof(b) == 1
    assert ctypes.sizeof(Old) == ctypes.sizeof(New) == 36
    offsets = {name: getattr(Old, name).offset for name, _ in Old._fields_}
    assert all(getattr(New, name).offset == offset for name, offset in offsets.items())
    assert New.u_q16.offset == Old.u.offset == 16 and New.v_q16.offset == Old.v.offset == 20
    return {'passed_fixed_width_layout': True, 'size_before': 36, 'size_after': 36,
            'field_offsets': offsets, 'aliases': {'u_q16': 16, 'v_q16': 20},
            'scope': 'Same-width float unions preserve existing scalar layout; target compile/disassembly remains a required check.'}


def main():
    assert not DEST.exists(), DEST
    for name, h in HASHES.items():
        assert sha(BASE / name) == h, name
    sources = {name: (BASE / name).read_text() for name in HASHES}
    pc = sources['gfx_pc.c']
    old_uv = '''        if (textured) {
            vc[i].u = (RSPV_TO_FLOAT(v->u) - uofs) / 32.0f + bias;
            vc[i].v = (RSPV_TO_FLOAT(v->v) - vofs) / 32.0f + bias;
        } else {'''
    new_uv = '''        if (textured) {
            if (!direct && !raw_geometry) {
                /* Stable G_VTX slots carry signed-short UVs; tile offsets
                 * are integers and bias is0/.5. Scale these exact binary
                 * values once, before the cache-packet conversion. Direct
                 * clipped records and worker records retain texel units. */
                vc[i].u_q16 = (RSPV_TO_FLOAT(v->u) - uofs) * 2048.0f + bias * 65536.0f;
                vc[i].v_q16 = (RSPV_TO_FLOAT(v->v) - vofs) * 2048.0f + bias * 65536.0f;
            } else {
                vc[i].u = (RSPV_TO_FLOAT(v->u) - uofs) / 32.0f + bias;
                vc[i].v = (RSPV_TO_FLOAT(v->v) - vofs) / 32.0f + bias;
            }
        } else {'''
    pc = once(pc, old_uv, new_uv)
    assert pc.replace(new_uv, old_uv) == sources['gfx_pc.c']

    gpu = sources['gfx_gpu.c']
    begin = gpu.index('void gfx_gpu_vtx_cache_tri(')
    end = gpu.index('\n/* Raw geometry path.', begin)
    original_cache = gpu[begin:end]
    new_cache = original_cache
    for axis in ('u', 'v'):
        old = f'lrintf(v[k].{axis} * 65536.0f)'
        new = f'lrintf(v[k].{axis}_q16)'
        assert new_cache.count(old) == 2
        new_cache = new_cache.replace(old, new)
    gpu = gpu[:begin] + new_cache + gpu[end:]
    assert gpu[:begin] == sources['gfx_gpu.c'][:begin] and gpu[begin+len(new_cache):] == sources['gfx_gpu.c'][end:]

    header = sources['gfx_gpu.h']
    header = once(header,
        ''' *      Nonzero return carries GFX_VC_TEXTURED (fill u/v with the final
 *      tile-relative texel coords, exactly as the 0x4E path would emit them).''',
        ''' *      Nonzero return carries GFX_VC_TEXTURED. For vtx_cache_tri fill
 *      u_q16/v_q16 with final tile-relative Q16.16 values held as floats;
 *      its lrintf conversion emits the same integer words as the0x4E path.
 *      geometry_tri and direct records instead retain ordinary u/v texels.''')
    header = once(header,
        '    float u, v;           /* final tile-relative texels (0 when untextured) */',
        '''    /* Same storage, explicit units selected by the consuming API:
     * cache_tri uses scaled float Q16.16; direct/geometry use texels. */
    union { float u; float u_q16; };
    union { float v; float v_q16; };''')
    sources.update({'gfx_pc.c': pc, 'gfx_gpu.c': gpu, 'gfx_gpu.h': header})
    arithmetic = range_proof()
    layout = layout_proof()
    DEST.mkdir()
    for name in HASHES:
        shutil.copy2(BASE / name, DEST / name)
        if sources[name] != (BASE / name).read_text():
            (DEST / name).write_text(sources[name])
    after = {name: sha(DEST / name) for name in HASHES}
    changed = [name for name in HASHES if after[name] != HASHES[name]]
    assert changed == ['gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h']
    (DEST / 'prepare.py').write_bytes(Path(__file__).read_bytes())
    (DEST / 'change.patch').write_text(''.join(''.join(difflib.unified_diff(
        (BASE / name).read_text().splitlines(True), sources[name].splitlines(True),
        fromfile='baseline/' + name, tofile='cached-uv-scale-v1/' + name)) for name in changed))
    proof = {'arithmetic': arithmetic, 'layout': layout,
             'source_closure': {
                 'only_cached_cpu_stable_slot_payload_changes': True,
                 'direct_and_raw_frontend_expressions_retained_verbatim': True,
                 'only_four_cached_backend_expressions_replaced': True,
                 'worker_direct_and_generic_backend_bodies_byte_identical': True,
                 'dirty_masks_slots_colors_alpha_clip_depth_packet_order_unchanged': True,
                 'input_range': 'gfx_sp_vertex assigns signed-shortU/V to the float LoadedVertex.u/v fields, including after texgen narrowing. The optional geometry attribute loader also narrows toshort. No other stable-slot UV writer exists. Clipper temporaries and rectangle slots64+ fail the existing32-slot address test and use unchanged direct/generic processing.',
                 'texture_offsets': 'rdp.texture_tile.uls/ult are uint16 and only receive0..4095 from command decodes; proof covers the stronger complete uint16 range.',
                 'untextured': 'Existing0.0f stores alias the same float storage and still convert to zero.',
             },
             'runtime_qualified': False}
    (DEST / 'proof.json').write_text(json.dumps(proof, indent=2) + '\n')
    record = {'candidate': DEST.name, 'parent': 'render-v6', 'qualified': False,
              'runtime_qualified': False, 'source_sha256': after['gfx_pc.c'],
              'parent_sha256': HASHES['gfx_pc.c'], 'parent_files': HASHES, 'source_files': after,
              'changed_files': changed, 'proof_sha256': sha(DEST / 'proof.json'),
              'generator_sha256': sha(Path(__file__)),
              'generator_constants_dependency_sha256': sha(Path(__file__).with_name('prepare_lighting_layout.py')),
              'change': 'Store exact pre-scaled texture coordinates only for stable CPU cached uploads, then remove two backend float multiplies per dirty vertex. Add float union aliases without changing record layout.',
              'risk': 'Extra frontend selection or changed register allocation can outweigh the removed multiplies; target build, actual emitted code, paired CPU measurements and exact GPU validation are still required.',
              'no_claims': ['No measured performance gain', 'No FPGA change', 'No device FPS result']}
    (DEST / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'candidate': str(DEST), 'changed_files': changed,
                      'source_files': after, 'arithmetic_cases': arithmetic['cases'],
                      'record_bytes': layout['size_after'], 'qualified': False}, indent=2))


if __name__ == '__main__':
    main()
