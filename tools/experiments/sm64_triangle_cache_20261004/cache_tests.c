/* Included after the actual old/new renderer functions and capture backend. */
static uint32_t rng=0x79459173u;
static unsigned random32(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static void setup_mesh(unsigned mesh) {
    reset();
    geometry_batch_source=0x10400000u+mesh*4096u;geometry_batch_mask=0xffffffffu;geometry_batch_context=0;
    for(unsigned i=0;i<32;i++) {
        geometry_vertices[i].context=0;
        for(unsigned k=0;k<3;k++)
            geometry_vertices[i].pos[k]=(int16_t)(i*i*127u+i*(k+1)*73u+mesh*113u*(i+3*k));
    }
    geometry_contexts[0].serial=7;
}
static void reset_capture(void) {
    batches=flushes=buf_vbo_len=0;
    saved_count=saved_tris=0;vc_slot_stale=0xffffffffu;
}
static void compare_run(Gfx *first) {
    reset_capture();
    Gfx *expected=reference_geometry_run(first);
    unsigned n=saved_count,nt=saved_tris,nb=batches,alpha=saved_alpha,stale=vc_slot_stale;
    uint32_t indices[64];struct gfx_geometry_upload upload[32];
    memcpy(indices,saved_index,nt*sizeof(*indices));
    memcpy(upload,saved_upload,n*sizeof(*upload));
    reset_capture();
    assert(gfx_geometry_run(first)==expected);
    assert(batches==nb && saved_count==n && saved_tris==nt && vc_slot_stale==stale);
    if(nb)assert(saved_alpha==alpha);
    assert(memcmp(saved_index,indices,nt*sizeof(*indices))==0);
    for(unsigned i=0;i<n;i++) {
        assert(saved_upload[i].matrix==upload[i].matrix && saved_upload[i].serial==upload[i].serial);
        assert(memcmp(saved_upload[i].words,upload[i].words,sizeof(upload[i].words))==0);
    }
}
int main(void) {
    legacy_tests();
    /* Host-only immutable address fixture; production uses the linked ELF's
     * const asset range. Nothing else in the cache implementation is changed. */
    for(unsigned j=0;j<1100;j++)for(unsigned i=0;i<64;i++)
        cache_fixture[j][i].words.w0=(G_TRI1<<24)|tri(i%30,(i%30)+1,(i%30)+2);
    unsigned recycles=0,previous=0;
    for(unsigned j=0;j<300;j++) {
        struct GeometryCachedRun *r=geometry_cached_run(cache_fixture[j]);
        assert(r && r->count==64 && r->next==cache_fixture[j]+64);
        assert(r->offset+r->count<=8192 && geometry_run_used<=8192);
        assert(geometry_cached_run(cache_fixture[j])==r);
        recycles+=geometry_run_used<previous;previous=geometry_run_used;
    }
    assert(recycles>=2);
    memset(geometry_runs,0,sizeof(geometry_runs));geometry_run_used=0;
    for(unsigned j=0;j<1100;j++)cache_fixture[j][1].words.w0=0;
    for(unsigned j=0;j<1100;j++) {
        struct GeometryCachedRun *r=geometry_cached_run(cache_fixture[j]);
        assert(r && r->count==1 && r->next==cache_fixture[j]+1);
    }
    assert(geometry_run_used<1024); /* table exhaustion resets the pool too */
    memset(geometry_runs,0,sizeof(geometry_runs));geometry_run_used=0;
    for(unsigned i=0;i<64;i++)cache_fixture[0][i].words.w0=(G_TRI1<<24)|tri(i%30,(i%30)+1,(i%30)+2);
    for(unsigned trial=0;trial<16000;trial++) {
        /* Positions stay immutable per mesh; matrices and draw state vary. */
        setup_mesh(trial/1000);
        rsp.geometry_mode=(trial&1)?G_CULL_FRONT:G_CULL_BACK;
        geometry_contexts[0].cull_det=(int32_t)random32()*0.000001f;
        for(unsigned k=0;k<3;k++)geometry_contexts[0].cull_origin[k]=(int32_t)random32()*0.25f;
        eligibility=1+(trial&2);rgb_input=(trial&4)?0:-1;
        alpha_input=(trial&8)?0:-1;material.shader_input_mapping[1][0]=CC_SHADE;
        compare_run(cache_fixture[0]);
    }
    setup_mesh(1);
    geometry_valid&=~2u;assert(gfx_geometry_cached_run(cache_fixture[0])==cache_fixture[0]);
    setup_mesh(1);geometry_batch_source=0;
    assert(gfx_geometry_cached_run(cache_fixture[0])==cache_fixture[0]);
    setup_mesh(1);geometry_batch_mask&=~2u;
    assert(gfx_geometry_cached_run(cache_fixture[0])==cache_fixture[0]);
    setup_mesh(1);geometry_batch_mask=0;
    assert(gfx_geometry_cached_run(cache_fixture[0])==cache_fixture[0]);
    setup_mesh(1);eligibility=0;
    assert(gfx_geometry_cached_run(cache_fixture[0])==cache_fixture[0]);
    setup_mesh(1);alpha_input=0;material.shader_input_mapping[1][0]=CC_SHADE;
    rsp.loaded_vertices[3].color.a=1;
    assert(gfx_geometry_cached_run(cache_fixture[0])==cache_fixture[0]);
    setup_mesh(1);alpha_input=0;material.shader_input_mapping[1][0]=CC_SHADE;
    geometry_vertices[1].fog=1;
    assert(gfx_geometry_cached_run(cache_fixture[0])==cache_fixture[0]);
    Gfx mutable_commands[2]={0};mutable_commands[0].words.w0=(G_TRI1<<24)|tri(0,1,2);
    assert(geometry_cached_run(mutable_commands)==NULL);
    setup_mesh(1);compare_run(mutable_commands);
    mutable_commands[0].words.w0=(G_TRI1<<24)|tri(4,5,6);compare_run(mutable_commands);
    /* Exercise the cached TRI2 decoder, not only the mutable fallback above.
     * Reset the test cache before changing our synthetic immutable fixture. */
    memset(geometry_runs,0,sizeof(geometry_runs));geometry_run_used=0;
    memset(cache_fixture,0,sizeof(cache_fixture));
    for(unsigned i=0;i<32;i++) {
        cache_fixture[0][i].words.w0=(G_TRI2<<24)|tri(i%30,(i%30)+1,(i%30)+2);
        cache_fixture[0][i].words.w1=tri((i+7)%30,(i+7)%30+1,(i+7)%30+2);
    }
    setup_mesh(3);compare_run(cache_fixture[0]);
    assert(saved_tris==64 && geometry_cached_run(cache_fixture[0])->next==cache_fixture[0]+32);
    for(unsigned trial=0;trial<256;trial++) {
        setup_mesh(3);
        rsp.geometry_mode=trial&3;
        geometry_contexts[0].cull_det=(int32_t)random32()*0.000001f;
        for(unsigned k=0;k<3;k++)geometry_contexts[0].cull_origin[k]=(int32_t)random32()*0.25f;
        compare_run(cache_fixture[0]);
    }
    memset(geometry_runs,0,sizeof(geometry_runs));geometry_run_used=0;
    cache_fixture[0][3].words.w0=0; /* Stop at a material/state command. */
    setup_mesh(3);compare_run(cache_fixture[0]);assert(saved_tris==6);
    memset(geometry_runs,0,sizeof(geometry_runs));geometry_run_used=0;
    Gfx *last=&cache_fixture[1099][64];last->words.w0=(G_TRI1<<24)|tri(0,1,2);
    struct GeometryCachedRun *edge=geometry_cached_run(last);
    assert(edge->count==1 && edge->next==last+1); /* Whole command at ELF end. */
    puts("PASS: 16000 old/new culling/submission comparisons, source/matrix changes, pool/table exhaustion, mutable commands, mixed reloads, alpha and fog fallbacks");
    puts("PASS: cached TRI2 pairs, both culling modes, whole-command capacity, state boundaries and immutable range end");
    return 0;
}
