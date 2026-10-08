/* Private lazy CPU fallback. Included before gfx_sp_vertex. */
static struct RSP mesh_lazy_rsp;
static const Vtx *mesh_lazy_source;
static unsigned mesh_lazy_first;
static float mesh_lazy_aspect;
static bool mesh_lazy_fog;
static uint32_t mesh_lazy_epoch,mesh_lazy_pending;
static bool mesh_lazy_fixed;
static int32_t mesh_lazy_matrix[12];
/* Separate from geometry_valid: these slots have recovered original raw-path
 * depth, but do not own a legacy hardware geometry-cache context. */
static uint32_t mesh_lazy_depth_valid;
/* loads deferred, vertices deferred, materialized, discarded, preserved on
 * reload, eager loads, eager vertices, materialization batches. */
unsigned mesh_lazy_stats[8];

static void gfx_mesh_fixed_vertices(unsigned first,unsigned count) {
    const struct RSP *saved=&mesh_lazy_rsp;
    for(unsigned slot=first;slot<first+count;slot++) {
        const Vtx *source=mesh_lazy_source+slot-mesh_lazy_first;
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
            for(unsigned k=0;k<3;k++)sum+=(int64_t)mesh_lazy_matrix[row*4+k]*pos[k];
            q[row]=(int32_t)((sum>>16)+mesh_lazy_matrix[row*4+3]);
        }
        d->x=q[0]*(1.0f/256.0f);d->y=q[1]*(1.0f/256.0f);d->w=q[2]*(1.0f/256.0f);
        d->z=pos[0]*saved->MP_matrix[0][2]+pos[1]*saved->MP_matrix[1][2]
            +pos[2]*saved->MP_matrix[2][2]+saved->MP_matrix[3][2];
        d->w_inv=RSPV_RCP(d->w);
        geometry_depth[slot]=(uint32_t)((UINT64_C(1)<<38)/(uint32_t)q[2]);
        if(mesh_lazy_fog&&(saved->geometry_mode&G_FOG)) {
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
    /* Contiguous spans keep the original loop intact and pay its matrix loads
     * once per span. Never change current RSP state or the current memo epoch. */
    while(needed) {
        unsigned first=__builtin_ctz(needed),count=1;
        while(first+count<32 && (needed&(1u<<(first+count))))count++;
        uint32_t span=count==32?0xffffffffu:((1u<<count)-1u)<<first;
        if(mesh_lazy_fixed)gfx_mesh_fixed_vertices(first,count);
        else gfx_mesh_cpu_vertices(count,first,mesh_lazy_source+first-mesh_lazy_first,
            mesh_lazy_aspect,&mesh_lazy_rsp,mesh_lazy_fog,mesh_lazy_epoch);
        needed&=~span;mesh_lazy_pending&=~span;
        mesh_lazy_stats[2]+=count;mesh_lazy_stats[7]++;
        SM64_PERF_COUNT(SM64_PERF_CPU,count);
    }
}

static void gfx_mesh_lazy_materialize_slot(unsigned slot) {
    if(slot<32)gfx_mesh_lazy_materialize(1u<<slot);
}

static void gfx_mesh_lazy_before_load(uint32_t overwritten) {
    mesh_lazy_depth_valid&=~overwritten;
    if(!mesh_lazy_pending)return;
    mesh_lazy_stats[3]+=__builtin_popcount(mesh_lazy_pending&overwritten);
    mesh_lazy_pending&=~overwritten;
    mesh_lazy_stats[4]+=__builtin_popcount(mesh_lazy_pending);
    gfx_mesh_lazy_materialize(0xffffffffu);
}

static void gfx_mesh_lazy_save(size_t count,size_t first,const Vtx *vertices,float aspect) {
    /* This RSP object is storage only: snapshot precisely the fields read by
     * the original math loop. No modelview stack or LoadedVertex array copy. */
    memcpy(mesh_lazy_rsp.MP_matrix,rsp.MP_matrix,sizeof(rsp.MP_matrix));
    memcpy(mesh_lazy_rsp.current_lights,rsp.current_lights,sizeof(rsp.current_lights));
    memcpy(mesh_lazy_rsp.current_lights_coeffs,rsp.current_lights_coeffs,
           sizeof(rsp.current_lights_coeffs));
    memcpy(mesh_lazy_rsp.current_lookat_coeffs,rsp.current_lookat_coeffs,
           sizeof(rsp.current_lookat_coeffs));
    mesh_lazy_rsp.current_num_lights=rsp.current_num_lights;
    mesh_lazy_rsp.geometry_mode=rsp.geometry_mode;
    mesh_lazy_rsp.fog_mul=rsp.fog_mul;mesh_lazy_rsp.fog_offset=rsp.fog_offset;
    mesh_lazy_rsp.texture_scaling_factor=rsp.texture_scaling_factor;
    mesh_lazy_aspect=aspect;mesh_lazy_fog=configEnableFog;mesh_lazy_epoch=lighting_epoch;
    mesh_lazy_first=first;mesh_lazy_source=vertices;
    mesh_lazy_fixed=false;
    mesh_lazy_pending=count==32?0xffffffffu:((1u<<count)-1u)<<first;
    mesh_lazy_stats[0]++;mesh_lazy_stats[1]+=count;
}

static int gfx_mesh_lazy_capture(size_t,size_t,const Vtx *,float);
