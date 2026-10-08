// Isolated native-bus RV32IMF simulator. RAM responses are synchronous;
// this models local memory, not SDRAM, a cache, DMA or a complete application.
#include "VVexiiRiscv.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <deque>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#define IP(n) cpu.FetchCachelessPlugin_logic_bus_##n
#define DP(n) cpu.LsuCachelessPlugin_logic_bus_##n
static uint32_t word(const std::vector<uint8_t>& b,size_t a) {
    if(a+4>b.size()) throw std::runtime_error("truncated input word");
    return uint32_t(b[a])|(uint32_t(b[a+1])<<8)|(uint32_t(b[a+2])<<16)|(uint32_t(b[a+3])<<24);
}
static uint16_t half(const std::vector<uint8_t>& b,size_t a) {
    if(a+2>b.size()) throw std::runtime_error("truncated ELF header");
    return uint16_t(b[a])|(uint16_t(b[a+1])<<8);
}
static std::vector<uint8_t> read_file(const std::string& path) {
    std::ifstream f(path,std::ios::binary);if(!f)throw std::runtime_error("cannot read "+path);
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(f),{});
}
struct Response { uint64_t due;unsigned id;uint32_t data;bool error;bool exit;uint32_t status; };
struct Channel {
    std::deque<Response> pending;std::array<bool,2> busy{};
    bool stalled=false;std::array<uint32_t,6> held{};
    uint64_t commands=0,stall_cycles=0,response_cycles=0;size_t max_pending=0;
    void check(bool valid,bool ready,const std::array<uint32_t,6>& payload) {
        if(stalled&&(!valid||held!=payload))throw std::runtime_error("native command changed while stalled");
        stalled=valid&&!ready;if(stalled){held=payload;++stall_cycles;}
    }
    void add(Response r) {
        if(r.id>1||busy[r.id])throw std::runtime_error("native request ID reused before response");
        busy[r.id]=true;pending.push_back(r);++commands;max_pending=std::max(max_pending,pending.size());
    }
    void clear(){pending.clear();busy={};stalled=false;}
};
struct Options {
    std::string elf,bin,input,out,stats;uint64_t max_cycles=100000000,reset_at=~uint64_t(0);
    unsigned seed=1,stall_pct=0,max_delay=1;uint32_t expected_exit=1;
};
static Options options(int argc,char**argv) {
    Options o;
    for(int i=1;i<argc;++i){std::string a=argv[i];if(i+1==argc)throw std::runtime_error("missing value for "+a);std::string v=argv[++i];
        if(a=="--elf")o.elf=v;else if(a=="--bin")o.bin=v;else if(a=="--input")o.input=v;
        else if(a=="--out")o.out=v;else if(a=="--stats")o.stats=v;
        else if(a=="--max-cycles")o.max_cycles=std::stoull(v,nullptr,0);
        else if(a=="--reset-at")o.reset_at=std::stoull(v,nullptr,0);
        else if(a=="--seed")o.seed=std::stoul(v,nullptr,0);
        else if(a=="--stall-pct")o.stall_pct=std::stoul(v,nullptr,0);
        else if(a=="--max-delay")o.max_delay=std::stoul(v,nullptr,0);
        else if(a=="--expected-exit")o.expected_exit=std::stoul(v,nullptr,0);
        else throw std::runtime_error("unknown argument "+a);
    }
    if(o.elf.empty()==o.bin.empty()||o.stall_pct>99||o.max_delay<1)throw std::runtime_error("require exactly one --elf/--bin; stall-pct 0..99; max-delay >=1");
    return o;
}
static void load_image(const Options&o,std::vector<uint8_t>& mem) {
    if(!o.bin.empty()){auto b=read_file(o.bin);if(b.size()>mem.size())throw std::runtime_error("binary exceeds local RAM");std::copy(b.begin(),b.end(),mem.begin());return;}
    auto b=read_file(o.elf);
    if(b.size()<52||memcmp(b.data(),"\177ELF",4)||b[4]!=1||b[5]!=1||half(b,18)!=243)throw std::runtime_error("require little-endian ELF32 RISC-V");
    if(word(b,24)!=0)throw std::runtime_error("ELF entry must match reset PC 0");
    uint32_t ph=word(b,28);unsigned size=half(b,42),count=half(b,44);if(size<32)throw std::runtime_error("short program header");
    for(unsigned i=0;i<count;++i){size_t p=uint64_t(ph)+uint64_t(i)*size;if(word(b,p)!=1)continue;
        uint32_t off=word(b,p+4),vaddr=word(b,p+8),paddr=word(b,p+12),filesz=word(b,p+16),memsz=word(b,p+20);
        if(vaddr!=paddr||filesz>memsz||uint64_t(paddr)+memsz>mem.size()||uint64_t(off)+filesz>b.size())throw std::runtime_error("unsupported ELF segment address/size");
        std::fill(mem.begin()+paddr,mem.begin()+paddr+memsz,0);std::copy(b.begin()+off,b.begin()+off+filesz,mem.begin()+paddr);
    }
}
int main(int argc,char**argv)try {
    auto o=options(argc,argv);Verilated::commandArgs(argc,argv);VVexiiRiscv cpu;
    std::vector<uint8_t> mem(0x20000),stream;load_image(o,mem);auto initial_mem=mem;
    if(!o.input.empty())stream=read_file(o.input);if(stream.size()%4)throw std::runtime_error("input is not word aligned");
    std::vector<uint32_t> output;size_t input_pos=0;Channel ic,dc;uint64_t cycles=0,reads=0,writes=0,io_reads=0,io_writes=0;
    uint32_t random=o.seed?o.seed:1;auto rng=[&](){random^=random<<13;random^=random>>17;random^=random<<5;return random;};
    auto ready=[&](){return o.stall_pct==0||rng()%100>=o.stall_pct;};
    auto latency=[&](){return o.max_delay==1?1u:1u+rng()%o.max_delay;};
    bool done=false;uint32_t exit_status=0;unsigned resets=0;
    cpu.PrivilegedPlugin_logic_harts_0_int_m_timer=0;cpu.PrivilegedPlugin_logic_harts_0_int_m_software=0;cpu.PrivilegedPlugin_logic_harts_0_int_m_external=0;
    auto reset=[&](){ic.clear();dc.clear();IP(rsp_valid)=0;DP(rsp_valid)=0;IP(cmd_ready)=0;DP(cmd_ready)=0;cpu.reset=1;
        for(int i=0;i<16;++i){cpu.clk=0;cpu.eval();cpu.clk=1;cpu.eval();}cpu.reset=0;};
    reset();
    while(!done&&cycles<o.max_cycles){
        if(cycles==o.reset_at){reset();++resets;mem=initial_mem;input_pos=0;output.clear();}
        cpu.clk=0;cpu.PrivilegedPlugin_logic_rdtime=cycles;
        IP(cmd_ready)=ready();DP(cmd_ready)=ready();IP(rsp_valid)=0;DP(rsp_valid)=0;
        auto respond=[&](Channel&c,bool instruction){
            // Delays may complete different IDs out of order, as allowed by the native bus.
            auto it=std::find_if(c.pending.begin(),c.pending.end(),[&](const Response&r){return r.due<=cycles;});
            if(it==c.pending.end())return;auto r=*it;c.pending.erase(it);c.busy[r.id]=false;++c.response_cycles;
            if(instruction){IP(rsp_valid)=1;IP(rsp_payload_id)=r.id;IP(rsp_payload_error)=r.error;IP(rsp_payload_word)=r.data;}
            else {DP(rsp_valid)=1;DP(rsp_payload_id)=r.id;DP(rsp_payload_error)=r.error;DP(rsp_payload_data)=r.data;if(r.exit){done=true;exit_status=r.status;}}
        };
        respond(ic,true);respond(dc,false);cpu.eval();
        std::array<uint32_t,6> ip={IP(cmd_payload_id),IP(cmd_payload_address),0,0,0,0};
        std::array<uint32_t,6> dp={DP(cmd_payload_id),DP(cmd_payload_address),DP(cmd_payload_write),DP(cmd_payload_data),DP(cmd_payload_mask),DP(cmd_payload_size)};
        ic.check(IP(cmd_valid),IP(cmd_ready),ip);dc.check(DP(cmd_valid),DP(cmd_ready),dp);
        bool ia=IP(cmd_valid)&&IP(cmd_ready),da=DP(cmd_valid)&&DP(cmd_ready);
        // All bus commands are sampled before the active edge; no combinational response.
        cpu.clk=1;cpu.eval();
        if(ia){uint32_t addr=ip[1];bool error=(addr&3)||addr>=0x10000;uint32_t data=error?0:word(mem,addr);ic.add({cycles+latency(),ip[0],data,error,false,0});if(error)throw std::runtime_error("instruction address outside local executable RAM");}
        if(da){uint32_t addr=dp[1],data=0;bool write=dp[2],error=false,exit=false;uint32_t status=0;
            if(addr<mem.size()){
                uint32_t base=addr&~3u;if(write){++writes;for(unsigned lane=0;lane<4;++lane)if(dp[4]&(1u<<lane))mem[base+lane]=dp[3]>>(8*lane);}else{++reads;data=word(mem,base);}
            }else if(addr>=0x20000&&addr<0x21000){
                if((addr&3)||dp[5]!=2||dp[4]!=15)throw std::runtime_error("mailbox accesses must be aligned 32-bit words");
                if(write){++io_writes;if(addr==0x20000){exit=true;status=dp[3];}else if(addr==0x20004)output.push_back(dp[3]);else throw std::runtime_error("unknown mailbox write");}
                else{++io_reads;if(addr==0x20008)data=uint32_t(cycles);else if(addr==0x2000c)data=cycles>>32;else if(addr==0x20010){data=word(stream,input_pos);input_pos+=4;}else throw std::runtime_error("unknown mailbox read");}
            }else error=true;
            dc.add({cycles+latency(),dp[0],data,error,exit,status});if(error)throw std::runtime_error("data address outside local RAM/MMIO");
        }
        ++cycles;
    }
    cpu.final();if(!done)throw std::runtime_error("CPU timeout after "+std::to_string(cycles)+" cycles");
    if(!o.out.empty()){std::ofstream f(o.out,std::ios::binary);if(!f)throw std::runtime_error("cannot create output");for(uint32_t w:output)for(unsigned i=0;i<4;++i)f.put(w>>(8*i));}
    std::string result="{\n  \"cycles\": "+std::to_string(cycles)+",\n  \"exit_status\": "+std::to_string(exit_status)+",\n  \"instruction_requests\": "+std::to_string(ic.commands)+",\n  \"instruction_stall_cycles\": "+std::to_string(ic.stall_cycles)+",\n  \"data_reads\": "+std::to_string(reads)+",\n  \"data_writes\": "+std::to_string(writes)+",\n  \"io_reads\": "+std::to_string(io_reads)+",\n  \"io_writes\": "+std::to_string(io_writes)+",\n  \"data_stall_cycles\": "+std::to_string(dc.stall_cycles)+",\n  \"input_words\": "+std::to_string(input_pos/4)+",\n  \"output_words\": "+std::to_string(output.size())+",\n  \"max_instruction_inflight\": "+std::to_string(ic.max_pending)+",\n  \"max_data_inflight\": "+std::to_string(dc.max_pending)+",\n  \"resets\": "+std::to_string(resets)+"\n}\n";
    if(!o.stats.empty()){std::ofstream f(o.stats);f<<result;}std::cout<<result;
    if(exit_status!=o.expected_exit)throw std::runtime_error("unexpected firmware exit status");return 0;
}catch(const std::exception&e){std::cerr<<"ERROR: "<<e.what()<<"\n";return 1;}
