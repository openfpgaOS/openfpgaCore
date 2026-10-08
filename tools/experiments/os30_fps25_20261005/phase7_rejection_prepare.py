#!/usr/bin/env python3
"""Consume a failed whole TRI command without repeating its rejection checks."""
import difflib
import hashlib
import json
from pathlib import Path
import phase7_eligibility_prepare as common
from phase7_eligibility_tests import function

OUT = common.OUT.with_name('rejection-v1')
sha = lambda b: hashlib.sha256(b).hexdigest()


def transform(original):
    candidate = original
    # The common fallback keeps the original post-cull clip/push body exact.
    tri = function(original, 'gfx_sp_tri1')
    start = tri.index('#ifdef GFX_MANUAL_CLIPPING\n')
    tail = tri[start:]
    helper = '''/* Complete one fallback triangle using an already computed rejection
 * class. The exact original post-cull clip/push tail follows below. */
static void gfx_cpu_push_checked(unsigned a, unsigned b, unsigned c, unsigned reject) {
    struct LoadedVertex *v1 = &rsp.loaded_vertices[a];
    struct LoadedVertex *v2 = &rsp.loaded_vertices[b];
    struct LoadedVertex *v3 = &rsp.loaded_vertices[c];
#ifdef TARGET_POCKET
    tri_diag.total++;
    if (reject == 1) tri_diag.clip_reject++;
    else if (reject == 2) tri_diag.cull_reject++;
#endif
    if (reject) return;
    const uint8_t clip_and = 0;
''' + tail + '\n\n'
    anchor = '/* A run ends at EVERY non-triangle command, including G_VTX.'
    assert candidate.count(anchor) == 1
    candidate = candidate.replace(anchor, helper + anchor)
    old_run = function(candidate, 'gfx_cpu_indexed_run')
    run = old_run
    run = run.replace('''    unsigned count = 0, consumed = 0;
''', '''    unsigned count = 0, consumed = 0;
    unsigned slot[6], reject[2], n = 0, checked = 0;
    bool fallback = false;
''')
    run = run.replace('''        unsigned slot[6], reject[2], n = gfx_geometry_decode(cmd, slot);
''', '''        n = gfx_geometry_decode(cmd, slot);
        checked = 0;
''')
    run = run.replace('''            if ((s[0] | s[1] | s[2]) >= 32) goto submit;
''', '''            if ((s[0] | s[1] | s[2]) >= 32) goto fallback_command;
''')
    run = run.replace('''            reject[t] = gfx_cpu_batch_reject(v1, v2, v3);
''', '''            reject[t] = gfx_cpu_batch_reject(v1, v2, v3);
            checked = t + 1;
''')
    run = run.replace('goto submit;', 'goto fallback_command;')
    assert 'goto submit;' not in run
    before = '''submit:
    if (count) {
'''
    after = '''    goto submit;
fallback_command:
    /* Reject work has no side effects. Complete only the unvisited TRI2
     * half, then submit the accepted prefix before this whole fallback
     * command. A material change cannot intervene inside a TRI command. */
    for (unsigned t = checked; t < n; ++t) {
        const unsigned *s = &slot[3*t];
        reject[t] = gfx_cpu_batch_reject(&rsp.loaded_vertices[s[0]],
                                       &rsp.loaded_vertices[s[1]],
                                       &rsp.loaded_vertices[s[2]]);
    }
    fallback = true;
submit:
    if (count) {
'''
    assert before in run
    run = run.replace(before, after)
    before = '''    return cmd;
}'''
    after = '''    if (fallback) {
        for (unsigned t = 0; t < n; ++t) {
            const unsigned *s = &slot[3*t];
            gfx_cpu_push_checked(s[0], s[1], s[2], reject[t]);
        }
        ++cmd;
    }
    return cmd;
}'''
    assert run.count(before) == 1
    run = run.replace(before, after)
    candidate = candidate.replace(old_run, run)
    return candidate


def main():
    original = common.BASE.read_text()
    assert sha(original.encode()) == common.EXPECTED
    candidate = transform(original)
    OUT.mkdir(exist_ok=True)
    inputs = json.loads((OUT.parent / 'baseline.json').read_text())['inputs']
    for name in inputs:
        data = (common.OUT / name).read_bytes() if name != 'gfx_pc.c' else candidate.encode()
        dest = OUT / name
        assert not dest.exists() or dest.read_bytes() == data, dest
        dest.write_bytes(data)
    (OUT / 'rejection.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='F8/gfx_pc.c', tofile='rejection-v1/gfx_pc.c')))
    record = dict(parent_sha256=sha(original.encode()), candidate_sha256=sha(candidate.encode()),
        recipe_sha256=sha(Path(__file__).read_bytes()),
        changes='Reuse completed CPU rejection checks and consume a whole fallback TRI command after the already accepted indexed prefix.',
        original_post_cull_tail_exact=True, qualified=False)
    (OUT / 'rejection-source.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
