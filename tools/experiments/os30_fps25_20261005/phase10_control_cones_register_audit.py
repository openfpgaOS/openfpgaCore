#!/usr/bin/env python3
"""Read existing mapped reports; no synthesis or live database access."""
import collections,hashlib,json,re,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'rvcpipe-gpu32-forward-texture-map-v1';C=P/'control-cones-compose-map-v1';O=P/'control-cones-map-register-audit-v1'
REPORT=Path('map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt')
GPU=Path('frozen/src/fpga/common/gpu_core.v')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def table(text,title):
 a=text.index('; '+title);b=text.find('\n\n',a);out={}
 for line in text[a:b].splitlines():
  fields=[v.strip() for v in line.split(';')[1:-1]]
  if len(fields)==2:out[fields[0]]=fields[1]
 return out
def main():
 assert not O.exists()
 old,new=(x/REPORT for x in [B,C]);a,b=(table(x.read_text(),'Registers Removed During Synthesis') for x in [old,new])
 stats=[table(x.read_text(),'General Register Statistics') for x in [old,new]]
 data=json.loads((C/'result.json').read_text())
 assert data['total_delta']['registers']==1 and data['entity_delta']['gpu_core']['registers']==1
 assert data['entity_delta']['VexiiRiscv']['registers']==0
 scope='core_top:ic|gpu_core:gpu|'
 changes={k:{'parent':a.get(k),'candidate':b.get(k)} for k in sorted(a.keys()|b.keys()) if k.startswith(scope) and a.get(k)!=b.get(k)}
 assert a[scope+'sp_fastpath']=='Stuck at GND due to stuck port data_in' and scope+'sp_fastpath' not in b
 sources=[(x/GPU).read_text() for x in [B,C]]
 writes=[re.findall(r'\bsp_fastpath\s*<=\s*([^;]+);',s) for s in sources]
 assert writes[0]==writes[1] and len(writes[0])==4
 declarations=[re.findall(r'(?m)^reg\s+sp_fastpath\s*;',s) for s in sources]
 assert declarations[0]==declarations[1] and len(declarations[0])==1
 assert b[scope+'sp_zinv_step[28..31]']=='Lost fanout'
 O.mkdir()
 review=dict(passed=True,scope='Existing report/source read only; no CAD, simulations or live database queries',
  top_registers=[int(x['Total registers']) for x in stats],cpu_register_delta=0,gpu_local_registers=[7675,7676],
  synthesis_register_controls=stats,removed_register_changes=changes,
  sp_fastpath=dict(parent_removal=a[scope+'sp_fastpath'],candidate_removal=None,source_declaration_unchanged=True,all_four_source_writes_unchanged=True),
  observed_alias_differences='cmap_pending_addr merge chains and texture axi_araddr/lat_addr representatives change; texture entity register count stays137.',
  observed_pruning_differences='persp_pss anonymous temporary numbering changes; candidate explicitly reports sp_zinv_step[28..31] lost fanout.',
  conclusion='The +1 is in mapped GPU-local registers, not added RTL pipeline/state. Retention of the existing sp_fastpath bit is visible, while other aliases/pruning also change.',
  limits=['Removal tables alone do not uniquely account for every mapped register or prove a one-to-one explanation of the +1 balance.',
          'Functional ownership and cycle identity rely on separately bound GPU source/component and 17-artifact native gates. No physical timing claim.'])
 (O/'review.json').write_text(json.dumps(review,indent=2)+'\n')
 (O/'REPORT.txt').write_text('GPU-local mapped registers increase7675→7676; CPU registers unchanged. The original sp_fastpath declaration and all four writes are exact, but the parent reports it removed as stuck0 and candidate does not. Other alias representatives, anonymous FSM temporary names and high-bit pruning also change. This is synthesis representation, not a new RTL pipeline stage. The report does not uniquely allocate the net+1 to one named bit. No further compilation or DB access.\n')
 shutil.copy2(__file__,O/'recipe.py')
 files=[old,new,B/GPU,C/GPU,C/'result.json',C/'manifest.json',P/'gpu-control-owner-source-review-v1/manifest.json',P/'native-control-cones-check-v1/manifest.json',Path(__file__),*O.iterdir()]
 (O/'manifest.json').write_text(json.dumps(dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}),indent=2)+'\n')
 print(sha(O/'manifest.json'))
if __name__=='__main__':main()
