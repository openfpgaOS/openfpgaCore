#!/usr/bin/env python3
"""Seal native functional real-source client evidence; no hardware FPS claim."""
import csv,json,struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
import phase10_lod6_capture as m
P=m.OUT;sha=m.sha;write=m.write
v=m.module('pool_vec',P/'vector16.py');tex=v.vector((P/'capture-normal/gpuvec/frame_00720.vec').read_bytes())['texture']
with(m.CAND/'app.elf').open('rb')as f:
 elf=ELFFile(f);segments=[(int(s['p_vaddr']),s.data())for s in elf.iter_segments()if s['p_filesz']]
def read(a,n):
 x=[b[a-base:a-base+n]for base,b in segments if base<=a and a+n<=base+len(b)];assert len(x)==1;return x[0]
entries=[]
for ptr,oc in struct.iter_unpack('<II',(P/'capture-normal/lod6-pool.bin').read_bytes()):
 if not ptr:continue
 off=oc&0xffffff;n=oc>>24;assert 1<=n<=32 and not(off&63)and off>=0xfc0000 and off+24*n<=0x1000000
 assert read(ptr,24*n)==tex[off:off+24*n]
 entries.append(dict(source=ptr,offset=off,count=n,bytes=24*n,allocation=(24*n+63)&~63))
entries.sort(key=lambda x:x['offset']);assert len(entries)==440
for a,b in zip(entries,entries[1:]):assert a['offset']+a['allocation']==b['offset']
proof=dict(passed=True,entries=entries,source_bytes=sum(e['bytes']for e in entries),allocation_bytes=sum(e['allocation']for e in entries),begin=entries[0]['offset'],end=entries[-1]['offset']+entries[-1]['allocation'],source_stride_bytes=24,all_source_bytes_match_immutable_elf=True,texture_snapshot_sha256=sha(P/'capture-normal/gpuvec/frame_00720.vec'),table_sha256=sha(P/'capture-normal/lod6-pool.bin'))
write(P/'pool-byte-proof.json',proof)
results={n:json.loads((P/f'result-{n}.json').read_text())for n in('normal','faults','absent','timeout','fullpool')};assert all(x['passed']for x in results.values())
# Bind warm lookup cost without poll-latency optimism: interval360..419 has no misses.
def profile(name):return {x['function']:x for x in csv.DictReader((P/'capture-normal'/name).open(),delimiter='\t')}
a=profile('profile-0360.tsv');b=profile('profile-0420.tsv');n='gfx_gpu_cram_vertex_source'
warm=dict(frames=60,first=360,last=419,calls=int(b[n]['calls'])-int(a[n]['calls']),self_instructions=int(b[n]['self_insns'])-int(a[n]['self_insns']),self_cycles=int(b[n]['self_cycles'])-int(a[n]['self_cycles']),ms_per_frame=(int(b[n]['self_cycles'])-int(a[n]['self_cycles']))/6000000,source_uploads=0,includes_client_counters=True)
write(P/'cost-scope.json',dict(warm_lookup=warm,cold_physical_upload_timing='LOD6 cold physical timing not measured. Source upload algorithm is unchanged from LOD5; functional upload-ready is instantaneous.',source_stride='Vtx24-byte source stride is compile-time asserted; XYZ0/4/8,flag12,TC14/16,RGBA18..21,pad22..23.'))
(P/'REPORT.txt').write_text(f'''Private transform-only LOD6 client functional proof.
CPU lighting executes after START with the original memo and native arithmetic.
Hardware supplies transformed coordinates/reciprocal/UV/clip and raw RGBA;
lit success copying preserves CPU-produced RGB/alpha.

Normal, absent, injected LOAD failures, drained timeout, and forced fullpool
runs each preserve all721 scene/geometry/culling records and commandwordcounts.
Eight sampled ordered rendering streams match, excluding explicitly identified
source-pool upload words and upload-address setup. Texture payload differences
remain only the independently verified pre-existing linkedpointer padding.
These are protocol/count proofs: qsim does not render0x50..0x57, so they are NOT
721frame pixel equivalence. ActualRTL render windows are separate evidence.

Normal:440 immutable descriptors;145104 actual source bytes, {proof['allocation_bytes']}
allocated bytes;171517 hits,0 capacity/probe failures. Every final cached
source span matches its immutable ELF bytes at the actual high CRAM address.
No host source remap or startup pool preload is used. The app performs uploads.
Fullpool:171957 CPU fallbacks,0sourceuploads. Absent:0poolcalls/uploads.
Warm360..419 lookup costs {warm['ms_per_frame']:.9f} modeledms/frame, including
all counters,326calls/frame,0misses. Cold physicalupload cost must come from
actualcoupledCRAM; functional worker latency and upload busy are synthetic.

The256KiB pool never recycles during an application. Different levels can fill
it and safely fallback to originalCPU LOAD. SourceVtx stride is the retained
24-byte immutable format. No production edits or deployment.
''')
write(P/'manifest.json',dict(files={str(p.relative_to(P)):sha(p)for p in P.rglob('*')if p.is_file()and 'build'not in p.parts and p.name!='qsim'},helpers={Path(m.__file__).name:sha(Path(m.__file__)),Path(__file__).name:sha(Path(__file__))},client_elf_sha256=sha(m.CAND/'app.elf'),host_cache_review_sha256='95cdb46686b365a62b2426b249d320ca6a68226f9b93db06949bdea47c448f1a',qualified_for_deployment=False))
print(proof['allocation_bytes'],warm)
