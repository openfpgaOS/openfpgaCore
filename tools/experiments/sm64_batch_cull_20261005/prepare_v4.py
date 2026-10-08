#!/usr/bin/env python3
"""Remove the bounded immutable-range divide and skip empty hardware cleanup."""
from pathlib import Path
import hashlib,json,difflib
from prepare import ROOT,OUT,GFX,BASE,EXPECTED,sha,once
from prepare_v3 import transform as v3_transform

def transform(name,s):
 s=v3_transform(name,s)
 if name!='gfx_geometry.h':return s
 start=s.index('static const float *geometry_software_bounds(');end=s.index('static __attribute__((noinline)) void geometry_software_update_planes(',start)
 part=s[start:end]
 part=once(part,'count<=(end-address)/sizeof(Vtx)','count*sizeof(Vtx)<=end-address')
 # Production caller admits 8..32 before this helper; 32*sizeof(Vtx)=768 on RV32.
 part=once(part,'    uintptr_t address=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;','    /* The caller admits at most 32 vertices, so this product cannot wrap. */\n    uintptr_t address=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;')
 s=s[:start]+part+s[end:]
 start=s.index('static __attribute__((noinline)) int geometry_prepare_software(');end=s.index('    if(gCurrLevelNum',start)
 part=s[start:end];body=part[part.index('    uint32_t mask='):]
 part=part[:part.index('    uint32_t mask=')]+'    if(geometry_valid|geometry_pending) {\n'+''.join('    '+line if line.strip() else line for line in body.splitlines(True))+'    }\n'
 s=s[:start]+part+s[end:]
 return s

def main():
 records={};patch=[];delta=[]
 for name,h in EXPECTED.items():
  original=(BASE/name).read_text();assert sha(original)==h
  parent=v3_transform(name,original);candidate=transform(name,original);target=GFX/name
  assert target.read_text() in (parent,candidate),name
  target.write_text(candidate)
  records[name]={'before':h,'parent_v3':sha(parent),'after':sha(candidate),'changed':original!=candidate}
  patch.extend(difflib.unified_diff(original.splitlines(True),candidate.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
  delta.extend(difflib.unified_diff(parent.splitlines(True),candidate.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'batch-cull-v4.patch').write_text(''.join(patch));(OUT/'batch-cull-v3-to-v4.patch').write_text(''.join(delta))
 # For every allowed count, both predicates change only at count*sizeof(Vtx).
 checks=0
 for count in range(33):
  for available in list(range(1025))+[0xffffffff,0xfffffff0]:
   assert (count<=available//24)==(count*24<=available);checks+=1
 (OUT/'candidate-source-v4.json').write_text(json.dumps({'files':records,'recipes':{p:hashlib.sha256((Path(__file__).parent/p).read_bytes()).hexdigest() for p in ['prepare.py','prepare_v2.py','prepare_v3.py','prepare_v4.py']},'status':'source-frozen; qualification pending','scope':['Bounded 8..32 vertex input range uses multiplication rather than unsigned division','Hardware ownership cleanup skips only when both bitsets are zero'],'range_proof':{'tests':checks,'max_rv32_product':768,'size_of_vtx_rv32':24,'identity':'For positive S and n*S representable, n<=floor(a/S) iff n*S<=a','caller_guards':'count>32-first || count<8 checked before the only production helper call; first<32'},'empty_cleanup_proof':'Both original zero masks remain zero and release loop executes zero times; context owners and all other state unchanged','software_bss_bytes_rv32':32944,'unchanged':'all culling/confirmation/cache/retry/clip/recovery expressions and GEO1 body'},indent=2)+'\n')
 print(json.dumps(records,indent=2))
if __name__=='__main__':main()
