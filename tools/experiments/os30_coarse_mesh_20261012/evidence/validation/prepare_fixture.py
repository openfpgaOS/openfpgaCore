#!/usr/bin/env python3
"""Fresh same-ELF, capability-off capture and bounded native guest for AJ03."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

V=Path(__file__).resolve().parent
I=V.parent
P=I.parent
R=P.parents[2]
OLD=P/'gpu-bottleneck-v1/fixture-normal-v1'
O=V/'fixture-v1'
C=O/'capture'
G=O/'guest'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
def write(p,d):p.write_text(json.dumps(d,indent=2)+'\n')
def module(name,path):
    s=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def symbols(elf):
    return {r.split()[2]:int(r.split()[0],16) for r in subprocess.check_output(
        ['riscv64-elf-nm','--defined-only',str(elf)],text=True).splitlines() if len(r.split())==3}

def capture(app):
    C.mkdir(parents=True,exist_ok=False)
    shutil.copytree(OLD/'capture/model/frozen',C/'model/frozen')
    shutil.copytree(OLD/'capture/qsim',C/'qsim')
    source=C/'model/frozen/coupled.cpp'
    s=source.read_text()
    before='''        if(native_call_frame==362) {
            native_cpu_dump("frame362-entry.bin",c);'''
    after='''        if(native_call_frame==362) {
            const auto *surface=qsim_syms_lookup(&E->syms,"sSurfacePool");assert(surface);
            const auto *count=qsim_syms_lookup(&E->syms,"sSurfacePoolSize");assert(count);
            uint32_t pointer,records;
            memcpy(&pointer,qsim_mem_ptr(c,surface->addr,4),4);
            memcpy(&records,qsim_mem_ptr(c,count->addr,4),4);
            assert(records==2300 && pointer>=c->sdram_base &&
                   uint64_t(pointer)+records*48u<=uint64_t(c->sdram_base)+c->sdram_size);
            native_binary("surface-pool-final.bin",qsim_mem_ptr(c,pointer,records*48u),records*48u);
            native_cpu_dump("frame362-entry.bin",c);'''
    assert s.count(before)==1
    source.write_text(s.replace(before,after))
    cmd=[v.replace(str(OLD/'capture'),str(C)) for v in read(OLD/'capture/build-command.json')]
    write(C/'build-command.json',cmd)
    start=time.monotonic()
    inputs={str(p):sha(p) for root in [C/'model/frozen',C/'qsim'] for p in root.rglob('*') if p.is_file()}
    inputs[str(app)]=sha(app)
    with(C/'model/build.log').open('x')as f:
        subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(C/'model/build.json',dict(command=cmd,simulator_sha256=sha(C/'model/obj/Vtb_gpu'),
        seconds=time.monotonic()-start,inputs_sha256=inputs))
    recipe=R/'tools/experiments/os30_overlap_20261006/run.py'
    m=module('bram_import_capture',recipe);m.OUT=C
    os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
    sys.argv=[str(Path(__file__)),'reference','--elf',str(app),'--start','360','--frames','3']
    m.main()
    S=C/'run-reference/snapshot'
    assert read(S/'complete.json')['passed']
    syms=symbols(app)
    sd=(S/'sdram.bin').read_bytes()
    probes={}
    for name in ('sm64_bram_import_capability','sm64_queued_front_capability','sm64_mesh_front_capability','sm64_async_front_capability','sm64_async_group_capability'):
        address=syms[name];value=int.from_bytes(sd[address-0x10000000:address-0x10000000+4],'little')
        assert value in (0,2),(name,value)
        probes[name]={'address':address,'value':value}
    assert probes['sm64_bram_import_capability']['value']==2
    assert (S/'surface-pool-final.bin').stat().st_size==110400
    for p,h in inputs.items():assert sha(Path(p))==h,p
    write(C/'result.json',dict(passed=True,elf=str(app),elf_sha256=sha(app),
        reference_import_capability=False,reference_mesh_capability=False,capability_probes=probes,
        final_pool_sha256=sha(S/'surface-pool-final.bin'),timing_evidence=False,
        snapshot_sha256={p.name:sha(p) for p in S.iterdir() if p.is_file()}))
    write(C/'manifest.json',dict(passed=True,files={str(p.relative_to(C)):sha(p)
        for p in C.rglob('*') if p.is_file() and p.name!='manifest.json' and 'obj' not in p.parts and 'build' not in p.parts}))
    print('AJ03 SAME-ELF CAPABILITY-OFF REFERENCE CAPTURE PASS',flush=True)

def guest(app):
    assert read(C/'result.json')['elf_sha256']==sha(app)
    S=G/'services';S.mkdir(parents=True,exist_ok=False)
    syms=symbols(app);token=syms['g_flip_token']
    for name in ['native_services.c','native_services.S','host_check.c']:
        source=(OLD/'guest/services'/name).read_text()
        source=source.replace('0x10fd8084',hex(token))
        (S/name).write_text(source)
    source=(OLD/'guest/services/recipe.py').read_text().replace(str(OLD),str(O))
    (S/'recipe.py').write_text(source)
    subprocess.run([sys.executable,str(S/'recipe.py')],check=True)
    sys.path.insert(0,str(R/'tools/experiments/os30_fps25_20261005'))
    m=module('bram_import_guest',R/'tools/experiments/os30_fps25_20261005/phase10_native_warm_guest.py')
    m.OUT=G/'overlay';m.CAP=C;m.SNAP=C/'run-reference/snapshot';m.SERV=S;m.APP=app
    m.build()
    check=(OLD/'guest/check.py').read_text().replace(str(OLD),str(O))
    (G/'check.py').write_text(check)
    subprocess.run([sys.executable,str(G/'check.py')],check=True)
    cfg=dict(line.split('=',1) for line in (OLD/'input.cfg').read_text().splitlines() if '=' in line)
    cfg={k:v.replace(str(OLD),str(O)) for k,v in cfg.items()}
    cfg.update(dict(line.split('=',1) for line in (G/'overlay/run-config.txt').read_text().splitlines() if '=' in line))
    pool=read(C/'run-reference/snapshot/surface-pool.json')
    cfg.update(surface_base=str(pool['address']),surface_bytes=str(pool['bytes']),
               cleared_bufs_address=str(syms['cleared_bufs.0']),
               import_probe_address=str(syms['sm64_bram_import_capability']),
               import_stats_address=str(syms['gfx_bram_stats']),
               front_probe_address=str(syms['sm64_queued_front_capability']),
               front_stats_address=str(syms['gfx_queued_stats']),
               mesh_probe_address=str(syms['sm64_mesh_front_capability']),
               mesh_stats_address=str(syms['gfx_mesh_stats']),mesh_stats_bytes='64',
               async_probe_address=str(syms['sm64_async_front_capability']),
               group_probe_address=str(syms['sm64_async_group_capability']),
               async_stats_address=str(syms['gfx_async_stats']),async_stats_bytes='64')
    (O/'input.cfg').write_text(''.join(f'{k}={v}\n' for k,v in cfg.items()))
    write(G/'manifest.json',dict(passed=True,elf_sha256=sha(app),token_address=token,
        files={str(p.relative_to(G)):sha(p) for p in G.rglob('*') if p.is_file() and p.name!='manifest.json'}))
    write(O/'prepared.json',dict(passed=True,elf=str(app),elf_sha256=sha(app),
        config=str(O/'input.cfg'),surface_pool=pool,
        state_preparation=['Verified zero third color buffer; cleared_bufs3->7.',
            'Reset BIM2, QGF1, MTF2 and AJ03 capability caches to unknown in both models; each redetects available hardware.'],
        limitation='Two intervals, synthetic scan/audio and bounded guest; not sustained hardware FPS.',
        files={str(p):sha(p) for p in [Path(__file__).resolve(),O/'input.cfg',C/'manifest.json',G/'manifest.json',app]}))
    print('AJ03 BOUNDED GUEST PASS',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['capture','guest']);p.add_argument('elf',type=Path)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    O=a.out.resolve();assert O.is_relative_to(V), 'All generated fixture files must remain under validation/'
    C=O/'capture';G=O/'guest'
    globals()[a.action](a.elf.resolve())
