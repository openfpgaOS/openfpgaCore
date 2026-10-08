#!/usr/bin/env python3
"""Seal the one-seed, QSF-only geometry-area resource experiment."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'gpu-direct-local-area-v1'
BASE=P/'gpu-direct-core-compact-v2'
QSF='src/fpga/targets/pocket/ap_core.qsf'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def reports(folder):
 d=folder/'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/output_files'
 f=(d/'ap_core.fit.rpt').read_text(errors='replace');m=(d/'ap_core.map.rpt').read_text(errors='replace')
 def num(label,text):
  matches=[x for x in text.splitlines() if x.startswith('; '+label+' ')]
  assert matches,label
  return int(matches[0].split(';')[2].strip().split()[0].replace(',',''))
 hierarchy={}
 for l in f.splitlines():
  a=[x.strip() for x in l.split(';')]
  if len(a)==18 and a[14].startswith('|apf_top'):
   hierarchy[a[14]]={k:a[i] for i,k in enumerate(['needed','placed','recoverable','unavailable','memory_alm','alut','registers','io_registers','memory_bits','dsp'],2)}
 return dict(needed_alm=num('ALMs needed [=A-B+C]',f),labs=num('Total LABs:  partially or completely used',f),m10k=num('M10K blocks',f),dsp=num('Total DSP Blocks',f),map_alut=num('Combinational ALUT usage for logic',m),map_registers=num('Dedicated logic registers',m),hierarchy=hierarchy)
def main():
 prep=json.loads((OUT/'prepared.json').read_text());old=json.loads((BASE/'prepared.json').read_text())
 for k,v in prep['source_sha256'].items():assert sha(OUT/'frozen'/k)==v,k
 for k,v in old['source_sha256'].items():assert sha(BASE/'frozen'/k)==v,k
 changed=[k for k,v in prep['source_sha256'].items() if old['source_sha256'].get(k)!=v]
 assert changed==[QSF] or set(changed)=={QSF}
 fit=json.loads((OUT/'fit-seed2/results.json').read_text());assert len(fit)==1 and fit[0]['seed']==2
 base=reports(BASE);candidate=reports(OUT)
 result=dict(scope=__doc__,rtl_byte_identical=True,source_file_count=len(prep['source_sha256']),changed_files=changed,fit_passed=fit[0]['valid'],deployment_ready=False,seed=2,seed_sweep=False,baseline=base,candidate=candidate,delta={k:candidate[k]-base[k] for k in ['needed_alm','labs','m10k','dsp','map_alut','map_registers']},required_alm_capacity=18480,remaining_alm_gap=candidate['needed_alm']-18480,fit_returncodes=fit[0]['returncodes'],fmax_mhz=None if not fit[0]['valid'] else fit[0].get('worst'),limitations=['Resource probe only. No RTL semantics, CPU configuration, cache, or frequency change.','A failed fitter has no routed timing/Fmax or deployable bitstream.','Failed-fit hierarchy area values are estimates, not independently additive savings.'])
 save(OUT/'result.json',result)
 text=f'''Geometry-local area settings did not produce a deployable core.

Compared with frozen gpu-direct-core-compact-v2, only ap_core.qsf changed.
All {result['source_file_count']} frozen source files were checked; all RTL is byte-identical.
One saved os30 seed2 was run. The CPU, 128KiB D-cache, 32KiB I-cache,
ISA, clocks, and global speed settings were preserved.

Quartus 25.1's installed assignment database confirms Cyclone V support for
instance OPTIMIZATION_TECHNIQUE AREA, QII_AUTO_PACKED_REGISTERS MINIMIZE AREA,
and PHYSICAL_SYNTHESIS_COMBO_LOGIC_FOR_AREA ON. These three settings were
applied only to core_top:ic|geometry_direct_mmio:geometry_direct.
The original global optimization was SPEED (Balanced was the report default).

                   compact-v2  local-area  difference
ALMs needed        {base['needed_alm']:>10}  {candidate['needed_alm']:>10}  {result['delta']['needed_alm']:>+10}
LABs               {base['labs']:>10}  {candidate['labs']:>10}  {result['delta']['labs']:>+10}
M10Ks              {base['m10k']:>10}  {candidate['m10k']:>10}  {result['delta']['m10k']:>+10}
DSP blocks         {base['dsp']:>10}  {candidate['dsp']:>10}  {result['delta']['dsp']:>+10}
Map ALUTs          {base['map_alut']:>10}  {candidate['map_alut']:>10}  {result['delta']['map_alut']:>+10}
Map registers      {base['map_registers']:>10}  {candidate['map_registers']:>10}  {result['delta']['map_registers']:>+10}

Fitter passed: {result['fit_passed']}; remaining ALM gap: {result['remaining_alm_gap']}.
No routed Fmax or device-performance claim is available from a failed fit.
No production or SD artifact was changed. All prior attempts remain intact.
'''
 (OUT/'REPORT.txt').write_text(text)
 evidence=[Path(__file__),ROOT/'tools/experiments/os30_fps25_20261005/phase10_local_area_fit.py',BASE/'prepared.json',OUT/'prepared.json',OUT/'candidate.patch',OUT/'result.json',OUT/'REPORT.txt',OUT/'assignment-help.tcl',OUT/'assignment-help.log',OUT/'option-values.tcl',OUT/'option-values.log',OUT/'fit-seed2/results.json',OUT/'fit-seed2/complete.json',OUT/'fit-seed2/config.json',OUT/'fit-seed2/experiment.json',OUT/'fit-seed2/inventory.json',OUT/'fit-seed2/frozen-sources.json']
 for parent in [BASE,OUT]:
  evidence.extend(parent/'frozen'/k for k in json.loads((parent/'prepared.json').read_text())['source_sha256'])
  d=parent/'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket'
  evidence.append(d/'ap_core.qsf')
  evidence.extend(d/'output_files'/f'ap_core.{x}' for x in ['map.rpt','fit.rpt','map.summary','fit.summary'])
  evidence.extend(parent/'fit-seed2/results/pocket-os30-s2'/x for x in ['map.log','fit.log','result.json'])
 hashes={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(evidence))}
 save(OUT/'final-manifest.json',dict(evidence_sha256=hashes,scope=__doc__,resource_probe_completed=True,fit_passed=result['fit_passed'],deployment_ready=False))
 print(json.dumps({k:result[k] for k in ['fit_passed','delta','remaining_alm_gap']},indent=2));print('manifest_sha256',sha(OUT/'final-manifest.json'))
if __name__=='__main__':main()
