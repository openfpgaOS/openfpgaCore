#!/usr/bin/env python3
"""Compile actual SM64 load/cull/recovery functions against ordinary CPU loading."""
from pathlib import Path
import importlib.util,subprocess,hashlib,json,os
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005'
GFX=OUT/'SM64/src/sm64/sm64/src/pc/gfx'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
spec=importlib.util.spec_from_file_location('old',ROOT/'tools/experiments/sm64_triangle_cache_20261004/check_visibility.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
pre=old.PRE.replace('#define OF_PC 1','').replace('#define SM64_PERF_SCOPE(x) ((void)0)','#define SM64_PERF_SCOPE(x) ((void)0)\n#define SM64_PERF_COUNT(x,n) ((void)0)')
pre=pre.replace('static int gfx_gpu_geometry_enabled(void) {return 1;}','static int hw_geometry;\nstatic int gfx_gpu_geometry_enabled(void) {return hw_geometry;}')
pre=pre.replace('static struct {\n    float MP_matrix','static struct {\n    bool matrix_changed,lights_changed,lookat_changed;\n    float modelview_matrix_stack[11][4][4],P_matrix[4][4],current_lookat_coeffs[2][3];\n    unsigned modelview_matrix_stack_size;\n    float MP_matrix')
pre+=r'''
#define TARGET_OPENFPGA 1
#define F3DEX_GBI_2 1
#define F3DEX_GBI 1
#define RSPV_TO_FLOAT(x) (x)
static uintptr_t test_ro_begin,test_ro_end;
static uint32_t lighting_epoch=1,vc_slot_stale;
struct LightingMemo {uint32_t epoch,normal;struct RGBA color;};
static struct LightingMemo lighting_memo[256];
static struct {float aspect_ratio;} gfx_current_dimensions={4.0f/3.0f};
static void gfx_matrix_mul(float d[4][4],float a[4][4],float b[4][4]) {
    float tmp[4][4]={0};
    for(unsigned i=0;i<4;i++)for(unsigned j=0;j<4;j++)
        for(unsigned k=0;k<4;k++)tmp[i][j]+=a[i][k]*b[k][j];
    memcpy(d,tmp,sizeof(tmp));
}
static void calculate_normal_dir(const Light_t *light,float *dst) {(void)light;(void)dst;}
static void gfx_materialize_vertex_attributes(unsigned slot);
'''
s=(GFX/'gfx_pc.c').read_text()
attrs='#ifdef TARGET_OPENFPGA\n'+s[s.index('static void gfx_load_geometry_attributes('):s.index('static __attribute__((noinline)) void gfx_sp_vertex(')]
vertex=s[s.index('static __attribute__((noinline)) void gfx_sp_vertex('):s.index('static inline struct ColorCombiner *gfx_pick_combiner(')]
reference=vertex.replace('void gfx_sp_vertex(', 'void reference_sp_vertex(').replace('geometry_prepare(vertices,n_vertices,dest_index,aspect_scale)','0')
tri_start=s.index('    if(culled_pending) {',s.index('static void gfx_sp_tri1('));tri_end=s.index('    if((vtx1_idx|vtx2_idx|vtx3_idx)<32)',tri_start)
tri='''static void recover_triangle(uint8_t vtx1_idx,uint8_t vtx2_idx,uint8_t vtx3_idx) {
 struct LoadedVertex *v1=&rsp.loaded_vertices[vtx1_idx],*v2=&rsp.loaded_vertices[vtx2_idx],*v3=&rsp.loaded_vertices[vtx3_idx];
'''+s[tri_start:tri_end]+'}\n'
production=''.join((GFX/n).read_text() for n in ['gfx_vertex_cull.h','gfx_geometry.h'])
production=production.replace('(uintptr_t)&_start','test_ro_begin').replace('(uintptr_t)__EH_FRAME_BEGIN__','test_ro_end')
TEST=r'''
static uint32_t seed=0xa17c5593;
static uint32_t random32(void) {seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
static float rf(float lo,float hi) {return lo+(hi-lo)*(random32()*(1.0f/4294967296.0f));}
static unsigned batches,culled,visible,vertices_checked,mixed,confirms;
static Vtx immutable[65536];
static void setup(unsigned mode) {
    memset(&rsp,0,sizeof(rsp));memset(lighting_memo,0,sizeof(lighting_memo));++lighting_epoch;
    memset(geometry_contexts,0,sizeof(geometry_contexts));geometry_valid=geometry_pending=0;
    geometry_cached_context=-1;geometry_batch_mask=0;geometry_context_next=0;
    memset(culled_contexts,0,sizeof(culled_contexts));culled_pending=0;culled_cached_context=-1;
    vertex_attributes_pending=0;geometry_planes_epoch=0;++geometry_mvp_epoch;++vertex_state_epoch;
    memset(geometry_bounds_cache,0,sizeof(geometry_bounds_cache));geometry_input_used=0;
    test_ro_begin=test_ro_end=0;gCurrLevelNum=9;hw_geometry=0;
    rsp.geometry_mode=G_ZBUFFER|mode;rsp.current_num_lights=2;
    rsp.MP_matrix[0][0]=1.5f;rsp.MP_matrix[1][1]=2;rsp.MP_matrix[2][2]=-1.01f;
    rsp.MP_matrix[2][3]=-1;rsp.MP_matrix[3][2]=950;rsp.MP_matrix[3][3]=1000;
    rsp.current_lights[0].col[0]=160;rsp.current_lights[0].col[1]=100;rsp.current_lights[0].col[2]=80;
    rsp.current_lights[1].col[0]=20;rsp.current_lights[1].col[1]=30;rsp.current_lights[1].col[2]=40;
    rsp.current_lights_coeffs[0][0]=0.5f;rsp.current_lights_coeffs[0][1]=0.25f;rsp.current_lights_coeffs[0][2]=-0.125f;
    rsp.texture_scaling_factor.s=32767;rsp.texture_scaling_factor.t=65535;
    rsp.fog_mul=100;rsp.fog_offset=30;configEnableFog=true;
    gfx_current_dimensions.aspect_ratio=4.0f/3.0f;
}
static void init_vertices(Vtx *v,unsigned n,int x,int y,int z) {
    for(unsigned i=0;i<n;i++) {
        memset(&v[i],0,sizeof(v[i]));v[i].v.ob[0]=x+(int)(i&1);v[i].v.ob[1]=y+(int)((i>>1)&1);v[i].v.ob[2]=z+(int)((i>>2)&1);
        v[i].v.tc[0]=37*(int)i-400;v[i].v.tc[1]=43*(int)i+70;
        for(unsigned k=0;k<4;k++)v[i].v.cn[k]=(i*23+k*31)&127;
    }
}
static void same_vertex(const struct LoadedVertex *a,const struct LoadedVertex *b) {
    const float *af=&a->x,*bf=&b->x;
    for(unsigned i=0;i<7;i++) {
        if(memcmp(&af[i],&bf[i],sizeof(float))!=0 && af[i]!=bf[i]) {
            fprintf(stderr,"float mismatch batch%u component%u %.9g %.9g\n",batches,i,af[i],bf[i]);assert(0);
        }
    }
    if(memcmp(&a->color,&b->color,sizeof(a->color)) || a->clip_rej!=b->clip_rej) {
        fprintf(stderr,"attribute mismatch batch%u clip%x/%x rgba%u,%u,%u,%u / %u,%u,%u,%u\n",batches,a->clip_rej,b->clip_rej,a->color.r,a->color.g,a->color.b,a->color.a,b->color.r,b->color.g,b->color.b,b->color.a);assert(0);
    }
    vertices_checked++;
}
static void load_compare(Vtx *v,unsigned n,unsigned first,bool mutate) {
    struct LoadedVertex expected[64];
    reference_sp_vertex(n,first,v);memcpy(expected,rsp.loaded_vertices,sizeof(expected));
    memset(rsp.loaded_vertices,0xa5,sizeof(rsp.loaded_vertices));memset(lighting_memo,0,sizeof(lighting_memo));++lighting_epoch;
    gfx_sp_vertex(n,first,v);batches++;
    assert(geometry_valid==0&&geometry_pending==0&&geometry_batch_mask==0);
    if(culled_pending) {
        culled++;
        for(unsigned i=first;i<first+n;i++) assert((expected[i].clip_rej&rsp.loaded_vertices[i].clip_rej)==rsp.loaded_vertices[i].clip_rej);
        if(mutate) {
            memset(v,0xff,n*sizeof(*v));memset(rsp.MP_matrix,0,sizeof(rsp.MP_matrix));
            memset(rsp.current_lights,0,sizeof(rsp.current_lights));memset(rsp.current_lights_coeffs,0,sizeof(rsp.current_lights_coeffs));
            rsp.geometry_mode=0;rsp.texture_scaling_factor.s=rsp.texture_scaling_factor.t=0;
            rsp.fog_mul=0;rsp.fog_offset=255;configEnableFog=false;++vertex_state_epoch;++geometry_mvp_epoch;
        }
        for(unsigned i=first;i<first+n;i++)vertex_cull_materialize(i);
    } else visible++;
    for(unsigned i=first;i<first+n;i++)same_vertex(&expected[i],&rsp.loaded_vertices[i]);
    assert(!culled_pending);for(unsigned i=0;i<32;i++)assert(!culled_contexts[i].owners);
}
static void boundary_tests(void) {
    static const unsigned clips[]={CLIP_RIGHT,CLIP_LEFT,CLIP_TOP,CLIP_BOTTOM,CLIP_NEAR,CLIP_FAR};
    Vtx v[32];
    for(unsigned p=0;p<6;p++)for(int delta=-3;delta<=3;delta++)for(int ws=-1;ws<=1;ws++) {
        setup(0);memset(rsp.MP_matrix,0,sizeof(rsp.MP_matrix));
        unsigned axis=p/2;float sign=(p&1)?-1:1;
        rsp.MP_matrix[axis][axis]=1;rsp.MP_matrix[3][3]=ws*100;
        init_vertices(v,8,0,0,0);
        for(unsigned i=0;i<8;i++)v[i].v.ob[axis]=sign*(ws*100+delta);
        reference_sp_vertex(8,0,v);
        bool all=true;for(unsigned i=0;i<8;i++)all &= (rsp.loaded_vertices[i].clip_rej&clips[p])!=0;
        assert(geometry_confirm_outside(v,8,1,clips[p])==all);confirms++;
    }
    /* Existing coefficient-combined AABB rejects, while original CPU x==w. */
    setup(0);memset(rsp.MP_matrix,0,sizeof(rsp.MP_matrix));
    rsp.MP_matrix[0][0]=4096.0009765625f;rsp.MP_matrix[0][3]=4096;rsp.MP_matrix[3][3]=28;
    init_vertices(v,8,32766,0,0);float box[6];const float *b=geometry_bounds(v,8,box);geometry_update_planes(1);
    assert(geometry_outside(b)==CLIP_RIGHT);assert(!geometry_confirm_outside(v,8,1,CLIP_RIGHT));
    load_compare(v,8,0,false);assert(!culled_pending);
    /* Reject confirmation fails when even the last vertex is on its plane. */
    setup(0);memset(rsp.MP_matrix,0,sizeof(rsp.MP_matrix));rsp.MP_matrix[0][0]=1;rsp.MP_matrix[3][3]=100;
    init_vertices(v,8,101,0,0);v[7].v.ob[0]=100;assert(!geometry_confirm_outside(v,8,1,CLIP_RIGHT));
    /* Bit-level finite check remains effective under finite-math-only. */
    uint32_t infinity=0x7f800000u;memcpy(&rsp.MP_matrix[3][0],&infinity,4);assert(!geometry_confirm_outside(v,8,1,CLIP_RIGHT));
    uint32_t nan=0x7fc01234u;memcpy(&rsp.MP_matrix[3][0],&nan,4);assert(!geometry_confirm_outside(v,8,1,CLIP_RIGHT));
    printf("PASS selected-plane boundaries=%u, cancellation fallback, last-vertex and finite-bit guards\n",confirms);
}
static void random_tests(void) {
    Vtx v[32];
    for(unsigned t=0;t<12000;t++) {
        setup((t&1?G_LIGHTING:0)|(t&2?G_FOG:0));
        init_vertices(v,8+8*(t%4),(t&16)?(int)(random32()%28000)-14000:0,(t&16)?(int)(random32()%24000)-12000:0,(t&16)?(int)(random32()%6000)-3000:0);
        for(unsigned r=0;r<4;r++)for(unsigned c=0;c<4;c++)rsp.MP_matrix[r][c]+=rf(-0.0625f,0.0625f);
        gfx_current_dimensions.aspect_ratio=(t&4)?1.2f:4.0f/3.0f;
        load_compare(v,8+8*(t%4),0,(t&8)!=0);
    }
    /* Fractional, over-range, tiny batches, texture generation, slots32+. */
    for(unsigned k=0;k<6;k++) {
        setup(k==3?G_TEXTURE_GEN:0);init_vertices(v,8,5000,0,0);
        if(k==0)v[0].v.ob[0]+=0.5f;if(k==1)v[1].v.ob[1]=40000;
        unsigned n=k==2?7:8,first=k==4?32:0;if(k==5)gCurrLevelNum=1;
        unsigned before=culled;load_compare(v,n,first,false);assert(culled==before);
    }
}
static void ownership_tests(void) {
    Vtx a[32],b[32];struct LoadedVertex old[8],fresh[8];
    setup(G_LIGHTING|G_FOG);init_vertices(a,8,5000,0,0);reference_sp_vertex(8,0,a);memcpy(old,rsp.loaded_vertices,sizeof(old));
    gfx_sp_vertex(8,0,a);assert(culled_pending==0xff);
    memset(a,0xff,sizeof(a));++vertex_state_epoch;++geometry_mvp_epoch;
    rsp.MP_matrix[3][0]=99;rsp.current_lights[1].col[0]=99;rsp.fog_offset=99;rsp.texture_scaling_factor.s=400;++lighting_epoch;
    init_vertices(b,8,-5000,20,0);reference_sp_vertex(8,32,b);memcpy(fresh,rsp.loaded_vertices+32,sizeof(fresh));
    gfx_sp_vertex(8,4,b);assert(culled_pending==0xfff);
    for(unsigned i=0;i<4;i++){vertex_cull_materialize(i);same_vertex(&old[i],&rsp.loaded_vertices[i]);}
    for(unsigned i=0;i<8;i++){vertex_cull_materialize(i+4);same_vertex(&fresh[i],&rsp.loaded_vertices[i+4]);}
    assert(!culled_pending);
    /* Mixed low/high triangle, and all-clipped triangle retaining deferral. */
    setup(0);init_vertices(a,8,5000,0,0);reference_sp_vertex(8,0,a);memcpy(old,rsp.loaded_vertices,sizeof(old));
    gfx_sp_vertex(8,0,a);assert(culled_pending==0xff);
    rsp.loaded_vertices[32].clip_rej=0;rsp.loaded_vertices[33].clip_rej=0;
    recover_triangle(0,32,33);assert(culled_pending==0xfe);same_vertex(&old[0],&rsp.loaded_vertices[0]);mixed++;
    recover_triangle(1,2,3);assert(culled_pending==0xfe);mixed++;
    rsp.loaded_vertices[32].clip_rej=0;recover_triangle(32,1,33);assert(culled_pending==0xfc);same_vertex(&old[1],&rsp.loaded_vertices[1]);mixed++;
    recover_triangle(32,33,2);assert(culled_pending==0xf8);same_vertex(&old[2],&rsp.loaded_vertices[2]);mixed++;
    vertex_cull_release(0,32);for(unsigned i=0;i<32;i++)assert(!culled_contexts[i].owners);
    /* 32 overwritten slots always free enough snapshot contexts, including partial loads. */
    for(unsigned t=0;t<512;t++) {
        setup(0);for(unsigned j=0;j<16;j++) {
            init_vertices(a,8,5000+j,0,0);++vertex_state_epoch;++geometry_mvp_epoch;
            gfx_sp_vertex(8,(j*3)%25,a);
            for(unsigned i=0;i<32;i++)if(culled_pending&(1u<<i))assert(culled_context[i]<32);
        }
        vertex_cull_release(0,32);assert(!culled_pending);
        for(unsigned i=0;i<32;i++)assert(!culled_contexts[i].owners);
    }
    printf("PASS mutable/load-state recovery, partial overwrite, mixed-index triangles=%u, context ownership\n",mixed);
}
static void software_cache_tests(void) {
    setup(0);init_vertices(immutable,4096,0,0,0);
    memset(geometry_software_bounds_cache,0,sizeof(geometry_software_bounds_cache));
    geometry_software_planes_epoch=0;
    test_ro_begin=(uintptr_t)immutable;test_ro_end=(uintptr_t)(immutable+4096);
    float temporary[6];unsigned checks=0;
    for(unsigned count=8;count<=32;count+=8)for(unsigned first=0;first+count<=64;first++) {
        const float *box=geometry_software_bounds(immutable+first,count,temporary);
        assert(box!=temporary);float saved[6];memcpy(saved,box,sizeof(saved));
        assert(geometry_software_bounds(immutable+first,count,temporary)==box);
        for(unsigned k=0;k<3;k++)for(unsigned i=0;i<count;i++) {
            float p=immutable[first+i].v.ob[k];assert(p>=box[2*k]&&p<=box[2*k+1]);
        }
        assert(!memcmp(saved,box,sizeof(saved)));checks++;
    }
    unsigned collisions=0;int seen[1024];for(unsigned i=0;i<1024;i++)seen[i]=-1;
    for(unsigned i=0;i<2048;i++) {
        uintptr_t address=(uintptr_t)(immutable+i);
        unsigned hash=(((uint32_t)(address>>3)^(8*40503u))*2654435761u)>>22;
        if(seen[hash]>=0) {
            unsigned prior=seen[hash];
            struct GeometrySoftwareBounds *found;
            assert(geometry_software_bounds(immutable+prior,8,temporary,&found)&&found->source==immutable+prior);
            assert(geometry_software_bounds(immutable+i,8,temporary,&found)&&found->source==immutable+i);
            assert(geometry_software_bounds(immutable+prior,8,temporary,&found)&&found->source==immutable+prior);collisions++;
        }
        seen[hash]=i;
    }
    assert(collisions>0);
    /* A cached start address cannot authorize a range beyond the immutable end. */
    const float *box=geometry_software_bounds(immutable,8,temporary);assert(box!=temporary);
    test_ro_end=(uintptr_t)(immutable+7);immutable[0].v.ob[0]=-55;
    box=geometry_software_bounds(immutable,8,temporary);assert(box==temporary&&box[0]==-55);checks++;
    test_ro_begin=test_ro_end=0;immutable[0].v.ob[0]=0.5f;assert(!geometry_software_bounds(immutable,8,temporary));
    immutable[0].v.ob[0]=40000;assert(!geometry_software_bounds(immutable,8,temporary));
    init_vertices(immutable,64,0,0,0);
    /* Same equations as hardware frustum cache, without hardware guard planes. */
    unsigned rounded_differences=0;
    for(unsigned t=0;t<1000;t++) {
        ++geometry_mvp_epoch;float aspect=(t&1)?1.2f:1;
        for(unsigned r=0;r<4;r++)for(unsigned c=0;c<4;c++)rsp.MP_matrix[r][c]=rf(-10,10);
        geometry_update_planes(aspect);geometry_software_update_planes(aspect);
        /* Separate software/helper code may contract w +/- aspect*x into
         * FMA; the hardware helper reuses a rounded product for its guard
         * planes. These heuristic coefficients need not be byte-identical.
         * Record and prove the actual first difference, then test the real
         * software cache refresh contract against its own computation. */
        for(unsigned p=0;p<6;p++)for(unsigned k=0;k<4;k++) {
            float old=geometry_frustum_planes[p].c[k],now=geometry_software_planes[p].c[k];
            if(old!=now) {
                if(!rounded_differences) {
                    assert(p<2);
                    volatile float product=aspect*rsp.MP_matrix[k][0];
                    float split=rsp.MP_matrix[k][3]+((p&1)?product:-product);
                    float fused=fmaf((p&1)?aspect:-aspect,rsp.MP_matrix[k][0],rsp.MP_matrix[k][3]);
                    assert(old==split&&now==fused);
                    uint32_t ob,nb;memcpy(&ob,&old,4);memcpy(&nb,&now,4);
                    printf("CONFIRMED plane rounding: split=%08x fused=%08x plane=%u coefficient=%u\n",ob,nb,p,k);
                }
                rounded_differences++;
            }
        }
        float bounds[6]={-20,40,-30,50,-10,70};
        struct GeometryPlane fresh[6];memcpy(fresh,geometry_software_planes,sizeof(fresh));
        unsigned result=geometry_software_outside(bounds,aspect);
        assert(!memcmp(fresh,geometry_software_planes,sizeof(fresh)));
        geometry_software_planes_epoch=geometry_mvp_epoch-1;
        assert(result==geometry_software_outside(bounds,aspect));
        assert(!memcmp(fresh,geometry_software_planes,sizeof(fresh)));
        geometry_software_planes_aspect=aspect+0.25f;
        assert(result==geometry_software_outside(bounds,aspect));
        assert(!memcmp(fresh,geometry_software_planes,sizeof(fresh)));checks++;
    }
    /* Eligible visible work never produces raw state or touches the hardware input pool. */
    setup(0);init_vertices(immutable,64,0,0,0);test_ro_begin=(uintptr_t)immutable;test_ro_end=(uintptr_t)(immutable+64);
    unsigned pool=geometry_input_used;
    for(unsigned i=0;i<1000;i++){gfx_sp_vertex(8,0,immutable);assert(!culled_pending&&!geometry_valid&&!geometry_pending&&geometry_input_used==pool);}
    printf("PASS software-only cache/plane checks=%u collisions=%u rounded-coefficient-differences=%u, 1000 eligible visible loads, no normalized pool writes\n",checks,collisions,rounded_differences);
}
static void retry_tests(void) {
    setup(0);memset(geometry_software_bounds_cache,0,sizeof(geometry_software_bounds_cache));
    init_vertices(immutable,4096,0,0,0);test_ro_begin=(uintptr_t)immutable;test_ro_end=(uintptr_t)(immutable+4096);
    gfx_sp_vertex(8,0,immutable);assert(!culled_pending);
    uintptr_t a=(uintptr_t)immutable;
    unsigned hash=(((uint32_t)(a>>3)^(8*40503u))*2654435761u)>>22;
    struct GeometrySoftwareBounds *entry=&geometry_software_bounds_cache[hash];
    assert(entry->source==immutable&&entry->count==8&&entry->retry==7);
    struct LoadedVertex expected[8];
    /* Camera motion during backoff may lose an optimization, never geometry. */
    for(unsigned retry=7;retry;retry--) {
        rsp.MP_matrix[3][0]=5000+retry;gfx_current_dimensions.aspect_ratio=retry&1?1.2f:4.0f/3.0f;
        ++geometry_mvp_epoch;++vertex_state_epoch;
        reference_sp_vertex(8,0,immutable);memcpy(expected,rsp.loaded_vertices,sizeof(expected));
        gfx_sp_vertex(8,0,immutable);assert(!culled_pending&&entry->retry==retry-1);
        for(unsigned i=0;i<8;i++)same_vertex(&expected[i],&rsp.loaded_vertices[i]);
    }
    gfx_sp_vertex(8,0,immutable);assert(culled_pending==0xff&&entry->retry==0);vertex_cull_release(0,32);
    /* Successful rejects must be tested again after motion back into view. */
    rsp.MP_matrix[3][0]=0;++geometry_mvp_epoch;++vertex_state_epoch;
    gfx_sp_vertex(8,0,immutable);assert(!culled_pending&&entry->retry==7);
    /* Matching address, different count must not inherit the eight-slot delay. */
    rsp.MP_matrix[3][0]=5000;++geometry_mvp_epoch;++vertex_state_epoch;
    gfx_sp_vertex(16,0,immutable);assert(culled_pending==0xffff);vertex_cull_release(0,32);
    /* A same-hash different source replaces the key and resets its cooldown. */
    entry->retry=0;
    rsp.MP_matrix[3][0]=0;++geometry_mvp_epoch;++vertex_state_epoch;
    gfx_sp_vertex(8,0,immutable);assert(entry->retry==7);
    unsigned other=0;
    for(unsigned i=1;i<4088;i++) {
        uintptr_t b=(uintptr_t)(immutable+i);
        unsigned h=(((uint32_t)(b>>3)^(8*40503u))*2654435761u)>>22;
        if(h==hash){other=i;break;}
    }
    assert(other);rsp.MP_matrix[3][0]=5000;++geometry_mvp_epoch;++vertex_state_epoch;
    gfx_sp_vertex(8,0,immutable+other);assert(culled_pending==0xff&&entry->source==immutable&&entry->retry==7);vertex_cull_release(0,32);
    /* Mutable source never uses immutable backoff, even if the address has a stale key. */
    test_ro_begin=test_ro_end=0;rsp.MP_matrix[3][0]=0;++geometry_mvp_epoch;++vertex_state_epoch;
    gfx_sp_vertex(8,0,immutable+other);assert(!culled_pending);
    rsp.MP_matrix[3][0]=5000;++geometry_mvp_epoch;++vertex_state_epoch;
    gfx_sp_vertex(8,0,immutable+other);assert(culled_pending==0xff);vertex_cull_release(0,32);
    printf("PASS seven-use immutable retry, camera/aspect motion, fresh reject tests, count isolation/collision retry retention, mutable bypass\n");
}
static void hardware_cleanup_tests(void) {
    Vtx v[64];init_vertices(v,64,0,0,0);unsigned checks=0;
    for(unsigned k=0;k<256;k++)for(unsigned first=0;first<=32;first+=4) {
        setup(0);rsp.geometry_mode=0;
        uint32_t before_valid=k==0?0:random32(),before_pending=k==0?0:random32();
        geometry_valid=before_valid;geometry_pending=before_pending;
        unsigned n=8u<<(k%4);
        for(unsigned i=0;i<32;i++)geometry_vertices[i].context=i/4;
        for(unsigned i=0;i<8;i++)geometry_contexts[i].owners=before_valid&(15u<<(i*4));
        uint32_t mask=first>=32?0:n>=32-first?0xffffffffu<<first:((1u<<n)-1u)<<first;
        assert(geometry_prepare(v,n,first,1)==0);
        assert(geometry_valid==(before_valid&~mask)&&geometry_pending==(before_pending&~mask)&&geometry_batch_mask==0);
        for(unsigned i=0;i<8;i++)assert(geometry_contexts[i].owners==(before_valid&(15u<<(i*4))&~mask));
        checks++;
    }
    printf("PASS hardware-ownership zero/nonzero/partial cleanup checks=%u\n",checks);
}
static void probe_budget_tests(void) {
    setup(0);memset(geometry_software_bounds_cache,0,sizeof(geometry_software_bounds_cache));
    init_vertices(immutable,65536,0,0,0);test_ro_begin=(uintptr_t)immutable;test_ro_end=(uintptr_t)(immutable+65536);
    unsigned colliders[18],found=0;
    for(unsigned i=0;i<65528&&found<18;i++) {
        uintptr_t a=(uintptr_t)(immutable+i);
        unsigned hash=(((uint32_t)(a>>3)^(8*40503u))*2654435761u)>>22;
        if(hash==1023)colliders[found++]=i;
    }
    assert(found==18);float tmp[6];struct GeometrySoftwareBounds *entry;
    for(unsigned i=0;i<16;i++) {
        assert(geometry_software_bounds(immutable+colliders[i],8,tmp,&entry));
        assert(entry==&geometry_software_bounds_cache[(1023+i)&1023]);
        assert(entry->source==immutable+colliders[i]&&entry->count==8&&entry->retry==0);
        entry->retry=2+i%6;
    }
    struct GeometrySoftwareBounds saved[16];
    for(unsigned i=0;i<16;i++)saved[i]=geometry_software_bounds_cache[(1023+i)&1023];
    assert(geometry_software_bounds(immutable+colliders[16],8,tmp,&entry));
    assert(entry==&geometry_software_bounds_cache[1023]&&entry->source==immutable+colliders[16]&&entry->retry==0);
    for(unsigned i=1;i<16;i++)assert(!memcmp(&saved[i],&geometry_software_bounds_cache[(1023+i)&1023],sizeof(saved[i])));
    /* Invalid new immutable data must not modify the selected replacement. */
    struct GeometrySoftwareBounds home=*entry;
    immutable[colliders[17]].v.ob[0]=0.5f;
    assert(!geometry_software_bounds(immutable+colliders[17],8,tmp,&entry));
    assert(entry==&geometry_software_bounds_cache[1023]&&!memcmp(&home,entry,sizeof(home)));
    immutable[colliders[17]].v.ob[0]=0;
    assert(geometry_software_bounds(immutable+colliders[17],8,tmp,&entry)&&entry->retry==0);
    /* Exact-hit retry remains attached to the collided key, and decrements. */
    unsigned index=(1023+7)&1023;uint16_t retry=geometry_software_bounds_cache[index].retry;
    assert(retry>0&&!geometry_software_bounds(immutable+colliders[7],8,tmp,&entry));
    assert(entry==&geometry_software_bounds_cache[index]&&entry->retry==retry-1);
    printf("PASS 18 same-hash keys,16-probe budget,1023 wrap,home replacement,invalid preservation and exact-hit retry\n");
}
int main(void) {
    boundary_tests();random_tests();ownership_tests();software_cache_tests();retry_tests();hardware_cleanup_tests();probe_budget_tests();
    assert(culled>1000&&visible>1000);
    printf("PASS actual CPU comparison: batches=%u culled=%u ordinary=%u vertices=%u GEO0 raw=0\n",batches,culled,visible,vertices_checked);
}
'''

TEST=TEST.replace(',temporary)',',temporary,NULL)')

def main():
 out=OUT/'function-checks';out.mkdir(exist_ok=True)
 source=pre+production+attrs+vertex+reference+tri+TEST
 c=out/'functions.c';c.write_text(source)
 flags=[('checked',['-O2','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer']),('fast',['-O3','-ffast-math','-fno-math-errno','-fno-trapping-math','-fsingle-precision-constant','-fno-strict-aliasing','-fwrapv'])]
 results=[]
 for label,opts in flags:
  exe=out/label;cmd=['cc','-std=gnu11',*opts,str(c),'-lm','-o',str(exe)]
  subprocess.run(cmd,check=True)
  r=subprocess.run([str(exe)],env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'),capture_output=True,text=True)
  (out/(label+'.log')).write_text(r.stdout+r.stderr)
  print(label,r.stdout,r.stderr,flush=True);r.check_returncode()
  results.append({'label':label,'command':cmd,'log_sha256':sha(out/(label+'.log')),'binary_sha256':sha(exe)})
 (out/'manifest.json').write_text(json.dumps({'passed':True,'source_files':{n:sha(GFX/n) for n in ['gfx_geometry.h','gfx_vertex_cull.h','gfx_pc.c']},'harness_sha256':sha(c),'script_sha256':sha(Path(__file__)),'results':results,'substitutions':['ELF immutable source range uses controlled host address bounds','hardware geometry getter is controllable','reference exact gfx_sp_vertex changes only geometry_prepare call to0','lighting direction update stub unused; tests install coefficients and no pending matrix updates','triangle wrapper contains exact candidate recovery block only'],'limitations':['host arithmetic, not RISC-V performance','finite position/matrix test domain; explicit nonfinite confirmation guards tested','actual emitted application captures/GPU replay qualify RISC-V separately']},indent=2)+'\n')
if __name__=='__main__':main()
