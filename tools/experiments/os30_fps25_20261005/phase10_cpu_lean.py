#!/usr/bin/env python3
"""Bound a smaller full-ISA CPU: queues + fetch32 + radix2 + serial I2F."""
import argparse,hashlib,json,shutil
from pathlib import Path
import phase10_cpu_queues as base
OUT=base.P/'cpu-lean-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def prepare():
 base.OUT=OUT;base.prepare()
 recipe=json.loads((OUT/'recipe.json').read_text());(OUT/'recipe.json').rename(OUT/'queue-preparation.json')
 changes={'--fetch-l1-mem-data-width-min=64':'--fetch-l1-mem-data-width-min=32','--div-radix=4':'--div-radix=2'}
 recipe['flags']=[changes.get(x,x) for x in recipe['flags'] if x!='--fpu-i2f-pipelined']
 recipe['changes'].update(changes);recipe['changes']['--fpu-i2f-pipelined']='removed'
 helper=OUT/'helpers/retime_fetch_reads.pl';text=helper.read_text();assert text.count('read_cmd_payload]", 64);')==1
 helper.write_text(text.replace('read_cmd_payload]", 64);','read_cmd_payload]", 32);'))
 recipe['helper_sha256'][helper.name]=sha(helper);recipe['scope']=__doc__;recipe['recipe_sha256']=sha(Path(__file__))
 (OUT/'recipe.json').write_text(json.dumps(recipe,indent=2)+'\n')
 removed=[]
 for rel in ['ext/SpinalHDL/project/project/target','ext/SpinalHDL/project/target','project/project/target','project/target']:
  p=OUT/'source'/rel
  if p.exists():shutil.rmtree(p);removed.append(rel)
 (OUT/'cache-preparation.json').write_text(json.dumps(dict(reason='Remove copied absolute-path SBT metadata before first generation',removed=removed),indent=2)+'\n')
def generate():base.OUT=OUT;base.generate()
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','generate']);a=p.parse_args();globals()[a.action]()
