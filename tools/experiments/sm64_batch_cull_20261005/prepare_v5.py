#!/usr/bin/env python3
"""Keep colliding immutable batch keys with bounded linear probing."""
from pathlib import Path
import hashlib,json,difflib
from prepare import ROOT,OUT,GFX,BASE,EXPECTED,sha,once
from prepare_v4 import transform as v4_transform

def transform(name,s):
 s=v4_transform(name,s)
 if name!='gfx_geometry.h':return s
 start=s.index('static const float *geometry_software_bounds(');end=s.index('static __attribute__((noinline)) void geometry_software_update_planes(',start)
 part=s[start:end]
 part=once(part,'        cached=&geometry_software_bounds_cache[hash];','''        /* Different immutable batches sharing a hash keep independent
         * bounds/retry state. After sixteen probes, replace only the home entry. */
        unsigned tries=0,index=hash;
        for(;tries<16;tries++,index=(index+1)&1023u) {
            cached=&geometry_software_bounds_cache[index];
            if(!cached->source || (cached->source==vertices && cached->count==count))break;
        }
        if(tries==16)cached=&geometry_software_bounds_cache[hash];''')
 return s[:start]+part+s[end:]

def main():
 records={};patch=[];delta=[]
 for name,h in EXPECTED.items():
  original=(BASE/name).read_text();assert sha(original)==h
  parent=v4_transform(name,original);candidate=transform(name,original);target=GFX/name
  assert target.read_text() in (parent,candidate),name
  target.write_text(candidate)
  records[name]={'before':h,'parent_v4':sha(parent),'after':sha(candidate),'changed':original!=candidate}
  patch.extend(difflib.unified_diff(original.splitlines(True),candidate.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
  delta.extend(difflib.unified_diff(parent.splitlines(True),candidate.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'batch-cull-v5.patch').write_text(''.join(patch));(OUT/'batch-cull-v4-to-v5.patch').write_text(''.join(delta))
 (OUT/'candidate-source-v5.json').write_text(json.dumps({'files':records,'recipes':{p:hashlib.sha256((Path(__file__).parent/p).read_bytes()).hexdigest() for p in ['prepare.py','prepare_v2.py','prepare_v3.py','prepare_v4.py','prepare_v5.py']},'status':'source-frozen; qualification pending','scope':['Only immutable source/count cache lookup changes from direct map to bounded linear probe','At most16 examined entries; empty or exact key ends search; budget exhaustion replaces home slot','Each distinct retained key has independent retry; all replacement retry resets unchanged'],'software_bss_bytes_rv32':32944,'unchanged':'all arithmetic, confirmation, eligibility, cache ownership, cooldown policy, capture/recovery and GEO1 source'},indent=2)+'\n')
 print(json.dumps(records,indent=2))
if __name__=='__main__':main()
