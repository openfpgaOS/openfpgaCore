#!/usr/bin/env python3
"""Export all sub-90 MHz register endpoints from a copy of the completed fit."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
from phase10_compact_timing_audit_v12_paths import TCL as OLD_TCL

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P/'parallel-control-fit-v1'
OUT = P/'parallel-endpoint-census-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert sha(BASE/'manifest.json') == 'fb50ae4d110ac75bb22e7d5b5607a730d372b48172cb4c64c4a2af7228b49b7d'
    parent = json.loads((BASE/'manifest.json').read_text())
    for name,digest in parent['evidence_sha256'].items():
        assert sha(R/name) == digest, name
    assert json.loads((BASE/'fit-seed2/complete.json').read_text())['state'] == 'complete'
    source = BASE/'fit-seed2/job'
    inputs = {str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file()}
    OUT.mkdir(exist_ok=False)
    shutil.copytree(source,OUT/'job')
    assert all(sha(OUT/'job'/n)==h for n,h in inputs.items())
    # Retain the existing complete endpoint census, omit obsolete per-family
    # probes and their old instance names. No SDC or timing model is changed.
    start = OLD_TCL.index(' report_timing -setup -npaths 40')
    end = OLD_TCL.index(' # One worst path per register endpoint;')
    tcl = OLD_TCL[:start]+OLD_TCL[end:]
    (OUT/'census.tcl').write_text(tcl)
    shutil.copyfile(__file__,OUT/'recipe.py')
    sys.path.insert(0,str(R/'tools'))
    import sweep_target_matrix as sweep
    wd = OUT/'job/src/fpga/targets/pocket'
    cmd = sweep.docker(OUT,'pocket',wd,['quartus_sta','-t',str(OUT/'census.tcl'),'ap_core',str(OUT/'reports')])
    (OUT/'prepared.json').write_text(json.dumps(dict(parent_manifest_sha256=sha(BASE/'manifest.json'),
        input_sha256=inputs,command=cmd,fit_invoked=False,production_modified=False,
        diagnostic_only=True,threshold_mhz=90,tcl_sha256=sha(OUT/'census.tcl')),indent=2)+'\n')
    with (OUT/'sta.log').open('x') as f:
        result=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
    assert all(sha(source/n)==h for n,h in inputs.items()), 'Original fitted job changed'
    reports=sorted((OUT/'reports').glob('*'))
    evidence=[Path(__file__),OUT/'recipe.py',OUT/'prepared.json',OUT/'sta.log',OUT/'census.tcl',BASE/'manifest.json',*reports]
    (OUT/'manifest.json').write_text(json.dumps(dict(returncode=result.returncode,fit_invoked=False,
        original_job_unchanged=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}),indent=2)+'\n')
    assert result.returncode == 0
    print((OUT/'reports/census.tsv').read_text()); print('MANIFEST',sha(OUT/'manifest.json'))


if __name__ == '__main__':
    main()
