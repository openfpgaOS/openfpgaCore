#!/usr/bin/env python3
"""Read-only exact F3DEX2E command-graph analysis of the captured frame360.

Does not assume that subsequent frame entry CPU states include updated SDRAM.
Static matching is reported as observed ELF identity, not proof of immutability.
"""
from pathlib import Path
import collections,hashlib,json,struct
HERE=Path(__file__).resolve().parent;P=HERE.parents[1]
Q=P/'queued-front-v1';APP=Q/'work/sm64-v2'
ELF=APP/'.obj/sm64-queued-front-v2-perfdiag/app.elf'
SNAP=Q/'fixture-v2/capture/run-reference/snapshot'
SRC=APP/'src/sm64/sm64/src/pc/gfx/gfx_pc.c'
GBI=APP/'src/sm64/sm64/include/PR/gbi.h'
sha=lambda b:hashlib.sha256(b).hexdigest()
files=[Path(__file__).resolve(),HERE/'layout-v2.json',ELF,SNAP/'sdram.bin',SNAP/'state.json',SNAP/'cpu-state.bin',SRC,GBI]
binding={str(p):sha(p.read_bytes()) for p in files}
eb=ELF.read_bytes();eh=struct.unpack_from('<16sHHIIIIIHHHHHH',eb)
sections=[struct.unpack_from('<IIIIIIIIII',eb,eh[6]+eh[11]*i) for i in range(eh[12])]
st=sections[eh[13]];names=eb[st[4]:st[4]+st[5]]
sections={names[s[0]:].split(b'\0')[0].decode():s for s in sections}
symtab=sections['.symtab'];strings=sections['.strtab'];strings=eb[strings[4]:strings[4]+strings[5]]
symbols={}
for off in range(symtab[4],symtab[4]+symtab[5],symtab[9]):
    n,v,z,info,other,section=struct.unpack_from('<IIIBBH',eb,off)
    symbols[strings[n:].split(b'\0')[0].decode()]=(v,z,info,section)
base=0x10000000;memory=(SNAP/'sdram.bin').read_bytes();state=json.loads((SNAP/'state.json').read_text())
def mem(a,n):
    assert base<=a<=base+len(memory)-n,(hex(a),n)
    return memory[a-base:a-base+n]
def read(a):return struct.unpack('<II',mem(a,8))
def elf_match(a,n):
    for s in sections.values():
        if s[1]!=8 and s[2]&2 and s[3]<=a and a+n<=s[3]+s[5]:
            return mem(a,n)==eb[s[4]+a-s[3]:s[4]+a-s[3]+n]
    return False
def in_asset(a,n):return symbols['_start'][0]<=a and a+n<=symbols['__EH_FRAME_BEGIN__'][0]
def slots(w0,w1):
    op=w0>>24
    if op==5:return [[(w0>>16&255)//2,(w0>>8&255)//2,(w0&255)//2]]
    if op==6:return [[(w>>16&255)//2,(w>>8&255)//2,(w&255)//2] for w in (w0,w1)]
    return []
fog_addr=symbols['configEnableFog'][0];fog=bool(mem(fog_addr,1)[0])
layout=json.loads((HERE/'layout-v2.json').read_text())['fields']
assert symbols['rsp'][1]==layout['RSP_size'] and symbols['rdp'][1]==layout['RDP_size']
geom=struct.unpack('<I',mem(symbols['rsp'][0]+layout['RSP_geometry_mode'],4))[0];geom_known=0xffffffff
om_l=struct.unpack('<I',mem(symbols['rdp'][0]+layout['RDP_other_mode_l'],4))[0];om_known=0xffffffff
initial_state={'geometry_mode':geom,'other_mode_l':om_l}
lights=2;latest=set();live=set();events=[];calls=[]
def walk(start,depth=0):
    global geom,geom_known,om_l,om_known,lights,latest,live
    assert depth<128
    pc=start;calls.append(start)
    while True:
        assert len(events)<200000
        w0,w1=read(pc);op=w0>>24
        rec={'address':pc,'w0':w0,'w1':w1,'opcode':op,'depth':depth,'list':start,'geometry_mode':geom,'geometry_known':geom_known,'lights':lights,'other_mode_l':om_l}
        if op==0xd9:
            keep=w0&0xffffff;geom=(geom&keep)|w1;geom_known=((geom_known&keep)|(~keep)|w1)&0xffffffff
        elif op==0xdb and (w0>>16&255)==2:lights=w1//24+1
        elif op==0xe2:
            shift=31-(w0>>8&255);size=(w0&255)+1;mask=((1<<size)-1)<<shift
            om_l=(om_l&~mask)|w1;om_known|=mask
        elif op==1:
            count=w0>>12&255;first=(w0>>1&127)-count;own=set(range(first,first+count))
            raw_reasons=[]
            if not count or first<0 or first>=32 or count>32-first:raw_reasons.append('shape')
            if w1&3:raw_reasons.append('unaligned')
            if not in_asset(w1,24*count):raw_reasons.append('nonstatic_vertex_source')
            required=0x70000
            if geom_known&required!=required:raw_reasons.append('unknown_initial_geometry')
            if geom&0x40000:raw_reasons.append('texgen')
            if geom&0x20000 and lights!=2:raw_reasons.append('lighting_count')
            if fog and geom&0x10000:
                if om_known&0xc0000000!=0xc0000000:raw_reasons.append('unknown_initial_fog_mode')
                elif om_l>>30!=3:raw_reasons.append('eager_fog')
            rec.update(count=count,first=first,vertex_source=w1,own=sorted(own),raw_reasons=raw_reasons,vertex_bytes=count*24,vertex_elf_match=elf_match(w1,count*24))
            latest=own;live|=own
        ts=slots(w0,w1)
        if ts:
            used=set(sum(ts,[]));rec.update(triangles=ts,used=sorted(used),latest_owned=used<=latest,live_owned=used<=live)
        events.append(rec)
        if op==0xdf:return
        if op==0xde:
            if w0>>16&1:pc=w1;continue
            walk(w1,depth+1)
        pc+=24 if op in (0xe4,0xe5) else 16 if op==0xf6 else 8
walk(state['commands'])
# Tight candidate never crosses a list/state boundary and must consume whole
# TRI2 commands. A broader record allows repeated VTX and surviving slot reuse
# while still stopping every non-VTX/TRI opcode.
def candidate_groups(broad):
    out=[];i=0
    while i<len(events):
        first=events[i]
        if first['opcode']!=1:i+=1;continue
        j=i+1;owned=set(first['own']);group=[first]
        while j<len(events):
            e=events[j]
            if e['address']!=group[-1]['address']+8 or e['depth']!=first['depth'] or e['list']!=first['list']:break
            if e['opcode']==1 and broad:owned|=set(e['own'])
            elif e['opcode'] in (5,6):pass
            else:break
            group.append(e);j+=1
        tris=[t for e in group for t in e.get('triangles',[])]
        if tris:
            used=set(sum(tris,[]));raw=[e for e in group if e['opcode']==1]
            # Evaluate ownership at each triangle, including incoming partial
            # slots only in separate retained-state coverage below.
            active=set();owned_all=True
            for e in group:
                if e['opcode']==1:active|=set(e['own'])
                else:owned_all &= set(e.get('used',[]))<=active
            data=b''.join(struct.pack('<II',e['w0'],e['w1']) for e in group)
            topology=b''.join(struct.pack('<II',e['w0'],0 if e['opcode']==1 else e['w1']) for e in group)
            reasons=sorted(set(r for e in raw for r in e['raw_reasons']))
            if not owned_all:reasons.append('incoming_partial_slots')
            out.append({'start':first['address'],'end':group[-1]['address']+8,'commands':len(group),'loads':len(raw),'triangles':len(tris),'vertices':sum(e['count'] for e in raw),'own_references':owned_all,'raw_reasons':reasons,'raw_eligible':not reasons,'dl_bytes':len(data),'index_u16_bytes':len(tris)*2,'descriptor_bytes_assuming_16B_per_load_plus_16B_run':16*len(raw)+16,'elf_matches':all(elf_match(e['address'],8) for e in group),'vertices_elf_match':all(e['vertex_elf_match'] for e in raw),'exact_key':sha(data),'topology_key':sha(topology),'geometry_mode':first['geometry_mode']})
        i=j
    return out
tight=candidate_groups(False);broad=candidate_groups(True)
def dist(xs):return dict(sorted(collections.Counter(xs).items()))
def group_summary(gs):
    valid=[g for g in gs if g['raw_eligible']];keys=collections.Counter(g['exact_key'] for g in valid)
    return {'groups':len(gs),'triangles':sum(g['triangles'] for g in gs),'raw_eligible_groups':len(valid),'raw_eligible_triangles':sum(g['triangles'] for g in valid),'raw_eligible_vertices':sum(g['vertices'] for g in valid),'raw_eligible_dl_bytes_offered':sum(g['dl_bytes'] for g in valid),'raw_eligible_descriptor_bytes_16Bload_16Brun':sum(g['descriptor_bytes_assuming_16B_per_load_plus_16B_run'] for g in valid),'raw_eligible_elf_matching_groups':sum(g['elf_matches'] and g['vertices_elf_match'] for g in valid),'raw_eligible_unique_exact_spans':len(keys),'raw_eligible_repeat_invocations':sum(n-1 for n in keys.values()),'raw_eligible_unique_topologies':len(set(g['topology_key'] for g in valid)),'triangles_per_group':dist(g['triangles'] for g in gs),'loads_per_group':dist(g['loads'] for g in gs),'raw_fallback_reasons':dist(r for g in gs for r in g['raw_reasons'])}
loads=[e for e in events if e['opcode']==1];tri_events=[e for e in events if 'triangles' in e]
out={'status':'PASS_OFFLINE_FROZEN_FRAME360_COMMAND_GRAPH','frame':360,'root':hex(state['commands']),'inputs_sha256':binding,'fog_enabled':fog,'initial_renderer_state':initial_state,'command_count':len(events),'display_list_calls':len(calls),'unique_display_list_entries':len(set(calls)),'opcodes':{hex(k):v for k,v in sorted(collections.Counter(e['opcode'] for e in events).items())},'raw_loads':len(loads),'raw_vertices':sum(e['count'] for e in loads),'raw_load_reasons':dist(r for e in loads for r in e['raw_reasons']),'triangles_total':sum(len(e['triangles']) for e in tri_events),'triangles_latest_load_owned':sum(len(e['triangles']) for e in tri_events if e['latest_owned']),'triangles_all_prior_loads_owned':sum(len(e['triangles']) for e in tri_events if e['live_owned']),'tight':group_summary(tight),'broader':group_summary(broad),'caveats':['Only frame360 has complete captured SDRAM. Later CPU entry dumps reuse the root address but do not snapshot mutated dynamic display lists; no warm-frame topology or cross-frame reuse is inferred.','Raw eligibility here checks source range/alignment/shape/geometry/light count/fog convention only. It does not establish transformed arithmetic, clipping, equal-W, or material eligibility.','ELF byte matching establishes observed static asset identity, not future lifetime or absence of self-modification. Const/source provenance and an explicit immutability contract remain required.','Descriptor byte estimates exclude matrix/light/material instance state, backend command bytes, and ownership/caching overhead.','Static traversal follows exact current G_DL push/branch and F3DEX2E rectangle lengths, preserving every state/list boundary. No CPU or GPU state is changed.']}
(HERE/'coverage-frame360.json').write_text(json.dumps(out,indent=2)+'\n')
(HERE/'coverage-frame360-events.json').write_text(json.dumps(events,separators=(',',':'))+'\n')
(HERE/'coverage-frame360-spans.json').write_text(json.dumps({'tight':tight,'broader':broad},indent=2)+'\n')
assert binding=={str(p):sha(p.read_bytes()) for p in files}
print(json.dumps({k:out[k] for k in ['command_count','display_list_calls','raw_loads','raw_vertices','triangles_total','triangles_latest_load_owned','tight','broader']},indent=2))
