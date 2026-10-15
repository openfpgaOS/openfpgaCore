#!/usr/bin/env python3
"""Passive GPU profiling of an immutable native CPU/game replay."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

G = Path(__file__).resolve().parent
P = G.parent
C = P/'cached-cpu-v1'
F = P/'first-word-v1'
BASE = C/'native-sdram-v2'
OBSERVER=G/'observer-v2'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text())


def write(p, d):
    p.write_text(json.dumps(d, indent=2)+'\n')


def once(s, old, new):
    assert s.count(old) == 1, (old, s.count(old))
    return s.replace(old, new)


def verify(inventory):
    for p, h in inventory.items():
        assert sha(Path(p)) == h, p


def build(a):
    model = a.model.resolve()
    out = G/a.label
    out.mkdir(exist_ok=False)
    prepared = read(model/'prepared.json')
    verify({str(model/p):h for p,h in prepared['source_sha256'].items()})
    config = a.config.resolve()
    cfg = dict(line.split('=',1) for line in config.read_text().splitlines() if '=' in line)
    source = (F/'prepared-base/native_observed.cpp').read_text()
    source = once(source, '#include "first_word_observer_v2.hpp"',
                  '#include "first_word_observer_v2.hpp"\n#include "gpu_observer.hpp"')
    assert sha(a.elf.resolve()) == read(a.config.resolve().parent/'prepared.json')['elf_sha256']
    symbols={}
    for line in subprocess.check_output(['nm','--defined-only',str(a.elf.resolve())],text=True).splitlines():
        s=line.split()
        if len(s)==3:symbols[s[2]]=int(s[0],16)
    event_names=['gfx_run','gfx_end_frame','sm64_perf_render_begin','sm64_perf_render_end',
                 'sm64_perf_step_begin','sm64_perf_step_end','gfx_gpu_present','gpu_end_frame',
                 'game_loop_one_iteration','create_next_audio_buffer']
    events={name:symbols[name] for name in event_names}
    # Event symbols are extracted from this exact ELF. Legacy static poll-PC
    # attribution is not used for conclusions; frame counts and HW state are direct.
    profile_config=''.join(f'\n    gpu_profile.add_event(0x{pc:08x}u,{json.dumps(name)});' for name,pc in events.items())


    source = once(source, '    os30_first_word::Observer firstword(cached_psram);',
                  '    os30_first_word::Observer firstword(cached_psram);\n'
                  '    gpu_bottleneck::Observer gpu_profile;'+profile_config)
    source = once(source, '    unsigned entries=0;',
                  '    unsigned entries=0;\n'
                  '    std::map<unsigned,std::map<uint32_t,uint64_t>> phase_pcs,phase_gaps;')
    source = once(source,
                  'if(start!=UINT64_MAX&&finished==UINT64_MAX) {++retired;++pcs[pc];gaps[pc]+=c-last_retire;}',
                  'if(start!=UINT64_MAX&&finished==UINT64_MAX) {++retired;++pcs[pc];gaps[pc]+=c-last_retire;'
                  '++phase_pcs[entries][pc];phase_gaps[entries][pc]+=c-last_retire;}')
    marker = '        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;'
    source = once(source, marker,
                  '        gpu_profile.sample(c,start==UINT64_MAX?0:entries,*tb,'
                  'tb->vlSymsp->TOP__tb_system,tb->vlSymsp->TOP__tb_system__cpu_sys__cpu);\n'+marker)
    source = once(source, '    tb->final();delete tb;return status;',
                  '    gpu_profile.write(out+"/gpu-profile");\n'
                  '    for(auto &phase:phase_pcs) {\n'
                  '        std::ofstream p(out+"/pc-phase-"+std::to_string(phase.first)+".csv");\n'
                  '        p<<"pc,retired,cycles_since_previous_retirement\\n";\n'
                  '        for(auto entry:phase.second)p<<entry.first<<","<<entry.second<<","<<phase_gaps[phase.first][entry.first]<<"\\n";\n'
                  '    }\n'
                  '    tb->final();delete tb;return status;')
    if 'surface_base' in cfg:
        source = once(source, 'dump_cpu_arena(tb,cached_psram,out+"/surface-arena");',
                      'dump_cpu_arena(tb,cached_psram,out+"/surface-arena",n("surface_base"),n("surface_bytes"));')
    begin=source.index('    // The capture has already cleared buffers0/1.')
    end=source.index('    auto br=',begin)
    preparation=r'''    // Equal pre-release preparation in legacy and direct-import models.
    uint32_t clear_offset=n("cleared_bufs_address")-0x10000000;
    uint32_t probe_offset=n("import_probe_address")-0x10000000;
    require(clear_offset<sd.size() && sd[clear_offset]==3,"cleared_bufs preimage");
    require(probe_offset+4<=sd.size() && le32(&sd[probe_offset])==2,"import probe preimage");
    for(unsigned i=0;i<153600;++i)
        require(sd[0x00200000+i]==0,"third color buffer not initialized");
    sd[clear_offset]=7;
    for(unsigned i=0;i<4;++i)sd[probe_offset+i]=0;
    uint32_t front_probe_offset=n("front_probe_address")-0x10000000;
    require(front_probe_offset+4<=sd.size() && (le32(&sd[front_probe_offset])==0 || le32(&sd[front_probe_offset])==2),"front probe preimage");
    uint32_t front_before=le32(&sd[front_probe_offset]);
    for(unsigned i=0;i<4;++i)sd[front_probe_offset+i]=0;
    overlay(0x10000000,sd);
    std::ofstream prep(out+"/prepared-memory.json");
    prep<<"{\"cleared_bufs_address\":"<<n("cleared_bufs_address")
        <<",\"cleared_bufs_before\":3,\"cleared_bufs_after\":7,\"import_probe_address\":"
        <<n("import_probe_address")<<",\"probe_before\":2,\"probe_after\":0,"
          "\"third_buffer_zero_bytes_checked\":153600}\n";
    prep.close();
    std::ofstream fp(out+"/prepared-front.json");
    fp<<"{\"probe_address\":"<<n("front_probe_address")<<",\"probe_before\":"<<front_before<<",\"probe_after\":0}\n";
    fp.close();
'''
    source=source[:begin]+preparation+source[end:]
    marker='dump_cpu_arena(tb,cached_psram,out+"/surface-arena",n("surface_base"),n("surface_bytes"));'
    source=once(source,marker,marker+'\n                dump_cpu_arena(tb,false,out+"/import-stats",n("import_stats_address"),32);\n                dump_cpu_arena(tb,false,out+"/front-stats",n("front_stats_address"),64);')
    if '__PVT__gpu__DOT__importer_done' in (model/'obj/Vtb_system_tb_system.h').read_text():
        decl=r"""    std::ofstream import_results(out+"/imports.tsv");
    import_results<<"cycle\ttoken\tsource\tdestination\tclip_x\tclip_y\tclip_w\ttex_u\ttex_v\trgb565\tdepth\n";
    std::ofstream import_events(out+"/import-events.tsv");
    import_events<<"cycle\tstate\tgeneration\tleased\towner\terror\tstart\tdone\tconvert_error\trelease\tfault\ttoken\tdescriptor\treadptr\twriteptr\n";
    uint64_t import_previous=UINT64_MAX;
"""
        source=once(source,'    unsigned entries=0;',decl+'    unsigned entries=0;')
        sample=r"""        {
            const auto &s=tb->vlSymsp->TOP__tb_system;
            uint64_t bits=uint64_t(s.__PVT__geometry_load__DOT__import_generation)<<32
                |uint64_t(s.__PVT__geometry_load__DOT__import_leased)<<16
                |uint64_t(s.__PVT__geometry_load__DOT__import_owner)<<17
                |uint64_t(s.__PVT__geometry_load__DOT__import_sticky_error)<<18
                |uint64_t(s.__PVT__gpu__DOT__import_start)<<19
                |uint64_t(s.__PVT__gpu__DOT__importer_done)<<20
                |uint64_t(s.__PVT__gpu__DOT__importer_error)<<21
                |uint64_t(s.__PVT__geometry_import_release)<<22
                |uint64_t(s.__PVT__geometry_import_fault)<<23;
            if(bits!=import_previous) {
                import_previous=bits;
                import_events<<c<<'\t'<<unsigned(s.__PVT__gpu__DOT__state)
                    <<'\t'<<s.__PVT__geometry_load__DOT__import_generation
                    <<'\t'<<unsigned(s.__PVT__geometry_load__DOT__import_leased)
                    <<'\t'<<unsigned(s.__PVT__geometry_load__DOT__import_owner)
                    <<'\t'<<unsigned(s.__PVT__geometry_load__DOT__import_sticky_error)
                    <<'\t'<<unsigned(s.__PVT__gpu__DOT__import_start)
                    <<'\t'<<unsigned(s.__PVT__gpu__DOT__importer_done)
                    <<'\t'<<unsigned(s.__PVT__gpu__DOT__importer_error)
                    <<'\t'<<unsigned(s.__PVT__geometry_import_release)
                    <<'\t'<<unsigned(s.__PVT__geometry_import_fault)
                    <<'\t'<<s.__PVT__gpu__DOT__import_command_token
                    <<'\t'<<s.__PVT__gpu__DOT__import_descriptor
                    <<'\t'<<s.__PVT__gpu__DOT__ring_rdptr
                    <<'\t'<<s.__PVT__gpu__DOT__ring_wrptr<<'\n';
            }
            if(s.__PVT__gpu__DOT__importer_done&&!s.__PVT__gpu__DOT__importer_error)
                import_results<<c<<'\t'<<s.__PVT__gpu__DOT__import_command_token
                    <<'\t'<<(s.__PVT__gpu__DOT__import_descriptor&31)
                    <<'\t'<<((s.__PVT__gpu__DOT__import_descriptor>>5)&31)
                    <<'\t'<<s.__PVT__gpu__DOT__imported_x
                    <<'\t'<<s.__PVT__gpu__DOT__imported_y
                    <<'\t'<<s.__PVT__gpu__DOT__imported_w
                    <<'\t'<<s.__PVT__gpu__DOT__imported_u
                    <<'\t'<<s.__PVT__gpu__DOT__imported_v
                    <<'\t'<<s.__PVT__gpu__DOT__imported_rgb
                    <<'\t'<<s.__PVT__gpu__DOT__imported_depth<<'\n';
        }
"""
        source=once(source,'        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;',sample+'        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;')
    if '__PVT__geometry_load__DOT__import_view__DOT__bank_valid' in (model/'obj/Vtb_system_tb_system.h').read_text():
        source=source.replace('s.__PVT__geometry_load__DOT__import_leased','(s.__PVT__geometry_load__DOT__import_view__DOT__copy_state != 0)')
        source=once(source,'#include "gpu_observer.hpp"','#include "gpu_observer.hpp"\n#include <array>')
        decl=r"""    std::ofstream bank_events(out+"/bank-events.tsv");
    bank_events<<"cycle\tgeneration\tvalid\tready\ttoken0\ttoken1\tcount0\tcount1\tcopy_state\tcopy_bank\towner\tread_bank\tclaim_result\n";
    std::array<uint64_t,12> bank_previous={UINT64_MAX};
"""
        source=once(source,'    unsigned entries=0;',decl+'    unsigned entries=0;')
        sample=r"""        {
            const auto &s=tb->vlSymsp->TOP__tb_system;
            const std::array<uint64_t,12> now={
                s.__PVT__geometry_load__DOT__import_generation,
                s.__PVT__geometry_load__DOT__import_view__DOT__bank_valid,
                s.__PVT__geometry_load__DOT__import_view__DOT__bank_ready,
                s.__PVT__geometry_load__DOT__import_view__DOT__bank_token[0],
                s.__PVT__geometry_load__DOT__import_view__DOT__bank_token[1],
                s.__PVT__geometry_load__DOT__import_view__DOT__bank_count[0],
                s.__PVT__geometry_load__DOT__import_view__DOT__bank_count[1],
                s.__PVT__geometry_load__DOT__import_view__DOT__copy_state,
                s.__PVT__geometry_load__DOT__import_view__DOT__copy_bank,
                s.__PVT__geometry_load__DOT__import_owner,
                s.__PVT__geometry_load__DOT__import_view__DOT__gpu_read_bank,
                s.__PVT__geometry_load__DOT__import_claim_result};
            if(now!=bank_previous) {
                bank_events<<c;
                for(auto value:now) bank_events<<'\t'<<value;
                bank_events<<'\n';
                bank_previous=now;
            }
        }
"""
        source=once(source,'        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;',sample+'        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;')
    if '__PVT__geometry_load__DOT__preflight__DOT__state' in (model/'obj/Vtb_system_tb_system.h').read_text():
        decl=r"""    std::ofstream front_events(out+"/front-events.tsv");
    front_events<<"cycle\tstate\tgeneration\ttoken\tcount\tjob\tindices\tmode\tdone\tfallback\terror\tkeep_mask\tfallback_mask\tclip_reject_count\towner\tproducer_active\tc_out\tc_reject\tc_bad\tc_clip\n";
    std::array<uint64_t,19> front_previous={UINT64_MAX};
"""
        source=once(source,'    unsigned entries=0;',decl+'    unsigned entries=0;')
        sample=r"""        {
            const auto &s=tb->vlSymsp->TOP__tb_system;
            const std::array<uint64_t,19> now={
                s.__PVT__geometry_load__DOT__preflight__DOT__state,
                s.__PVT__geometry_load__DOT__import_generation,
                s.__PVT__geometry_load__DOT__preflight__DOT__token,
                s.__PVT__geometry_load__DOT__preflight__DOT__count,
                s.__PVT__geometry_load__DOT__preflight__DOT__job_index,
                s.__PVT__geometry_load__DOT__preflight__DOT__indices,
                s.__PVT__geometry_load__DOT__preflight__DOT__mode,
                s.__PVT__geometry_load__DOT__preflight__DOT__done,
                s.__PVT__geometry_load__DOT__preflight__DOT__any_fallback,
                s.__PVT__geometry_load__DOT__preflight__DOT__protocol_error,
                s.__PVT__geometry_load__DOT__preflight__DOT__keep_mask,
                s.__PVT__geometry_load__DOT__preflight__DOT__fallback_mask,
                s.__PVT__geometry_load__DOT__preflight__DOT__clip_reject_count,
                s.__PVT__geometry_load__DOT__tf_owner,
                s.__PVT__geometry_load__DOT__active,
                s.__PVT__geometry_load__DOT__preflight__DOT__c_out,
                s.__PVT__geometry_load__DOT__preflight__DOT__c_reject,
                s.__PVT__geometry_load__DOT__preflight__DOT__c_bad,
                s.__PVT__geometry_load__DOT__preflight__DOT__c_clip};
            if(now!=front_previous) {
                front_events<<c;
                for(auto value:now) front_events<<'\t'<<value;
                front_events<<'\n';
                front_previous=now;
            }
        }
"""
        source=once(source,'        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;',sample+'        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;')
    if '__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__state' in (model/'obj/Vtb_system_tb_system.h').read_text():
        old=r"""    s.__PVT__gpu__DOT__ring_wr_addr=n("ring_word_pointer");s.__PVT__gpu__DOT__ring_wrptr=n("ring_word_pointer");
    s.__PVT__gpu__DOT__ring_rd_addr=n("ring_word_pointer");s.__PVT__gpu__DOT__ring_rdptr=n("ring_word_pointer");"""
        new=r"""    // The drained QGF ingress and output rings have independent pointers.
    // Restore only the ingress position needed by the captured SDK. Keep the
    // output FIFO's already drained pointers and decoder state unchanged.
    require(s.__PVT__gpu__DOT__upload_rdptr==s.__PVT__gpu__DOT__ring_wrptr,"QGF drained ingress preimage");
    require(s.__PVT__gpu__DOT__ring_rdptr==s.__PVT__gpu__DOT__command_wrptr,"QGF drained output preimage");
    require(s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__state==1,"QGF header preimage");
    std::ofstream qp(out+"/prepared-queue.json");
    qp<<"{\"ingress_before\":"<<s.__PVT__gpu__DOT__upload_rdptr
      <<",\"ingress_after\":"<<n("ring_word_pointer")
      <<",\"output_empty_pointer_preserved\":"<<s.__PVT__gpu__DOT__ring_rdptr
      <<",\"host_clock_edges_added\":0}\n";
    s.__PVT__gpu__DOT__ring_wr_addr=n("ring_word_pointer");s.__PVT__gpu__DOT__ring_wrptr=n("ring_word_pointer");
    s.__PVT__gpu__DOT__upload_rdptr=n("ring_word_pointer");
    s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__state=0; // synchronous ingress prime
"""
        source=once(source,old,new)
        decl=r"""    std::ofstream worker_commands(out+"/worker-commands.bin",std::ios::binary);
    std::ofstream queue_events(out+"/queue-events.tsv");
    queue_events<<"cycle\tstate\ttoken\tjob\tkeep\tused\trepair\terror\timport_error\tmath_owner\tmath_abort\tmath_outstanding\traw_active\traw_pending\tinput_read\tinput_write\toutput_write\toutput_read\n";
    std::array<uint64_t,17> queue_previous={UINT64_MAX};
    bool queue_push=false;uint32_t queue_write_index=0;
"""
        source=once(source,'    unsigned entries=0;',decl+'    unsigned entries=0;')
        sample=r"""        {
            const auto &s=tb->vlSymsp->TOP__tb_system;
            queue_push=s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__emit;
            queue_write_index=s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__write_ptr;
            const std::array<uint64_t,17> now={
                s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__state,
                s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__token,
                s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__job_index,
                s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__keep_mask,
                s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__vertex_mask,
                s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__repair_pending,
                s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__protocol_error,
                s.__PVT__geometry_load__DOT__import_sticky_error,
                s.__PVT__geometry_load__DOT__queue_math_owner,
                s.__PVT__geometry_load__DOT__queue_math_abort,
                s.__PVT__geometry_load__DOT__queue_math_outstanding,
                s.__PVT__geometry_load__DOT__active,
                s.__PVT__geometry_load__DOT__pending,
                s.__PVT__gpu__DOT__upload_rdptr,
                s.__PVT__gpu__DOT__ring_wrptr,
                s.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__write_ptr,
                s.__PVT__gpu__DOT__ring_rdptr};
            if(now!=queue_previous) {
                queue_events<<c;for(auto value:now)queue_events<<'\t'<<value;
                queue_events<<'\n';queue_previous=now;
            }
        }
"""
        marker='        half(true);++cycles;tb->inj_burst_rd=0;if(audio_taken)tb->inj_audio_valid=0;'
        source=once(source,marker,sample+marker+"\n        if(queue_push)put32(worker_commands,tb->vlSymsp->TOP__tb_system.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__queue[queue_write_index&(tb->vlSymsp->TOP__tb_system.__PVT__gpu__DOT__g_queued_front__DOT__worker__DOT__queue.size()-1)]);")
    main = out/'native_profiled.cpp' 
    main.write_text(source)
    obj = model/'obj'
    runtime = BASE/'obj/libverilated.a'
    cmd = ['g++','-std=c++20','-fcoroutines','-DVM_TIMING=1','-O2',
           '-I'+str(obj),'-I'+str(C),'-I'+str(P/'early-restart-v1/observer'),
           '-I'+str(F/'observer'),'-I'+str(OBSERVER),
           '-I/usr/share/verilator/include','-I/usr/share/verilator/include/vltstd',
           str(main),str(obj/'Vtb_system__ALL.a'),str(runtime),'-pthread','-latomic',
           '-o',str(out/'native_profiled')]
    files = [Path(__file__),main,F/'prepared-base/native_observed.cpp',
             C/'cache_snapshot.hpp',P/'early-restart-v1/observer/stall_observer.hpp',
             F/'observer/first_word_observer_v2.hpp',OBSERVER/'gpu_observer.hpp',
             model/'prepared.json',obj/'Vtb_system__ALL.a',runtime,
             obj/'Vtb_system_VexiiRiscv.h',obj/'Vtb_system_cpu_system.h',obj/'Vtb_system_tb_system.h',
             config,a.elf.resolve()]
    files += [p for p in Path(cfg['snapshot']).rglob('*') if p.is_file()]
    files += [Path(cfg[f'segment{i}_path']) for i in range(int(cfg['segments']))]
    files += [p.resolve() for p in a.evidence]
    hashes = {str(p):sha(p) for p in files}
    start = time.monotonic()
    with (out/'build.log').open('w') as f:
        rc = subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT).returncode
    verify(hashes)
    write(out/'build.json',dict(command=cmd,returncode=rc,seconds=time.monotonic()-start,
          model=str(model),config=str(config),elf=str(a.elf.resolve()),
          cpu_event_points=events,
          observer='passive, before active edge',fixed_schedule=True,prepared_buffer_flag=True,
          inputs_sha256=hashes))
    print(a.label,'build',rc,flush=True)
    raise SystemExit(rc)


def run(a):
    out = G/a.label
    d = read(out/'build.json')
    assert d['returncode']==0
    verify(d['inputs_sha256'])
    model = Path(d['model'])
    verify({str(model/p):h for p,h in read(model/'prepared.json')['source_sha256'].items()})
    run = out/'run'
    run.mkdir(exist_ok=False)
    (run/'input.cfg').write_bytes(Path(d['config']).read_bytes())
    cmd = [str(out/'native_profiled'),str(run/'input.cfg'),str(run),str(a.max_cycles)]
    start = time.monotonic()
    with (run/'run.log').open('w') as f:
        rc = subprocess.run(cmd,cwd=model/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT).returncode
    write(run/'process.json',dict(command=cmd,returncode=rc,seconds=time.monotonic()-start,
          inputs_sha256=d['inputs_sha256'],executable_sha256=sha(out/'native_profiled')))
    verify(d['inputs_sha256'])
    assert rc==0,rc
    write(run/'manifest.json',dict(outputs_sha256={p.name:sha(p) for p in run.iterdir() if p.is_file()}))
    print(a.label,'run',rc,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['build','run'])
    p.add_argument('label')
    p.add_argument('--model',type=Path,default=BASE)
    p.add_argument('--config',type=Path,default=G/'fixture/input.cfg')
    p.add_argument('--elf',type=Path,default=G/'work/sm64/.obj/sm64-bram-import-perfdiag/app.elf')
    p.add_argument('--evidence',type=Path,nargs='*',default=[])
    p.add_argument('--max-cycles',type=int,default=15000000)
    a=p.parse_args()
    globals()[a.action](a)
