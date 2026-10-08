#!/usr/bin/env python3
"""Private F9 TRI1 scanner specialization and actual-loop differential tests."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
BASE = CAMP / 'phase7/combined-v1'
OUT = CAMP / 'phase9/tri1-scan-v1'
EXPECTED = '2c46ef4643bbdcbcf5e213b257daba4d79e6d1289c7d9da279a591fa34ff0662'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

FAST = r'''        /* Once this run owns its material, TRI1 needs no temporary slot
         * array or second validation/commit loop. The first material-setting
         * command and every TRI2 retain the complete original path below. */
        if (comb && (cmd->words.w0 >> 24) == (uint8_t)G_TRI1) {
            if (consumed == 64) break;
#ifdef F3DEX_GBI_2
            const uint32_t word = cmd->words.w0;
#else
            const uint32_t word = cmd->words.w1;
#endif
#if defined(F3DEX_GBI_2) || defined(F3DEX_GBI) || defined(F3DLP_GBI)
            const unsigned a = ((word >> 16) & 255u) / 2;
            const unsigned b = ((word >> 8) & 255u) / 2;
            const unsigned c = (word & 255u) / 2;
#else
            const unsigned a = ((word >> 16) & 255u) / 10;
            const unsigned b = ((word >> 8) & 255u) / 10;
            const unsigned c = (word & 255u) / 10;
#endif
            unsigned r = 0, did_reject = 0;
            if ((a | b | c) >= 32) goto tri1_fallback;
            const struct LoadedVertex *v1 = &rsp.loaded_vertices[a];
            const struct LoadedVertex *v2 = &rsp.loaded_vertices[b];
            const struct LoadedVertex *v3 = &rsp.loaded_vertices[c];
            r = gfx_cpu_batch_reject(v1, v2, v3);
            did_reject = 1;
            if (!r) {
                if (v1->clip_rej | v2->clip_rej | v3->clip_rej) goto tri1_fallback;
                const float w1 = RSPV_TO_FLOAT(v1->w);
                const float w2 = RSPV_TO_FLOAT(v2->w);
                const float w3 = RSPV_TO_FLOAT(v3->w);
                if (!(w1 > 0.0f && w1 < 32767.0f &&
                      w2 > 0.0f && w2 < 32767.0f &&
                      w3 > 0.0f && w3 < 32767.0f) ||
                    (w1 == w2 && w1 == w3)) goto tri1_fallback;
                unsigned dirty = ((stale >> a) & 1u) |
                                 (((stale >> b) & 1u) << 1) |
                                 (((stale >> c) & 1u) << 2);
                const uint32_t used = (1u << a) | (1u << b) | (1u << c);
                prepared |= stale & used;
                jobs[count++] = a | (b << 5) | (c << 10) | (dirty << 15);
                stale &= ~used;
            }
#ifdef TARGET_POCKET
            tri_diag.total++;
            if (r == 1) tri_diag.clip_reject++;
            else if (r == 2) tri_diag.cull_reject++;
            else tri_diag.direct++;
#endif
            ++consumed;
            ++cmd;
            continue;
tri1_fallback:
            /* Keep the accepted prefix and exact checked-rejection handoff. */
            slot[0] = a; slot[1] = b; slot[2] = c;
            reject[0] = r; n = 1; checked = did_reject;
            goto fallback_command;
        }
'''


def prepare():
    assert sha(BASE/'gfx_pc.c') == EXPECTED
    original = (BASE/'gfx_pc.c').read_text()
    start = original.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(')
    anchor = '    for (;;) {\n        n = gfx_geometry_decode(cmd, slot);'
    assert original[start:].count(anchor) == 1
    candidate = original[:start] + original[start:].replace(anchor,
        '    for (;;) {\n' + FAST + '        n = gfx_geometry_decode(cmd, slot);', 1)
    OUT.mkdir(parents=True, exist_ok=False)
    inputs = json.loads((BASE/'build-normal.json').read_text())['inputs']
    for name, record in inputs.items():
        assert sha(BASE/name) == record['sha256']
        (OUT/name).write_bytes(candidate.encode() if name == 'gfx_pc.c' else (BASE/name).read_bytes())
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),
        candidate.splitlines(True), fromfile='F9/gfx_pc.c', tofile='tri1-scan-v1/gfx_pc.c')))
    (OUT/'prepared.json').write_text(json.dumps(dict(
        parent='phase7/combined-v1/F9', parent_source_sha256=EXPECTED,
        source_sha256={name:sha(OUT/name) for name in inputs},
        recipe_sha256=sha(Path(__file__)),
        change='Scalar TRI1 scan only after the original whole-command material setup succeeds; original TRI2, first-command, payload, submission and fallback remain.',
        qualified_for_deployment=False), indent=2)+'\n')
    print(OUT)


def tests():
    helper = Path(__file__).with_name('phase7_eligibility_tests.py')
    source = helper.read_text()
    old = "assert results['candidate']['rejection_evaluations'] < results['baseline']['rejection_evaluations']"
    new = "assert results['candidate']['rejection_evaluations'] == results['baseline']['rejection_evaluations']"
    assert source.count(old) == 1
    source = source.replace(old,new)
    namespace = dict(__file__=str(Path(__file__).resolve()), __name__='phase9_tri1_suite')
    exec(compile(source,str(helper),'exec'),namespace)
    namespace.update(BASE=BASE/'gfx_pc.c',CAND=OUT/'gfx_pc.c',OUT=OUT/'tests')
    sys.argv=[str(Path(__file__))]
    namespace['main']()
    (OUT/'tests/adaptation.json').write_text(json.dumps(dict(
        helper_sha256=sha(helper),wrapper_sha256=sha(Path(__file__)),
        adaptation='Require equal rejection counts, since both baseline and candidate already include F9 rejection reuse.',
        result_sha256=sha(OUT/'tests/RESULT.json')),indent=2)+'\n')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','tests'])
    args=parser.parse_args()
    (prepare if args.action=='prepare' else tests)()
