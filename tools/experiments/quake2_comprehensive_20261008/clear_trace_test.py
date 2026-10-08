#!/usr/bin/env python3
"""Negative controls for the removed-only-clear proof, not renderer behavior."""
import clear_trace as t

c = (0x11000003, 0x50100000, (640 << 16) | 240, 640 << 16)
z = (0x11000003, 0x539ad160, (640 << 16) | 240, 640 << 16)
d = (0x4e000011,) + (0,) * 17
f = (0x02000001, 9)
def seq(*commands):
    return [(20, command) for command in commands]
assert len(t.prove(seq(c,z,c,d,f),seq(c,z,d,f))) == 1
cases = {
    'remove_first_clear': (seq(c,z,c,d,f),seq(z,c,d,f)),
    'remove_required_depth': (seq(c,z,c,d,f),seq(c,c,d,f)),
    'remove_after_draw': (seq(c,d,c,f),seq(c,d,f)),
    'remove_changed_color': (seq(c,z,c[:-1]+(c[-1]|255,),d),seq(c,z,d)),
    'remove_overlapping_fill': (seq(c,c[:1]+(c[1]+32,)+c[2:],c,d),seq(c,c[:1]+(c[1]+32,)+c[2:],d)),
    'drop_fence': (seq(c,z,c,d,f),seq(c,z,c,d)),
    'change_triangle': (seq(c,z,c,d,f),seq(c,z,c,d[:-1]+(1,),f)),
    'add_command': (seq(c,z,c,d,f),seq(c,z,c,d,d,f)),
    'move_frame': (seq(c,z,c,d,f),[(20,c),(20,z),(21,d),(20,f)]),
}
for name,(before,after) in cases.items():
    try:
        t.prove(before,after)
    except AssertionError:
        continue
    raise AssertionError('negative control accepted: '+name)
out=t.P/'command-proof-negative-controls-v1.json'
assert not out.exists()
t.put(out,dict(passed=True,valid_duplicate_accepted=True,rejected=list(cases),
    proof_recipe_sha256=t.sha(t.R/'tools/experiments/quake2_comprehensive_20261008/clear_trace.py'),
    test_recipe_sha256=t.sha(__import__('pathlib').Path(__file__))))
print('PASS',len(cases),'semantic proof negative controls')
