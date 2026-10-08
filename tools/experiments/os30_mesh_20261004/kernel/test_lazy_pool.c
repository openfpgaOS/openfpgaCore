/* Independent eager original helpers above are the numeric oracle. */
#include <assert.h>
static uint32_t rng=0x58ad8193;
static uint32_t next(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static float rf(float scale){return ((int32_t)(next()%65536)-32768)*scale/32768.0f;}
static struct LoadedVertex expected[68];
static uint32_t expected_depth[32],fixed_slots,valid;
static unsigned loads,referenced,discarded,peak_contexts;
static void require(int ok,const char *label,unsigned slot) {
    if(!ok){fprintf(stderr,"FAIL %s load%u slot%u\n",label,loads,slot);abort();}
}
static void check(void) {
    uint32_t live=0,pending=0;
    for(unsigned c=0;c<32;c++)if(mesh_lazy_context[c].pending) {
        require(!(pending&mesh_lazy_context[c].pending),"overlapping contexts",c);
        pending|=mesh_lazy_context[c].pending;live|=1u<<c;
        for(unsigned i=0;i<32;i++)if(mesh_lazy_context[c].pending&(1u<<i))
            require(mesh_lazy_owner[i]==c,"owner",i);
    }
    require(live==mesh_lazy_live&&pending==mesh_lazy_pending,"live masks",0);
    unsigned contexts=__builtin_popcount(live);if(contexts>peak_contexts)peak_contexts=contexts;
    require(mesh_lazy_depth_valid==(valid&fixed_slots),"depth ownership",0);
    for(unsigned i=0;i<32;i++)if(valid&(1u<<i)) {
        require(!memcmp(expected+i,rsp.loaded_vertices+i,sizeof expected[i]),"loaded vertex",i);
        if(fixed_slots&(1u<<i))require(geometry_depth[i]==expected_depth[i],"exact depth",i);
    }
}
static void materialize(uint32_t mask) {
    uint32_t needed=mesh_lazy_pending&mask;
    struct RSP before=rsp;
    gfx_mesh_lazy_materialize(mask);valid|=needed;
    referenced+=__builtin_popcount(needed);
    require(!memcmp(&rsp,&before,offsetof(struct RSP,loaded_vertices)),"current state",0);
    check();
}
static void random_state(void) {
    for(unsigned i=0;i<4;i++)for(unsigned k=0;k<4;k++)rsp.MP_matrix[i][k]=rf(2.0f);
    rsp.MP_matrix[0][3]=rsp.MP_matrix[1][3]=rsp.MP_matrix[2][3]=0;
    rsp.MP_matrix[3][3]=(next()%128+1)*.25f;
    rsp.current_num_lights=next()%3+1;
    for(unsigned i=0;i<MAX_LIGHTS+1;i++)for(unsigned k=0;k<3;k++)rsp.current_lights[i].col[k]=next();
    for(unsigned i=0;i<MAX_LIGHTS;i++)for(unsigned k=0;k<3;k++)rsp.current_lights_coeffs[i][k]=rf(1.0f);
    for(unsigned i=0;i<2;i++)for(unsigned k=0;k<3;k++)rsp.current_lookat_coeffs[i][k]=rf(.3f);
    rsp.geometry_mode=(next()&1?G_LIGHTING:0)|(next()&1?G_FOG:0);
    if((rsp.geometry_mode&G_LIGHTING)&&(next()&1))rsp.geometry_mode|=G_TEXTURE_GEN;
    rsp.texture_scaling_factor.s=next();rsp.texture_scaling_factor.t=next();
    rsp.fog_mul=(int)(next()%2000)-1000;rsp.fog_offset=next()%256;
    configEnableFog=next()&1;lighting_epoch++;
}
static int32_t fixed_matrix[12];
static int eager_reference(Vtx *v,unsigned count,unsigned first,float aspect,unsigned fixed) {
    struct LoadedVertex actual[68];uint32_t actual_depth[32];
    memcpy(actual,rsp.loaded_vertices,sizeof actual);memcpy(actual_depth,geometry_depth,sizeof actual_depth);
    if(!fixed)original_vertices(count,first,v,aspect);
    else {
        struct GeometryContext *g=&geometry_contexts[0];const unsigned columns[3]={0,1,3};
        for(unsigned r=0;r<3;r++)for(unsigned k=0;k<4;k++) {
            float value=rsp.MP_matrix[k][columns[r]]*(r==0?aspect:1.0f);
            g->matrix[r*4+k]=lrintf(value*(k==3?256.0f:16777216.0f));
        }
        memcpy(fixed_matrix,g->matrix,sizeof fixed_matrix);
        for(unsigned k=0;k<4;k++){g->z[k]=rsp.MP_matrix[k][2];g->fog_w[k]=rsp.MP_matrix[k][3];}
        for(unsigned i=0;i<count;i++) {
            int64_t sum=0;for(unsigned k=0;k<3;k++)sum+=(int64_t)g->matrix[8+k]*(int16_t)v[i].v.ob[k];
            if((int32_t)((sum>>16)+g->matrix[11])<=0)return 0;
        }
        struct GeometryAttributes *a=&geometry_attributes[0];memset(a,0,sizeof *a);
        unsigned lights=rsp.current_num_lights-1;a->lighting=(rsp.geometry_mode&G_LIGHTING)!=0;
        for(unsigned k=0;k<3;k++) {
            a->coeff[k]=a->lighting&&lights?rsp.current_lights_coeffs[0][k]:0.0f;
            a->words[3]|=(uint32_t)rsp.current_lights[0].col[k]<<(8*k);
            a->words[4]|=(uint32_t)rsp.current_lights[lights].col[k]<<(8*k);
        }
        a->words[5]=rsp.texture_scaling_factor.s|((uint32_t)rsp.texture_scaling_factor.t<<16);
        geometry_pending=geometry_attribute_pending=count==32?0xffffffffu:((1u<<count)-1u)<<first;
        for(unsigned i=0;i<count;i++) {
            struct GeometryVertex *gv=&geometry_vertices[first+i];memset(gv,0,sizeof *gv);
            for(unsigned k=0;k<3;k++)gv->pos[k]=(int16_t)v[i].v.ob[k];
            memcpy(gv->tc,v[i].v.tc,sizeof gv->tc);memcpy(&gv->cn,v[i].v.cn,4);
            gv->fog=configEnableFog&&(rsp.geometry_mode&G_FOG);gv->fog_mul=rsp.fog_mul;gv->fog_offset=rsp.fog_offset;
            geometry_materialize(first+i);expected_depth[first+i]=geometry_depth[first+i];
        }
    }
    memcpy(expected+first,rsp.loaded_vertices+first,count*sizeof *expected);
    memcpy(rsp.loaded_vertices,actual,sizeof actual);memcpy(geometry_depth,actual_depth,sizeof actual_depth);
    return 1;
}
static void load(Vtx *v,unsigned count,unsigned first,float aspect,unsigned fixed,unsigned eager) {
    uint32_t mask=count==32?0xffffffffu:((1u<<count)-1u)<<first;
    if(!eager_reference(v,count,first,aspect,fixed))return;
    struct LoadedVertex before[68];memcpy(before,rsp.loaded_vertices,sizeof before);
    discarded+=__builtin_popcount(mesh_lazy_pending&mask);
    gfx_mesh_lazy_before_load(mask);
    require(!memcmp(before,rsp.loaded_vertices,sizeof before),"reload performed geometry",0);
    valid&=~mask;fixed_slots&=~mask;if(fixed)fixed_slots|=mask;
    if(eager) {
        /* Unsupported new loads use the unchanged CPU body; old lazy slots
         * must survive alongside them with their original state. */
        original_vertices(count,first,v,aspect);valid|=mask;
    } else {
        gfx_mesh_lazy_save(count,first,v,aspect);
        struct MeshLazyContext *c=&mesh_lazy_context[mesh_lazy_current];
        c->fixed=fixed;if(fixed)memcpy(c->matrix,fixed_matrix,sizeof fixed_matrix);
    }
    loads++;check();
}
int main(int argc,char **argv) {
    (void)argc;
    memset(rsp.loaded_vertices,0xa5,sizeof rsp.loaded_vertices);memcpy(expected,rsp.loaded_vertices,sizeof expected);valid=0xffffffffu;
    static Vtx sources[128][32];
    /* Reach the mathematical maximum32 live source contexts, then consume
     * alternating old/new owners in one request. */
    for(unsigned i=0;i<32;i++) {
        random_state();sources[i][0].v.ob[0]=i;
        load(sources[i],1,i,1.0f,0,0);
    }
    require(__builtin_popcount(mesh_lazy_live)==32,"pool saturation",0);
    materialize(0x55555555u);materialize(0xaaaaaaaau);
    for(unsigned sequence=0;sequence<500;sequence++) {
        for(unsigned step=0;step<128;step++) {
            random_state();unsigned fixed=next()&1;
            if(fixed){rsp.current_num_lights=next()%2+1;rsp.geometry_mode&=~G_TEXTURE_GEN;}
            unsigned count=next()%12+1,first=next()%(33-count);
            Vtx *v=sources[step];memset(v,0,32*sizeof *v);
            for(unsigned i=0;i<count;i++) {
                for(unsigned k=0;k<3;k++)v[i].v.ob[k]=fixed?(int16_t)rf(300):rf(300);
                for(unsigned k=0;k<2;k++)v[i].v.tc[k]=next();
                for(unsigned k=0;k<4;k++)v[i].v.cn[k]=next();
            }
            load(v,count,first,.5f+fabsf(rf(1)),fixed,!fixed&&next()%4==0);
            if(next()%3==0)materialize(next()&next());
        }
        materialize(0xffffffffu);require(mesh_lazy_live==0,"context release",0);
    }
    FILE *f=fopen(argv[1],"rb");assert(f);uint32_t count,state[64],raw[128];unsigned scenes=0;
    while(fread(&count,4,1,f)==1) {
        assert(count&&count<=32&&fread(state,4,64,f)==64&&fread(raw,16,count,f)==count);
        random_state();Vtx *v=sources[0];memset(v,0,32*sizeof *v);
        for(unsigned r=0;r<4;r++)for(unsigned k=0;k<4;k++)memcpy(&rsp.MP_matrix[k][r],&state[40+4*r+k],4);
        float aspect;memcpy(&aspect,state+56,4);rsp.current_num_lights=2;
        for(unsigned k=0;k<3;k++) {
            rsp.current_lights_coeffs[0][k]=(int32_t)state[16+k]*(127.0f/1073741824.0f);
            rsp.current_lights[0].col[k]=state[19]>>(8*k);rsp.current_lights[1].col[k]=state[20]>>(8*k);
        }
        rsp.geometry_mode=state[22];rsp.texture_scaling_factor.s=state[21];rsp.texture_scaling_factor.t=state[21]>>16;
        rsp.fog_mul=state[23];rsp.fog_offset=state[23]>>16;configEnableFog=scenes&1;
        for(unsigned i=0;i<count;i++) {
            v[i].v.ob[0]=(int16_t)raw[4*i];v[i].v.ob[1]=(int16_t)(raw[4*i]>>16);v[i].v.ob[2]=(int16_t)raw[4*i+1];
            v[i].v.tc[0]=raw[4*i+2];v[i].v.tc[1]=raw[4*i+2]>>16;memcpy(v[i].v.cn,raw+4*i+3,4);
        }
        load(v,count,0,aspect,0,0);materialize(0xffffffffu);
        load(v,count,0,aspect,1,0);materialize(0xffffffffu);scenes++;
    }
    fclose(f);
    printf("PASS retained contexts: %u loads (%u scenes twice), %u materialized vertices, %u discarded without work, %u maximum contexts, %zu-byte compact entry; every original float/fixed value and depth exact\n",loads,scenes,referenced,discarded,peak_contexts,sizeof(struct MeshLazyContext));
    return 0;
}
