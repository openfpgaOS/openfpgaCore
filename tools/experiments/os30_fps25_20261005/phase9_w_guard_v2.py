#!/usr/bin/env python3
"""Positive finite bit-guard fast path; retain original exceptional predicate."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase7/combined-v1'
OUT=CAMP/'phase9/w-guard-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    inputs=json.loads((CAMP/'phase7/baseline.json').read_text())['inputs']
    parent=json.loads((BASE/'build-normal.json').read_text())
    OUT.mkdir(parents=True,exist_ok=False)
    old='''            if (!(w1 > 0.0f && w1 < 32767.0f &&
                  w2 > 0.0f && w2 < 32767.0f &&
                  w3 > 0.0f && w3 < 32767.0f) ||
                (w1 == w2 && w1 == w3)) goto fallback_command;'''
    new='''            /* Positive bounded IEEE binary32 encodings admit integer
             * range/equality tests. Keep the original floating predicate for
             * every other encoding, including its target fast-math behavior. */
            uint32_t wb1, wb2, wb3;
            memcpy(&wb1, &w1, sizeof(wb1));
            memcpy(&wb2, &w2, sizeof(wb2));
            memcpy(&wb3, &w3, sizeof(wb3));
            if (wb1 - 1u < 0x46fffdffu &&
                wb2 - 1u < 0x46fffdffu &&
                wb3 - 1u < 0x46fffdffu) {
                if (wb1 == wb2 && wb1 == wb3) goto fallback_command;
            } else if (!(w1 > 0.0f && w1 < 32767.0f &&
                         w2 > 0.0f && w2 < 32767.0f &&
                         w3 > 0.0f && w3 < 32767.0f) ||
                       (w1 == w2 && w1 == w3)) goto fallback_command;'''
    before,after,patch={},{},[]
    for name in inputs:
        assert sha(BASE/name)==parent['inputs'][name]['sha256']
        text=(BASE/name).read_text();candidate=text
        if name=='gfx_pc.c':
            assert text.count(old)==1
            candidate=text.replace(old,new)
        (OUT/name).write_text(candidate)
        before[name],after[name]=sha(BASE/name),sha(OUT/name)
        if text!=candidate:
            patch+=difflib.unified_diff(text.splitlines(True),candidate.splitlines(True),fromfile='F9/'+name,tofile='w-guard-v2/'+name)
    (OUT/'candidate.patch').write_text(''.join(patch))
    (OUT/'prepared.json').write_text(json.dumps(dict(parent='phase7/combined-v1/F9',parent_source_sha256=before,
        candidate_source_sha256=after,recipe_sha256=sha(Path(__file__)),qualified=False,production_changed=False,
        change='Integer fast path only when all W values are strictly positive finite below32767; original float predicate otherwise.',
        prior_version_limitation='v1 strict-source proof does not cover actual fast-math F9 NaN acceptance. v2 retains the original exceptional predicate; target instruction/correctness tests required.'),indent=2)+'\n')
    print(OUT)


if __name__=='__main__':main()
