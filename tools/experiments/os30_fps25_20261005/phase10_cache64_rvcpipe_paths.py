#!/usr/bin/env python3
"""Read-only diagnosis of the existing pipeline fit's top-20 setup reports."""
import collections,hashlib,json,re,shutil
from pathlib import Path
from phase10_timing_paths_parse import parse
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
F=P/'cache64-rvcpipe-fit-v1';O=P/'cache64-rvcpipe-paths-v1'
T=F/'fit-seed2/job/src/fpga/targets/pocket/timing'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def family(row):
 a,b=row['from_node'],row['to_node']
 if '|gpu_tex_cache:tex_cache|' in a:return 'texture_tag_to_data_ram_address'
 if 'LsuL1Plugin_logic_BANKS_MUXES' in a:return 'cpu_load_data_format_and_forward'
 if 'LsuL1Plugin_logic_WAYS_HITS' in a:return 'cpu_load_hit_selection_and_forward'
 if '_up_RD_PHYS_lane0' in a:return 'cpu_operand_tag_and_forward'
 raise AssertionError((a,b))
def detail(p,row):
 blocks=re.split(r'(?=Path #\d+: Setup)',p.read_text())[1:]
 block=next(b for b in blocks if row['from_node'] in b.split('Data Arrival Path')[0] and row['to_node'] in b.split('Data Arrival Path')[0])
 active=False;hops=[]
 for line in block.split('Data Required Path')[0].splitlines():
  c=[x.strip() for x in line.split(';')][1:-1]
  if len(c)>=7 and c[-1]=='data path':active=True;continue
  if active and len(c)>=7 and c[3] in ('CELL','IC'):
   hops.append(dict(increment_ns=float(c[1]),type=c[3],fanout=int(c[4]),location=c[5],element=c[-1]))
 assert hops;return dict(path=row,hops=hops)
O.mkdir(exist_ok=False)
assert sha(F/'manifest.json')=='92f39d98b5690542d18a50998ec12791514e33bd949333a2e3b01191812d5236'
fit=json.loads((F/'result.json').read_text());assert not fit['comparative_physical_gate_pass']
allpaths={};families={};details={}
for c in ['slow_85','slow_0']:
 report=T/(c+'_setup.rpt');rows=parse(report);assert len(rows)==20
 allpaths[c]=rows;buckets=collections.defaultdict(list)
 for r in rows:buckets[family(r)].append(r)
 families[c]={n:dict(exported_paths=len(v),exported_below90=sum(r['period_bound_mhz']<90 for r in v),worst=min(v,key=lambda r:r['slack_ns']))for n,v in buckets.items()}
 details[c]={n:detail(report,d['worst'])for n,d in families[c].items()}
put(O/'paths.json',allpaths);put(O/'mapped-cones.json',details)
put(O/'result.json',dict(readonly=True,new_sta_invoked=False,new_fit_invoked=False,source_changes=False,fit_manifest_sha256=sha(F/'manifest.json'),physical_result=fit,top20_families=families,previous_aligner_rvc_cone_absent_from_top20=True,complete_below90_census=False,limits=['Only the existing20 worst setup paths per slow corner are parsed. Hot report stops below90, so it is not an exhaustive failing-endpoint census.','The former expanded-instruction capture is moved structurally; absence from these reports does not prove every remaining frontend path exceeds90.','Physical timing and source register counts are separate from measured native cycle cost; no further fit or source edit is selected here.']))
shutil.copy2(__file__,O/'recipe.py')
files=[p for p in O.iterdir()if p.is_file()]+[Path(__file__),R/'tools/experiments/os30_fps25_20261005/phase10_timing_paths_parse.py',F/'manifest.json',F/'result.json',T/'slow_85_setup.rpt',T/'slow_0_setup.rpt']
put(O/'manifest.json',dict(readonly=True,timing_closed=False,evidence_sha256={str(p.relative_to(R)):sha(p)for p in files}))
print(json.dumps(families,indent=2));print('MANIFEST',sha(O/'manifest.json'))
