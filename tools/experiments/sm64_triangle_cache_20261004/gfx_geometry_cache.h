/* Cache only immutable display-list runs and immutable model-space faces.
 * Visibility, material, alpha and matrix state are evaluated on every draw.
 * Mutable lists/vertices and mixed vertex reloads retain the ordinary path. */
#ifndef GFX_GEOMETRY_RUN_CACHE
#define GFX_GEOMETRY_RUN_CACHE 1
#endif

#if GFX_GEOMETRY_RUN_CACHE && !defined(OF_PC)
struct GeometryCachedTriangle { uint32_t indices; float normal[3]; };
struct GeometryCachedRun {
    const Gfx *first;
    Gfx *next;
    uintptr_t source_base;
    uint32_t used;
    unsigned offset,count;
};
static struct GeometryCachedRun geometry_runs[1024];
static struct GeometryCachedTriangle geometry_run_triangles[8192];
static unsigned geometry_run_used;

static struct GeometryCachedRun *geometry_cached_run(Gfx *first) {
    uintptr_t address=(uintptr_t)first,end=(uintptr_t)__EH_FRAME_BEGIN__;
    if(address<(uintptr_t)&_start || address>=end || sizeof(Gfx)>end-address)return NULL;
    unsigned hash=((uint32_t)(address>>3)*2654435761u)>>22;
    unsigned index=hash,tries=0;
    struct GeometryCachedRun *run;
    for(;tries<1024;tries++,index=(index+1)&1023u) {
        run=&geometry_runs[index];
        if(run->first==first)return run;
        if(!run->first)break;
    }
    /* Linear probing retains colliding lists instead of repeatedly allocating
     * duplicates. Recycle only at a full table/pool and never during a draw. */
    if(tries==1024 || geometry_run_used+64>8192) {
        memset(geometry_runs,0,sizeof(geometry_runs));geometry_run_used=0;
        run=&geometry_runs[hash];
    }
    run->first=first;run->source_base=0;run->offset=geometry_run_used;
    run->count=run->used=0;
    Gfx *cmd=first;
    while((uintptr_t)cmd<end && sizeof(Gfx)<=end-(uintptr_t)cmd) {
        unsigned slots[6],n=gfx_geometry_decode(cmd,slots);
        if(!n || run->count+n>64)break;
        unsigned i;
        for(i=0;i<3*n;i++)if(slots[i]>=32)break;
        if(i<3*n)break;
        for(i=0;i<n;i++) {
            const unsigned *v=&slots[3*i];
            struct GeometryCachedTriangle *t=&geometry_run_triangles[geometry_run_used++];
            t->indices=v[0]|(v[1]<<5)|(v[2]<<10);
            run->used|=(1u<<v[0])|(1u<<v[1])|(1u<<v[2]);
            run->count++;
        }
        cmd++;
    }
    run->next=cmd;
    return run;
}

static __attribute__((noinline)) Gfx *gfx_geometry_cached_run(Gfx *first) {
    if(!geometry_batch_mask || !geometry_batch_source)return first;
    struct GeometryCachedRun *run=geometry_cached_run(first);
    if(!run || !run->count || (geometry_valid&run->used)!=run->used)return first;
    struct GeometryCachedTriangle *faces=&geometry_run_triangles[run->offset];
    if((run->used&geometry_batch_mask)!=run->used)return first;
    uintptr_t base=geometry_batch_source;
    unsigned context=geometry_batch_context;
    uint32_t remaining;
    /* A successful G_VTX owns this whole mask and one matrix context. Every
     * subsequent load invalidates the shortcut before testing its new data. */
    if(run->source_base!=base) {
        for(unsigned i=0;i<run->count;i++) {
            struct GeometryCachedTriangle *face=&faces[i];
            unsigned packed=face->indices;
            const struct GeometryVertex *v0=&geometry_vertices[packed&31u];
            const struct GeometryVertex *v1=&geometry_vertices[(packed>>5)&31u];
            const struct GeometryVertex *v2=&geometry_vertices[(packed>>10)&31u];
            float u[3],v[3];
            for(unsigned k=0;k<3;k++) {
                u[k]=(int)v0->pos[k]-(int)v1->pos[k];
                v[k]=(int)v2->pos[k]-(int)v1->pos[k];
            }
            face->normal[0]=u[1]*v[2]-u[2]*v[1];
            face->normal[1]=u[2]*v[0]-u[0]*v[2];
            face->normal[2]=u[0]*v[1]-u[1]*v[0];
        }
        run->source_base=base;
    }
    bool use_fog,use_alpha,use_texture;
    struct ColorCombiner *comb=gfx_prepare_triangle_material(&use_fog,&use_alpha,&use_texture);
    int rgb_in=-1,alpha_in=-1;
    int elig=gfx_gpu_vtx_cache_begin(&rgb_in,&alpha_in);
    if(!elig || (rgb_in>=0 && comb->shader_input_mapping[0][rgb_in]==CC_LOD)
       || (alpha_in>=0 && comb->shader_input_mapping[1][alpha_in]==CC_LOD))return first;
    if(!use_fog && alpha_in>=0 && comb->shader_input_mapping[1][alpha_in]==CC_SHADE) {
        remaining=run->used;
        for(unsigned i=0;remaining;i++,remaining>>=1)
            if((remaining&1u) && geometry_vertices[i].fog)return first;
    }
    const struct GeometryContext *g=&geometry_contexts[context];
    unsigned cull=rsp.geometry_mode&G_CULL_BOTH;
    unsigned cull_bits=((cull&G_CULL_FRONT)?1u:0u)|((cull&G_CULL_BACK)?2u:0u);
    uint32_t triangles[64],used=0;
    unsigned count=0;
    uint8_t alpha=255;
    for(unsigned i=0;i<run->count;i++) {
        const struct GeometryCachedTriangle *face=&faces[i];
        unsigned packed=face->indices;
        if(cull==G_CULL_BOTH)continue;
        if(cull) {
            const struct GeometryVertex *v=&geometry_vertices[(packed>>5)&31u];
            float x=face->normal[0]*(g->cull_det*v->pos[0]+g->cull_origin[0]);
            float y=face->normal[1]*(g->cull_det*v->pos[1]+g->cull_origin[1]);
            float z=face->normal[2]*(g->cull_det*v->pos[2]+g->cull_origin[2]);
            float cross=x+y+z;
            float margin=(fabsf(x)+fabsf(y)+fabsf(z))*0.0001f+1.0f;
            if(cull==G_CULL_BACK ? cross>margin : cross<-margin)continue;
        }
        uint8_t a=255;
        if(alpha_in>=0) {
            struct RGBA tmp;
            const struct LoadedVertex *v=&rsp.loaded_vertices[packed&31u];
            a=(use_fog && comb->shader_input_mapping[1][alpha_in]==CC_SHADE)
                ?255:gfx_vc_resolve(comb->shader_input_mapping[1][alpha_in],v,v,&tmp)->a;
        }
        /* A nonuniform run uses the original whole-opcode alpha splitter. */
        if(count && a!=alpha)return first;
        alpha=a;triangles[count++]=packed|(cull_bits<<15);
        used|=(1u<<(packed&31u))|(1u<<((packed>>5)&31u))|(1u<<((packed>>10)&31u));
    }
#ifdef TARGET_POCKET
    tri_diag.total+=run->count;
#endif
    if(count)gfx_geometry_submit(comb,elig,rgb_in,used,triangles,count,alpha);
    return run->next;
}
#else
static __attribute__((noinline)) Gfx *gfx_geometry_cached_run(Gfx *first) { return first; }
#endif
