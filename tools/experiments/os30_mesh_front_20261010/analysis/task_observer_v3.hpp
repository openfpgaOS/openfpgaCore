#pragma once
// Passive, pre-edge observer for one exact ELF and the qualified simple model.
// Never writes model state, clocks, fences, or substitutes memory contents.
#include <algorithm>
#include <array>
#include <map>
#include <sstream>
#include <vector>
#include "task_symbols_v1.hpp"

namespace mesh_task_trace {
struct Call { unsigned serial,task,phase; uint32_t pc,ra,sp; uint64_t cycle; };
struct Cost { uint64_t cycles=0, retired=0; };
class Observer {
    std::ofstream tasks,calls,raw,vertices,commands,reads,contexts,source_data,states,state_data;
    std::vector<Call> stack;
    std::map<std::tuple<unsigned,unsigned,int>,Cost> costs;
    std::map<std::pair<unsigned,int>,Cost> call_costs;
    unsigned task=0,call_serial=0,task_phase=0;
    unsigned raw_completions=0,read_failures=0,protocol_errors=0;
    bool previous_done=false,previous_start=false;
    uint64_t previous_retire=0;
    static int function(uint32_t pc) {
        auto it=std::upper_bound(symbols.begin(),symbols.end(),pc,
            [](uint32_t value,const Symbol& s){return value<s.pc;});
        if(it==symbols.begin())return -1;
        --it;return pc<it->pc+it->bytes?int(it-symbols.begin()):-1;
    }
    template<class Cpu,class Peek>
    bool memory(const Cpu& cpu,Peek peek,uint64_t cycle,uint32_t addr,unsigned bytes,
                std::vector<uint8_t>& out,const char *why) {
        try {
            if(addr<0x10000000u || uint64_t(addr)+bytes>0x14000000ull)
                throw std::runtime_error("outside captured SDRAM");
            const uint32_t base=addr&~3u;
            unsigned size=((addr-base)+bytes+3)&~3u;
            auto value=cpu_arena_snapshot::capture(cpu,base,size,peek);
            out.assign(value.logical.begin()+addr-base,value.logical.begin()+addr-base+bytes);
            return true;
        } catch(const std::exception& e) {
            ++read_failures;reads<<cycle<<'\t'<<task<<'\t'<<addr<<'\t'<<bytes<<'\t'<<why<<'\t'<<e.what()<<'\n';
            return false;
        }
    }
    static uint32_t word(const std::vector<uint8_t>& b,unsigned n=0) {
        return uint32_t(b[n])|uint32_t(b[n+1])<<8|uint32_t(b[n+2])<<16|uint32_t(b[n+3])<<24;
    }
    template<class Cpu,class Peek>
    uint64_t value(const Cpu& cpu,Peek peek,uint64_t c,uint32_t a,unsigned n,const char *why) {
        std::vector<uint8_t> b;if(!memory(cpu,peek,c,a,n,b,why))return UINT64_MAX;
        return n==1?b[0]:word(b);
    }
    template<class Cpu,class Peek>
    void source_span(const Cpu& cpu,Peek peek,uint64_t c,uint32_t ptr) {
        // Read one complete line at a time; do not claim a partially filling
        // line is architectural memory. All failures remain visible.
        for(unsigned i=0;i<257;++i) {
            std::vector<uint8_t> b;
            if(!memory(cpu,peek,c,ptr+i*8,8,b,"task command span"))break;
            uint32_t w0=word(b),w1=word(b,4),op=w0>>24;
            if(i && op!=5 && op!=6)break;
            commands<<task<<'\t'<<task_phase<<'\t'<<c<<'\t'<<ptr+i*8<<'\t'<<w0<<'\t'<<w1<<'\n';
            if(!i && op!=1){++protocol_errors;break;}
        }
    }
public:
    explicit Observer(const std::string& out) :
      tasks(out+"/task-entries.tsv"),calls(out+"/task-calls.tsv"),
      raw(out+"/task-raw.tsv"),vertices(out+"/task-raw-vertices.tsv"),
      commands(out+"/task-commands.tsv"),reads(out+"/task-read-failures.tsv"),
      contexts(out+"/task-raw-contexts.tsv"),source_data(out+"/task-raw-inputs.bin",std::ios::binary),
      states(out+"/task-state.tsv"),state_data(out+"/task-state.bin",std::ios::binary) {
        contexts<<"task\tphase\tcycle\tcount\tsource_base\taspect\tcx\tcy\tcz\tambient\tdiffuse\tscale_u\tscale_v\tlit\tdata_offset";
        for(unsigned i=0;i<16;++i)contexts<<"\tm"<<i;contexts<<'\n';
        states<<"task\tphase\tcycle\trsp_valid\trsp_offset\trdp_valid\trdp_offset\n";
        tasks<<"task\tphase\tcycle\tcount\tfirst\tsource\tdl\tra\tfrm\tgeometry_mode\tmaterial_ready\tmaterial_elig\tother_mode_l\tcombine_mode\n";
        calls<<"serial\ttask\tphase\tevent\tcycle\tfunction_pc\treturn_pc\tsp\ta0\ta1\ta2\ta3\ts1\n";
        raw<<"task\tphase\tcycle\tcount\tsource_base\tunsupported\tdma_error\tall_inside\tstate\n";
        vertices<<"task\tphase\tcycle\tslot\tx\ty\tz\tw\tinvw\tuv\trgba\tclip\n";
        commands<<"task\tphase\tcycle\taddress\tw0\tw1\n";
        reads<<"cycle\ttask\taddress\tbytes\tpurpose\treason\n";
    }
    template<class Tb,class System,class Cpu,class Peek>
    void sample(uint64_t c,unsigned phase,const Tb& tb,const System& s,const Cpu& cpu,Peek peek) {
        auto reg=[&](unsigned i)->uint32_t{return i?cpu.integer_RegFilePlugin_logic_regfile_fpga->asMem_ram[i]:0;};
        if(tb.obs_retire) {
            uint32_t pc=tb.obs_retire_pc;
            while(!stack.empty() && pc==stack.back().ra && reg(2)==stack.back().sp) {
                auto f=stack.back();stack.pop_back();
                calls<<f.serial<<'\t'<<f.task<<'\t'<<phase<<"\treturn\t"<<c<<'\t'<<f.pc<<'\t'<<f.ra<<'\t'<<reg(2);
                for(unsigned r:{10,11,12,13,9})calls<<'\t'<<reg(r);calls<<'\n';
            }
            if(phase && pc==PC_gfx_sp_vertex) {
                ++task;task_phase=phase;
                tasks<<task<<'\t'<<phase<<'\t'<<c<<'\t'<<reg(10)<<'\t'<<reg(11)<<'\t'<<reg(12)<<'\t'<<reg(9)<<'\t'<<reg(1)
                    <<'\t'<<unsigned(cpu.__PVT__FpuCsrPlugin_api_rm)
                    <<'\t'<<value(cpu,peek,c,ADDR_rsp+928,4,"geometry mode")
                    <<'\t'<<value(cpu,peek,c,ADDR_triangle_material_ready,1,"material ready")
                    <<'\t'<<value(cpu,peek,c,ADDR_triangle_vc_elig,4,"material eligibility")
                    <<'\t'<<value(cpu,peek,c,ADDR_rdp+48,4,"other mode")
                    <<'\t'<<value(cpu,peek,c,ADDR_rdp+56,4,"combine mode")<<'\n';
                source_span(cpu,peek,c,reg(9));
            }
            if(phase && pc==0x104ce880u) {
                std::vector<uint8_t> rb,db;
                bool rv=memory(cpu,peek,c,ADDR_rsp,940,rb,"LOAD context rsp");
                bool dv=memory(cpu,peek,c,ADDR_rdp,104,db,"LOAD context rdp");
                uint64_t ro=uint64_t(state_data.tellp());if(rv)state_data.write((const char*)rb.data(),rb.size());
                uint64_t d_off=uint64_t(state_data.tellp());if(dv)state_data.write((const char*)db.data(),db.size());
                states<<task<<'\t'<<phase<<'\t'<<c<<'\t'<<rv<<'\t'<<ro<<'\t'<<dv<<'\t'<<d_off<<'\n';
            }
            if(phase && std::find(call_points.begin(),call_points.end(),pc)!=call_points.end()) {
                Call f{++call_serial,task,phase,pc,reg(1),reg(2),c};stack.push_back(f);
                calls<<f.serial<<'\t'<<task<<'\t'<<phase<<"\tentry\t"<<c<<'\t'<<pc<<'\t'<<f.ra<<'\t'<<f.sp;
                for(unsigned r:{10,11,12,13,9})calls<<'\t'<<reg(r);calls<<'\n';
            }
            if(phase) {
                int fn=function(pc);auto &n=costs[{phase,task,fn}];++n.retired;n.cycles+=c-previous_retire;
                for(const auto& frame:stack)if(frame.pc==PC_gfx_sp_vertex || frame.pc==0x104d2640u) {
                    auto& cc=call_costs[{frame.serial,fn}];++cc.retired;cc.cycles+=c-previous_retire;
                }
            }
            previous_retire=c;
        }
        bool start=s.__PVT__geometry_load__DOT__raw_start;
        if(phase && start && !previous_start) {
            contexts<<task<<'\t'<<phase<<'\t'<<c<<'\t'<<unsigned(s.__PVT__geometry_load__DOT__count)
              <<'\t'<<s.__PVT__geometry_load__DOT__source_base<<'\t'<<s.__PVT__geometry_load__DOT__aspect
              <<'\t'<<s.__PVT__geometry_load__DOT__cx<<'\t'<<s.__PVT__geometry_load__DOT__cy<<'\t'<<s.__PVT__geometry_load__DOT__cz
              <<'\t'<<s.__PVT__geometry_load__DOT__ambient<<'\t'<<s.__PVT__geometry_load__DOT__diffuse
              <<'\t'<<s.__PVT__geometry_load__DOT__scale_u<<'\t'<<s.__PVT__geometry_load__DOT__scale_v
              <<'\t'<<unsigned(s.__PVT__geometry_load__DOT__lit)<<'\t'<<uint64_t(source_data.tellp());
            for(unsigned i=0;i<16;++i)contexts<<'\t'<<s.__PVT__geometry_load__DOT__load__DOT__compute__DOT__g_context_bram__DOT__context_memory[i];
            contexts<<'\n';
            for(unsigned off=0;off<unsigned(s.__PVT__geometry_load__DOT__count)*24;off+=4) {
                uint32_t i=(s.__PVT__geometry_load__DOT__source_base+off)/4;
                const auto& lo=s.paired__DOT__chip0__DOT__mem;const auto& hi=s.paired__DOT__chip1__DOT__mem;
                uint32_t w=(lo.exists(i)?lo.at(i):0)|(uint32_t(hi.exists(i)?hi.at(i):0)<<16);
                char b[4]={char(w),char(w>>8),char(w>>16),char(w>>24)};source_data.write(b,4);
            }
        }
        previous_start=start;
        bool done=s.__PVT__geometry_load__DOT__raw_done;
        if(phase && done && !previous_done) {
            ++raw_completions;
            raw<<task<<'\t'<<phase<<'\t'<<c<<'\t'<<unsigned(s.__PVT__geometry_load__DOT__count)
               <<'\t'<<s.__PVT__geometry_load__DOT__source_base
               <<'\t'<<unsigned(s.__PVT__geometry_load__DOT__unsupported)
               <<'\t'<<unsigned(s.__PVT__geometry_load__DOT__dma_error)
               <<'\t'<<unsigned(s.__PVT__geometry_load__DOT__all_inside)
               <<'\t'<<unsigned(s.__PVT__geometry_load__DOT__load__DOT__state)<<'\n';
            for(unsigned slot=0;slot<s.__PVT__geometry_load__DOT__count;++slot) {
                vertices<<task<<'\t'<<phase<<'\t'<<c<<'\t'<<slot;
                for(unsigned row=0;row<4;++row)vertices<<'\t'<<s.__PVT__geometry_load__DOT__load__DOT__compute__DOT__acc[row*32+slot];
                vertices<<'\t'<<s.__PVT__geometry_load__DOT__load__DOT__compute__DOT__g_context_bram__DOT__context_memory[32+slot]
                    <<'\t'<<s.__PVT__geometry_load__DOT__load__DOT__attributes[32+slot]
                    <<'\t'<<s.__PVT__geometry_load__DOT__load__DOT__attributes[slot]
                    <<'\t'<<s.__PVT__geometry_load__DOT__load__DOT__attributes[64+slot]<<'\n';
            }
        }
        previous_done=done;
    }
    void write(const std::string& out) {
        std::ofstream f(out+"/task-costs.tsv");
        f<<"phase\ttask\tfunction_pc\tfunction\tretired\tcharged_cycles\n";
        for(const auto& item:costs) {
            auto [phase,id,index]=item.first;
            f<<phase<<'\t'<<id<<'\t'<<(index<0?0:symbols[index].pc)<<'\t'<<(index<0?"UNMAPPED":symbols[index].name)
             <<'\t'<<item.second.retired<<'\t'<<item.second.cycles<<'\n';
        }
        std::ofstream cc(out+"/task-call-costs.tsv");
        cc<<"call_serial\tfunction_pc\tfunction\tretired\tcharged_cycles\n";
        for(const auto& item:call_costs) {
            auto [serial,index]=item.first;
            cc<<serial<<'\t'<<(index<0?0:symbols[index].pc)<<'\t'<<(index<0?"UNMAPPED":symbols[index].name)
              <<'\t'<<item.second.retired<<'\t'<<item.second.cycles<<'\n';
        }
        std::ofstream report(out+"/task-observer.json");
        report<<"{\"tasks\":"<<task<<",\"calls\":"<<call_serial<<",\"open_calls\":"<<stack.size()
              <<",\"raw_completions\":"<<raw_completions<<",\"read_failures\":"<<read_failures
              <<",\"protocol_errors\":"<<protocol_errors<<",\"host_clock_edges_added\":0,\"source_vertex_bytes\":24}\n";
    }
};
}
