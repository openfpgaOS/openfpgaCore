"""MTF2 native replay adaptation: source-bound hierarchy and passive accepted output; multi-packet private FIFO and source-bound merge depth."""
import re

def once(s,old,new):
    assert s.count(old)==1,(old,s.count(old))
    return s.replace(old,new)

def apply(source,model,cfg):
    header=(model/'obj/Vtb_system_tb_system.h').read_text()
    gpu_source=(model/'frozen/src/fpga/common/gpu_core.v').read_text()
    parameter=re.search(r'gpu_mesh_bridge\s*#\(\.ADDR_BITS\(RING_ADDR_BITS\),\.QUEUE_BITS\((4|7)\)',gpu_source)
    assert parameter,'merge depth must be the explicitly bound 16 or 128 word variant'
    merge_depth=1<<int(parameter.group(1))
    queue_source=(model/'frozen/src/fpga/common/geometry_mesh_queue.v').read_text()
    wrapper_source=(model/'frozen/src/fpga/common/phase10_load_alias.v').read_text()
    assert re.search(r'parameter\s+(?:integer\s+)?QUEUE_BITS\s*=\s*9\b',queue_source)
    queue_parameter=re.search(r'geometry_mesh_queue\s*#\(\s*\.QUEUE_BITS\((9|11|14)\)\s*\)\s+packet_queue',wrapper_source)
    if queue_parameter: private_depth=1<<int(queue_parameter.group(1))
    else:
        assert re.search(r'geometry_mesh_queue\s+packet_queue\s*\(',wrapper_source)
        private_depth=512
    source=once(source, '#include "gpu_observer.hpp"', '#include "/home/alberto/Repos/openfpgaCore/docs/measurements/os30-psram32-20261009/mesh-integrated-v1/validation/observer-v1/gpu_observer.hpp"')
    def field(name):
        direct='__PVT__'+name.replace('.','__DOT__')
        if re.search(r'\b'+re.escape(direct)+r'\b',header):return 's.'+direct
        # Exact combinational expressions from the bound bridge/wrapper; these
        # wires were made function-local by Verilator's optimizer.
        computed={
            'mesh_marker_valid':lambda:'('+field('gpu.g_mesh_front.bridge.running')+'&&'+field('gpu.g_mesh_front.bridge.state')+'==4)',
            'mesh_word_ready':lambda:'('+field('gpu.g_mesh_front.bridge.running')+'&&'+field('gpu.g_mesh_front.bridge.state')+'==5&&'+field('gpu.g_mesh_front.bridge.used')+'<'+str(merge_depth-1)+')',
            'geometry_load.queue_reserve':lambda:'('+field('geometry_load.arm_pending')+'&&'+field('geometry_load.mesh.packet.state')+'==10&&!'+field('geometry_load.mesh.publication_started')+'&&'+field('geometry_load.queue_reserve_ready')+'&&!'+field('geometry_load.consumer_cancel')+')',
            'geometry_load.raw_copy_start':lambda:field('geometry_load.mesh.publish_take'),
            'geometry_load.queue_busy':lambda:'('+field('geometry_load.packet_queue.fill_active')+'||'+field('geometry_load.packet_queue.state')+'!=0||'+field('geometry_load.packet_queue.read_ptr')+'!='+field('geometry_load.packet_queue.committed_ptr')+')',
        }
        if name in computed:return computed[name]()
        aliases={
            'geometry_load.queue_busy':['geometry_load.queue_busy'],
            'geometry_load.packet_queue.serial':['geometry_load.packet_queue.serial','geometry_load.queue_serial'],
        }
        candidates=aliases.get(name,[name])
        actual=['__PVT__'+n.replace('.','__DOT__') for n in candidates]
        name=next((n for n in actual if re.search(r'\b'+re.escape(n)+r'\b',header)),actual[0])
        assert re.search(r'\b'+re.escape(name)+r'\b',header),name
        return 's.'+name
    old='__PVT__geometry_load__DOT__load__DOT__'
    new='__PVT__geometry_load__DOT__mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__'
    assert source.count(old)==4,source.count(old)
    source=source.replace(old,new)
    # Alias expected-data construction still reads exact source storage, not DUT responses.
    for m in re.finditer(r's\.(__PVT__geometry_load__DOT__mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__\w+)',source):
        assert m.group(1) in header,m.group(1)
    source=once(source,
        '    s.__PVT__gpu__DOT__ring_rd_addr=n("ring_word_pointer");s.__PVT__gpu__DOT__ring_rdptr=n("ring_word_pointer");',
        '    require('+field('gpu.g_mesh_front.bridge.state')+'==0,"mesh bridge initializer state");\n'
        '    require('+field('gpu.command_wrptr')+'=='+field('gpu.ring_rdptr')+',"mesh output initializer not empty");\n'
        '    '+field('gpu.upload_rdptr')+'=n("ring_word_pointer");\n'
        '    // Preserve drained output pointers: SDK pointer belongs only to ingress.\n')
    idle='!'+field('geometry_load.active')+'&&!'+field('geometry_load.pending')+'&&!'+field('geometry_load.arm_pending')+'&&!'+field('geometry_load.queue_busy')
    source=once(source,'            require(!tb->obs_gpu_busy,"GPU not drained at subsequent gfx entry");',
        '            require(!tb->obs_gpu_busy,"GPU not drained at subsequent gfx entry");\n'
        '            require('+idle+',"unpublished mesh owner at subsequent gfx entry");\n'
        '            require(!'+field('geometry_load.protocol_error')+',"mesh protocol error at boundary");')
    # Rows capture the ownership state whenever it changes; cycle-only overlap data
    # is accumulated independently, so trace compression never changes denominators.
    fields={
      'owner':'geometry_load.active','legacy':'geometry_load.legacy','pending':'geometry_load.pending',
      'serial':'geometry_load.serial','descriptor_serial':'geometry_load.descriptor_serial',
      'preflight':'geometry_load.preflight','fallback':'geometry_load.fallback','raw_seen':'geometry_load.raw_seen',
      'raw_success':'geometry_load.success','protocol_error':'geometry_load.protocol_error',
      'reason':'geometry_load.mesh.engine_reason','live':'geometry_load.live_count',
      'checked':'geometry_load.checked_count','clip':'geometry_load.clip_count','cull':'geometry_load.cull_count',
      'used':'geometry_load.used_mask','owned':'geometry_load.owned_mask',
      'validate_pending':'geometry_load.validation_pending','validate_start':'geometry_load.validate_start',
      'validation_started':'geometry_load.mesh.validation_started','publication_started':'geometry_load.mesh.publication_started',
      'packet_state':'geometry_load.mesh.packet.state','packet_complete':'geometry_load.mesh.packet_complete',
      'packet_error':'geometry_load.packet_error','packet_done':'mesh_packet_done',
      'flags':'geometry_load.flags_rgb','uofs':'geometry_load.uofs','vofs':'geometry_load.vofs',
      'marker_valid':'mesh_marker_valid','marker_ready':'mesh_marker_ready','marker_error':'mesh_marker_error',
      'marker_serial':'mesh_marker_serial','marker_flags':'mesh_marker_flags','marker_uofs':'mesh_marker_uofs','marker_vofs':'mesh_marker_vofs',
      'raw_state':'geometry_load.mesh.engine.frontend.raw.state',
      'cull_owner':'geometry_load.mesh.engine.frontend.cull_owner',
      'bridge_state':'gpu.g_mesh_front.bridge.state','gpu_state':'gpu.state',
      'arm_pending':'geometry_load.arm_pending','queue_reserve':'geometry_load.queue_reserve',
      'copy_start':'geometry_load.raw_copy_start','copy_done':'geometry_load.raw_copy_done',
      'queue_state':'geometry_load.packet_queue.state','queue_serial':'geometry_load.packet_queue.serial',
      'queue_flags':'geometry_load.packet_queue.flags','queue_uofs':'geometry_load.packet_queue.uofs','queue_vofs':'geometry_load.packet_queue.vofs',
      'queue_count':'geometry_load.packet_queue.count','queue_writes':'geometry_load.packet_queue.write_count','queue_reads':'geometry_load.packet_queue.read_count',
      'producer_valid':'geometry_load.producer_valid','producer_ready':'geometry_load.producer_ready',
      'fill_active':'geometry_load.packet_queue.fill_active','fill_serial':'geometry_load.packet_queue.fill_serial',
      'fill_flags':'geometry_load.packet_queue.fill_flags','fill_uofs':'geometry_load.packet_queue.fill_uofs','fill_vofs':'geometry_load.packet_queue.fill_vofs',
      'fill_count':'geometry_load.packet_queue.fill_count','fill_header':'geometry_load.packet_queue.fill_header',
      'write_ptr':'geometry_load.packet_queue.write_ptr','committed_ptr':'geometry_load.packet_queue.committed_ptr',
      'read_ptr':'geometry_load.packet_queue.read_ptr','reserved_end':'geometry_load.packet_queue.reserved_end',
      'queue_busy':'geometry_load.queue_busy',
      'queue_commit':'geometry_load.packet_queue.commit','reserve_take':'geometry_load.packet_queue.reserve_take',

    }
    expressions=[field(v) for v in fields.values()]
    queue_field=field('gpu.g_mesh_front.bridge.queue').removeprefix('s.')
    size=re.search(r'VlUnpacked<[^;\n]+,\s*(\d+)>\s+'+re.escape(queue_field)+r';',header)
    assert size and int(size.group(1))==merge_depth,'generated merge storage must match bound source parameter'
    merge_mask=int(size.group(1))-1
    private_field=field('geometry_load.packet_queue.queue').removeprefix('s.')
    private_size=re.search(r'VlUnpacked<[^;\n]+,\s*(\d+)>\s+'+re.escape(private_field)+r';',header)
    assert private_size and int(private_size.group(1))==private_depth,'private FIFO must match bound source parameter'
    private_mask=private_depth-1
    for pointer in ('read_ptr','write_ptr','committed_ptr','reserved_end'):
        pointer_field=field('geometry_load.packet_queue.'+pointer).removeprefix('s.')
        assert re.search(r'[CSIQ]Data/\*'+str(private_depth.bit_length()-1)+r':0\*/\s+'+re.escape(pointer_field)+r';',header),'generated FIFO pointer width must include wrap bit'
    traceinit='''    std::ofstream mesh_events(out+"/mesh-events.tsv"), mesh_words(out+"/worker-commands.bin",std::ios::binary), mesh_accepts(out+"/worker-words.tsv"), mesh_copy(out+"/mesh-copy-words.tsv");
    require(bool(mesh_events)&&bool(mesh_words)&&bool(mesh_accepts)&&bool(mesh_copy),"mesh trace files");
    mesh_accepts<<"cycle\\tphase\\toutput_pointer\\tword\\n";
    mesh_copy<<"cycle\\tphase\\tserial\\tindex\\tword\\n";
    std::string mesh_last;
    std::map<unsigned,std::map<unsigned,uint64_t>> mesh_overlap;
'''
    traceinit+='    mesh_events<<"cycle\\tphase\\t'+r'\t'.join(fields)+'\\n";\n'
    source=once(source,'    unsigned entries=0;', '    unsigned entries=0;\n'+traceinit)
    pre='''        // Read-only MTF2 observation before this exact active edge.
        std::ostringstream mesh_row;
'''
    pre+='        mesh_row'+''.join(('<<"\\t"' if i else '')+'<<uint32_t('+x+')' for i,x in enumerate(expressions))+';\n'
    pre+='''        if(mesh_row.str()!=mesh_last) {mesh_events<<c<<"\\t"<<entries<<"\\t"<<mesh_row.str()<<"\\n";mesh_last=mesh_row.str();}
'''
    pre+='        const bool mesh_emit='+field('gpu.g_mesh_front.bridge.emit')+';\n'
    pre+='        const unsigned mesh_emit_pointer='+field('gpu.command_wrptr')+';\n'
    pre+='        const bool mesh_copy_push='+field('geometry_load.producer_valid')+'&&'+field('geometry_load.producer_ready')+';\n'
    pre+='        const unsigned mesh_copy_index='+field('geometry_load.packet_queue.write_count')+';\n'
    pre+='        const unsigned mesh_copy_serial='+field('geometry_load.packet_queue.fill_serial')+';\n' 
    pre+='        const unsigned mesh_copy_pointer='+field('geometry_load.packet_queue.write_ptr')+';\n'
    pre+='        if(start!=UINT64_MAX) {\n'
    pre+='            const unsigned mask=(tb->obs_retire?1:0)|(tb->obs_gpu_busy?2:0)|('+field('geometry_load.active')+'?4:0)|('+field('geometry_load.mesh.engine.frontend.raw.state')+'?8:0)|('+field('geometry_load.mesh.engine.frontend.cull_owner')+'?16:0)|(mesh_emit?32:0)|('+field('mesh_word_valid')+'&&'+field('mesh_word_ready')+'?64:0)|('+field('gpu.state')+'==6?128:0)|(mesh_copy_push?256:0)|('+field('geometry_load.queue_busy')+'?512:0)|('+field('geometry_load.active')+'&&!'+field('geometry_load.legacy')+'&&'+'('+field('geometry_load.packet_queue.state')+'==2||'+field('geometry_load.packet_queue.state')+'==3)&&'+field('geometry_load.serial')+'!='+field('geometry_load.packet_queue.serial')+'?1024:0)|('+field('geometry_load.arm_pending')+'?2048:0);\n'
    pre+='            ++mesh_overlap[entries][mask];\n        }\n'
    marker='        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;'
    post='\n        if(mesh_emit) {const uint32_t w='+field('gpu.g_mesh_front.bridge.queue')+'[mesh_emit_pointer&'+str(merge_mask)+'];put32(mesh_words,w);mesh_accepts<<c<<"\\t"<<entries<<"\\t"<<mesh_emit_pointer<<"\\t"<<w<<"\\n";}\n'
    post+='        if(mesh_copy_push) {const uint32_t w='+field('geometry_load.packet_queue.queue')+'[mesh_copy_pointer&'+str(private_mask)+'];mesh_copy<<c<<"\\t"<<entries<<"\\t"<<mesh_copy_serial<<"\\t"<<mesh_copy_index<<"\\t"<<w<<"\\n";}\n'
    source=once(source,marker,pre+marker+post)
    source=once(source,'    tb->final();delete tb;return status;',
       '''    std::ofstream overlap(out+"/mesh-overlap.tsv");overlap<<"phase\\tmask\\tcycles\\n";
    for(const auto &p:mesh_overlap)for(const auto &m:p.second)overlap<<p.first<<"\\t"<<m.first<<"\\t"<<m.second<<"\\n";
    tb->final();delete tb;return status;''')
    if '#include <sstream>' not in source: source='#include <sstream>\n'+source
    return source
