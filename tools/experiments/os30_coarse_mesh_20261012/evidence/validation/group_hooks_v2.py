"""Source-bound AJ03 detached-packet hierarchy and passive accepted-word/lifetime traces."""
import re
from pathlib import Path

def once(s,old,new):
    assert s.count(old)==1,(old,s.count(old))
    return s.replace(old,new)

def apply_display_list(source,symbols,binding):
    """Observe the exact F28 call ABI without clocking or changing guest state.

    Register and return sites come from a separately hash-bound disassembly
    review. No default s1 assumption is permitted. This part applies equally
    to the unchanged capability-off model and the AJ03 candidate.
    """
    assert binding['schema']=='f28-display-list-observer-v1'
    assert symbols['gfx_run_dl']==binding['entry_pc']
    assert symbols['gfx_sp_vertex']==binding['vertex_entry_pc']
    assert symbols['async_return_command']==binding['app_continuation_address']
    register=binding['command_register']; parent_register=binding['parent_command_register']
    assert 1<=register<32 and 1<=parent_register<32
    assert binding['parent_command_addend'] in (-8,0)
    assert binding['return_instructions'] and binding['recursive_return_pcs'] and binding['root_return_pcs']
    hx=lambda x:'0x%08xu'%x
    contains=lambda name,values:'('+'||'.join(name+'=='+hx(v) for v in values)+')'
    returns=''.join('case '+hx(r['pc'])+':dl_return_instruction='+hx(r['bits'])+';break;'
                    for r in binding['return_instructions'])
    init=r'''    struct DlInvocation { uint64_t id,parent;uint32_t command,continuation,ra; };
    std::vector<DlInvocation> dl_stack;
    uint64_t dl_next_id=0,dl_entries=0,dl_returns=0,dl_resumes=0,dl_vtx=0;
    uint64_t dl_parent_read_failures=0,dl_command_read_failures=0,dl_app_read_failures=0;
    bool dl_return_pending=false;
    std::ofstream dl_events(out+"/dl-events.tsv"),vtx_calls(out+"/vtx-calls.tsv");
    require(bool(dl_events)&&bool(vtx_calls),"display-list passive trace files");
    dl_events<<"cycle\tphase\tkind\tinvocation\tparent_invocation\tdepth\tcmd\tcontinuation\tra\tpc\tinstruction\tparent_command\tparent_w0\tparent_w1\tparent_valid\ta0\ta1\n";
    vtx_calls<<"cycle\tphase\ta0\ta1\ta2\ts1\tra\tcommand\tdl_invocation\tdl_depth\tdl_continuation\tapp_continuation\tapp_continuation_valid\tcommand_w0\tcommand_w1\tcommand_valid\n";
    // capture() rejects an ambiguous pending store/refill rather than guess
    // at architectural ordering. A failed observation is explicit in the TSV
    // and summary; it never clocks or changes the simulation to obtain data.
    auto dl_read_words=[&](uint32_t address,uint32_t bytes,uint32_t *words)->bool {
        if((address&3u)||address<0x10000000u||uint64_t(address)+bytes>0x14000000ull)return false;
        try {
            const auto &cpu=tb->vlSymsp->TOP__tb_system__cpu_sys__cpu;
            auto view=cpu_arena_snapshot::capture(cpu,address,bytes,[](uint32_t a){return peek(a);});
            for(uint32_t i=0;i<bytes/4;++i)words[i]=le32(&view.logical[i*4]);
            return true;
        } catch(const std::exception &) { return false; }
    };
'''
    source=once(source,'    unsigned entries=0;','    unsigned entries=0;\n'+init)
    sample=r'''        if(tb->obs_retire) {
            const uint32_t dl_pc=tb->obs_retire_pc;
            auto &rf=*tb->vlSymsp->TOP__tb_system__cpu_sys__cpu.integer_RegFilePlugin_logic_regfile_fpga;
            auto dl_event=[&](unsigned kind,const DlInvocation &f,uint32_t instruction,
                             uint32_t parent_command,uint32_t w0,uint32_t w1,bool valid) {
                dl_events<<c<<"\t"<<entries<<"\t"<<kind<<"\t"<<f.id<<"\t"<<f.parent<<"\t"<<dl_stack.size()
                    <<"\t"<<f.command<<"\t"<<f.continuation<<"\t"<<f.ra<<"\t"<<dl_pc<<"\t"<<instruction
                    <<"\t"<<parent_command<<"\t"<<w0<<"\t"<<w1<<"\t"<<valid
                    <<"\t"<<rf.asMem_ram[10]<<"\t"<<rf.asMem_ram[11]<<"\n";
            };
            if(dl_return_pending) {
                require(!dl_stack.empty()&&dl_pc==dl_stack.back().ra,"display-list return did not resume bound caller");
                dl_event(2,dl_stack.back(),0,0,0,0,false);
                dl_stack.pop_back();dl_return_pending=false;++dl_resumes;
            }
            if(dl_pc==@ENTRY@) {
                const uint32_t command=rf.asMem_ram[10],continuation=rf.asMem_ram[11],ra=rf.asMem_ram[1];
                const uint32_t parent_command=dl_stack.empty()?0:uint32_t(rf.asMem_ram[@PARENT_REGISTER@]+(@PARENT_ADDEND@));
                uint32_t parent_words[2]={0,0};bool valid=false;
                if(dl_stack.empty())require(@ROOT_RETURN@,"unbound root display-list callsite");
                else {
                    require(@RECURSIVE_RETURN@,"unbound recursive display-list callsite");
                    valid=dl_read_words(parent_command,8,parent_words);
                    if(!valid)++dl_parent_read_failures;
                }
                const uint64_t parent=dl_stack.empty()?0:dl_stack.back().id;
                dl_stack.push_back({++dl_next_id,parent,command,continuation,ra});++dl_entries;
                dl_event(1,dl_stack.back(),@ENTRY_INSTRUCTION@,parent_command,parent_words[0],parent_words[1],valid);
            }
            uint32_t dl_return_instruction=0;
            switch(dl_pc) { @RETURN_SWITCH@ default:break; }
            if(dl_return_instruction) {
                require(!dl_stack.empty()&&rf.asMem_ram[1]==dl_stack.back().ra,"display-list return token mismatch");
                dl_event(3,dl_stack.back(),dl_return_instruction,0,0,0,false);
                dl_return_pending=true;++dl_returns;
            }
            if(dl_pc==@VTX@) {
                require(!dl_stack.empty(),"vertex callback outside observed display-list invocation");
                require(@VTX_RETURN@,"unbound display-list vertex callsite");
                const uint32_t command=rf.asMem_ram[@COMMAND_REGISTER@];
                uint32_t app=0,words[2]={0,0};
                const bool app_valid=dl_read_words(@APP_CONTINUATION@,4,&app);
                const bool command_valid=dl_read_words(command,8,words);
                if(!app_valid)++dl_app_read_failures;
                if(!command_valid)++dl_command_read_failures;
                vtx_calls<<c<<"\t"<<entries<<"\t"<<rf.asMem_ram[10]<<"\t"<<rf.asMem_ram[11]<<"\t"<<rf.asMem_ram[12]
                    <<"\t"<<rf.asMem_ram[9]<<"\t"<<rf.asMem_ram[1]<<"\t"<<command
                    <<"\t"<<dl_stack.back().id<<"\t"<<dl_stack.size()<<"\t"<<dl_stack.back().continuation
                    <<"\t"<<app<<"\t"<<app_valid<<"\t"<<words[0]<<"\t"<<words[1]<<"\t"<<command_valid<<"\n";
                ++dl_vtx;
            }
        }
'''
    replacements={'@ENTRY@':hx(binding['entry_pc']),'@ENTRY_INSTRUCTION@':hx(binding['entry_instruction']),
                  '@VTX@':hx(binding['vertex_entry_pc']),'@COMMAND_REGISTER@':str(register),
                  '@PARENT_REGISTER@':str(parent_register),'@RETURN_SWITCH@':returns,
                  '@PARENT_ADDEND@':str(binding['parent_command_addend']),
                  '@APP_CONTINUATION@':hx(binding['app_continuation_address']),
                  '@ROOT_RETURN@':contains('ra',binding['root_return_pcs']),
                  '@RECURSIVE_RETURN@':contains('ra',binding['recursive_return_pcs']),
                  '@VTX_RETURN@':contains('rf.asMem_ram[1]',binding['vertex_return_pcs'])}
    for old,new in replacements.items(): sample=sample.replace(old,new)
    assert '@' not in sample
    marker='        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;'
    source=once(source,marker,sample+marker)
    ending=r'''    require(dl_stack.empty()&&!dl_return_pending,"display-list invocation live at final boundary");
    std::ofstream dl_summary(out+"/dl-observer.json");
    dl_summary<<"{\"schema\":\"f28-display-list-observer-v1\",\"entries\":"<<dl_entries
        <<",\"return_retirements\":"<<dl_returns<<",\"return_resumes\":"<<dl_resumes<<",\"vtx_calls\":"<<dl_vtx
        <<",\"parent_read_failures\":"<<dl_parent_read_failures<<",\"command_read_failures\":"<<dl_command_read_failures
        <<",\"app_read_failures\":"<<dl_app_read_failures<<",\"extra_fabric_edges\":0}\n";
'''
    source=once(source,'    tb->final();delete tb;return status;',ending+'    tb->final();delete tb;return status;')
    return source

def apply(source,model,cfg):
    header=(model/'obj/Vtb_system_tb_system.h').read_text()
    gpu=(model/'frozen/src/fpga/common/gpu_core.v').read_text()
    assert re.search(r'gpu_mesh_job_front\s*#\(\.ADDR_BITS\(RING_ADDR_BITS\),\.QUEUE_BITS\(9\)\)',gpu)
    observer=Path(__file__).parent/'observer-v1/gpu_observer.hpp'
    source=once(source,'#include "gpu_observer.hpp"','#include "'+str(observer.resolve())+'"')
    def field(name):
        g='geometry_load.async_mesh.'
        aliases={
            g+'mesh.packet_snapshot_valid':g+'packet_snapshot_valid',
            g+'mesh.packet_store_rdata':g+'mesh.engine.ctrl.list_q',
            g+'mesh.engine.ctrl.live_count':g+'live_count',
            g+'mesh.engine.ctrl.check_indices':g+'mesh.engine.check_indices',
        }
        name=aliases.get(name,name)
        if name==g+'job_take':
            return '('+field(g+'group_take')+'||('+field(g+'member_take')+'&&!'+field(g+'group_active')+'))'
        if name==g+'descriptor_pair_take':
            return '('+field(g+'group_active')+'&&'+field(g+'group_state')+'==2&&'+field(g+'mesh.descriptor_owner')+'&&'+field(g+'mesh.fetch_command_valid')+'&&!'+field('mesh_consumer_reset')+')'
        if name in (g+'descriptor_w0',g+'descriptor_w1'):
            return field(g+'mesh.fetch_'+name[-2:])
        for item,register in {'vertex':'group_vertex','commands':'command_source','cpu_vertex':'cpu_vertex','cpu_commands':'cpu_commands'}.items():
            if name==g+'member_'+item:
                return '('+field(g+'group_active')+'?'+field(g+register)+':'+field('mesh_job_'+item)+')'
        if name==g+'matrix_data':
            return '('+field(g+'matrix_legacy')+'?'+field('geo_mmio_data')+':'+field(g+'context_apply_data')+')'
        if name.startswith('member_common_'):
            name=name[len('member_common_'):]
            register={'flags':'flags_rgb'}.get(name,name)
            return '('+field(g+'group_active')+'?'+field(g+register)+':'+field('mesh_job_'+name)+')'
        if name==g+'mesh.engine.ctrl.result_keep':
            return '('+field(g+'mesh.engine.result_reject_class')+'==0)'
        if name==g+'mesh.packet_store_wdata':
            # This combinational wire is optimized into local C++ temporaries.
            # Reconstruct from actual source registers, then independently record
            # the physical RAM cell after every actual enabled write below.
            packet=g+'mesh.packet.'
            values=[]
            for item in ('x','y','w','u','v'):
                values += ['('+field(packet+item)+'&65535u)', '('+field(packet+item)+'>>16)']
            values += [field(packet+'rgb'),'('+field(packet+'depth')+'&65535u)','('+field(packet+'depth')+'>>16)']
            return '('+''.join(field(packet+'half_index')+'=='+str(i)+'?'+value+':' for i,value in enumerate(values))+'0u)'
        direct='__PVT__'+name.replace('.','__DOT__')
        assert re.search(r'\b'+re.escape(direct)+r'\b',header),name
        return 's.'+direct
    g='geometry_load.async_mesh.';f='gpu.g_async_mesh.front.'
    old='__PVT__geometry_load__DOT__load__DOT__'
    new='__PVT__geometry_load__DOT__async_mesh__DOT__mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__'
    assert source.count(old)==4
    source=source.replace(old,new)
    for m in re.finditer(r's\.('+new+r'\w+)',source):assert m.group(1) in header,m.group(1)
    source=once(source,
        '    s.__PVT__gpu__DOT__ring_rd_addr=n("ring_word_pointer");s.__PVT__gpu__DOT__ring_rdptr=n("ring_word_pointer");',
        '    require('+field(f+'state')+'==0,"AJ03 initializer state");\n'
        '    require('+field('gpu.command_wrptr')+'=='+field('gpu.ring_rdptr')+',"AJ03 output initializer not empty");\n'
        '    '+field('gpu.upload_rdptr')+'=n("ring_word_pointer");\n')
    idle='!'+field(g+'active')+'&&!'+field(g+'pending')+'&&!'+field(g+'aborting')+'&&!'+field(g+'repair_pending')+'&&!'+field(g+'output_pending')+'&&!'+field(g+'group_active')
    source=once(source,'            require(!tb->obs_gpu_busy,"GPU not drained at subsequent gfx entry");',
        '            require(!tb->obs_gpu_busy,"GPU not drained at subsequent gfx entry");\n'
        '            require('+idle+',"AJ03 unpublished job at frame boundary");\n'
        '            require(!'+field(g+'protocol_error')+'&&!'+field('mesh_bridge_fault')+',"AJ03 protocol fault at frame boundary");')
    fields={name:g+name for name in (
        'active','pending','legacy','aborting','protocol_error','completed','success',
        'output_pending','snapshot_detached','output_serial',
        'lease_pending','lease_granted','lease_release_pending','lease_target','lease_ticket','granted_ticket',
        'context_open','context_next',
        'repair_pending','repair_end_pending','repair_token','repair_ack','epoch',
        'descriptor_serial','completed_serial','source_base','vtx_w0','command_source','configuration',
        'flags_rgb','uofs','vofs','cpu_vertex','cpu_commands','alpha','owned_mask','used_mask',
        'live_count','checked_count','clip_count','cull_count','stream_busy','stream_ready','stream_raw_ready','fallback',
        'packet_done','packet_error','fetch_error','ctx_take','old_start','matrix_we','matrix_addr','matrix_data',
        'group_take','group_active','group_state','group_count','group_index','group_first_seq','group_pointer',
        'descriptor_start','descriptor_pair_take','descriptor_pair_index','descriptor_w0','descriptor_w1',
        'context_apply','context_apply_index','context_apply_data','restore_apply','restore_index')}
    fields.update({'front_context_pending':f+'context_pending','front_state':f+'state','front_fault':'mesh_bridge_fault','gpu_state':'gpu.state',
        'raw_state':g+'mesh.engine.frontend.raw.state','cull_owner':g+'mesh.engine.frontend.cull_owner',
        'ctx_index':f+'context_index','ctx_data':'gpu.g_async_mesh.data','job_done':'mesh_job_done',
        'view_active':'alias_shim.active','raw_start':'geometry_load.raw_start'})
    fields.update({name:g+'mesh.'+name for name in (
        'packet_snapshot_valid','packet_store_we','packet_store_addr',
        'packet_store_wdata','packet_store_rdata','packet_raw_read_active')})
    fields.update({'packet_'+name:g+'mesh.packet.'+name for name in ('state','mask','count')})
    fields.update({'survivor_'+name:g+'mesh.engine.ctrl.'+name for name in (
        'accepted_result','result_keep','live_count','check_indices')})
    # Named channel words are held by the front until its job handshake.
    fields.update({'job_take':g+'member_take','front_job_take':g+'job_take'})
    fields.update({'job_'+n:g+'member_'+n for n in ('seq','vertex','vtx_w0','commands','control','cpu_vertex','cpu_commands')})
    # Common material stays in wrapper registers across a group. A single JOB
    # takes these directly from its front handshake on the same pre-edge.
    fields.update({'job_'+n:'member_common_'+n for n in ('flags','uofs','vofs','alpha')})
    fields.update({'group_request_'+n:'mesh_job_'+n for n in ('seq','vertex','vtx_w0','flags','uofs','vofs','alpha')})
    expressions=[field(v) for v in fields.values()]
    queue=field(f+'queue');emit=field(f+'emit')
    init='''    std::ofstream async_events(out+"/async-events.tsv"), async_words(out+"/worker-commands.bin",std::ios::binary), async_accepts(out+"/worker-words.tsv"), async_sources(out+"/job-sources.tsv");
    require(bool(async_events)&&bool(async_words)&&bool(async_accepts),"AJ03 trace files");
    async_accepts<<"cycle\\tphase\\toutput_pointer\\tword\\tfront_state\\tserial\\trepair\\n";
    async_sources<<"cycle\\tphase\\tserial\\tkind\\taddress\\tindex\\tword\\n";
    std::ofstream async_stores(out+"/snapshot-stores.tsv");
    async_stores<<"cycle\\tphase\\twriter\\taddress\\tword\\n";
    std::ofstream group_descriptors(out+"/group-descriptors.tsv"), context_stores(out+"/context-stores.tsv");
    group_descriptors<<"cycle\\tphase\\tfirstseq\\taddress\\tcount\\tindex\\tword\\n";
    context_stores<<"cycle\\tphase\\tindex\\tword\\n";
    std::string async_last;
    std::map<unsigned,std::map<unsigned,uint64_t>> async_overlap;
'''
    init+='    async_events<<"cycle\\tphase\\t'+r'\t'.join(fields)+'\\n";\n'
    source=once(source,'    unsigned entries=0;','    unsigned entries=0;\n'+init)
    pre='''        // Pure passive AJ03 sampling immediately before this active edge.
        std::ostringstream async_row;
'''
    pre+='        async_row'+''.join(('<<"\\t"' if i else '')+'<<uint32_t('+x+')'for i,x in enumerate(expressions))+';\n'
    pre+='        if(async_row.str()!=async_last) {async_events<<c<<"\\t"<<entries<<"\\t"<<async_row.str()<<"\\n";async_last=async_row.str();}\n'
    pre+='        if('+field(g+'member_take')+') {\n'
    pre+='            for(unsigned kind=0;kind<2;++kind) {const uint32_t a=kind?'+field(g+'member_commands')+':'+field(g+'member_vertex')+';const unsigned count=kind?('+field(g+'member_control')+'&127)*2:(('+field(g+'member_vtx_w0')+'>>12)&63)*6;\n'
    pre+='                require((a&3)==0&&a+count*4<=0x02000000,"AJ03 source bounds");\n'
    pre+='                for(unsigned i=0;i<count;++i) {const uint32_t at=(a/4)+i;auto &lo=s.paired__DOT__chip0__DOT__mem;auto &hi=s.paired__DOT__chip1__DOT__mem;const uint32_t w=(lo.exists(at)?lo.at(at):0)|uint32_t(hi.exists(at)?hi.at(at):0)<<16;\n'
    pre+='                    async_sources<<c<<"\\t"<<entries<<"\\t"<<'+field(g+'member_seq')+'<<"\\t"<<kind<<"\\t"<<a<<"\\t"<<i<<"\\t"<<w<<"\\n";\n                }\n            }\n        }\n'
    pre+='        if('+field(g+'group_take')+') {\n'
    pre+='            const uint32_t a='+field('mesh_job_vertex')+', count='+field('mesh_job_vtx_w0')+';\n'
    pre+='            require(count>=1&&count<=32&&(a&7)==0&&uint64_t(a)+count*24<=0x01000000,"AJ03 descriptor source bounds");\n'
    pre+='            for(unsigned i=0;i<count*6;++i) {const uint32_t at=(a/4)+i;auto &lo=s.paired__DOT__chip0__DOT__mem;auto &hi=s.paired__DOT__chip1__DOT__mem;const uint32_t w=(lo.exists(at)?lo.at(at):0)|uint32_t(hi.exists(at)?hi.at(at):0)<<16;\n'
    pre+='                group_descriptors<<c<<"\\t"<<entries<<"\\t"<<'+field('mesh_job_seq')+'<<"\\t"<<a<<"\\t"<<count<<"\\t"<<i<<"\\t"<<w<<"\\n";}\n        }\n'
    pre+='        const bool context_store='+field(g+'ctx_take')+';\n'
    pre+='        const unsigned context_store_index='+field(f+'context_index')+';\n'
    pre+='        const bool async_emit='+emit+';\n' 
    pre+='        const unsigned async_pointer='+field('gpu.command_wrptr')+';\n'
    pre+='        const unsigned async_state='+field(f+'state')+',async_serial='+'('+field(g+'output_pending')+'?'+field(g+'output_serial')+':'+field(g+'descriptor_serial')+')'+',async_repair='+field(g+'repair_pending')+';\n'
    pre+='        if(start!=UINT64_MAX) {\n'
    pre+='            const unsigned mask=(tb->obs_retire?1:0)|(tb->obs_gpu_busy?2:0)|('+field(g+'active')+'?4:0)|('+field(g+'mesh.engine.frontend.raw.state')+'?8:0)|('+field(g+'mesh.engine.frontend.cull_owner')+'?16:0)|(async_emit?32:0)|('+field(g+'repair_pending')+'?64:0)|('+field('gpu.state')+'==6?128:0)|('+field(f+'used')+'==511?256:0)|('+field(g+'lease_granted')+'?512:0)|('+field(g+'lease_pending')+'?1024:0)|('+field(g+'snapshot_detached')+'?2048:0)|('+field(g+'output_pending')+'&&'+field(g+'lease_granted')+'?4096:0);\n'
    pre+='            ++async_overlap[entries][mask];\n        }\n'
    pre+='        const bool snapshot_packet_write='+field(g+'mesh.packet_store_we')+';\n'
    pre+='        const bool snapshot_survivor_write='+field(g+'mesh.engine.ctrl.accepted_result')+'&&'+field(g+'mesh.engine.ctrl.result_keep')+';\n'
    pre+='        require(!(snapshot_packet_write&&snapshot_survivor_write),"snapshot double writer");\n'
    pre+='        const unsigned snapshot_writer=snapshot_survivor_write?2:(snapshot_packet_write?1:0);\n'
    pre+='        const unsigned snapshot_address=snapshot_survivor_write?('+field(g+'live_count')+'&63u):'+field(g+'mesh.packet_store_addr')+';\n'
    marker='        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;'
    post='\n        if(async_emit) {const uint32_t w='+queue+'[async_pointer&511];put32(async_words,w);async_accepts<<c<<"\\t"<<entries<<"\\t"<<async_pointer<<"\\t"<<w<<"\\t"<<async_state<<"\\t"<<async_serial<<"\\t"<<async_repair<<"\\n";}\n'
    post+='        if(snapshot_writer) {require(snapshot_address<512,"snapshot RAM bounds");async_stores<<c<<"\\t"<<entries<<"\\t"<<snapshot_writer<<"\\t"<<snapshot_address<<"\\t"<<'+field(g+'mesh.engine.ctrl.survivors')+'[snapshot_address]<<"\\n";}\n'
    post+='        if(context_store) {require(context_store_index<28,"context shadow bounds");context_stores<<c<<"\\t"<<entries<<"\\t"<<context_store_index<<"\\t"<<'+field(g+'mesh.engine.frontend.raw.attributes')+'[96+context_store_index]<<"\\n";}\n'
    source=once(source,marker,pre+marker+post)
    source=once(source,'    tb->final();delete tb;return status;',
        '''    std::ofstream overlap(out+"/async-overlap.tsv");overlap<<"phase\\tmask\\tcycles\\n";
    for(const auto &p:async_overlap)for(const auto &m:p.second)overlap<<p.first<<"\\t"<<m.first<<"\\t"<<m.second<<"\\n";
    tb->final();delete tb;return status;''')
    if '#include <sstream>'not in source:source='#include <sstream>\n'+source
    return source
