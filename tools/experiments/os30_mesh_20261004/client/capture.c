/* Called by the functional CPU harness at a completed frame boundary. */
static void mesh_capture(qenv *env,const char *out,uint64_t frame) {
    const qsim_sym *cnt=qsim_syms_lookup(&env->syms,"mesh_shadow_count");
    const qsim_sym *jobs=qsim_syms_lookup(&env->syms,"mesh_shadow_jobs");
    const qsim_sym *stats=qsim_syms_lookup(&env->syms,"mesh_shadow_stats");
    if(!cnt||!jobs||!stats)return;
    uint32_t header[18]={0x3148534d,0};
    memcpy(&header[1],qsim_mem_ptr(env->cpu,cnt->addr,4),4);
    memcpy(header+2,qsim_mem_ptr(env->cpu,stats->addr,64),64);
    if(header[1]>512)abort();
    const char *start=getenv("MESH_CAPTURE_START");
    if(frame<(uint64_t)(start?atoi(start):0))return;
    char name[4096];snprintf(name,sizeof name,"%s/mesh-%05llu.bin",out,(unsigned long long)frame);
    FILE *f=fopen(name,"wb");if(!f)abort();
    if(fwrite(header,sizeof header,1,f)!=1)abort();
    for(unsigned i=0;i<header[1];i++) {
        uint32_t job[72];memcpy(job,qsim_mem_ptr(env->cpu,jobs->addr+i*sizeof job,sizeof job),sizeof job);
        if(!job[3]||job[3]>32||!job[4]||job[4]>64)abort();
        if(fwrite(job,sizeof job,1,f)!=1)abort();
        if(fwrite(qsim_mem_ptr(env->cpu,job[1],job[3]*16),job[3]*16,1,f)!=1)abort();
        if(fwrite(qsim_mem_ptr(env->cpu,job[2],job[4]*4),job[4]*4,1,f)!=1)abort();
    }
    fclose(f);
    const qsim_sym *material=qsim_syms_lookup(&env->syms,"mesh_material_state");
    const qsim_sym *status=qsim_syms_lookup(&env->syms,"mesh_material_status");
    if(material&&status) {
        snprintf(name,sizeof name,"%s/material-%05llu.bin",out,(unsigned long long)frame);
        f=fopen(name,"wb");if(!f)abort();
        uint32_t mh[2]={0x31544d4d,header[1]};
        if(fwrite(mh,sizeof mh,1,f)!=1)abort();
        uint32_t texaddr[512],texsize[512],textures=0;
        for(unsigned i=0;i<header[1];i++) {
            uint32_t value,state[64];
            memcpy(&value,qsim_mem_ptr(env->cpu,status->addr+4*i,4),4);
            memcpy(state,qsim_mem_ptr(env->cpu,material->addr+256*i,256),256);
            if(fwrite(&value,4,1,f)!=1||fwrite(state,256,1,f)!=1)abort();
            if(!value) {
                unsigned width=state[28],mask=state[29]>>16;
                unsigned height=mask==65535?(state[34]>>16)+1:mask+1;
                if(!width||width>4096||!height||height>4096)abort();
                unsigned bytes=width*height*2,n;
                for(n=0;n<textures;n++)if(texaddr[n]==state[27])break;
                if(n<textures) {if(texsize[n]!=bytes)abort();}
                else {texaddr[n]=state[27];texsize[n]=bytes;textures++;}
            }
        }
        fclose(f);
        snprintf(name,sizeof name,"%s/textures-%05llu.bin",out,(unsigned long long)frame);
        f=fopen(name,"wb");if(!f)abort();
        uint32_t th[2]={0x31584d54,textures};
        if(fwrite(th,sizeof th,1,f)!=1)abort();
        for(unsigned n=0;n<textures;n++) {
            uint32_t item[2]={texaddr[n],texsize[n]};
            if(fwrite(item,sizeof item,1,f)!=1)abort();
            if(fwrite(qsim_mem_ptr(env->cpu,item[0],item[1]),item[1],1,f)!=1)abort();
        }
        fclose(f);
    }
}
