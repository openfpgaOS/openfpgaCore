/* Harness tail: check_lazy.py prepends actual original renderer types/math. */
static uint32_t rng=0x849f7351u;
static uint32_t random32(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static float random_float(float scale) {return ((int32_t)(random32()%65536)-32768)*scale/32768.0f;}
static unsigned tests,vertices_checked;
static void fail(const char *why,unsigned n,unsigned slot) {
    fprintf(stderr,"FAIL %s case %u count %u slot %u\n",why,tests,n,slot);exit(1);
}
static void context(void) {
    memset(&rsp,0,sizeof rsp);
    for(unsigned i=0;i<4;i++)for(unsigned k=0;k<4;k++)rsp.MP_matrix[i][k]=random_float(2.0f);
    /* Keep random fog-to-integer conversions in the source's defined range. */
    rsp.MP_matrix[0][3]=rsp.MP_matrix[1][3]=rsp.MP_matrix[2][3]=0;
    rsp.MP_matrix[3][3]=(random32()%32+1)*0.25f;
    rsp.current_num_lights=random32()%3+1;
    for(unsigned i=0;i<MAX_LIGHTS+1;i++)for(unsigned k=0;k<3;k++)rsp.current_lights[i].col[k]=random32();
    for(unsigned i=0;i<MAX_LIGHTS;i++)for(unsigned k=0;k<3;k++)rsp.current_lights_coeffs[i][k]=random_float(1.0f);
    for(unsigned i=0;i<2;i++)for(unsigned k=0;k<3;k++)rsp.current_lookat_coeffs[i][k]=random_float(.3f);
    rsp.geometry_mode=(random32()&1?G_LIGHTING:0)|(random32()&1?G_FOG:0);
    if((rsp.geometry_mode&G_LIGHTING)&&(random32()&1))rsp.geometry_mode|=G_TEXTURE_GEN;
    rsp.texture_scaling_factor.s=random32();rsp.texture_scaling_factor.t=random32();
    rsp.fog_mul=(int)(random32()%2000)-1000;rsp.fog_offset=random32()%256;
    configEnableFog=random32()&1;lighting_epoch++;
}
static void check_load(Vtx *v,unsigned count,unsigned first,float aspect) {
    struct LoadedVertex expected[68],sentinel[68];
    for(unsigned i=0;i<sizeof sentinel;i++)((uint8_t *)sentinel)[i]=(uint8_t)random32();
    memcpy(rsp.loaded_vertices,sentinel,sizeof sentinel);
    memset(lighting_memo,0,sizeof lighting_memo);
    original_vertices(count,first,v,aspect);
    memcpy(expected,rsp.loaded_vertices,sizeof expected);
    memcpy(rsp.loaded_vertices,sentinel,sizeof sentinel);
    gfx_mesh_lazy_save(count,first,v,aspect);
    struct RSP saved=rsp;
    context(); /* matrix/light/UV/fog/geometry state changes must not flush */
    memcpy(rsp.loaded_vertices,sentinel,sizeof sentinel);
    struct RSP current=rsp;
    /* Poison memo entries with a newer epoch; delayed lights use saved epoch. */
    memset(lighting_memo,0x5a,sizeof lighting_memo);
    uint32_t original_pending=mesh_lazy_pending;
    uint32_t first_mask=random32();
    gfx_mesh_lazy_materialize(first_mask);
    for(unsigned i=0;i<68;i++) {
        const void *want=(i<32 && (original_pending&first_mask&(1u<<i)))?&expected[i]:&sentinel[i];
        if(memcmp(&rsp.loaded_vertices[i],want,sizeof expected[i]))fail("partial",count,i);
    }
    if(memcmp(&rsp,&current,offsetof(struct RSP,loaded_vertices)))fail("current state",count,0);
    /* New G_VTX discards overwritten old slots and resolves preserved slots. */
    uint32_t overwrite=random32(),discard=mesh_lazy_pending&overwrite;
    gfx_mesh_lazy_before_load(overwrite);
    if(mesh_lazy_pending)fail("pending",count,0);
    for(unsigned i=0;i<68;i++) {
        const void *want=(i<32 && (original_pending&~discard&(1u<<i)))?&expected[i]:&sentinel[i];
        if(memcmp(&rsp.loaded_vertices[i],want,sizeof expected[i]))fail("preserved",count,i);
    }
    if(memcmp(&rsp,&current,offsetof(struct RSP,loaded_vertices)))fail("reload state",count,0);
    /* Also verify the helper used by the ordinary eager path. */
    rsp=saved;memcpy(rsp.loaded_vertices,sentinel,sizeof sentinel);
    memset(lighting_memo,0,sizeof lighting_memo);
    gfx_mesh_cpu_vertices(count,first,v,aspect,&rsp,mesh_lazy_fog,mesh_lazy_epoch);
    if(memcmp(rsp.loaded_vertices,expected,sizeof expected))fail("eager helper",count,0);
    vertices_checked+=count;tests++;
}
int main(int argc,char **argv) {
    for(unsigned n=0;n<30000;n++) {
        context();Vtx v[32];memset(v,0,sizeof v);
        unsigned count=random32()%32+1,first=random32()%(33-count);
        for(unsigned i=0;i<count;i++) {
            for(unsigned k=0;k<3;k++)v[i].v.ob[k]=random_float(300.0f);
            for(unsigned k=0;k<2;k++)v[i].v.tc[k]=random32();
            for(unsigned k=0;k<4;k++)v[i].v.cn[k]=random32();
            if(i && !(i&3))memcpy(v[i].v.cn,v[i-1].v.cn,4); /* memo hit */
        }
        check_load(v,count,first,.5f+fabsf(random_float(1.0f)));
    }
    FILE *f=fopen(argv[1],"rb");if(!f)abort();
    uint32_t count,state[64],raw[128];unsigned scenes=0;
    while(fread(&count,4,1,f)==1) {
        if(count<1||count>32||fread(state,4,64,f)!=64||fread(raw,16,count,f)!=count)abort();
        context();Vtx v[32];memset(v,0,sizeof v);
        for(unsigned row=0;row<4;row++)for(unsigned k=0;k<4;k++)memcpy(&rsp.MP_matrix[k][row],&state[40+row*4+k],4);
        float aspect;memcpy(&aspect,&state[56],4);
        rsp.current_num_lights=2;
        for(unsigned k=0;k<3;k++) {
            rsp.current_lights_coeffs[0][k]=(int32_t)state[16+k]*(127.0f/1073741824.0f);
            rsp.current_lights[0].col[k]=state[19]>>(8*k);rsp.current_lights[1].col[k]=state[20]>>(8*k);
        }
        rsp.geometry_mode=state[22];rsp.texture_scaling_factor.s=state[21];rsp.texture_scaling_factor.t=state[21]>>16;
        rsp.fog_mul=state[23];rsp.fog_offset=state[23]>>16;
        configEnableFog=scenes&1;
        for(unsigned i=0;i<count;i++) {
            v[i].v.ob[0]=(int16_t)raw[4*i];v[i].v.ob[1]=(int16_t)(raw[4*i]>>16);v[i].v.ob[2]=(int16_t)raw[4*i+1];
            v[i].v.tc[0]=raw[4*i+2];v[i].v.tc[1]=raw[4*i+2]>>16;
            memcpy(v[i].v.cn,&raw[4*i+3],4);
        }
        check_load(v,count,0,aspect);scenes++;
    }
    fclose(f);
    printf("PASS lazy exact CPU: %u loads (%u scene), %u vertices, saved RSP %zu bytes; state/memo/partial-reload ownership exact\n",tests,scenes,vertices_checked,sizeof mesh_lazy_rsp);
    return 0;
}
