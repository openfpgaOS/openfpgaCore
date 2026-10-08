#!/usr/bin/env python3
"""Seal the one LOD5+span-v3 fit and separate hierarchy from placement limits."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'lod5-cram-span-core-v1';FIT=OUT/'fit-seed2';JOB=FIT/'jobs/pocket-os30-s2/src/fpga/targets/pocket'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
def tables(label):
 path=P/label/'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/output_files/ap_core.fit.rpt'
 s=path.read_text(errors='replace');h=None;r=None;hier={};ram={}
 for line in s.splitlines():
  c=[x.strip() for x in line.split(';')][1:-1]
  if c and c[0]=='Compilation Hierarchy Node':h=c
  if h and len(c)==len(h):
   d=dict(zip(h,c));name=d['Full Hierarchy Name']
   if name.startswith('|apf_top'):hier[name]=d
  if c and c[0]=='Name' and 'M10K blocks' in c:r=c
  if r and len(c)==len(r) and c[0].startswith('core_top:ic|'):
   d=dict(zip(r,c));name=re.sub(r'\|(?:altsyncram_[^|]+|dpram_[^|]+):auto_generated.*','',d['Name']);ram[name]=d
 return path,s,hier,ram
def main():
 res=json.loads((FIT/'results/pocket-os30-s2/result.json').read_text())
 assert res['seed']==2 and res['returncodes']=={'map':0,'fit':3}
 path,text,hier,ram=tables(OUT.name)
 assert 'Fitter requires 1867 LABs' in text
 qsf=JOB/'ap_core.qsf';seeds=re.findall(r'^set_global_assignment -name SEED (\d+)$',qsf.read_text(),re.M)
 assert seeds==['2']
 assert re.search(r'Fitter Initial Placement Seed\s*;\s*2\s*;',text)
 labels=['vcache-narrow-v1','load-bram-core-area-v1',OUT.name]
 all_tables={l:tables(l) for l in labels}
 rows={}
 names=['|apf_top','|apf_top|core_top:ic','|apf_top|core_top:ic|cpu_system:cpu|VexiiRiscv:cpu','|apf_top|core_top:ic|gpu_core:gpu','|apf_top|core_top:ic|geometry_load_alias:geometry_load','|apf_top|core_top:ic|geometry_bram_alias:geometry_alias','|apf_top|core_top:ic|geometry_cram1_read_arbiter:geometry_cram_reader']
 for name in names:
  rows[name]={l:{k:all_tables[l][2][name][k] for k in ['ALMs needed [=A-B+C]','Combinational ALUTs','Dedicated Logic Registers','Block Memory Bits','DSP Blocks']} for l in labels if name in all_tables[l][2]}
 oldram=all_tables['load-bram-core-area-v1'][3]
 cols=['Port A Depth','Port A Width','Port B Depth','Port B Width','Type','Mode']
 assert len(oldram)==len(ram)==73 and oldram.keys()==ram.keys()
 assert all(oldram[n][c]==ram[n][c] for n in ram for c in cols)
 evidence=[x[0] for x in all_tables.values()]
 save(OUT/'hierarchy-comparison.json',dict(rows=rows,inferred_ram_count=73,inferred_ram_dimensions_byte_identical=True,
  span_tuple_old_physical_locations={n:{k:oldram[n][k] for k in ['M10K blocks','Location']} for n in ram if any(f':{v}_rtl_0' in n for v in ['spanprod_u','spanprod_v','spanprod_count'])},
  limitation='Failed-fit hierarchy precedes final memory packing and is not additive to the global neededALM estimate.',
  evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence}))
 result=dict(whole_core_fit_successful=False,private_only=True,production_modified=False,
  configured_mhz=100,cpu_cache_kib=128,seed=2,seed_sweep=False,fit_invocations=1,
  effective_qsf_sha256=sha(qsf),seed_confirmed_in_fitter_report=True,
  resources=dict(alms_needed=18358,alm_capacity=18480,labs_required=1867,lab_capacity=1848,
   lab_excess=19,m10k=307,m10k_capacity=308,dsp=45,dsp_capacity=66),
  slow_0_fmax_mhz=None,slow_85_fmax_mhz=None,setup=None,hold=None,unconstrained=None,
  timing_note='Placement failed; no routed netlist. STA and a frequency estimate would not be meaningful.',
  full_core_functionally_qualified=False,deployment_ready=False,
  prepared_sha256=sha(OUT/'prepared.json'),independent_review_sha256=sha(OUT/'independent-review.json'),
  fitter_result_sha256=sha(FIT/'results/pocket-os30-s2/result.json'))
 save(OUT/'result.json',result)
 report='''Single private LOD5 CRAM + span-v3 AREA seed2 fit: NOT FITTING

Original CPU128 is unchanged. The effective fitted QSF has exactly one SEED2,
and the fitter report confirms2. Global BALANCED/AREA and duplicationOFF,
CPU instance SPEED, original clocks and span-v3 macro are retained.

18,358 /18,480 ALMs needed;1,867 /1,848 LABs required:19LABs over capacity.
307 /308 M10Ks and45 /66 DSPs. Synthesis passes; placement fails170012/11802.
No routing, bitstream or meaningful timing/Fmax/hold result exists. There was
one invocation and no sweep. Component and physical-address/reset tests pass;
this does not qualify a complete core or authorize deployment.

Against the prior fitted LOD4 AREA hierarchy, GPU adds252ALUT and83.1ALM;
the new CRAM reader arbiter is19ALUT/6.4ALM, core-local adds11.9ALM, LOAD
hierarchy drops44.8ALM due optimization/packing. Unchanged CPU varies11.6ALM.
The failed-fit hierarchy total18,280 excludes78memory-packingALM included in
the global18,358 requirement; individual hierarchy sums cannot prove fit.

All73 inferred RAM instances have identical dimensions/types/modes to LOD4.
There is no newly inferred array or MLAB-to-M10K conversion. Prior successful
fitting shares spanprod_u/v/count among two physicalM10Ks (X11_Y20 andY29);
this failed-fit report leaves those three primitives unassigned and separate.
That accounts for307 nominal versus306 previously placed M10Ks; a reduction
must not be presumed before an actual successful placement.

See hierarchy-comparison.json and the bound full fitter reports for details.
No additional fit or semantic change is made by this reporting script.
'''
 (OUT/'REPORT.txt').write_text(report)
 files=[OUT/n for n in ['prepared.json','independent-review.json','result.json','hierarchy-comparison.json','REPORT.txt']]
 files += [FIT/n for n in ['inventory.json','frozen-sources.json','config.json','experiment.json','recipe.py']]
 files += sorted((FIT/'results/pocket-os30-s2').rglob('*'))
 files += sorted((JOB/'output_files').glob('*.rpt'))+[qsf,Path(__file__)]
 files += [P/'cram1-share-full-v1/manifest.json',P/'lod5-client-review-v2/manifest.json',P/'lod5-components-v1/final-manifest.json']
 files += [OUT/'frozen'/n for n in json.loads((OUT/'prepared.json').read_text())['source_sha256']]
 save(OUT/'final-manifest.json',dict(completed=True,fit_passed=False,deployment_ready=False,evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(files)) if p.is_file()}))
 print(json.dumps(result,indent=2));print('manifest',sha(OUT/'final-manifest.json'))
if __name__=='__main__':main()
