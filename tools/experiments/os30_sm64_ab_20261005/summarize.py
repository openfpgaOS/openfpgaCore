#!/usr/bin/env python3
"""Summarize manually transcribed frozen P3 snapshots; never write to the card."""
import argparse,csv,json,math,statistics
from collections import defaultdict
from pathlib import Path

CONFIG={
 'A':{'label':'previous single, GEO0','geo':0,'rbf_sha256':'d3d90e20c7116304108a2f94b65d3a69335347603fab388b905cafc3fc6f5f9f'},
 'B':{'label':'current dual, GEO0','geo':0,'rbf_sha256':'b4d55466283320c798047effa1dd9895d990fd6f297857e52ad99ec9bbf3778d'},
 'C':{'label':'older single plus geometry, GEO1','geo':1,'rbf_sha256':'a950b6d7a8343d610cd46adb1cd85676ad3f83c6e80985522ba6c29c682295a0'},
}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('csv',type=Path);a=p.parse_args()
 groups=defaultdict(list);warnings=[];seen=set()
 for line,row in enumerate(csv.DictReader(a.csv.open(newline='')),2):
  if not row.get('fps','').strip():continue
  config=row['configuration'].strip();scene=row['scene_id'].strip()
  if config not in CONFIG or not scene:raise ValueError(f'Line{line}: configuration A/B/C and scene_id required')
  key=(scene,config,row['session'],row['sample'])
  if key in seen:raise ValueError(f'Line{line}: duplicate snapshot {key}')
  seen.add(key)
  values={k:float(row[k]) for k in ['fps','lcd','rend_ms','smp','geo']}
  if not all(math.isfinite(v) and v>=0 for v in values.values()):raise ValueError(f'Line{line}: invalid numbers')
  if values['fps']<=0 or values['lcd']<=0:raise ValueError(f'Line{line}: nonpositive frame rate')
  if values['smp']!=int(values['smp']) or values['geo']!=CONFIG[config]['geo']:raise ValueError(f'Line{line}: invalid SMP or unexpected GEO')
  if not values['smp']:warnings.append(f'{key}: SMP0; detailed timing unusable')
  if abs(values['fps']-values['lcd'])>0.5:warnings.append(f'{key}: FPS/LCD differ; inspect presentation behavior')
  if not row.get('screenshot','').strip():warnings.append(f'{key}: screenshot reference absent')
  values.update(session=row['session'],sample=row['sample'],screenshot=row.get('screenshot',''))
  groups[(scene,config)].append(values)
 summaries={}
 for (scene,config),rows in groups.items():
  if len(rows)<3:warnings.append(f'{scene}/{config}: fewer than3 snapshots; incomplete initial pass')
  elif len(rows)<5:warnings.append(f'{scene}/{config}: initial pass only;5 snapshots preferred')
  summaries.setdefault(scene,{})[config]={
   'configuration':CONFIG[config],'snapshots':len(rows),'sessions':sorted(set(v['session'] for v in rows)),
   'metrics':{k:{'median':statistics.median(v[k] for v in rows),'minimum':min(v[k] for v in rows),'maximum':max(v[k] for v in rows)} for k in ['fps','lcd','rend_ms','smp']},
  }
 comparisons=[]
 for scene,items in summaries.items():
  for before,after in [('A','B'),('C','B')]:
   if before not in items or after not in items:continue
   x,y=[items[k]['metrics'] for k in [before,after]]
   comparisons.append({'scene':scene,'reference':before,'candidate':after,
    'fps_gain_percent':100*(y['fps']['median']/x['fps']['median']-1),
    'lcd_gain_percent':100*(y['lcd']['median']/x['lcd']['median']-1),
    'render_time_reduction_percent':100*(1-y['rend_ms']['median']/x['rend_ms']['median']) if x['rend_ms']['median'] else None,
    'fps_ranges_overlap':max(x['fps']['minimum'],y['fps']['minimum'])<=min(x['fps']['maximum'],y['fps']['maximum']),
    'lcd_ranges_overlap':max(x['lcd']['minimum'],y['lcd']['minimum'])<=min(x['lcd']['maximum'],y['lcd']['maximum'])})
 print(json.dumps({'scope':'Measured complete-core configurations using the same P3 app/OS; no pure issue-width or frame-pacing claim. Window medians, not pooled-frame rates.','scenes':summaries,'comparisons':comparisons,'warnings':warnings},indent=2))
if __name__=='__main__':main()
