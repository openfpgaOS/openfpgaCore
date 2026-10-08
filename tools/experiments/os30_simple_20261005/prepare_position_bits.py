#!/usr/bin/env python3
"""Avoid byte-by-byte memcpy lowering of three already float-typed inputs."""
from pathlib import Path
import hashlib,json,difflib
ROOT=Path(__file__).resolve().parents[3];C=ROOT/'docs/measurements/os30-simple-20261005'
sha=lambda p:hashlib.sha256(p).hexdigest()
old=(C/'position-memo-v3/gfx_pc.c').read_text()
assert sha(old.encode())=='ef7227b8287daefc14522bca8d20c2f2f1a80b8c7436c5a928809d6813a21cbd'
anchor='struct PositionMemo {'
helper='''/* C union type punning reads the stored float representation without
 * requiring the compiler to copy untyped bytes through a temporary buffer. */
static inline uint32_t gfx_position_bits(float value) {
    union { float f; uint32_t u; } bits = { .f = value };
    return bits.u;
}

'''
assert old.count(anchor)==1;s=old.replace(anchor,helper+anchor)
a='''        uint32_t position_x, position_y, position_z;
        memcpy(&position_x,&v->ob[0],sizeof(position_x));
        memcpy(&position_y,&v->ob[1],sizeof(position_y));
        memcpy(&position_z,&v->ob[2],sizeof(position_z));'''
b='''        const uint32_t position_x=gfx_position_bits(v->ob[0]);
        const uint32_t position_y=gfx_position_bits(v->ob[1]);
        const uint32_t position_z=gfx_position_bits(v->ob[2]);'''
assert s.count(a)==1;s=s.replace(a,b)
s=s.replace('A changed MVP/aspect clears all keys before lookup, including epoch wrap.','A changed MVP/aspect invalidates keys; tag wrap clears them before reuse.')
out=C/'position-memo-v4';out.mkdir(exist_ok=True)
dest=out/'gfx_pc.c';assert not dest.exists() or dest.read_text()==s
dest.write_text(s)
(out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
base=(C/'baseline/gfx_pc.c').read_text()
(out/'change.patch').write_text(''.join(difflib.unified_diff(base.splitlines(True),s.splitlines(True),fromfile='a/gfx_pc.c',tofile='b/gfx_pc.c')))
record={'qualified':False,'parent_candidate':'position-memo-v3','parent_sha256':sha(old.encode()),'candidate_sha256':sha(s.encode()),'recipe_sha256':sha(Path(__file__).read_bytes()),'change':'C union float-bit extraction for three float-typed coordinate inputs; complete cache math, key bits, lifetime, attributes and fallback unchanged.'}
(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
