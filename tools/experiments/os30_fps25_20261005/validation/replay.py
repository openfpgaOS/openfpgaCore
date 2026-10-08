#!/usr/bin/env python3
"""Render exact A GPU and Pocket memory; outputs and component cycles only."""
from common import *
import argparse,concurrent.futures,re,subprocess

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('labels',nargs='+');p.add_argument('--modes',default='1,2');a=p.parse_args()
 exe=PREVIOUS/'replay-single-A/obj/Vtb_gpu';manifest=read(PREVIOUS/'replay-single-A/manifest.json');assert sha(exe)==manifest['simulator_sha256']
 for rel,digest in manifest['sources'].items():assert sha(PREVIOUS/'replay-single-A'/rel)==digest,rel
 retained=load('replay');jobs=[]
 for label in a.labels:
  src=capture_path(label);check_capture(src)
  for mode in map(int,a.modes.split(',')):
   for vec in sorted((src/'gpuvec').glob('*.vec')):jobs.append((label,mode,vec))
 def run(job):
  label,mode,vec=job;dest=OUT/'replays-single-A'/label/str(mode);dest.mkdir(parents=True,exist_ok=True);stem=dest/vec.stem
  assert not stem.with_suffix('.log').exists(),stem
  counts,depth=retained.with_depth(vec,stem.with_suffix('.vec'))
  cmd=[str(exe),str(stem.with_suffix('.vec')),str(stem.with_suffix('.bin')),f'+memory_traffic={mode}']
  with stem.with_suffix('.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
  log=stem.with_suffix('.log').read_text();stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',log)}
  data=stem.with_suffix('.bin').read_bytes();assert len(data)==153612+(153600 if depth else 0)
  return dict(label=label,mode=mode,frame=int(vec.stem.split('_')[1]),stats=stats,hardware_sha256=sha(exe),framebuffer_sha256=hashlib.sha256(data[:153600]).hexdigest(),depth_sha256=hashlib.sha256(data[153612:]).hexdigest() if depth else None,vector_sha256=sha(vec),commands=counts,binary=str(stem.with_suffix('.bin').relative_to(ROOT)),log_sha256=sha(stem.with_suffix('.log')))
 rows=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  for row in pool.map(run,jobs):
   rows.append(row);(OUT/('replay-results-'+'-'.join(a.labels)+'.json')).write_text(json.dumps(rows,indent=2)+'\n');print(row['label'],row['mode'],row['frame'],row['stats']['cycles'],flush=True)
if __name__=='__main__':main()
