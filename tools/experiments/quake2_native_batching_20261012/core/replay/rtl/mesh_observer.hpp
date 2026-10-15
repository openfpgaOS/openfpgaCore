// Passive validation of native geometry reads from the shared physical CRAM1.
static bool mesh_inflight=false;
static uint32_t mesh_address=0;
static unsigned mesh_expected_beats=0,mesh_beat=0;
static uint64_t mesh_accepted=0,mesh_fills=0,mesh_beats=0,mesh_wait=0,mesh_service=0;
static uint64_t mesh_raster_overlap=0,mesh_texture_overlap=0,mesh_padding_beats=0;
static uint64_t worker_empty=0,worker_output_stall=0,worker_active=0,worker_raster_overlap=0;
static uint64_t worker_states[128]={},gpu_states[64]={},gpu_fbss[16]={};
static void mesh_observe_tick() {
    if(!tb->reset_n) {mesh_inflight=false;return;}
    if(tb->obs_mesh_rvalid) {
        if(!mesh_inflight || mesh_beat>=mesh_expected_beats)
            throw std::runtime_error("Unexpected native mesh response");
        const uint32_t expected=resident_words[(mesh_address>>2)+mesh_beat];
        if(obs_enabled&&!resident_known[(mesh_address>>2)+mesh_beat])++mesh_padding_beats;
        if(tb->obs_mesh_rdata!=expected) {
            fprintf(stderr,"mesh read mismatch address=%08x beat=%u got=%08x expected=%08x\n",
                    mesh_address,mesh_beat,tb->obs_mesh_rdata,expected);
            throw std::runtime_error("Native mesh response differs from immutable upload");
        }
        if(bool(tb->obs_mesh_rlast)!=(mesh_beat+1==mesh_expected_beats))
            throw std::runtime_error("Native mesh burst length mismatch");
        ++mesh_beat;
        if(obs_enabled)++mesh_beats;
        if(tb->obs_mesh_rlast) {
            mesh_inflight=false;
            if(obs_enabled)mesh_service+=cycles-mesh_accepted;
        }
    }
    if(tb->obs_mesh_arvalid&&tb->obs_mesh_arready) {
        const uint32_t a=tb->obs_mesh_araddr;
        const unsigned n=unsigned(tb->obs_mesh_arlen)+1;
        if(mesh_inflight||(a&31)||n!=8||uint64_t(a)+n*4>0x1000000)
            throw std::runtime_error("Invalid native mesh request");
        mesh_inflight=true;mesh_address=a;mesh_expected_beats=n;mesh_beat=0;
        mesh_accepted=cycles;
        if(obs_enabled)++mesh_fills;
    }
    if(obs_enabled) {
        worker_empty+=tb->obs_worker_empty;worker_output_stall+=tb->obs_worker_output_stall;
        worker_active+=tb->obs_worker_active;worker_raster_overlap+=tb->obs_worker_active&&tb->dbg_state!=0;
        ++worker_states[tb->obs_worker_state]; ++gpu_states[tb->dbg_state]; ++gpu_fbss[tb->obs_fbss];
        mesh_wait+=tb->obs_mesh_arvalid&&!tb->obs_mesh_arready;
        mesh_raster_overlap+=mesh_inflight&&tb->dbg_state!=0;
        mesh_texture_overlap+=mesh_inflight&&texture_inflight;
    }
}
static void mesh_report() {
    for(unsigned s=0;s<64;++s)if(gpu_states[s])printf("GPU_STATE %u %llu\n",s,(unsigned long long)gpu_states[s]);
    for(unsigned s=0;s<16;++s)if(gpu_fbss[s])printf("GPU_FBSS %u %llu\n",s,(unsigned long long)gpu_fbss[s]);
    printf("WORKER empty=%llu output_stall=%llu active=%llu raster_overlap=%llu\n",
        (unsigned long long)worker_empty,(unsigned long long)worker_output_stall,
        (unsigned long long)worker_active,(unsigned long long)worker_raster_overlap);
    for(unsigned s=0;s<128;++s)if(worker_states[s])printf("WORKER_STATE %u %llu\n",s,(unsigned long long)worker_states[s]);
    if(mesh_inflight)throw std::runtime_error("Mesh read remains outstanding after final drain");
    printf("MESH padding_beats=%llu padding_seed=%u fills=%llu beats=%llu request_wait=%llu service_sum=%llu raster_read_overlap=%llu texture_requests_overlap=%llu\n",
        (unsigned long long)mesh_padding_beats,mesh_padding_seed,
        (unsigned long long)mesh_fills,(unsigned long long)mesh_beats,
        (unsigned long long)mesh_wait,(unsigned long long)mesh_service,
        (unsigned long long)mesh_raster_overlap,(unsigned long long)mesh_texture_overlap);
}
