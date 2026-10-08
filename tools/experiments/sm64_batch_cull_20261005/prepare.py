#!/usr/bin/env python3
"""Reproduce the private GEO-independent software batch-culling edit."""
from pathlib import Path
import hashlib,json,difflib
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005'
GFX=OUT/'SM64/src/sm64/sm64/src/pc/gfx'
BASE=OUT/'source-baseline'
EXPECTED={'gfx_geometry.h':'02f95cda2cdc7bf7b3c1aef9cc9bf387072440a0c84b4fa24bcec6e0a216a1a6','gfx_vertex_cull.h':'6c48e51766818099864b4629a1c06a28abd665da6ec4188605b755244b7788d2','gfx_pc.c':'4d8bd5cdef7b663adf86233763b54fda13d977abfaf225bdc8c0797d8e854e84'}
def sha(s):return hashlib.sha256(s.encode()).hexdigest()
def once(s,a,b):
 assert s.count(a)==1,(a,s.count(a))
 return s.replace(a,b)
HELPER='''/* With software transforms, confirm only a prospective reject plane using
 * the CPU dot products. Combining matrix coefficients into w +/- x/y/z can
 * round differently near cancellation even with the AABB's two-unit margin.
 * Visible batches do not pay this check; nonfinite results use the CPU. */
static __attribute__((noinline)) bool geometry_confirm_outside(const Vtx *vertices,
        size_t count,float aspect,unsigned clip) {
    const unsigned axis=(clip&(CLIP_RIGHT|CLIP_LEFT))?0:
                        (clip&(CLIP_TOP|CLIP_BOTTOM))?1:2;
    const bool positive=(clip&(CLIP_RIGHT|CLIP_TOP|CLIP_NEAR))!=0;
    const float m0=rsp.MP_matrix[0][axis],m1=rsp.MP_matrix[1][axis];
    const float m2=rsp.MP_matrix[2][axis],m3=rsp.MP_matrix[3][axis];
    const float m03=rsp.MP_matrix[0][3],m13=rsp.MP_matrix[1][3];
    const float m23=rsp.MP_matrix[2][3],m33=rsp.MP_matrix[3][3];
    for(size_t i=0;i<count;i++) {
        const float ox=vertices[i].v.ob[0],oy=vertices[i].v.ob[1],oz=vertices[i].v.ob[2];
        float coordinate=ox*m0+oy*m1+oz*m2+m3;
        if(axis==0)coordinate*=aspect;
        const float w=ox*m03+oy*m13+oz*m23+m33;
        uint32_t cb,wb;
        memcpy(&cb,&coordinate,sizeof(cb));memcpy(&wb,&w,sizeof(wb));
        if((cb&0x7f800000u)==0x7f800000u || (wb&0x7f800000u)==0x7f800000u)
            return false;
        if(positive ? !(coordinate>w) : !(coordinate < -w))return false;
    }
    return true;
}

'''
def transform(name,s):
 if name=='gfx_geometry.h':
  s=once(s,'static __attribute__((noinline)) int geometry_prepare(',HELPER+'static __attribute__((noinline)) int geometry_prepare(')
  s=once(s,'    if (!gfx_gpu_geometry_enabled() || first >= 32)\n','    const bool hardware_geometry=gfx_gpu_geometry_enabled();\n    if (first >= 32)\n')
  s=once(s,'    if(outside) {vertex_cull_capture(vertices,count,first,aspect,outside);return 2;}\n','''    if(outside && (hardware_geometry || geometry_confirm_outside(vertices,count,aspect,outside))) {
        vertex_cull_capture(vertices,count,first,aspect,outside);return 2;
    }
    /* Coarse software rejection is independent of the hardware transform
     * capability. All accepted software batches still use the ordinary CPU. */
    if(!hardware_geometry)return 0;
''')
 elif name=='gfx_pc.c':
  s=once(s,'''    if((vtx1_idx|vtx2_idx|vtx3_idx)<32 && culled_pending) {
        if(v1->clip_rej&v2->clip_rej&v3->clip_rej)return;
        vertex_cull_materialize(vtx1_idx);vertex_cull_materialize(vtx2_idx);vertex_cull_materialize(vtx3_idx);
    }
''','''    if(culled_pending) {
        if(v1->clip_rej&v2->clip_rej&v3->clip_rej)return;
        /* A triangle may mix deferred low slots with ordinary slots 32+. */
        if(vtx1_idx<32)vertex_cull_materialize(vtx1_idx);
        if(vtx2_idx<32)vertex_cull_materialize(vtx2_idx);
        if(vtx3_idx<32)vertex_cull_materialize(vtx3_idx);
    }
''')
 return s

def main():
 records={};patch=[]
 for name,h in EXPECTED.items():
  original=(BASE/name).read_text();assert sha(original)==h
  candidate=transform(name,original)
  target=GFX/name
  assert target.read_text() in (original,candidate),name
  target.write_text(candidate)
  assert transform(name,original)==target.read_text()
  records[name]={'before':h,'after':sha(candidate),'changed':original!=candidate}
  patch.extend(difflib.unified_diff(original.splitlines(True),candidate.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'batch-cull.patch').write_text(''.join(patch))
 (OUT/'candidate-source.json').write_text(json.dumps({'files':records,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'status':'source-frozen; qualification pending','scope':['GEO0 coarse reject plus CPU-plane confirmation','CPU fallback remains mandatory for GEO0 accepted batches','recover pending low slots even in mixed-index triangles'],'unchanged':'hardware geometry context, quantization, eligibility, bounds cache, captured state and materialization'},indent=2)+'\n')
 print(json.dumps(records,indent=2))
if __name__=='__main__':main()
