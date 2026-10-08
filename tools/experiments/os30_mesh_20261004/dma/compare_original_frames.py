#!/usr/bin/env python3
"""Compare hybrid color/depth dumps to the original production GPU captures."""
import argparse,json,struct
from pathlib import Path

def words(data):return struct.unpack('<%dH'%(len(data)//2),data)
def difference(a,b):
 assert len(a)==len(b)==76800
 changed=[(x,y)for x,y in zip(a,b)if x!=y]
 return dict(different=len(changed),coverage_different=sum((x==0)!=(y==0)for x,y in changed),
             one_encoded_step=sum(abs(x-y)==1 for x,y in changed),
             max_encoded_delta=max([abs(x-y)for x,y in changed],default=0),
             within_one_rgb565_channel_step=sum(all(abs(((x>>s)&m)-((y>>s)&m))<=1 for s,m in [(11,31),(5,63),(0,31)])for x,y in changed))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--original',type=Path,required=True);ap.add_argument('--replay',type=Path,required=True);a=ap.parse_args();rows=[]
 for frame in range(360,368):
  original=(a.original/f'frame-{frame}/original.bin').read_bytes();d=a.replay/f'frame-{frame}';info=json.loads((d/'result.json').read_text());current=(d/'mesh.bin').read_bytes()
  assert len(original)==307212,'Original format must be color153600,fence4,depth153600,hash8'
  row=dict(frame=frame)
  for kind,start in [('dump',0),('depth_dump',153604)]:
   ranges=[r for r in info['output_ranges']if r['kind']==kind];assert len(ranges)==1;r=ranges[0];assert r['length']==153600
   row[kind]=difference(words(original[start:start+153600]),words(current[r['offset']:r['offset']+153600]))
  rows.append(row)
 report=dict(scope=__doc__,results=rows,all_colors_exact=all(r['dump']['different']==0 for r in rows),all_depths_exact=all(r['depth_dump']['different']==0 for r in rows))
 (a.replay/'original-comparison.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
