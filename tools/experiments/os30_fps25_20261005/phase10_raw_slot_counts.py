#!/usr/bin/env python3
"""Read-only bound on pre-cull raw-slot transport from existing F11 observer."""
from pathlib import Path
import struct,json,collections,hashlib
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
F=P/'run-consumer-v1/worker-input.bin';OUT=P/'overlap-architecture-audit-v1'
verts=[None]*32;gens=[0]*32;generation=0;lastframe=-1
uploaded_known=[0]*32;uploaded_all=[0]*32
stats=collections.Counter();pending=None;draw=None
with F.open('rb') as f:
 events=struct.unpack('<I',f.read(4))[0]
 for event in range(events):
  op,n=struct.unpack('<2I',f.read(8));p=struct.unpack('<%dI'%n,f.read(n*4))
  if op==4:
   frame,source,count,first=p[:4];generation+=1
   if frame!=lastframe:uploaded_known=[0]*32;uploaded_all=[0]*32;lastframe=frame
   for k in range(count):verts[first+k]=p[4+15*k+6:4+15*k+15];gens[first+k]=generation
   if 360<=frame<=720:stats['loaded_vertices']+=count;stats['loads']+=1
  elif op==5:pending=p;draw=None
  elif op==6:draw=p
  elif op==7:
   frame,source,cull,owned=pending[:4];expected_end,words,triangles=p[1:4]
   if not 360<=frame<=720:continue
   stats['runs']+=1;stats['original_uploaded_vertices']+=(words-2*triangles)//9;stats['original_surviving_triangles']+=triangles;stats['original_packet_words']+=words
   assert (words-2*triangles)%9==0
   flags=draw[1] if draw else 0;rgb=flags>>24;alpha=flags>>20&15
   mat=draw and ((flags>>8)&255) and rgb!=7 and alpha!=7 and not(alpha==4 and not(flags&(1<<16)))
   stats['material_known_eligible_runs']+=bool(mat);stats['material_unknown_allculled_runs']+=not bool(draw)
   # The original consumed range can include its final fallback TRI command.
   commands=pending[4:4+(expected_end-source)//4]
   for ci in range(0,len(commands),2):
    w0,w1=commands[ci:ci+2];nt=1 if w0>>24==5 else 2 if w0>>24==6 else 0
    if not nt:raise ValueError('nontri')
    slots=[[(w>>(16-8*k)&255)//2 for k in range(3)] for w in [w0,w1][:nt]]
    ownedok=all(s<32 and owned&(1<<s) and verts[s] is not None for tri in slots for s in tri)
    stats['consumed_commands']+=1;stats['consumed_triangles']+=nt
    if not ownedok:stats['unowned_commands']+=1;continue
    # Entire TRI2 must meet cheap pre-cull geometry guards to avoid async error/fallback.
    inside=all((verts[s][8]&255)==0 for tri in slots for s in tri)
    goodw=all(0<verts[s][3]<0x46fffe00 for tri in slots for s in tri)
    varied=all(not(verts[t[0]][3]==verts[t[1]][3]==verts[t[2]][3]) for t in slots)
    if not(inside and goodw and varied):stats['preculled_geometry_guard_fallback_commands']+=1;continue
    for key,eligible,uploaded in [('known',bool(mat),uploaded_known),('optimistic',True,uploaded_all)]:
     if not eligible:continue
     stats[key+'_raw_triangles']+=nt
     for tri in slots:
      for slot in tri:
       if uploaded[slot]!=gens[slot]:stats[key+'_raw_uploads']+=1;uploaded[slot]=gens[slot]
for key in ['known','optimistic']:stats[key+'_raw_packet_words']=9*stats[key+'_raw_uploads']+2*stats[key+'_raw_triangles']
r={'scope':'Read-only F11 IEEE observer counts; no new hardware, cost claim, native equivalence or material call-order proof. CPU raw-slot design guards entire TRI1/TRI2 for clip/W before cull. Raw source subset is immutable because old observer omits mutable LOAD bodies. Raw uploads deduplicated by actual LOAD generation and frame, independent of material. Known path counts only calls whose existing CPU run prepared an eligible material; optimistic includes unknown all-culled calls and unsupported materials as an upper coverage/traffic bound. Existing prefix counts include original selected material behavior.','frames':361,'counts':dict(stats),'per_frame':{k:v/361 for k,v in stats.items()},'source_sha256':hashlib.sha256(F.read_bytes()).hexdigest()}
(OUT/'raw-slot-counts.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r['per_frame'],indent=2))
