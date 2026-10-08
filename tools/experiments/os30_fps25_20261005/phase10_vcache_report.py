#!/usr/bin/env python3
"""Bind the narrow vertex cache's pixel, full-bit and saved-seed fit proof."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/vcache-narrow-v1'
FIT=OUT/'fit-seed2'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
replay=json.loads((OUT/'replay-results.json').read_text());slots=json.loads((OUT/'slot-tests/result.json').read_text());obs=json.loads((OUT/'observer/result.json').read_text());negative=json.loads((OUT/'negative-control/result.json').read_text())
assert replay['passed'] and slots['passed'] and obs['passed'] and negative['passed']
assert obs['runs'][0]['observer_counts']==[['1484','854']] and obs['runs'][1]['observer_counts']==[['2968','2562']]
f=json.loads((FIT/'results/pocket-os30-s2/result.json').read_text());assert f['valid'] and f['state']=='complete'
report=FIT/'results/pocket-os30-s2/ap_core.fit.rpt';text=report.read_text(errors='replace')
def number(pattern):return float(re.search(pattern,text).group(1).replace(',',''))
metrics=dict(alms_needed=f['alms'],placed_alms=int(number(r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)')),labs=int(number(r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)')),m10ks=f['m10ks'],dsps=int(number(r';\s*Total DSP Blocks\s*;\s*([\d,]+)')),worst_slack_ns=f['worst'])
rams=[]
for line in text.split('; Fitter RAM Summary')[1].splitlines():
 r=[x.strip() for x in line.split(';')][1:-1]
 if len(r)==28 and r[0].startswith('core_top:ic|'):rams.append(dict(name=r[0],depth=int(r[4]),width=int(r[5]),m10ks=int(r[18]),locations=[a.strip() for a in r[21].split(',')]))
locs={a for r in rams for a in r['locations'] if a.startswith('M10K_')};assert len(locs)==302
vc=[r for r in rams if ':vc_mem_' in r['name']];assert len(vc)==1 and (vc[0]['depth'],vc[0]['width'],vc[0]['m10ks'])==(192,32,1)
sta=(FIT/'results/pocket-os30-s2/ap_core.sta.rpt').read_text(errors='replace')
fm=re.findall(r';\s*([\d.]+) MHz\s*;\s*[\d.]+ MHz\s*;\s*ic\|mp_ram\|',sta);assert len(fm)==2
metrics['main_fmax_mhz']=dict(slow85=float(fm[0]),slow0=float(fm[1]))
aggregate=[]
for t in range(3):
 rows=[r for r in replay['comparisons'] if r['traffic']==t and r['frame']>=360];b=sum(r['baseline_cycles'] for r in rows);c=sum(r['narrow_cycles'] for r in rows)
 aggregate.append(dict(traffic=t,baseline_cycles=b,narrow_cycles=c,cycle_change_percent=100*(c/b-1),mean_extra_ms_at100=(c-b)/len(rows)/100000))
prepared=json.loads((OUT/'prepared.json').read_text());assert sha(OUT/'gpu_core.v')==prepared['candidate_sha256']
frozen=json.loads((FIT/'frozen-sources.json').read_text());base=ROOT/'docs/measurements/os30-dual-20261004/fit-single128-v1';old=json.loads((base/'frozen-sources.json').read_text());assert [p for p in frozen if frozen[p]!=old[p]]==['src/fpga/common/gpu_core.v']
for p,h in frozen.items():assert sha(FIT/'frozen'/p)==h
for p,h in json.loads((OUT/'model/inputs.json').read_text()).items():assert sha(OUT/'model/frozen'/p)==h
result=dict(passed_correctness=True,private_only=True,deployed=False,whole_core_fit_successful=True,full_100mhz_timing_closed=False,seed=2,seed_sweep=False,candidate_sha256=sha(OUT/'gpu_core.v'),baseline_sha256=prepared['original_sha256'],metrics=metrics,delta=dict(alms_needed=-164,placed_alms=-36,labs=-2,m10ks=-4,dsps=0),vertex_ram=vc[0],unique_physical_m10ks=len(locs),original_frame_comparisons=24,slot_test_comparisons=6,full_176_bit_writes=4452,full_176_bit_triangles=3416,negative_control_detected=True,gameplay_replay_aggregates=aggregate,limitations=['Serial GPU replay with physical SDRAM/CRAM1 models, not simultaneous full CPU/game execution or Pocket FPS.','Adds5 cycles per vertex write and15 per indexed triangle; state timing is intentionally changed.','32 slots/176-bit records and 0x56/0x54 packet ABI preserved; fitted configuration is retained A only.','GPU cache contents are undefined after reset; commands must reinitialize state and reload vertices.','No raw geometry accelerator included in this resource/timing fit.'])
save(OUT/'result.json',result)
(OUT/'REPORT.txt').write_text('''Private narrow vertex cache, retained A os30 seed2

The six-word layout preserves all32 vertex slots and all176 bits per slot,
including independent depth and reciprocal-depth fields. The old32x176 RAM
used5 M10Ks; fitted192x32 uses1. Packet ABI and viewport arithmetic are unchanged.
Serialization adds5 cycles per vertex write and15 per indexed triangle.

Full fitter:17084 ALMs needed (−164),18005 placed (−36),1844/1848 LABs (−2),
302/308 M10Ks (−4),42 DSPs (unchanged). CPU and all other frozen sources match
retained A. Exactly one saved-seed2 fit; no sweep and no production changes.
Timing:92.14 MHz slow85 /91.58 MHz slow0. Worst setup−0.919 ns, hold+0.118 ns,
recovery+2.364 ns, removal+0.430 ns. Above90 MHz, but100 MHz is not closed.
Retained A was92.53/93.65 MHz, so this seed has a modest timing regression.

Correctness:24 paired comparisons (8 F11 frames x3 memory schedules) preserve
color/depth/fences exactly. Six more paired comparisons move all vertices to
slots16–31 or interleave contrasting shadow writes to upper slots plus
duplicate-index degenerate triangles. All equal original output. A passive
wide golden cache checks4452 writes and3416 triangles at the full176-bit
storage/derive boundary; an intentional low-depth-bit corruption triggers it.

For gameplay samples360,400,500,600,720, serial GPU cycles increase0.775%,
0.748%,0.687% under schedules0,1,2 respectively (~0.165 ms/frame at100 MHz).
This measures serial GPU replay with real memory-controller RTL, not Pocket
FPS or full CPU/GPU overlap. A separate integrated accelerator test must show
that its benefit exceeds this small backend cost. No application, FPGA
package, SD card or production source was replaced.
''')
evidence={}
for folder in [OUT/'replays',OUT/'slot-tests',OUT/'observer',OUT/'negative-control']:
 for p in folder.rglob('*'):
  if p.is_file() and ('obj' not in p.relative_to(folder).parts):evidence[str(p.relative_to(ROOT))]=sha(p)
for p in [OUT/'gpu_core.v',OUT/'candidate.patch',OUT/'prepared.json',OUT/'replay-results.json',OUT/'result.json',OUT/'REPORT.txt',OUT/'model/build.json',OUT/'model/inputs.json',OUT/'model/build.log',FIT/'frozen-sources.json',FIT/'experiment.json',FIT/'inventory.json',FIT/'results.json']:
 evidence[str(p.relative_to(ROOT))]=sha(p)
for p in (FIT/'results/pocket-os30-s2').rglob('*'):
 if p.is_file():evidence[str(p.relative_to(ROOT))]=sha(p)
for name in ['phase10_vcache_narrow.py','phase10_vcache_narrow_fit.py','phase10_vcache_slots.py','phase10_vcache_observer.py','phase10_vcache_negative.py','phase10_vcache_report.py']:
 p=Path(__file__).with_name(name);evidence[str(p.relative_to(ROOT))]=sha(p)
save(OUT/'final-manifest.json',dict(passed=True,evidence_sha256=evidence))
print(json.dumps(dict(passed=True,metrics=metrics,manifest_sha256=sha(OUT/'final-manifest.json'))))
