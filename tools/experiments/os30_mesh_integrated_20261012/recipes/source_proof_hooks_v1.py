"""Passive v4 source-key/probe proof, separate from the measured replay hook."""
from pathlib import Path
import importlib.util
import hashlib

def apply(source,model,cfg):
    parent=Path(__file__).resolve().parent
    if (model/'frozen/src/fpga/common/geometry_mesh_queue.v').exists():
        spec=importlib.util.spec_from_file_location('mesh_hook',parent/'mesh3_hooks_v1.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        source=m.apply(source,model,cfg)
    def once(old,new):
        nonlocal source
        assert source.count(old)==1,(old,source.count(old))
        source=source.replace(old,new)
    once('    unsigned entries=0;', '''    unsigned entries=0;
    std::ofstream source_proof(out+"/source-proof.tsv"), source_windows(out+"/source-windows.tsv");
    source_proof<<"cycle\\tphase\\tpc";
    for(unsigned i=0;i<32;++i)source_proof<<"\\tr"<<i;
    source_proof<<"\\n";
    source_windows<<"cycle\\tphase\\tcommand\\tcommands\\tslot\\tsource\\toffset_count\\n";
    uint32_t source_command=0,source_command_count=0;
''')
    once('            uint32_t pc=tb->obs_retire_pc;lastpc=pc;', '''            uint32_t pc=tb->obs_retire_pc;lastpc=pc;
            if(pc==0x104ce880u || pc==0x104dc900u || pc==0x104dca3au ||
               pc==0x104dcb78u || pc==0x104dcb7cu || pc==0x104cece8u || pc==0x104dce02u) {
                const auto &proof_cpu=tb->vlSymsp->TOP__tb_system__cpu_sys__cpu;
                const auto &proof_regs=proof_cpu.integer_RegFilePlugin_logic_regfile_fpga->asMem_ram;
                source_proof<<c<<"\\t"<<entries<<"\\t"<<pc;
                for(unsigned i=0;i<32;++i)source_proof<<"\\t"<<(i?uint32_t(proof_regs[i]):0);
                source_proof<<"\\n";
                if(pc==0x104dc900u) {source_command=proof_regs[12];source_command_count=proof_regs[13];}
                if(pc==0x104dcb7cu) {
                    require(source_command && source_command_count<=64,"source proof key");
                    const uint32_t h=((source_command>>3)^(source_command>>13)^(source_command_count*33u))&511u;
                    for(unsigned i=0;i<16;++i) {
                        const unsigned slot=(h+i)&511;
                        auto view=cpu_arena_snapshot::capture(proof_cpu,0x111697f0u+slot*8,8,peek);
                        source_windows<<c<<"\\t"<<entries<<"\\t"<<source_command<<"\\t"<<source_command_count
                                      <<"\\t"<<slot<<"\\t"<<le32(view.logical.data())<<"\\t"<<le32(view.logical.data()+4)<<"\\n";
                    }
                }
            }
''')
    return source
