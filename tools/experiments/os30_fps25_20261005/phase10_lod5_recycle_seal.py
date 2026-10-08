#!/usr/bin/env python3
"""Seal whole-app functional recycling and generation byte ownership."""
import csv,json,struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
import phase10_lod5_recycle_capture as m
P=m.OUT;sha=m.sha;write=m.write
v=m.module('recycle_vec',P/'vector16.py')
with(m.CAND/'app.elf').open('rb')as f:elf=ELFFile(f);segments=[(int(s['p_vaddr']),s.data())for s in elf.iter_segments()if s['p_filesz']]
def read(a,n):
 matches=[b[a-base:a-base+n]for base,b in segments if base<=a and a+n<=base+len(b)];assert len(matches)==1;return matches[0]
proof={}
for mode in('normal','fullpool'):
 tex=v.vector((P/f'capture-{mode}/gpuvec/frame_00720.vec').read_bytes())['texture'];entries=[]
 for ptr,oc in struct.iter_unpack('<II',(P/f'capture-{mode}/lod5-pool.bin').read_bytes()):
  if not ptr:continue
  off=oc&0xffffff;n=oc>>24;assert 1<=n<=32 and not(off&63) and 0xfc0000<=off and off+24*n<=0x1000000
  assert read(ptr,24*n)==tex[off:off+24*n]
  entries.append(dict(source=ptr,offset=off,count=n,bytes=24*n,allocation=(24*n+63)&~63))
 entries.sort(key=lambda x:x['offset'])
 for a,b in zip(entries,entries[1:]):assert a['offset']+a['allocation']==b['offset']
 assert entries[0]['offset']==0xfc0000
 proof[mode]=dict(entries=entries,count=len(entries),bytes=sum(e['bytes']for e in entries),allocated=sum(e['allocation']for e in entries),all_final_source_bytes_match_immutable_elf=True)
write(P/'pool-byte-proof.json',proof)
results={n:json.loads((P/f'result-{n}.json').read_text())for n in('normal','faults','absent','timeout','fullpool')};assert all(x['passed']for x in results.values())
rows=list(csv.DictReader((P/'pool-stats-fullpool.csv').open()));events=[];last=0
for r in rows:
 n=int(r['recycles'])
 if n!=last:assert n==last+1;events.append(int(r['frame']));last=n
assert events==[2,235,355] and events[2]-events[1]==120
assert int(rows[-1]['throttled'])==40693 and int(rows[-1]['hits'])==130533
write(P/'recycle-events.json',dict(recycles_at_frames=events,forced_capacity_at='Initial ID read; frame callbacks234and235 set allocationtop=end without touching activeDMA.',misses_during_cooldown=40693,hits=130533,uploads=731,all_frames_counts_and_eight_streams_exact=True,scope='Functional capacity stress; physical full256KiB allocation, collisions and wrap tested independently by actual-C host tests.'))
(P/'REPORT.txt').write_text('''Private LOD5 bounded source-pool recycling.

The source cache now clears all old descriptors and restarts its reserved
256KiB arena on capacity miss. First recycle is immediate; later recycling
requires120 renderedframes, measured by a naturally wrapping uint32serial.
Hits remain available duringcooldown. Misses fallbacktooriginalCPU; bounded
probe exhaustion neverrecycles. Existing synchronousLOAD ownership protects
reuse; GPU packets containcopiedresults and never reference source bytes.

All five modes(normal,faults,absent,drainedtimeout,forcedcapacity) preserve
721 scene/geometry/culling records and commandwordcounts plus eight sampled
ordered renderstreams. qsimdoesnotrender indexedopcodes; these areNOT721
renderedframe comparisons. Hardware rasterqualification remains separate.
Normal doeszero recycles. Capacitystressrecyclesatframes2,235,355; the latter
interval isexactly120. Thereare40693 throttledmisses,130533hits,731uploads
acrossthreegenerations. Every finalcachedspan matchesimmutableELFbytes at
its actual highCRAMoffset. Capacityinjectionchangesonlythebump-pointer to
force the cold path; independenthosttestsfillthephysicalarena andtestwrap.

AppELF isnew; originalLOD5clientremainssealedandunchangedforunifiedhardware
comparisons. New source-strideassert requiresVtx24B. No deployment, hardware
fit, or physicalFPSclaim. This workload doesnotexercise real multipleworld
working sets; cooldownchoice mayneeddeviceworkload tuning later.
''')
write(P/'manifest.json',dict(files={str(p.relative_to(P)):sha(p)for p in P.rglob('*')if p.is_file()and'build'not in p.parts and p.name not in('qsim','manifest.json')},helpers={Path(m.__file__).name:sha(Path(m.__file__)),Path(__file__).name:sha(Path(__file__))},client_elf_sha256=sha(m.CAND/'app.elf'),qualified_for_deployment=False))
print('manifest',sha(P/'manifest.json'));print(events,{k:v['count']for k,v in proof.items()})
