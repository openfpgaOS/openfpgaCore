#!/usr/bin/env python3
"""Correct the position memo key for this port's F3DEX2E float vertices."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase9/checks-state-f11'
PARENT=CAMP/'phase10/position-memo-v1'
OUT=CAMP/'phase10/position-memo-v2'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    inputs={name:(PARENT/name).read_text() for name in json.loads((PARENT/'source.json').read_text())['source_sha256']}
    s=inputs['gfx_pc.c']
    s=s.replace('    uint32_t epoch, xy;\n    uint16_t z;\n    uint8_t clip_rej, pad;', '    uint32_t epoch, key_x, key_y, key_z;\n    uint8_t clip_rej, pad[3];')
    s=s.replace('static struct PositionMemo position_memo[128];','static struct PositionMemo position_memo[64];')
    old='''        uint32_t key_xy;
        memcpy(&key_xy, &v->ob[0], sizeof(key_xy));
        const uint16_t key_z = (uint16_t)v->ob[2];
        const uint32_t hash = (key_xy ^ ((uint32_t)key_z * 0x9e3779b9u)) * 0x85ebca6bu;
        struct PositionMemo *pm = &position_memo[hash >> 25];
        if (pm->epoch == position_memo_epoch && pm->xy == key_xy && pm->z == key_z) {'''
    new='''        const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
        uint32_t key_x, key_y, key_z;
        memcpy(&key_x, &ox, sizeof(key_x));
        memcpy(&key_y, &oy, sizeof(key_y));
        memcpy(&key_z, &oz, sizeof(key_z));
        const uint32_t hash = ((key_x * 31u + key_y) * 31u + key_z) * 0x85ebca6bu;
        struct PositionMemo *pm = &position_memo[hash >> 26];
        if (pm->epoch == position_memo_epoch && pm->key_x == key_x &&
            pm->key_y == key_y && pm->key_z == key_z) {'''
    assert s.count(old)==1;s=s.replace(old,new)
    old='''            const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
            x ='''
    new='''#if !GFX_CPU_POSITION_MEMO
            const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
#endif
            x ='''
    assert s.count(old)==1;s=s.replace(old,new)
    s=s.replace('pm->epoch = position_memo_epoch; pm->xy = key_xy; pm->z = key_z;', 'pm->epoch = position_memo_epoch;\n            pm->key_x = key_x; pm->key_y = key_y; pm->key_z = key_z;')
    inputs['gfx_pc.c']=s
    OUT.mkdir(exist_ok=False)
    for n,c in inputs.items():(OUT/n).write_text(c)
    original=(BASE/'gfx_pc.c').read_text()
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='F11/gfx_pc.c',tofile='position-memo-v2/gfx_pc.c')))
    (OUT/'source.json').write_text(json.dumps(dict(parent='phase9/checks-state-f11',rejected_prototype='position-memo-v1 assumed original N64 int16 position layout; current F3DEX2E stores float XYZ and needs full 96-bit keys.',recipe_sha256=sha(Path(__file__)),source_sha256={n:sha(OUT/n) for n in inputs},qualified=False),indent=2)+'\n')
    print(OUT)

if __name__=='__main__':main()
