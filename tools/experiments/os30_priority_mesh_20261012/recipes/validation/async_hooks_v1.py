"""Source-bound AJ02 hierarchy adaptation and passive accepted-word/lifetime traces."""
import re
from pathlib import Path

def once(s,old,new):
    assert s.count(old)==1,(old,s.count(old))
    return s.replace(old,new)

def apply(source,model,cfg):
    header=(model/'obj/Vtb_system_tb_system.h').read_text()
    gpu=(model/'frozen/src/fpga/common/gpu_core.v').read_text()
    assert re.search(r'gpu_mesh_job_front\s*#\(\.ADDR_BITS\(RING_ADDR_BITS\),\.QUEUE_BITS\(9\)\)',gpu)
    observer=Path(__file__).parent/'observer-v1/gpu_observer.hpp'
    source=once(source,'#include "gpu_observer.hpp"','#include "'+str(observer.resolve())+'"')
    def field(name):
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
        '    require('+field(f+'state')+'==0,"AJ02 initializer state");\n'
        '    require('+field('gpu.command_wrptr')+'=='+field('gpu.ring_rdptr')+',"AJ02 output initializer not empty");\n'
        '    '+field('gpu.upload_rdptr')+'=n("ring_word_pointer");\n')
    idle='!'+field(g+'active')+'&&!'+field(g+'pending')+'&&!'+field(g+'aborting')+'&&!'+field(g+'repair_pending')
    source=once(source,'            require(!tb->obs_gpu_busy,"GPU not drained at subsequent gfx entry");',
        '            require(!tb->obs_gpu_busy,"GPU not drained at subsequent gfx entry");\n'
        '            require('+idle+',"AJ02 unpublished job at frame boundary");\n'
        '            require(!'+field(g+'protocol_error')+'&&!'+field('mesh_bridge_fault')+',"AJ02 protocol fault at frame boundary");')
    fields={name:g+name for name in (
        'active','pending','legacy','aborting','protocol_error','completed','success',
        'lease_pending','lease_granted','lease_release_pending','lease_target','lease_ticket','granted_ticket',
        'context_open','context_next',
        'repair_pending','repair_end_pending','repair_token','repair_ack','epoch',
        'descriptor_serial','completed_serial','source_base','vtx_w0','command_source','configuration',
        'flags_rgb','uofs','vofs','cpu_vertex','cpu_commands','alpha','owned_mask','used_mask',
        'live_count','checked_count','clip_count','cull_count','stream_busy','stream_ready','fallback',
        'packet_done','packet_error','fetch_error','job_take','ctx_take','old_start','matrix_we','matrix_addr','matrix_data')}
    fields.update({'front_context_pending':f+'context_pending','front_state':f+'state','front_fault':'mesh_bridge_fault','gpu_state':'gpu.state',
        'raw_state':g+'mesh.engine.frontend.raw.state','cull_owner':g+'mesh.engine.frontend.cull_owner',
        'ctx_index':f+'context_index','ctx_data':'gpu.g_async_mesh.data','job_done':'mesh_job_done',
        'view_active':'alias_shim.active','raw_start':'geometry_load.raw_start'})
    # Named channel words are held by the front until its job handshake.
    fields.update({'job_'+n:'mesh_job_'+n for n in ('seq','vertex','vtx_w0','commands','control','flags','uofs','vofs','cpu_vertex','cpu_commands','alpha')})
    expressions=[field(v) for v in fields.values()]
    queue=field(f+'queue');emit=field(f+'emit')
    init='''    std::ofstream async_events(out+"/async-events.tsv"), async_words(out+"/worker-commands.bin",std::ios::binary), async_accepts(out+"/worker-words.tsv"), async_sources(out+"/job-sources.tsv");
    require(bool(async_events)&&bool(async_words)&&bool(async_accepts),"AJ02 trace files");
    async_accepts<<"cycle\\tphase\\toutput_pointer\\tword\\tfront_state\\tserial\\trepair\\n";
    async_sources<<"cycle\\tphase\\tserial\\tkind\\taddress\\tindex\\tword\\n";
    std::string async_last;
    std::map<unsigned,std::map<unsigned,uint64_t>> async_overlap;
'''
    init+='    async_events<<"cycle\\tphase\\t'+r'\t'.join(fields)+'\\n";\n'
    source=once(source,'    unsigned entries=0;','    unsigned entries=0;\n'+init)
    pre='''        // Pure passive AJ02 sampling immediately before this active edge.
        std::ostringstream async_row;
'''
    pre+='        async_row'+''.join(('<<"\\t"' if i else '')+'<<uint32_t('+x+')'for i,x in enumerate(expressions))+';\n'
    pre+='        if(async_row.str()!=async_last) {async_events<<c<<"\\t"<<entries<<"\\t"<<async_row.str()<<"\\n";async_last=async_row.str();}\n'
    pre+='        if('+field(g+'job_take')+') {\n'
    pre+='            for(unsigned kind=0;kind<2;++kind) {const uint32_t a=kind?'+field('mesh_job_commands')+':'+field('mesh_job_vertex')+';const unsigned count=kind?('+field('mesh_job_control')+'&127)*2:(('+field('mesh_job_vtx_w0')+'>>12)&63)*6;\n'
    pre+='                require((a&3)==0&&a+count*4<=0x02000000,"AJ02 source bounds");\n'
    pre+='                for(unsigned i=0;i<count;++i) {const uint32_t at=(a/4)+i;auto &lo=s.paired__DOT__chip0__DOT__mem;auto &hi=s.paired__DOT__chip1__DOT__mem;const uint32_t w=(lo.exists(at)?lo.at(at):0)|uint32_t(hi.exists(at)?hi.at(at):0)<<16;\n'
    pre+='                    async_sources<<c<<"\\t"<<entries<<"\\t"<<'+field('mesh_job_seq')+'<<"\\t"<<kind<<"\\t"<<a<<"\\t"<<i<<"\\t"<<w<<"\\n";\n                }\n            }\n        }\n'
    pre+='        const bool async_emit='+emit+';\n' 
    pre+='        const unsigned async_pointer='+field('gpu.command_wrptr')+';\n'
    pre+='        const unsigned async_state='+field(f+'state')+',async_serial='+field(g+'descriptor_serial')+',async_repair='+field(g+'repair_pending')+';\n'
    pre+='        if(start!=UINT64_MAX) {\n'
    pre+='            const unsigned mask=(tb->obs_retire?1:0)|(tb->obs_gpu_busy?2:0)|('+field(g+'active')+'?4:0)|('+field(g+'mesh.engine.frontend.raw.state')+'?8:0)|('+field(g+'mesh.engine.frontend.cull_owner')+'?16:0)|(async_emit?32:0)|('+field(g+'repair_pending')+'?64:0)|('+field('gpu.state')+'==6?128:0)|('+field(f+'used')+'==511?256:0)|('+field(g+'lease_granted')+'?512:0)|('+field(g+'lease_pending')+'?1024:0);\n'
    pre+='            ++async_overlap[entries][mask];\n        }\n'
    marker='        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;'
    post='\n        if(async_emit) {const uint32_t w='+queue+'[async_pointer&511];put32(async_words,w);async_accepts<<c<<"\\t"<<entries<<"\\t"<<async_pointer<<"\\t"<<w<<"\\t"<<async_state<<"\\t"<<async_serial<<"\\t"<<async_repair<<"\\n";}\n'
    source=once(source,marker,pre+marker+post)
    source=once(source,'    tb->final();delete tb;return status;',
        '''    std::ofstream overlap(out+"/async-overlap.tsv");overlap<<"phase\\tmask\\tcycles\\n";
    for(const auto &p:async_overlap)for(const auto &m:p.second)overlap<<p.first<<"\\t"<<m.first<<"\\t"<<m.second<<"\\n";
    tb->final();delete tb;return status;''')
    if '#include <sstream>'not in source:source='#include <sstream>\n'+source
    return source
