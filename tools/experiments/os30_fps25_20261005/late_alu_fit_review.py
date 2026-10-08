#!/usr/bin/env python3
"""Record the completed single late-ALU fit and strict acceptance decision."""
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/late-alu-v1'
FIT=OUT/'fit-A-seed2'
result=json.loads((FIT/'results.json').read_text(errors='replace'))
assert len(result)==1 and result[0]['state']=='complete' and result[0]['valid']
result=result[0];directory=Path(result['directory']);timing=directory/'timing'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
report=(directory/'output_files/ap_core.fit.rpt').read_text(errors='replace')
labs=re.search(r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)\s*/\s*([\d,]+)',report)
assert labs
sta=(directory/'output_files/ap_core.sta.rpt').read_text(errors='replace')
clock='ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk'
limits=[]
for line in sta.splitlines():
    columns=[c.strip()for c in line.split(';')]
    if len(columns)>3 and columns[3]==clock and re.fullmatch(r'[\d.]+ MHz',columns[1]):
        limits.append(float(columns[1].split()[0]))
assert len(limits)==2
paths=[]
for line in (timing/'slow_85_setup.rpt').read_text(errors='replace').splitlines():
    columns=[c.strip()for c in line.split(';')]
    if len(columns)==10 and re.fullmatch(r'-?\d+\.\d+',columns[1]) and columns[4]==clock:
        paths.append(dict(slack_ns=float(columns[1]),source=columns[2],destination=columns[3]))
assert len(paths)==20,len(paths)
assert len(result['timing'])==20
root_cpu=ROOT/'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
assert sha(root_cpu)=='33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'
review=dict(scope=__doc__,seed=2,configured_mhz=100,full_fit_success=True,
    main_fmax_hot_mhz=limits[0],main_fmax_cold_mhz=limits[1],
    alms=result['alms'],alms_capacity=18480,labs=[int(v.replace(',',''))for v in labs.groups()],
    m10ks=result['m10ks'],m10ks_capacity=308,all_corner_checks=result['worst'],
    all_checks_pass=result['all_checks_pass'],greater_than_90mhz=min(limits)>90,
    decision='REJECT for deployment: less than 90 MHz and mixed component performance.',
    root_cpu_unchanged_sha256=sha(root_cpu),published=False,deployed=False,
    setup_paths_hot=paths,artifact_hashes=result['hashes'],
    non_cpu_sources_exact_frozen_A=True,fit_seconds=result['seconds'],
    report_sha256={str(p.relative_to(ROOT)):sha(p)for p in [FIT/'results.json',
        FIT/'experiment.json',directory/'output_files/ap_core.fit.rpt',
        directory/'output_files/ap_core.sta.rpt',timing/'summary.tsv',timing/'slow_85_setup.rpt']})
(OUT/'fit-review.json').write_text(json.dumps(review,indent=2)+'\n')
print(json.dumps({k:v for k,v in review.items()if k not in ['setup_paths_hot','report_sha256']},indent=2))
