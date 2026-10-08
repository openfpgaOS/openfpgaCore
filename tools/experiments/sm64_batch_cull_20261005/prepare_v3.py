#!/usr/bin/env python3
"""Conservative retry backoff: skip only software culling, never vertex loading."""
from pathlib import Path
import hashlib,json,difflib
from prepare import ROOT,OUT,GFX,BASE,EXPECTED,sha,once
from prepare_v2 import transform as v2_transform

def transform(name,s):
 s=v2_transform(name,s)
 if name!='gfx_geometry.h':return s
 s=once(s,'struct GeometrySoftwareBounds {const Vtx *source;unsigned count;float box[6];};','''/* Visible immutable batches retry culling every eighth load. A skipped test
 * always falls back to full CPU loading; a rejection is never reused. */
struct GeometrySoftwareBounds {const Vtx *source;uint16_t count,retry;float box[6];};''')
 s=once(s,'static const float *geometry_software_bounds(const Vtx *vertices,unsigned count,float box[6]) {\n    struct GeometrySoftwareBounds *cached=NULL;','''static const float *geometry_software_bounds(const Vtx *vertices,unsigned count,float box[6],
        struct GeometrySoftwareBounds **entry) {
    struct GeometrySoftwareBounds *cached=NULL;
    if(entry)*entry=NULL;''')
 s=once(s,'        if(cached->source==vertices && cached->count==count)return cached->box;','''        if(entry)*entry=cached;
        if(cached->source==vertices && cached->count==count) {
            if(cached->retry) {cached->retry--;return NULL;}
            return cached->box;
        }''')
 s=once(s,'    if(cached) {cached->source=vertices;cached->count=count;box=cached->box;}','    if(cached) {cached->source=vertices;cached->count=count;cached->retry=0;box=cached->box;}')
 s=once(s,'    const float *box=geometry_software_bounds(vertices,count,temporary);','    struct GeometrySoftwareBounds *cached;\n    const float *box=geometry_software_bounds(vertices,count,temporary,&cached);')
 s=once(s,'''    if(outside && geometry_confirm_outside(vertices,count,aspect,outside)) {
        vertex_cull_capture(vertices,count,first,aspect,outside);return 2;
    }
    return 0;
}

static __attribute__((noinline)) int geometry_prepare(''','''    if(outside && geometry_confirm_outside(vertices,count,aspect,outside)) {
        vertex_cull_capture(vertices,count,first,aspect,outside);return 2;
    }
    if(cached)cached->retry=7;
    return 0;
}

static __attribute__((noinline)) int geometry_prepare(''')
 return s

def main():
 records={};patch=[];v2patch=[]
 for name,h in EXPECTED.items():
  original=(BASE/name).read_text();assert sha(original)==h
  parent=v2_transform(name,original);candidate=transform(name,original);target=GFX/name
  assert target.read_text() in (parent,candidate),name
  target.write_text(candidate)
  records[name]={'before':h,'parent_v2':sha(parent),'after':sha(candidate),'changed':original!=candidate}
  patch.extend(difflib.unified_diff(original.splitlines(True),candidate.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
  v2patch.extend(difflib.unified_diff(parent.splitlines(True),candidate.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'batch-cull-v3.patch').write_text(''.join(patch));(OUT/'batch-cull-v2-to-v3.patch').write_text(''.join(v2patch))
 (OUT/'candidate-source-v3.json').write_text(json.dumps({'files':records,'recipes':{p:hashlib.sha256((Path(__file__).parent/p).read_bytes()).hexdigest() for p in ['prepare.py','prepare_v2.py','prepare_v3.py']},'status':'source-frozen; qualification pending','scope':['Only immutable source/count-keyed culling-attempt backoff after a failed reject test','7 subsequent hits skip cull and always run full CPU vertex loading','current successful rejects always recompute planes and confirm actual CPU clipping','mutable inputs never back off; cache replacement clears retry'],'software_bss_bytes_rv32':32944,'unchanged':'all accepted/rejected math, raw capability gates, capture/recovery, GEO1 source, and hardware state'},indent=2)+'\n')
 print(json.dumps(records,indent=2))
if __name__=='__main__':main()
