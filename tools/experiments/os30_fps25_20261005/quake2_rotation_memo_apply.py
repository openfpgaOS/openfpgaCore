#!/usr/bin/env python3
"""Return the exact reviewed rotation hunk in memory; never write a target."""
import hashlib
from pathlib import Path
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-optimize-20261008'
O=P/'components/rotation-v1'
BASE_SHA='fd93411dd58ec75dc931928c5bf1303cf6e85ede607ca9a3cb85468d69e8de54'
CANDIDATE_SHA='3d4715d693a686458f74382c1976c9fe697d121e63580245d5223144f450d507'
sha=lambda s:hashlib.sha256(s.encode()).hexdigest()
base=(P/'work/baseline/src/quake2/openfpga/of_gl1.c').read_text()
candidate=(O/'of_gl1.c').read_text()
assert sha(base)==BASE_SHA and sha(candidate)==CANDIDATE_SHA
OLD=(O/'old-function.c').read_text().removesuffix('\n')
NEW=(O/'new-function.c').read_text().removesuffix('\n')
assert base.count(OLD)==1 and base.replace(OLD,NEW)==candidate
def apply_to_text(source):
    assert source.count(OLD)==1 and NEW not in source,'Original rotation hunk missing or duplicated'
    return source.replace(OLD,NEW)
