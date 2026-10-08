#!/usr/bin/env python3
"""Dedicated GEO0 bounds-only path; leave GEO1 hardware preparation unchanged."""
from pathlib import Path
import hashlib,json,difflib
from prepare import ROOT,OUT,GFX,BASE,EXPECTED,HELPER,sha,once
SOFTWARE=r'''/* GEO0 uses only cached bounds and frustum planes. It never builds the
 * hardware's normalized input pool or eight acceptance guard planes. */
struct GeometrySoftwareBounds {const Vtx *source;unsigned count;float box[6];};
static struct GeometrySoftwareBounds geometry_software_bounds_cache[1024];
static struct GeometryPlane geometry_software_planes[6];
static uint32_t geometry_software_planes_epoch;
static float geometry_software_planes_aspect;

static const float *geometry_software_bounds(const Vtx *vertices,unsigned count,float box[6]) {
    struct GeometrySoftwareBounds *cached=NULL;
#ifndef OF_PC
    uintptr_t address=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;
    if(address>=(uintptr_t)&_start && address<end && count<=(end-address)/sizeof(Vtx)) {
        unsigned hash=(((uint32_t)(address>>3)^(count*40503u))*2654435761u)>>22;
        cached=&geometry_software_bounds_cache[hash];
        if(cached->source==vertices && cached->count==count)return cached->box;
    }
#endif
    float lo[3]={vertices[0].v.ob[0],vertices[0].v.ob[1],vertices[0].v.ob[2]};
    float hi[3]={lo[0],lo[1],lo[2]};
    for(unsigned i=0;i<count;i++)for(unsigned k=0;k<3;k++) {
        float value=vertices[i].v.ob[k];
        if(!(value>=-32768.0f && value<=32767.0f))return NULL;
        if((float)(int16_t)value!=value)return NULL;
        if(value<lo[k])lo[k]=value;
        if(value>hi[k])hi[k]=value;
    }
    if(cached) {cached->source=vertices;cached->count=count;box=cached->box;}
    for(unsigned k=0;k<3;k++) {box[2*k]=lo[k];box[2*k+1]=hi[k];}
    return box;
}

static __attribute__((noinline)) void geometry_software_update_planes(float aspect) {
    for(unsigned p=0;p<6;p++)for(unsigned k=0;k<4;k++) {
        const unsigned axis=p/2;
        float v=rsp.MP_matrix[k][axis]*(axis==0?aspect:1.0f);
        float c=rsp.MP_matrix[k][3]+((p&1)?v:-v);
        geometry_software_planes[p].c[k]=c;
        if(k<3)geometry_software_planes[p].corner[k]=2*k+(c<0.0f);
    }
    geometry_software_planes_epoch=geometry_mvp_epoch;
    geometry_software_planes_aspect=aspect;
}

static __attribute__((noinline)) unsigned geometry_software_outside(const float *box,float aspect) {
    if(geometry_software_planes_epoch!=geometry_mvp_epoch || geometry_software_planes_aspect!=aspect)
        geometry_software_update_planes(aspect);
    static const unsigned clip[6]={CLIP_RIGHT,CLIP_LEFT,CLIP_TOP,CLIP_BOTTOM,CLIP_NEAR,CLIP_FAR};
    for(unsigned i=0;i<6;i++) {
        const struct GeometryPlane *p=&geometry_software_planes[i];
        float maximum=box[p->corner[0]^1]*p->c[0]+box[p->corner[1]^1]*p->c[1]
                     +box[p->corner[2]^1]*p->c[2]+p->c[3];
        if(maximum < -2.0f)return clip[i];
    }
    return 0;
}

static __attribute__((noinline)) int geometry_prepare_software(const Vtx *vertices,
        size_t count,size_t first,float aspect) {
    if(first>=32)return 0;
    uint32_t mask=count>=32-first?(0xffffffffu<<first):(((1u<<count)-1u)<<first);
    uint32_t release=geometry_valid&mask;
    geometry_valid&=~mask;geometry_pending&=~mask;
    for(unsigned i=first;release;i++)if(release&(1u<<i)) {
        struct GeometryContext *context=&geometry_contexts[geometry_vertices[i].context];
        release&=~context->owners;context->owners&=~mask;
    }
    if(gCurrLevelNum<=LEVEL_UNKNOWN_3 || count>32-first || count<8
       || !(rsp.geometry_mode&G_ZBUFFER) || (rsp.geometry_mode&G_TEXTURE_GEN)
       || (rsp.MP_matrix[0][3]==0 && rsp.MP_matrix[1][3]==0 && rsp.MP_matrix[2][3]==0))return 0;
    float temporary[6];
    const float *box=geometry_software_bounds(vertices,count,temporary);
    if(!box)return 0;
    if(!((box[0]!=box[1] && rsp.MP_matrix[0][3]!=0)
       || (box[2]!=box[3] && rsp.MP_matrix[1][3]!=0)
       || (box[4]!=box[5] && rsp.MP_matrix[2][3]!=0)))return 0;
    unsigned outside=geometry_software_outside(box,aspect);
    if(outside && geometry_confirm_outside(vertices,count,aspect,outside)) {
        vertex_cull_capture(vertices,count,first,aspect,outside);return 2;
    }
    return 0;
}

'''
def transform(name,s):
 if name=='gfx_geometry.h':
  s=once(s,'static __attribute__((noinline)) int geometry_prepare(',HELPER+SOFTWARE+'static __attribute__((noinline)) int geometry_prepare(')
  s=once(s,'    if (!gfx_gpu_geometry_enabled() || first >= 32)\n','    if (!gfx_gpu_geometry_enabled())return geometry_prepare_software(vertices,count,first,aspect);\n    if (first >= 32)\n')
 elif name=='gfx_pc.c':
  s=once(s,'''    if((vtx1_idx|vtx2_idx|vtx3_idx)<32 && culled_pending) {
        if(v1->clip_rej&v2->clip_rej&v3->clip_rej)return;
        vertex_cull_materialize(vtx1_idx);vertex_cull_materialize(vtx2_idx);vertex_cull_materialize(vtx3_idx);
    }
''','''    if(culled_pending) {
        if(v1->clip_rej&v2->clip_rej&v3->clip_rej)return;
        /* A triangle may mix deferred low slots with ordinary slots 32+. */
        if(vtx1_idx<32 && (culled_pending&(1u<<vtx1_idx)))vertex_cull_materialize(vtx1_idx);
        if(vtx2_idx<32 && (culled_pending&(1u<<vtx2_idx)))vertex_cull_materialize(vtx2_idx);
        if(vtx3_idx<32 && (culled_pending&(1u<<vtx3_idx)))vertex_cull_materialize(vtx3_idx);
    }
''')
 return s

def main():
 records={};patch=[]
 for name,h in EXPECTED.items():
  original=(BASE/name).read_text();assert sha(original)==h
  candidate=transform(name,original);target=GFX/name
  v1=(OUT/'candidate-v1/SM64/src/sm64/sm64/src/pc/gfx'/name)
  # Published source may be the exact prior guarded candidate or a replay.
  from prepare import transform as old_transform
  assert target.read_text() in (original,old_transform(name,original),candidate),name
  target.write_text(candidate)
  records[name]={'before':h,'after':sha(candidate),'changed':original!=candidate}
  patch.extend(difflib.unified_diff(original.splitlines(True),candidate.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'batch-cull-v2.patch').write_text(''.join(patch))
 (OUT/'candidate-source-v2.json').write_text(json.dumps({'files':records,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'helper_recipe_sha256':hashlib.sha256((Path(__file__).parent/'prepare.py').read_bytes()).hexdigest(),'status':'source-frozen; qualification pending','scope':['GEO0 early dedicated noinline software path','1024-entry direct-mapped immutable bounds-only cache; mutable scans; original integral/range eligibility','six software frustum planes; epoch/aspect invalidation','unchanged exact CPU-plane confirmation','no materialize call when referenced low slot is not pending'],'software_bss_bytes_rv32':1024*32+6*28+8,'unchanged':'GEO1 hardware preparation body/normalized cache/planes/context/quantization and all captured state/materialization'},indent=2)+'\n')
 print(json.dumps(records,indent=2))
if __name__=='__main__':main()
