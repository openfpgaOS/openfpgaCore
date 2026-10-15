// Sample-domain reference: deliberately independent of the RTL state machine.
// The old mixer is also checked against this reference before candidate use.
#include <array>
#include <algorithm>

struct ReferenceVoice {
    uint32_t base=0, length=0, rate=0, pos=0, frac=0;
    uint32_t loop_start=0, loop_end=0, wptr=0, ctrl=0;
    uint8_t left=0, right=0, target_l=0, target_r=0, ramp=0, group=0;
    bool active=false;
};
static uint32_t reference_rng=0x27392b18;
static uint32_t rnd() { reference_rng^=reference_rng<<13; reference_rng^=reference_rng>>17; reference_rng^=reference_rng<<5; return reference_rng; }
static int64_t floor_shift(int64_t x,unsigned n) {
    return x>=0 ? x/(int64_t(1)<<n) : -((-x+(int64_t(1)<<n)-1)/(int64_t(1)<<n));
}
static int16_t clip16(int64_t x) { return int16_t(std::max<int64_t>(-32768,std::min<int64_t>(32767,x))); }
static uint8_t reference_ramp(uint8_t current,uint8_t target,uint8_t step) {
    if(!step) return target;
    if(current<target) return std::min<unsigned>(target,unsigned(current)+step);
    return std::max<int>(target,int(current)-step);
}
static int16_t reference_sample(const ReferenceVoice &v,uint32_t pos,unsigned channel) {
    uint32_t byte=v.base+pos*((v.ctrl&CTRL_STEREO)?4:2);
    if(v.ctrl&CTRL_STEREO) byte+=channel*2;
    uint32_t word=dut->rootp->tb_audio_mixer->sdram_mem[(byte>>2)&16383];
    return int16_t(word>>((byte&2)*8));
}
static uint32_t reference_frame(std::array<ReferenceVoice,32>& voices, uint8_t master,
                               const std::array<uint8_t,4>& groups,uint32_t &pending) {
    int64_t sums[2]={0,0};
    for(unsigned slot=0;slot<32;slot++) {
        auto &v=voices[slot]; if(!v.active) continue;
        bool stream=(v.ctrl&CTRL_STREAM)!=0, loop=(v.ctrl&CTRL_LOOP)!=0;
        uint32_t available=v.wptr>=v.pos?v.wptr-v.pos:v.wptr+v.length-v.pos;
        bool hold=stream && available<4, quiet=stream && available<256;
        if(!hold) {
            if(v.pos>=v.length) {v.active=false;pending|=1u<<slot;continue;}
            uint32_t next=v.pos+1;
            if(loop && v.loop_end>v.loop_start && next>=v.loop_end) next=v.loop_start;
            if(next>=v.length) next=v.pos;
            for(unsigned ch=0;ch<2;ch++) {
                int64_t a=reference_sample(v,v.pos,ch),b=reference_sample(v,next,ch);
                int64_t interpolated=a+floor_shift((b-a)*v.frac,16);
                sums[ch]+=clip16(floor_shift(interpolated*(ch?v.right:v.left),8));
            }
            uint32_t frac=v.frac+(v.rate&65535), pos=(v.pos+(v.rate>>16)+(frac>>16))&0x3fffff;
            bool ended=false;
            if(loop) {
                if(v.loop_end<=v.loop_start) ended=true;
                else while(pos>=v.loop_end) pos-=v.loop_end-v.loop_start;
            } else ended=pos>=v.length;
            // RTL retires without committing the advanced position/fraction/ramp.
            if(ended) {v.active=false;pending|=1u<<slot;continue;}
            v.pos=pos;v.frac=frac&65535;
        }
        unsigned gain=(unsigned(master)*groups[v.group])>>8;
        uint8_t l=quiet?0:(unsigned(v.target_l)*gain)>>8;
        uint8_t r=quiet?0:(unsigned(v.target_r)*gain)>>8;
        v.left=reference_ramp(v.left,l,v.ramp);
        v.right=reference_ramp(v.right,r,v.ramp);
    }
    return (uint32_t(uint16_t(clip16(floor_shift(sums[0],4))))<<16) |
            uint16_t(clip16(floor_shift(sums[1],4)));
}
static void ref_reset() {
    reset_inputs();dut->mixer_enable=0;dut->reset_n=0;tick(20);dut->reset_n=1;tick(20);
}
static void safe_write(unsigned v,unsigned field,uint32_t value) {
    unsigned guard=0;
    while(!dut->voice_wr_ready && guard++<200000)tick();
    if(!dut->voice_wr_ready) {printf("REFERENCE ERROR write timeout\n");exit(2);}
    mmio_voice_write(v,field,value);
}
static void program_reference_voice(unsigned i,const ReferenceVoice &v) {
    safe_write(i,VTBL_CTRL,0);
    safe_write(i,VTBL_ADDR,v.base); safe_write(i,VTBL_LEN,v.length);
    safe_write(i,VTBL_RATE,v.rate); safe_write(i,VTBL_POS_INT,v.pos);
    safe_write(i,VTBL_POS_FRAC,v.frac);
    safe_write(i,VTBL_LOOP_START,v.loop_start);safe_write(i,VTBL_LOOP_END,v.loop_end);
    safe_write(i,VTBL_VOL_LR,v.left|(unsigned(v.right)<<8));
    safe_write(i,VTBL_VOL_TARGET,v.target_l|(unsigned(v.target_r)<<8));
    safe_write(i,VTBL_VOL_RATE,v.ramp); safe_write(i,VTBL_WPTR,v.wptr);
    safe_write(i,VTBL_CTRL,v.ctrl);
}
static uint64_t reference_hash=14695981039346656037ull;
static void hash_word(uint32_t value) { reference_hash=(reference_hash^value)*1099511628211ull; }
static unsigned check_irq_and_readback_races() {
    unsigned failures=0;
    // Exercise all decoded end-event bits, including bit31, with W1C exactly
    // on the end-pending commit edge. Clear must win over a simultaneous end.
    for(unsigned voice=0;voice<32;voice++)for(unsigned clear_case=0;clear_case<2;clear_case++) {
        ref_reset();sdram_fill_constant(0x7fff8000);
        ReferenceVoice v;v.length=1;v.rate=65536;v.ctrl=CTRL_ACTIVE;
        v.left=v.right=255;program_reference_voice(voice,v);tick(200);
        dut->mixer_enable=1;
        auto *tb=dut->rootp->tb_audio_mixer;
        unsigned timeout=0;
        while(!(tb->__PVT__dut__DOT__cur_voice==voice && tb->__PVT__dut__DOT__state==28) && timeout++<20000)tick();
        if(timeout>=20000){failures++;continue;}
        tick(); // NEXT_VOICE, event registered; pending update is next edge.
        dut->irq_clear=clear_case?(1u<<voice):~(1u<<voice);dut->irq_clear_wr=1;
        tick();dut->irq_clear_wr=0;
        uint32_t expected=clear_case?0:1u<<voice;
        if(dut->voice_end_pending!=expected || (dut->voice_active_mask&(1u<<voice)))failures++;
        // Readback reports the last FSM-committed position, not an immediate
        // echo of POS_WR. Advance a rate-zero loop once to publish all slots.
        dut->mixer_enable=0;tick(400);
        for(unsigned slot=0;slot<32;slot++) {
            ReferenceVoice p;p.pos=32+slot;p.length=p.loop_end=128;
            p.ctrl=CTRL_ACTIVE|CTRL_LOOP;program_reference_voice(slot,p);
        }
        tick(200);dut->mixer_enable=1;
        do{tick();}while(!dut->sample_wr);
        dut->mixer_enable=0;
        for(unsigned slot=0;slot<32;slot++) {
            dut->voice_sel_rd=slot;tick(1);
            if(dut->pos_readback!=32+slot)failures++;
        }
    }
    printf("IRQ_READBACK cases=64 position_reads=2048 failures=%u\n",failures);
    return failures;
}
static unsigned await_reference_frame(bool stall_pattern,unsigned scenario,unsigned frame) {
    unsigned cycles=0;
    dut->mixer_enable=1;
    do {
        dut->stall_sdram=stall_pattern && ((cycles+scenario*7)%137<19);
        dut->stall_ar=stall_pattern && ((cycles+frame*5)%71<11);
        tick();
        if(++cycles>400000) {printf("REFERENCE ERROR sample timeout\n");exit(2);}
    } while(!dut->sample_wr);
    dut->mixer_enable=0;dut->stall_sdram=0;dut->stall_ar=0;
    return cycles;
}
static int run_independent_reference() {
    uint64_t total_cycles=0;unsigned checked=0, errors=0, saturations=0, stream_holds=0;
    errors+=check_irq_and_readback_races();
    constexpr uint32_t rates[]={0,1,32768,65535,65536,65537,90317,131072,0x81ffff,0x201234};
    for(unsigned scenario=0;scenario<96;scenario++) {
        ref_reset();
        std::array<ReferenceVoice,32> voices;
        std::array<uint8_t,4> groups={uint8_t(rnd()),uint8_t(rnd()),uint8_t(rnd()),uint8_t(rnd())};
        uint8_t master=uint8_t(rnd());
        bool clip_case=scenario<2;
        if(clip_case){master=255;groups.fill(255);}
        dut->master_vol=master;dut->group_vol_0=groups[0];dut->group_vol_1=groups[1];
        dut->group_vol_2=groups[2];dut->group_vol_3=groups[3];
        for(unsigned i=0;i<16384;i++) {
            uint32_t data=rnd();
            if(clip_case)data=scenario?0x80008000u:0x7fff7fffu;
            dut->rootp->tb_audio_mixer->sdram_mem[i]=data;
        }
        uint64_t packed_groups=0;
        for(unsigned i=0;i<32;i++) {
            auto &v=voices[i];
            v.base=i*2048;v.length=64;v.rate=rates[(i+scenario)%10];v.group=(i+scenario)&3;
            v.ctrl=CTRL_ACTIVE | ((i&1)?CTRL_STEREO:0) | ((i%5)?CTRL_LOOP:0);
            v.loop_start=rnd()%8;v.loop_end=32+rnd()%32;
            // POS_FRAC is not a writable MMIO field and the BRAM retains it
            // across reset. Snapshot that initial condition once; all later
            // fractional positions are predicted independently by the model.
            v.pos=rnd()%v.loop_end;v.frac=vtbl_read(i,VTBL_POS_FRAC)&65535;
            v.left=rnd();v.right=rnd();v.target_l=rnd();v.target_r=rnd();v.ramp=(i%4)?1+rnd()%255:0;
            if(i%11==0) {v.loop_start=32;v.loop_end=32;}
            if(i%13==0) {v.pos=65;}
            if(i==15 || i==31) {
                v.ctrl=CTRL_ACTIVE|CTRL_LOOP|CTRL_STEREO|CTRL_STREAM;
                v.length=512;v.loop_start=0;v.loop_end=512;v.rate=rates[scenario%8];
                v.pos=0;v.wptr=(scenario%3)?(scenario%3==1?32:511):0;
            }
            if(clip_case) {
                v.ctrl=CTRL_ACTIVE|CTRL_LOOP|((i&1)?CTRL_STEREO:0);
                v.pos=0;v.loop_start=0;v.loop_end=v.length=64;v.rate=65536;
                v.left=v.right=v.target_l=v.target_r=255;v.ramp=0;
            }
            v.active=true;packed_groups|=uint64_t(v.group)<<(2*i);
            program_reference_voice(i,v);
        }
        dut->voice_group_packed=packed_groups;dut->sdram_latency=(scenario%4)*13;tick(200);
        uint32_t pending=0;
        for(unsigned frame=0;frame<192;frame++) {
            if(frame==32 || frame==88 || frame==144) {
                // Boundary-controlled updates permit independent numerical comparison
                // despite different RTL scheduling; live races use the inherited suite.
                master=clip_case?255:uint8_t(rnd());dut->master_vol=master;
                for(unsigned i=0;i<32;i++) {
                    voices[i].target_l=rnd();voices[i].target_r=rnd();
                    safe_write(i,VTBL_VOL_TARGET,voices[i].target_l|(unsigned(voices[i].target_r)<<8));
                    if(voices[i].ctrl&CTRL_STREAM) {
                        voices[i].wptr=(voices[i].pos+((frame==88)?3:400))%voices[i].length;
                        safe_write(i,VTBL_WPTR,voices[i].wptr);
                    }
                }
                uint32_t clear=rnd();pending &=~clear;
                dut->irq_clear=clear;dut->irq_clear_wr=1;tick(1);dut->irq_clear_wr=0;tick(8);
            }
            for(auto &v:voices) if(v.active && (v.ctrl&CTRL_STREAM)) {
                uint32_t avail=v.wptr>=v.pos?v.wptr-v.pos:v.wptr+v.length-v.pos;
                stream_holds+=avail<4;
            }
            uint32_t expected=reference_frame(voices,master,groups,pending);
            total_cycles+=await_reference_frame(scenario%2,scenario,frame);
            bool good=expected==dut->sample_data && pending==dut->voice_end_pending;
            uint32_t active=0;
            for(unsigned i=0;i<32;i++) {
                if(voices[i].active)active|=1u<<i;
                uint32_t pos=vtbl_read(i,VTBL_POS_INT)&0x3fffff, frac=vtbl_read(i,VTBL_POS_FRAC)&65535;
                uint32_t vol=vtbl_read(i,VTBL_VOL_LR)&65535;
                good &= pos==voices[i].pos && frac==voices[i].frac && vol==(voices[i].left|(unsigned(voices[i].right)<<8));
                if(!good && errors<3 && (pos!=voices[i].pos || frac!=voices[i].frac || vol!=(voices[i].left|(unsigned(voices[i].right)<<8))))
                    printf("STATE scenario%u frame%u voice%u pos%u/%u frac%u/%u vol%x/%x\n",scenario,frame,i,pos,voices[i].pos,frac,voices[i].frac,vol,voices[i].left|(unsigned(voices[i].right)<<8));
                hash_word(pos);hash_word(frac);hash_word(vol);
            }
            good &= active==dut->voice_active_mask;
            if(!good) {
                if(errors++<3)printf("REFERENCE ERROR scenario%u frame%u pcm%08x/%08x irq%x/%x active%x/%x\n",scenario,frame,unsigned(dut->sample_data),expected,unsigned(dut->voice_end_pending),pending,unsigned(dut->voice_active_mask),active);
                if(errors>10)return 1;
            }
            saturations+=(int16_t(expected>>16)==32767 || int16_t(expected>>16)==-32768);
            hash_word(expected);hash_word(pending);hash_word(active);checked++;
            // Hold a nearly-full FIFO: the next walk must not start.
            dut->external_fifo_level=900;dut->mixer_enable=1;
            for(unsigned j=0;j<19;j++) {tick();if(dut->sample_wr)errors++;}
            dut->mixer_enable=0;dut->external_fifo_level=0;
        }
    }
    printf("REFERENCE samples=%u scenarios=96 errors=%u saturation_samples=%u stream_holds=%u hash=%016llx processing_cycles=%llu\n",checked,errors,saturations,stream_holds,(unsigned long long)reference_hash,(unsigned long long)total_cycles);
    delete dut;
    return errors || !saturations || !stream_holds;
}

static int run_capacity() {
    printf("mode,voices,read_delay_cycles,mean_cycles,max_cycles,max_khz_100,max_khz_90,requests_per_output,beats_per_output\n");
    for(unsigned mode=0;mode<7;mode++) for(unsigned count:{1u,8u,16u,20u,28u,31u,32u})
    for(unsigned delay:{0u,8u,16u,32u,48u,64u,96u}) {
        ref_reset();dut->sdram_latency=delay;sdram_fill_constant(0x40004000);
        for(unsigned i=0;i<count;i++) {
            ReferenceVoice v;v.base=0;v.length=v.loop_end=256;v.rate=65536;
            bool stereo=mode==1 || (mode>=2 && i==count-1);
            v.ctrl=CTRL_ACTIVE|CTRL_LOOP|(stereo?CTRL_STEREO:0);
            if(mode>=3)v.rate=uint32_t((uint64_t(stereo?44100:((mode==3 || mode==5)?11025:22050))<<16)/48000);
            if(mode>=5 && stereo) {
                v.ctrl|=CTRL_STREAM;v.length=v.loop_end=131072;v.wptr=65536;
            }
            v.left=v.right=v.target_l=v.target_r=32;program_reference_voice(i,v);
        }
        tick(200);dut->mixer_enable=1;
        for(unsigned warm=0;warm<64;warm++) {do{tick();}while(!dut->sample_wr);}
        uint64_t total=0;unsigned maximum=0;
        uint32_t requests_before=dut->read_requests,beats_before=dut->read_beats;
        for(unsigned sample=0;sample<512;sample++) {
            unsigned cycles=0;do{tick();cycles++;}while(!dut->sample_wr && cycles<1000000);
            if(cycles>=1000000)return 2;
            total+=cycles;maximum=std::max(maximum,cycles);
        }
        double mean=double(total)/512;
        const char *names[]={"mono48","stereo48","sfx48-plus-stereo48","sfx11-plus-stereo44","sfx22-plus-stereo44","sfx11-plus-stream44","sfx22-plus-stream44"};
        printf("%s,%u,%u,%.6f,%u,%.6f,%.6f,%.6f,%.6f\n",names[mode],count,delay,mean,maximum,100000.0/mean,90000.0/mean,double(dut->read_requests-requests_before)/512,double(dut->read_beats-beats_before)/512);
    }
    delete dut;return 0;
}
