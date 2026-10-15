#!/usr/bin/env python3
"""Full native CPU/raster replay for same-ELF asynchronous mesh controls and candidates."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time

G = Path(__file__).resolve().parent
P = G.parent.parent
C = P/'cached-cpu-v1'
F = P/'first-word-v1'
BASE = C/'native-sdram-v2'
OBSERVER=P/'queued-front-v1/observer-v2'


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
    uint32_t mesh_probe_offset=n("mesh_probe_address")-0x10000000;
    require(mesh_probe_offset+4<=sd.size() && (le32(&sd[mesh_probe_offset])==0 || le32(&sd[mesh_probe_offset])==2),"mesh probe preimage");
    uint32_t mesh_before=le32(&sd[mesh_probe_offset]);
    for(unsigned i=0;i<4;++i)sd[mesh_probe_offset+i]=0;
    uint32_t async_probe_offset=n("async_probe_address")-0x10000000;
    require(async_probe_offset+4<=sd.size() && (le32(&sd[async_probe_offset])==0 || le32(&sd[async_probe_offset])==2),"async probe preimage");
    const uint32_t async_before=le32(&sd[async_probe_offset]);
    for(unsigned i=0;i<4;++i)sd[async_probe_offset+i]=0;
    overlay(0x10000000,sd);
    std::ofstream ap(out+"/prepared-async.json");
    ap<<"{\"probe_address\":"<<n("async_probe_address")<<",\"probe_before\":"<<async_before<<",\"probe_after\":0}\n";
    ap.close();
    std::ofstream mp(out+"/prepared-mesh.json");
    mp<<"{\"probe_address\":"<<n("mesh_probe_address")<<",\"probe_before\":"<<mesh_before<<",\"probe_after\":0}\n";
    mp.close();
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
    source=once(source,marker,marker+'\n                dump_cpu_arena(tb,false,out+"/import-stats",n("import_stats_address"),32);\n                dump_cpu_arena(tb,false,out+"/front-stats",n("front_stats_address"),64);\n                dump_cpu_arena(tb,false,out+"/mesh-stats",n("mesh_stats_address"),n("mesh_stats_bytes"));\n                dump_cpu_arena(tb,false,out+"/async-stats",n("async_stats_address"),n("async_stats_bytes"));')
    witness = r'''    std::ofstream vtx_calls(out+"/vtx-calls.tsv");
    vtx_calls<<"cycle\tphase\ta0\ta1\ta2\ts1\tra\n";
'''
    source=once(source,'    unsigned entries=0;','    unsigned entries=0;\n'+witness)
    witness_sample='        if(tb->obs_retire&&tb->obs_retire_pc=='+str(symbols['gfx_sp_vertex'])+'u) {\n'
    witness_sample+='            auto &rf=*tb->vlSymsp->TOP__tb_system__cpu_sys__cpu.integer_RegFilePlugin_logic_regfile_fpga;\n'
    witness_sample+='            vtx_calls<<c<<"\\t"<<entries<<"\\t"<<rf.asMem_ram[10]<<"\\t"<<rf.asMem_ram[11]<<"\\t"<<rf.asMem_ram[12]<<"\\t"<<rf.asMem_ram[9]<<"\\t"<<rf.asMem_ram[1]<<"\\n";\n        }\n'
    source=once(source,marker,witness_sample+marker)
    if a.hook:
        hook_path=a.hook.resolve()
        spec=importlib.util.spec_from_file_location('mesh_replay_hooks',hook_path)
        hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)
        source=hook.apply(source,model,cfg)
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
             config,config.parent/'prepared.json',a.elf.resolve()]
    if a.hook:files.append(a.hook.resolve())
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
          hook=str(a.hook.resolve()) if a.hook else None,
          preparation='Same zero-buffer flag and capability redetection in both models; no app instruction patches.',
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
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--hook',type=Path)
    p.add_argument('--evidence',type=Path,nargs='*',default=[])
    p.add_argument('--max-cycles',type=int,default=15000000)
    a=p.parse_args()
    globals()[a.action](a)
