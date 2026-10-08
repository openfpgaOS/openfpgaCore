// Reuse the validated randomized AXI/ring/FIFO model, with immutable job memory.
#define main frontend_legacy_main
#include "frontend_test.cpp"
#undef main
int main(int argc,char **argv){Verilated::commandArgs(argc,argv);try{
    check(argc==2,"expected vector filename");FILE*f=std::fopen(argv[1],"rb");check(f,"vector open");
    uint32_t h[2];check(std::fread(h,4,2,f)==2,"header read");
    std::vector<uint32_t> code(h[0]);check(std::fread(code.data(),4,code.size(),f)==code.size(),"program read");
    Sim s;s.memory_base=0x03800000;s.memory.resize(h[1]*256);s.consume_hold=1000000;s.upload_raw(0,code);
    uint64_t vertices=0,triangles=0;
    for(unsigned job=0;job<h[1];++job){uint32_t meta[4];check(std::fread(meta,4,4,f)==4,"job header read");
        check(std::fread(s.memory.data()+job*256,4,256,f)==256,"job bank read");
        std::vector<uint32_t> expected(meta[3]);check(std::fread(expected.data(),4,expected.size(),f)==expected.size(),"job output read");
        uint32_t address=s.memory_base+job*1024;
        const uint32_t descriptor[]={0x62000007,address,meta[0]*4,address+192*4,
            (meta[2]<<16)|meta[1],address+128*4,64,0};
        s.input.insert(s.input.end(),std::begin(descriptor),std::end(descriptor));
        s.expected.insert(s.expected.end(),expected.begin(),expected.end());
        if(job%17==16)s.packet(1,0xface0000|job);
        vertices+=meta[0];triangles+=meta[2];
    }
    check(std::fgetc(f)==EOF,"trailing vector data");std::fclose(f);
    s.packet(0,0);s.drain(250000000);check(s.received==s.expected,"final stream differs");
    check(h[1]<2||s.overlap>0,"whole-mesh DMA overlap missing");
    check(s.expected.size()<=256||s.full_cycles>0,"whole-mesh FIFO backpressure missing");
    std::printf("{\"pass\":true,\"jobs\":%u,\"vertices\":%llu,\"triangles\":%llu,\"output_words_checked\":%zu,"
        "\"cycles\":%llu,\"read_bursts\":%llu,\"dma_engine_overlap_cycles\":%llu,\"fifo_full_cycles\":%llu}\n",
        h[1],(unsigned long long)vertices,(unsigned long long)triangles,s.received.size(),
        (unsigned long long)s.cycles,(unsigned long long)s.reads,(unsigned long long)s.overlap,(unsigned long long)s.full_cycles);
    return 0;
}catch(const std::exception&e){std::fprintf(stderr,"FAIL: %s\n",e.what());return 1;}}
