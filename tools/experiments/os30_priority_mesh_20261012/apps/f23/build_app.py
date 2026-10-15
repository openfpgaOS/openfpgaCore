#!/usr/bin/env python3
"""Build/freeze isolated F23 AJ02 app; no production update or deployment."""
from pathlib import Path
import difflib,hashlib,json,struct,subprocess
E=Path(__file__).resolve().parent;W=E.parents[1];A=W.parent;P=A.parent
PRE=P/'async-mesh-v1/work-app-v3'
REPAIR=P/'async-mesh-v1/repair'
BASE=P/'queued-front-v1/work/sm64-v2'
OBJ=W/'.obj/sm64-priority-mesh-v1-perfdiag';ELF=OBJ/'app.elf'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert not(E/'build-result.json').exists(),'Refuse to overwrite a frozen build'
FILES=['src/sm64/Makefile','src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
 'src/sm64/sm64/src/pc/gfx/gfx_gpu.h','src/sm64/sm64/src/pc/gfx/gfx_pc.c',
 'src/sm64/sm64/src/pc/gfx/async_mesh_repair.c','src/sm64/sm64/src/pc/gfx/async_mesh_repair.h',
 'src/sm64/pocket/perf_pocket.c']
tracked=FILES+['src/sdk/include/of_gpu.h','src/sdk/sdk.mk','src/sdk/app-sdram-staging.ld','tools/sdk-container.sh']
for name in ('async_mesh_repair.c','async_mesh_repair.h'):
 assert sha(REPAIR/name)==sha(W/'src/sm64/sm64/src/pc/gfx'/name),'Repair helper differs from qualified source'
# Exact inherited generated inputs; no ROM/asset generation in this experiment.
old=json.loads((PRE/'evidence/app-build-v3/asset-reuse.json').read_text())
assets=old['files_sha256']
for name,digest in assets.items():
 assert sha(PRE/'src/sm64/sm64/build'/name)==digest
 assert sha(W/'src/sm64/sm64/build'/name)==digest
(E/'asset-reuse.json').write_text(json.dumps({'source':str(PRE/'src/sm64/sm64/build'),'destination':str(W/'src/sm64/sm64/build'),'files_sha256':assets},indent=2)+'\n')
tests=W/'evidence/priority-tests-v2/result.json'
assert json.loads(tests.read_text())['status']=='PASS'
for name,digest in json.loads(tests.read_text())['inputs_sha256'].items():
 assert sha(Path(name))==digest
before={name:sha(W/name) for name in tracked}
cmd=['bash',str(W/'tools/sdk-container.sh'),'-C',str(W/'src/sm64'),'make','-j4',
 'PROFILE=0','PERF_DIAG=1','GEOMETRY=1','GEOMETRY_REFERENCE=0',f'OBJ_DIR={OBJ}',
 'APP_LD=../sdk/app-sdram-staging.ld','LDFLAGS=-Wl,--defsym=__of_sdram_staging=1',str(ELF)]
with(E/'final-build.log').open('w') as log:r=subprocess.run(cmd,cwd=W,stdout=log,stderr=subprocess.STDOUT)
assert r.returncode==0
assert before=={name:sha(W/name) for name in tracked}
data=ELF.read_bytes();assert data[:6]==b'\x7fELF\x01\x01' and b'PERF F23 PRI1\0' in data
phoff,=struct.unpack_from('<I',data,28);phsize,phnum=struct.unpack_from('<HH',data,42);segments=[]
for i in range(phnum):
 kind,offset,address,physical,size,memory,flags,align=struct.unpack_from('<8I',data,phoff+i*phsize)
 if kind==1:
  assert address+memory<=0x13200000
  segments.append(dict(address=hex(address),end=hex(address+memory),filesz=size,memsz=memory,flags=flags))
with(E/'app.dis').open('w') as out:subprocess.run(['riscv64-elf-objdump','-d',str(ELF)],stdout=out,check=True)
with(E/'symbols.txt').open('w') as out:subprocess.run(['riscv64-elf-nm','-S',str(ELF)],stdout=out,check=True)
symbols={}
for line in(E/'symbols.txt').read_text().splitlines():
 f=line.split()
 if len(f)==4 and (f[3].startswith(('gfx_async','sm64_async','gfx_gpu_async','gpu_async','async_','am_repair','sm64_bram','sm64_triangle','sm64_queued','sm64_mesh','load_alias','load_lazy','gfx_mesh','gfx_front','gfx_queued','gfx_bram')) or f[3] in ['gfx_run','gfx_end_frame','gfx_async_try_load','gfx_async_dead_suffix']):
  symbols[f[3]]=dict(address='0x'+f[0],size=int(f[1],16),kind=f[2])
assert symbols['sm64_async_front_capability']['size']==4 and symbols['gfx_async_stats']['size']==64
for name,base in [('app-aj01-to-aj02.patch',PRE),('qualified-to-priority.patch',BASE)]:
 patch=''
 for rel in FILES:
  old=(base/rel).read_text().splitlines(True) if (base/rel).exists() else []
  patch+=''.join(difflib.unified_diff(old,(W/rel).read_text().splitlines(True),fromfile='a/'+rel if old else '/dev/null',tofile='b/'+rel))
 (E/name).write_text(patch)
probes=['sm64_bram_import_capability','sm64_triangle_front_capability','sm64_queued_front_capability','sm64_mesh_front_capability','sm64_async_front_capability']
result=dict(status='PASS_BUILD_NOT_HARDWARE_QUALIFIED',command=cmd,elf=str(ELF),elf_sha256=sha(ELF),elf_bytes=len(data),
 source_sha256=before,predecessor=str(PRE),predecessor_source_sha256={rel:sha(PRE/rel) for rel in FILES if(PRE/rel).exists()},
 baseline=str(BASE),baseline_source_sha256={rel:sha(BASE/rel) for rel in FILES if(BASE/rel).exists()},
 segments=segments,symbols=symbols,capability='0x414a3032',capability_probe_reset_symbols=probes,
 abi_path=str(A/'design/abi.json'),abi_sha256=sha(A/'design/abi.json'),build_script_sha256=sha(Path(__file__)),
 artifact_sha256={n:sha(E/n) for n in ['final-build.log','app.dis','symbols.txt','asset-reuse.json','app-aj01-to-aj02.patch','qualified-to-priority.patch']},
 repair_source_sha256={str(REPAIR/n):sha(REPAIR/n) for n in ['async_mesh_repair.c','async_mesh_repair.h']},
 strict_repair_flags=['-fno-fast-math','-ffp-contract=off'],
 stats={str(i):n for i,n in enumerate(['candidates','dead_suffix_rejects','material_rejects','LOAD_rejects','queued_jobs','queued_input_triangles','context_words','repair_jobs','repair_output_words','recomputed_vertices','legacy_leases','lease_polls','source_drain_polls','protocol_faults','repair_reset_cancellations','absent_probes'])},
 limitations=['Only cached prepared eligible material and immutable own-slot TRI spans with a cached bounded dead-slot suffix proof enqueue async jobs. Cold or unproven spans remain canonical CPU paths.',
 'Same 512-entry/16-probe topology/source caches as predecessor. No warm cache prepopulation. Source-pool reuse drains queued source readers, with repair service.',
 'Priority legacy acquisition waits only for complete current job/context ownership. Source upload and prior pending submission precede acquisition. No worker-dependent wait occurs until the new LOAD result is completed and registered.',
 'Each asynchronous job carries an atomic full28-word context plus descriptor through ordered ingress. Conditional nonemitting wait hooks materialize/release a held lease whenever true completed sequence differs from last submitted sequence; ordinary quiescent legacy stretches retain ownership. Partial clipping/equal-W/arithmetic repair helper and exact-key route hint are unchanged.',
 'No integrated correctness/performance claim at build time. Fresh same-ELF capability-off capture and paired native replay are required.'],
 focused_tests_path=str(tests),focused_tests_sha256=sha(tests),
 packaged=False,deployed=False,physical_build=False)
(E/'build-result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'status':result['status'],'elf':str(ELF),'sha256':sha(ELF),'capability_probe':symbols['sm64_async_front_capability'],'stats':symbols['gfx_async_stats']}))
