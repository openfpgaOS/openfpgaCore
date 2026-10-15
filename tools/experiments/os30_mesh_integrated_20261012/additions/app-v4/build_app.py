#!/usr/bin/env python3
"""Build/freeze the private F19 adaptive-feed MTF2 integrated mesh ELF, without packaging or deployment."""
from pathlib import Path
import difflib
import hashlib
import json
import struct
import subprocess

E=Path(__file__).resolve().parent
W=E.parents[1]
T=W.parent
BASE=T.parent/'queued-front-v1/work/sm64-v2'
OBJ=W/'.obj/sm64-mesh-integrated-v4-perfdiag'
ELF=OBJ/'app.elf'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
FILES=['src/sdk/include/of_gpu.h','src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
       'src/sm64/sm64/src/pc/gfx/gfx_gpu.h','src/sm64/sm64/src/pc/gfx/gfx_pc.c',
       'src/sm64/pocket/perf_pocket.c']
assert not (E/'build-result.json').exists(),'Refuse to overwrite a frozen app build'
tracked=FILES+['src/sm64/Makefile','src/sdk/sdk.mk','src/sdk/app-sdram-staging.ld',
               'tools/sdk-container.sh']
before={name:sha(W/name) for name in tracked}
build_script_sha256=sha(Path(__file__))
cmd=['bash',str(W/'tools/sdk-container.sh'),'-C',str(W/'src/sm64'),'make','-j4',
     'PROFILE=0','PERF_DIAG=1','GEOMETRY=1','GEOMETRY_REFERENCE=0',f'OBJ_DIR={OBJ}',
     'APP_LD=../sdk/app-sdram-staging.ld','LDFLAGS=-Wl,--defsym=__of_sdram_staging=1',str(ELF)]
with (E/'final-build.log').open('w') as log:
    p=subprocess.run(cmd,cwd=W,stdout=log,stderr=subprocess.STDOUT)
assert p.returncode==0,p.returncode
assert before=={name:sha(W/name) for name in tracked}
data=ELF.read_bytes()
assert data[:6]==b'\x7fELF\x01\x01' and b'PERF F19 MESH4\0' in data
phoff,=struct.unpack_from('<I',data,28)
phsize,phnum=struct.unpack_from('<HH',data,42)
segments=[]
for i in range(phnum):
    kind,offset,address,physical,size,memory,flags,align=struct.unpack_from('<8I',data,phoff+i*phsize)
    if kind==1:
        assert address+memory<=0x13200000
        segments.append(dict(address=hex(address),end=hex(address+memory),filesz=size,memsz=memory,flags=flags))
with (E/'app.dis').open('w') as out: subprocess.run(['riscv64-elf-objdump','-d',str(ELF)],stdout=out,check=True)
with (E/'symbols.txt').open('w') as out: subprocess.run(['riscv64-elf-nm','-S',str(ELF)],stdout=out,check=True)
symbols={}
for line in (E/'symbols.txt').read_text().splitlines():
    fields=line.split()
    if len(fields)==4 and fields[3] in ['sm64_bram_import_capability','gfx_bram_stats','load_bram_slots',
        'load_bram_generation','load_bram_first','gfx_run','gfx_end_frame','sm64_perf_snapshot','g_bram_disabled',
        'sm64_triangle_front_capability','gfx_front_stats','sm64_queued_front_capability','gfx_queued_stats','gfx_queued_front_run','gfx_gpu_queued_front_service','load_lazy_slots','load_lazy_first','gfx_triangle_front_run','load_alias_materialize','load_alias_materialize_all','sm64_mesh_front_capability','gfx_mesh_stats','gfx_mesh_lookup','gfx_mesh_finish','gfx_gpu_mesh_sources','gfx_gpu_mesh_wait_source','gfx_gpu_mesh_packet']:
        symbols[fields[3]]=dict(address='0x'+fields[0],size=int(fields[1],16),kind=fields[2])
assert symbols['sm64_bram_import_capability']['size']==4
assert symbols['gfx_bram_stats']['size']==32
assert symbols['gfx_front_stats']['size']==64
assert symbols['sm64_triangle_front_capability']['size']==4
assert symbols['sm64_queued_front_capability']['size']==4
assert symbols['gfx_queued_stats']['size']==64
assert symbols['sm64_mesh_front_capability']['size']==4
assert symbols['gfx_mesh_stats']['size']==64
patch=''
for name in FILES:
    patch+=''.join(difflib.unified_diff((BASE/name).read_text().splitlines(True),(W/name).read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name))
(E/'sm64-mesh-integrated.patch').write_text(patch)
predecessor=T/'work-app-v3'
delta=''.join(''.join(difflib.unified_diff((predecessor/name).read_text().splitlines(True),(W/name).read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name)) for name in FILES)
(E/'app-v3-to-v4.patch').write_text(delta)
assert 'for(probe=0;probe<16;++probe)' in (W/FILES[1]).read_text()
assert 'g_cram_mesh_source[512]' in (W/FILES[1]).read_text()

result=dict(pass_build=True,command=cmd,returncode=0,elf=str(ELF),elf_sha256=sha(ELF),elf_bytes=len(data),
            source_sha256=before,predecessor_source_sha256={name:sha(predecessor/name) for name in FILES},predecessor_build_sha256=sha(predecessor/'evidence/app-build-v3/build-result.json'),baseline_source_sha256={name:sha(BASE/name) for name in FILES},
            segments=segments,symbols=symbols,capability=hex(0x4d544632),bram_capability=hex(0x42494d32),build_script_sha256=build_script_sha256,
            capability_probe_reset_symbols=['sm64_bram_import_capability','sm64_triangle_front_capability','sm64_queued_front_capability','sm64_mesh_front_capability'],
            capability_absence_sets_disabled=False,canonical_cpu_copy_preserved=False,lazy_canonical_fallback=True,
            artifact_sha256={name:sha(E/name) for name in ['final-build.log','app.dis','symbols.txt','sm64-mesh-integrated.patch','app-v3-to-v4.patch','asset-reuse.json']},
            abi_path=str(T/'abi-v2.json'),abi_sha256=sha(T/'abi-v2.json'),
            implementation_tradeoff='CPU waits for preflight and numeric packet validation, then ARM16 copies into a private FIFO without waiting for the ring marker. Mesh markers retain the normal512-word threshold/sync override, force publication with fewer than128 published CPU-ring words or on DMA fallback, and retain the one-time validated/unpublished owner-wait publication. Original LOAD/table/probe behavior remains fixed; no CPU decode-ahead before validation.',
            stats={str(i):name for i,name in enumerate(['task_lookups','topology_hits','topology_compiles','mesh_starts','raw_successes','accepted_preflights','accepted_input_triangles','packet_batches','emitted_triangles','geometry_fallbacks','material_fallbacks','conversion_fallbacks','zero_live_tasks','raw_owner_polls','protocol_or_raw_errors','absent_probes'])},
            packaged=False,deployed=False,hardware_verified=False,
            validation_pending='Same-ELF capability-off capture and native baseline/mesh comparison are owned by the reviewer/root. No full-frame performance or hardware claim.')
(E/'build-result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'pass_build':True,'elf':str(ELF),'elf_sha256':sha(ELF),'symbols':symbols}))
