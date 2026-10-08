#!/usr/bin/env python3
"""Seal revised LOD5+span-v6 fit; no retries, edits, or timing invention."""
import json,re
from pathlib import Path
import phase10_lod5_result as previous
R=previous.ROOT;P=previous.P;OUT=P/'lod5-cram-span-core-v2';FIT=OUT/'fit-seed2'
JOB=FIT/'jobs/pocket-os30-s2/src/fpga/targets/pocket';sha=previous.sha;save=previous.save
def main():
 result=json.loads((FIT/'results/pocket-os30-s2/result.json').read_text())
 assert result['seed']==2 and result['returncodes']=={'map':0,'fit':3}
 labels=['vcache-narrow-v1','load-bram-core-area-v1','lod5-cram-span-core-v1',OUT.name]
 tables={n:previous.tables(n) for n in labels};text=tables[OUT.name][1]
 assert 'Fitter requires 1862 LABs' in text
 qsf=JOB/'ap_core.qsf';assert re.findall(r'^set_global_assignment -name SEED (\d+)$',qsf.read_text(),re.M)==['2']
 assert re.search(r'Fitter Initial Placement Seed\s*;\s*2\s*;',text)
 names=['|apf_top','|apf_top|core_top:ic','|apf_top|core_top:ic|cpu_system:cpu|VexiiRiscv:cpu','|apf_top|core_top:ic|gpu_core:gpu','|apf_top|core_top:ic|geometry_load_alias:geometry_load','|apf_top|core_top:ic|geometry_bram_alias:geometry_alias','|apf_top|core_top:ic|geometry_cram1_read_arbiter:geometry_cram_reader']
 rows={n:{l:{k:tables[l][2][n][k] for k in ['ALMs needed [=A-B+C]','Combinational ALUTs','Dedicated Logic Registers','Block Memory Bits','DSP Blocks']} for l in labels if n in tables[l][2]} for n in names}
 save(OUT/'hierarchy-comparison.json',dict(rows=rows,evidence_sha256={str(t[0].relative_to(R)):sha(t[0]) for t in tables.values()},limitation='Failed-fit hierarchy excludes memory-packing adjustment; global resource summary governs.'))
 save(OUT/'result.json',dict(whole_core_fit_successful=False,private_only=True,production_modified=False,
  configured_mhz=100,cpu_cache_kib=128,seed=2,seed_sweep=False,fit_invocations=1,
  effective_qsf_sha256=sha(qsf),seed_confirmed_in_fitter_report=True,
  resources=dict(alms_needed=18342,alm_capacity=18480,labs_required=1862,lab_capacity=1848,lab_excess=14,m10k=307,m10k_capacity=308,dsp=45,dsp_capacity=66),
  change_from_v1=dict(alms_needed=-16,labs_required=-5,gpu_hierarchical_alm=-83.7),
  slow_0_fmax_mhz=None,slow_85_fmax_mhz=None,setup=None,hold=None,unconstrained=None,
  timing_note='Placement failed; no routed netlist. STA/frequency estimates are unavailable.',
  full_core_functionally_qualified=False,deployment_ready=False,
  prepared_sha256=sha(OUT/'prepared.json'),independent_review_sha256=sha(OUT/'independent-review.json')))
 (OUT/'REPORT.txt').write_text('''Revised LOD5 CRAM + span-v6 AREA seed2: STILL NOT FITTING

Only gpu_core.v changed from sealed v1; original CPU128, clocks, saved seed2,
other RTL and area settings preserved. Functional proof shows identical cycle
schedule and output, including33 unchanged fields, old tails and chunk handoff.

18,342 /18,480 ALMs needed;1,862 /1,848 LABs required:14LABs over capacity.
307 /308 M10K and45 /66 DSP. Synthesis passed, fitter failed170012/11802.
Effective jobQSF and fitter report both confirm seed2. One revised-source
invocation; no seed sweep. No routed timing/Fmax, hold result or bitstream.

Relative to v1, saves16 neededALM and5LAB. GPU hierarchy drops83.7ALM, but
unchanged LOAD shifts+31.9 and CPU+22.7 under whole-design optimization/packing.
Final GPU ALUT falls75; whole-core ALUT falls29. The larger map-only reduction
was therefore not an additive prediction of fitted resource savings. Hierarchy
total18,264 plus78memory-packing adjustment gives18,342 globalneededALM.
See hierarchy-comparison.json and the original full reports for exact figures.

Candidate remains private/unqualified. No production, sibling app or SD writes.
No further fit or new architecture is authorized by this report.
''')
 files=[OUT/n for n in ['prepared.json','independent-review.json','result.json','hierarchy-comparison.json','REPORT.txt','recipe.py']]
 files += [FIT/n for n in ['inventory.json','frozen-sources.json','config.json','experiment.json','recipe.py']]
 files += list((FIT/'results/pocket-os30-s2').rglob('*'))+list((JOB/'output_files').glob('*.rpt'))+[qsf,Path(__file__)]
 files += [P/'span-lookahead-review-v4/manifest.json',P/'lod5-cram-span-core-v1/final-manifest.json']
 files += [OUT/'frozen'/n for n in json.loads((OUT/'prepared.json').read_text())['source_sha256']]
 save(OUT/'final-manifest.json',dict(completed=True,fit_passed=False,deployment_ready=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files)) if p.is_file()}))
 print('manifest',sha(OUT/'final-manifest.json'))
if __name__=='__main__':main()
