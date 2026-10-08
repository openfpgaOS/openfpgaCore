#!/usr/bin/env python3
"""Private one-statement lighting hit-copy candidate; no target build/stage."""
import difflib
import hashlib
import json
from pathlib import Path
from prepare_lighting_layout import ROOT, OUT, BASE, HASHES

DEST = OUT / 'lighting-word-copy-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
OLD = '                d->color.r=memo->color.r;d->color.g=memo->color.g;d->color.b=memo->color.b;\n'
NEW = '''                /* The final fog/source-alpha assignment below overwrites
                 * this cached alpha before any consumer sees this vertex. */
                d->color = memo->color;
'''


def main():
    assert not DEST.exists(), DEST
    for name, h in HASHES.items(): assert sha(BASE/name) == h, name
    original = (BASE/'gfx_pc.c').read_text()
    assert original.count(OLD) == 1
    source = original.replace(OLD, NEW)
    assert source.replace(NEW, OLD) == original
    deferred_start = original.index('static void gfx_load_geometry_attributes(')
    deferred_end = original.index('/* Attribute evaluation may wait', deferred_start)
    assert original[deferred_start:deferred_end] == source[deferred_start:deferred_end]
    hit = source.index(NEW)
    final_alpha = source.index('#if GFX_CPU_DEFERRED_FOG\n        if (eager_fog)', hit)
    assert 'color.a' not in source[hit:final_alpha]
    # Alpha is overwritten for both branches and before the vertex loop ends.
    alpha = source[final_alpha:source.index('\nstatic inline struct ColorCombiner *gfx_pick_combiner',final_alpha)]
    assert 'd->color.a = (fog_i < 0) ? 0 : (fog_i > 255) ? 255 : fog_i;' in alpha
    assert '} else {\n            d->color.a = v->cn[3];\n        }' in alpha
    assert 'memo->color.a' not in source
    cases = 0
    for old_alpha in range(256):
        for final in range(256):
            expected = bytes((17, 83, 249, final))
            actual = bytearray((17, 83, 249, old_alpha))
            actual[3] = final
            assert actual == expected
            cases += 1
    DEST.mkdir()
    for name in HASHES: (DEST/name).write_bytes((BASE/name).read_bytes())
    (DEST/'gfx_pc.c').write_text(source)
    (DEST/'prepare.py').write_bytes(Path(__file__).read_bytes())
    (DEST/'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True),source.splitlines(True),
        fromfile='baseline/gfx_pc.c',tofile='lighting-word-copy-v1/gfx_pc.c')))
    after = {name:sha(DEST/name) for name in HASHES}
    assert [name for name in HASHES if after[name] != HASHES[name]] == ['gfx_pc.c']
    record = dict(candidate='lighting-word-copy-v1',parent='render-v6',qualified=False,
        runtime_qualified=False,parent_files=HASHES,source_files=after,
        generator_sha256=sha(Path(__file__)),
        change='Replace only the active gfx_sp_vertex memo-hit three-byte RGB assignment with one whole RGBA struct assignment.',
        source_proof={
            'one_executable_statement_replacement_reversible_exact':True,
            'all_six_other_inputs_byte_identical':True,
            'all_miss_writes_keys_epochs_table_layout_unchanged':True,
            'no_color_alpha_consumer_before_final_assignment':True,
            'final_alpha_unconditionally_from_original_source_or_original_fog_formula':True,
            'alpha_overwrite_cases':cases,
            'deferred_hit_path_byte_identical':True,
            'deferred_exclusion_reason':'Deferred loader sets source alpha before the hit and then continues; copying memo alpha there without restoring it would be incorrect.',
            'initialized_memo_alpha':'lighting_memo has static storage, is zero-initialized, and no code writes its alpha. Whole-color reading adds no indeterminate-byte read.',
            'alignment':'RGBA has four byte fields. LoadedVertex has4-byte alignment/36-byte stride/color offset28. LightingMemo has4-byte alignment/12-byte stride/color offset8. Both whole-color addresses are naturally4-byte aligned; no casts or aliasing violations.',
        },
        baseline_hit_codegen='Frozen baseline normal.elf at104cf336..104cf342 already uses LHU+LBU+SH+SB for RGB, not six byte operations. An address ADD precedes these.',
        hypothesis='Compiler can use aligned32-bit load/store instead of the existing two loads and two stores on hits. It may narrow the copy because alpha is dead, so emitted code and whole-trace measurement must decide.',
        remaining='Root target build/disassembly, matched capture and exact GPU validation. No source-level performance gain or device FPS claim.')
    (DEST/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'candidate':str(DEST),'gfx_pc_sha256':after['gfx_pc.c'],'alpha_overwrite_cases':cases,'qualified':False},indent=2))

if __name__=='__main__':main()
