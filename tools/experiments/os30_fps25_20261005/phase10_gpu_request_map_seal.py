#!/usr/bin/env python3
"""Bind single map result, source proof and area qualification limits."""
import json
from pathlib import Path
import phase10_gpu_request_map as task
m=task.m
def main():
 r=json.loads((m.OUT/'result.json').read_text());assert r['passed_map'] and not r['full_fit_attempted']
 prep=json.loads((m.OUT/'prepared.json').read_text());assert prep['only_changed_files']==[m.GPU]
 for n,h in prep['source_sha256'].items():assert m.sha(m.OUT/'frozen'/n)==h
 report=m.OUT/'job/src/fpga/targets/pocket/output_files/ap_core.map.rpt'
 baseline=next((m.BASE/'fit-seed2/jobs').rglob('ap_core.map.rpt'))
 assert m.sha(report)==r['candidate_report_sha256'] and m.sha(baseline)==r['baseline_report_sha256']
 text='''GPU v10 isolated synthesis screen, not a fit or timing result

One Quartus 25.1std map over exact compact-lod5-core-v2; only gpu_core.v changes.
Same CPU128, cache-v2, folded LOD5 worker, seed2, AREA settings and clocks.
The full v10d ownership/replay manifest and actual-divider-expression proof
were rehashed before preparation and again before synthesis.

                         Baseline      GPUv10      Delta
GPU ALUTs                  10065        10007        -58
GPU dedicated registers     8559         8559          0
GPU block memory bits     301360       301360          0
GPU DSP blocks                27           27          0
Whole-top ALUTs            26398        26435        +37
Whole dedicated registers 25363        25363          0
Whole total registers     25465        25465          0
Whole block memory bits 2343859      2343859          0
Whole DSP blocks              45           45          0
Whole memory ALUTs           212          212          0

Whole total registers include102 I/O registers excluded from the dedicated
logic register count. No memory/DSP/register reduction is established.
Although only GPU source changes, global synthesis remaps unchanged logic:
CPU-system ALUTs6992→7123 (+131); LOAD1748→1730 (−18). Consequently the local
GPU improvement does not provide a whole-map ALUT reduction. This screen
does not imply fitted ALM/LAB usage, placement success, Fmax or frame speed.

The standard map report exposes divider register/multiplexer groups but no
named LessThan cone in either baseline or candidate. The v10 source replaces
the comparator/subtractor with an extended difference, and functional proof
is exact; actual disappearance of that routed cone remains unverified here.
No fitter, assembler, TimeQuest or deployment was run by this experiment.
'''
 with (m.OUT/'REPORT.txt').open('x') as f:f.write(text)
 files=[Path(__file__),Path(task.__file__),Path(m.__file__),m.PROOF,task.DIVIDER,
  baseline,report,report.with_suffix('.summary'),m.OUT/'REPORT.txt',m.OUT/'result.json',
  m.OUT/'prepared.json',m.OUT/'request-proof.json',m.OUT/'request-map-recipe.py',
  m.OUT/'map.log',m.OUT/'invocation.json',m.OUT/'job/src/fpga/targets/pocket/ap_core.qsf']
 files += [p for p in (m.OUT/'frozen').rglob('*') if p.is_file()]
 m.save(m.OUT/'manifest.json',dict(passed_map=True,full_fit_attempted=False,
  timing_qualified=False,deployment_ready=False,only_changed_files=[m.GPU],
  evidence_sha256={str(p.relative_to(m.ROOT)):m.sha(p) for p in sorted(set(files))}))
 print(m.OUT/'manifest.json');print(m.sha(m.OUT/'manifest.json'))
if __name__=='__main__':main()
