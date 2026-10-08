#!/usr/bin/env python3
"""Private two-triangle loop unroll hint over frozen F9, with differential test."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase7/combined-v1'
OUT=CAMP/'phase9/unroll-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def prepare():
    original=(BASE/'gfx_pc.c').read_text()
    assert sha(BASE/'gfx_pc.c')=='2c46ef4643bbdcbcf5e213b257daba4d79e6d1289c7d9da279a591fa34ff0662'
    start=original.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(')
    end=original.index('\nfallback_command:',start)
    piece=original[start:end]
    loop='        for (unsigned t = 0; t < n; ++t) {'
    assert piece.count(loop)==2
    candidate=original[:start]+piece.replace(loop,'#if defined(__GNUC__)\n#pragma GCC unroll 2\n#endif\n'+loop)+original[end:]
    OUT.mkdir(exist_ok=False)
    inputs=json.loads((BASE/'build-normal.json').read_text())['inputs']
    for name,rec in inputs.items():
        assert sha(BASE/name)==rec['sha256']
        (OUT/name).write_bytes(candidate.encode() if name=='gfx_pc.c' else (BASE/name).read_bytes())
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),candidate.splitlines(True),
        fromfile='F9/gfx_pc.c',tofile='unroll-v1/gfx_pc.c')))
    (OUT/'prepared.json').write_text(json.dumps(dict(parent='phase7/combined-v1/F9',
        source_sha256={name:sha(OUT/name) for name in inputs},recipe_sha256=sha(Path(__file__)),
        change='GCC unroll2 hints only on validation and job commit loops; fallback and semantics unchanged.',
        qualified_for_deployment=False),indent=2)+'\n')

def tests():
    helper=Path(__file__).with_name('phase7_eligibility_tests.py')
    source=helper.read_text()
    old="assert results['candidate']['rejection_evaluations'] < results['baseline']['rejection_evaluations']"
    assert source.count(old)==1
    source=source.replace(old,"assert results['candidate']['rejection_evaluations'] == results['baseline']['rejection_evaluations']")
    ns=dict(__file__=str(Path(__file__).resolve()),__name__='phase9_unroll_suite')
    exec(compile(source,str(helper),'exec'),ns)
    ns.update(BASE=BASE/'gfx_pc.c',CAND=OUT/'gfx_pc.c',OUT=OUT/'tests')
    sys.argv=[str(Path(__file__))];ns['main']()
    (OUT/'tests/adaptation.json').write_text(json.dumps(dict(helper_sha256=sha(helper),
        wrapper_sha256=sha(Path(__file__)),result_sha256=sha(OUT/'tests/RESULT.json'),
        adaptation='Require equal rejection evaluations because parent is already F9.'),indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','tests']);a=p.parse_args()
    (prepare if a.action=='prepare' else tests)()
