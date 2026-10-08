#!/usr/bin/env python3
"""Prepare an isolated exact positive-W bit guard over frozen F9 sources."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT/'docs/measurements/os30-fps25-20261005'
BASE = CAMP/'phase7/combined-v1'
OUT = CAMP/'phase9/w-guard-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    inputs = json.loads((CAMP/'phase7/baseline.json').read_text())['inputs']
    parent = json.loads((BASE/'build-normal.json').read_text())
    OUT.mkdir(parents=True,exist_ok=False)
    old = '''            if (!(w1 > 0.0f && w1 < 32767.0f &&
                  w2 > 0.0f && w2 < 32767.0f &&
                  w3 > 0.0f && w3 < 32767.0f) ||
                (w1 == w2 && w1 == w3)) goto fallback_command;'''
    new = '''            /* Positive IEEE binary32 encodings are strictly ordered.
             * This accepts exactly 0 < w < 32767, excluding both zeros,
             * infinities and every NaN. Equality is then bit equality.
             * memcpy avoids aliasing assumptions; no projection math changes. */
            uint32_t wb1, wb2, wb3;
            memcpy(&wb1, &w1, sizeof(wb1));
            memcpy(&wb2, &w2, sizeof(wb2));
            memcpy(&wb3, &w3, sizeof(wb3));
            if (wb1 - 1u >= 0x46fffdffu ||
                wb2 - 1u >= 0x46fffdffu ||
                wb3 - 1u >= 0x46fffdffu ||
                (wb1 == wb2 && wb1 == wb3)) goto fallback_command;'''
    before,after,patch = {},{},[]
    for name in inputs:
        assert sha(BASE/name) == parent['inputs'][name]['sha256']
        source = (BASE/name).read_text()
        candidate = source
        if name == 'gfx_pc.c':
            assert source.count(old) == 1
            candidate = source.replace(old,new)
        (OUT/name).write_text(candidate)
        before[name],after[name] = sha(BASE/name),sha(OUT/name)
        if source != candidate:
            patch += difflib.unified_diff(source.splitlines(True),candidate.splitlines(True),
                                          fromfile='F9/'+name,tofile='w-guard-v1/'+name)
    (OUT/'candidate.patch').write_text(''.join(patch))
    record = dict(parent='phase7/combined-v1/F9',parent_source_sha256=before,candidate_source_sha256=after,
        recipe_sha256=sha(Path(__file__)),scope='Indexed-run W eligibility predicate only; immutable cull/projection/rounding and command ordering.',
        proof='For IEEE binary32, finite positive values are ordered by unsigned encoding. 32767.0 has bits0x46fffe00, so unsigned(bits-1)<0x46fffdff accepts encodings1 through0x46fffdff exactly. All accepted values have unique encodings, so equality is bit equality.',
        fflags_scope='Output/decision equivalence, not floating exception flag equivalence for NaNs. Active SM64 source does not read floating exception flags.',
        qualified=False,production_changed=False)
    (OUT/'prepared.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(dict(candidate=str(OUT),source_sha256=after['gfx_pc.c'])))


if __name__ == '__main__':
    main()
