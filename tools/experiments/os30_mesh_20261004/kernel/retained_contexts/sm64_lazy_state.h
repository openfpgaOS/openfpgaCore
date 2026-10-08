/* Private lazy CPU fallback. Included before gfx_sp_vertex. */
static struct RSP mesh_lazy_rsp;
/* One live context owns at least one of32 vertex slots, so32 entries suffice
 * for every partial-load pattern. Retain only the source shader fields; never
 * copy the full modelview stack or LoadedVertex array into an entry. */
#define MESH_LAZY_ARRAY_FIELDS(X) \
    X(MP_matrix) X(current_lights) X(current_lights_coeffs) \
    X(current_lookat_coeffs)
#define MESH_LAZY_SCALAR_FIELDS(X) \
    X(current_num_lights) X(geometry_mode) \
    X(fog_mul) X(fog_offset) X(texture_scaling_factor)
#define MESH_LAZY_FIELDS(X) MESH_LAZY_ARRAY_FIELDS(X) MESH_LAZY_SCALAR_FIELDS(X)
struct MeshLazyContext {
#define MESH_LAZY_DECLARE(name) __typeof__(((struct RSP *)0)->name) name;
    MESH_LAZY_FIELDS(MESH_LAZY_DECLARE)
#undef MESH_LAZY_DECLARE
    const Vtx *source;
    float aspect;
    uint32_t first,epoch,pending;
    bool fog,fixed;
    int32_t matrix[12];
};
static struct MeshLazyContext mesh_lazy_context[32];
static uint8_t mesh_lazy_owner[32];
static uint32_t mesh_lazy_live,mesh_lazy_pending;
static unsigned mesh_lazy_current;
#ifndef MESH_LAZY_FAST_DEPTH
#define MESH_LAZY_FAST_DEPTH 0
#endif
#ifndef MESH_LAZY_NOINLINE
#define MESH_LAZY_NOINLINE 0
#endif
#if MESH_LAZY_NOINLINE
#define MESH_LAZY_FIXED_ATTR __attribute__((noinline))
#else
#define MESH_LAZY_FIXED_ATTR
#endif
/* Separate from geometry_valid: these slots have recovered original raw-path
 * depth, but do not own a legacy hardware geometry-cache context. */
static uint32_t mesh_lazy_depth_valid;
/* loads deferred, vertices deferred, materialized, discarded, preserved on
 * reload, eager loads, eager vertices, materialization batches. */
unsigned mesh_lazy_stats[8];

/* The accepted LOAD has an exactly representable Q24.8 denominator. Reuse
 * its float reciprocal as an estimate, then correct with integer arithmetic
 * to recover the original floor(2^38/w) exactly. Other inputs retain the
 * general reference division. No extra floating-point division is needed. */
static inline uint32_t gfx_mesh_exact_depth(uint32_t den,float reciprocal) {
    if(den<1024u||den>=8388608u)
        return (uint32_t)((UINT64_C(1)<<38)/den);
    uint32_t q=(uint32_t)(reciprocal*1073741824.0f);
    uint64_t product=(uint64_t)q*den;
    while(product>(UINT64_C(1)<<38)) {q--;product-=den;}
    while(product+den<=(UINT64_C(1)<<38)) {q++;product+=den;}
    return q;
}

static MESH_LAZY_FIXED_ATTR void gfx_mesh_fixed_vertices(const struct MeshLazyContext *saved,
        unsigned first,unsigned count) {
    for(unsigned slot=first;slot<first+count;slot++) {
        const Vtx *source=saved->source+slot-saved->first;
        const int16_t pos[3]={(int16_t)source->v.ob[0],(int16_t)source->v.ob[1],(int16_t)source->v.ob[2]};
        struct LoadedVertex *d=&rsp.loaded_vertices[slot];
        /* Original geometry_attributes_materialize arithmetic and order. */
        d->u=(int16_t)((int32_t)source->v.tc[0]*saved->texture_scaling_factor.s>>16);
        d->v=(int16_t)((int32_t)source->v.tc[1]*saved->texture_scaling_factor.t>>16);
        d->color.a=source->v.cn[3];
        uint32_t color;memcpy(&color,source->v.cn,4);
        if(saved->geometry_mode&G_LIGHTING) {
            float intensity=saved->current_num_lights>1?((int8_t)source->v.cn[0]*saved->current_lights_coeffs[0][0]
                +(int8_t)source->v.cn[1]*saved->current_lights_coeffs[0][1]
                +(int8_t)source->v.cn[2]*saved->current_lights_coeffs[0][2])/127.0f:0.0f;
            if(intensity<0.0f)intensity=0.0f;
            color=0;
            for(unsigned k=0;k<3;k++) {
                int channel=saved->current_lights[saved->current_num_lights-1].col[k];
                channel+=intensity*saved->current_lights[0].col[k];
                if(channel>255)channel=255;
                color|=(uint32_t)channel<<(8*k);
            }
        }
        d->color.r=color;d->color.g=color>>8;d->color.b=color>>16;
        /* Original geometry_materialize fixed projection and float fog. */
        int32_t q[3];
        for(unsigned row=0;row<3;row++) {
            int64_t sum=0;
            for(unsigned k=0;k<3;k++)sum+=(int64_t)saved->matrix[row*4+k]*pos[k];
            q[row]=(int32_t)((sum>>16)+saved->matrix[row*4+3]);
        }
        d->x=q[0]*(1.0f/256.0f);d->y=q[1]*(1.0f/256.0f);d->w=q[2]*(1.0f/256.0f);
        d->z=pos[0]*saved->MP_matrix[0][2]+pos[1]*saved->MP_matrix[1][2]
            +pos[2]*saved->MP_matrix[2][2]+saved->MP_matrix[3][2];
        d->w_inv=RSPV_RCP(d->w);
#if MESH_LAZY_FAST_DEPTH
        geometry_depth[slot]=gfx_mesh_exact_depth((uint32_t)q[2],d->w_inv);
#else
        geometry_depth[slot]=(uint32_t)((UINT64_C(1)<<38)/(uint32_t)q[2]);
#endif
        if(saved->fog&&(saved->geometry_mode&G_FOG)) {
            float w=pos[0]*saved->MP_matrix[0][3]+pos[1]*saved->MP_matrix[1][3]
                +pos[2]*saved->MP_matrix[2][3]+saved->MP_matrix[3][3];
            float wi=w==0.0f?1.0f/0.001f:1.0f/w;
            if(wi<0.0f)wi=32767.0f;
            int fog_i=(int)(d->z*wi*saved->fog_mul+saved->fog_offset);
            d->color.a=fog_i<0?0:fog_i>255?255:fog_i;
        }
        d->clip_rej=0;
        mesh_lazy_depth_valid|=1u<<slot;
    }
}

static void gfx_mesh_lazy_materialize(uint32_t mask) {
    uint32_t needed=mesh_lazy_pending&mask;
    if(!needed)return;
    while(needed) {
        unsigned owner=mesh_lazy_owner[__builtin_ctz(needed)];
        struct MeshLazyContext *context=&mesh_lazy_context[owner];
        uint32_t group=needed&context->pending;
        if(!context->fixed) {
#define MESH_LAZY_RESTORE(name) memcpy(&mesh_lazy_rsp.name,&context->name,sizeof(context->name));
            MESH_LAZY_ARRAY_FIELDS(MESH_LAZY_RESTORE)
#undef MESH_LAZY_RESTORE
#define MESH_LAZY_RESTORE(name) mesh_lazy_rsp.name=context->name;
            MESH_LAZY_SCALAR_FIELDS(MESH_LAZY_RESTORE)
#undef MESH_LAZY_RESTORE
        }
        /* Contiguous spans retain the original loop's expression order and
         * reuse one saved context. Different owners are never combined. */
        while(group) {
            unsigned first=__builtin_ctz(group),count=1;
            while(first+count<32&&(group&(1u<<(first+count))))count++;
            uint32_t span=count==32?0xffffffffu:((1u<<count)-1u)<<first;
            if(context->fixed)gfx_mesh_fixed_vertices(context,first,count);
            else gfx_mesh_cpu_vertices(count,first,context->source+first-context->first,
                context->aspect,&mesh_lazy_rsp,context->fog,context->epoch);
            group&=~span;needed&=~span;context->pending&=~span;mesh_lazy_pending&=~span;
            mesh_lazy_stats[2]+=count;mesh_lazy_stats[7]++;
            SM64_PERF_COUNT(SM64_PERF_CPU,count);
        }
        if(!context->pending)mesh_lazy_live&=~(1u<<owner);
    }
}

static void gfx_mesh_lazy_materialize_slot(unsigned slot) {
    if(slot<32)gfx_mesh_lazy_materialize(1u<<slot);
}

static void gfx_mesh_lazy_before_load(uint32_t overwritten) {
    mesh_lazy_depth_valid&=~overwritten;
    uint32_t discard=mesh_lazy_pending&overwritten;
    mesh_lazy_stats[3]+=__builtin_popcount(discard);
    mesh_lazy_pending&=~overwritten;
    while(discard) {
        unsigned owner=mesh_lazy_owner[__builtin_ctz(discard)];
        struct MeshLazyContext *context=&mesh_lazy_context[owner];
        discard&=~context->pending;
        context->pending&=~overwritten;
        if(!context->pending)mesh_lazy_live&=~(1u<<owner);
    }
    /* Preserved vertices retain their immutable source/context until a real
     * CPU consumer requests them. State changes do not force computation. */
    mesh_lazy_stats[4]+=__builtin_popcount(mesh_lazy_pending);
}

static void gfx_mesh_lazy_save(size_t count,size_t first,const Vtx *vertices,float aspect) {
    /* Before-load removed the overwritten slots. A nonempty new load owns at
     * least one slot, hence at most31 old contexts can still be live here. */
    unsigned owner=__builtin_ctz(~mesh_lazy_live);
    struct MeshLazyContext *context=&mesh_lazy_context[owner];
#define MESH_LAZY_SAVE(name) memcpy(&context->name,&rsp.name,sizeof(context->name));
    MESH_LAZY_ARRAY_FIELDS(MESH_LAZY_SAVE)
#undef MESH_LAZY_SAVE
#define MESH_LAZY_SAVE(name) context->name=rsp.name;
    MESH_LAZY_SCALAR_FIELDS(MESH_LAZY_SAVE)
#undef MESH_LAZY_SAVE
    context->source=vertices;context->first=first;context->aspect=aspect;
    context->fog=configEnableFog;context->epoch=lighting_epoch;context->fixed=false;
    context->pending=count==32?0xffffffffu:((1u<<count)-1u)<<first;
    for(unsigned i=first;i<first+count;i++)mesh_lazy_owner[i]=owner;
    mesh_lazy_live|=1u<<owner;mesh_lazy_pending|=context->pending;mesh_lazy_current=owner;
    mesh_lazy_stats[0]++;mesh_lazy_stats[1]+=count;
}

static int gfx_mesh_lazy_capture(size_t,size_t,const Vtx *,float);
