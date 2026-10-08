#!/usr/bin/env python3
"""Private offline canonical position IDs in immutable Vtx.flag, CPU probe."""
import argparse
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import struct
from elftools.elf.elffile import ELFFile
ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase9/checks-state-f11'
PARENT=CAMP/'phase10/position-memo-v2'
OUT=CAMP/'phase10/position-id-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def prepare():
    inputs={n:(PARENT/n).read_text() for n in json.loads((PARENT/'source.json').read_text())['source_sha256']}
    s=inputs['gfx_pc.c']
    s=s.replace('    uint32_t epoch, key_x, key_y, key_z;\n    uint8_t clip_rej, pad[3];','    uint32_t epoch;\n    uint16_t key;\n    uint8_t clip_rej, pad;')
    s=s.replace('static struct PositionMemo position_memo[64];','static struct PositionMemo position_memo[1024];\nextern const unsigned char _start[], __EH_FRAME_BEGIN__[];')
    old='    const float aspect_scale = gfx_vertex_aspect_scale;'
    new='''    const float aspect_scale = gfx_vertex_aspect_scale;
#if GFX_CPU_POSITION_MEMO
    const uintptr_t source_address = (uintptr_t)vertices;
    const uintptr_t readonly_end = (uintptr_t)__EH_FRAME_BEGIN__;
    const bool position_ids_valid = source_address >= (uintptr_t)_start &&
        source_address < readonly_end && n_vertices <= (readonly_end - source_address) / sizeof(Vtx);
#endif'''
    assert s.count(old)==1;s=s.replace(old,new)
    old='''        const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
        uint32_t key_x, key_y, key_z;
        memcpy(&key_x, &ox, sizeof(key_x));
        memcpy(&key_y, &oy, sizeof(key_y));
        memcpy(&key_z, &oz, sizeof(key_z));
        const uint32_t hash = ((key_x * 31u + key_y) * 31u + key_z) * 0x85ebca6bu;
        struct PositionMemo *pm = &position_memo[hash >> 26];
        if (pm->epoch == position_memo_epoch && pm->key_x == key_x &&
            pm->key_y == key_y && pm->key_z == key_z) {'''
    new='''        const uint16_t key = position_ids_valid ? v->flag : 0;
        struct PositionMemo *pm = &position_memo[key & 1023u];
        if (key && pm->epoch == position_memo_epoch && pm->key == key) {'''
    assert s.count(old)==1;s=s.replace(old,new)
    s=s.replace('''#if !GFX_CPU_POSITION_MEMO
            const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
#endif''','''            const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];''')
    old='''            pm->epoch = position_memo_epoch;
            pm->key_x = key_x; pm->key_y = key_y; pm->key_z = key_z;
            pm->x_out = x; pm->y_out = y; pm->z_out = z; pm->w_out = w;
            pm->w_inv = w_inv; pm->clip_rej = clip_rej;'''
    new='''            if (key) {
                pm->epoch = position_memo_epoch; pm->key = key;
                pm->x_out = x; pm->y_out = y; pm->z_out = z; pm->w_out = w;
                pm->w_inv = w_inv; pm->clip_rej = clip_rej;
            }'''
    assert s.count(old)==1;s=s.replace(old,new)
    inputs['gfx_pc.c']=s
    OUT.mkdir(exist_ok=False)
    for n,c in inputs.items():(OUT/n).write_text(c)
    original=(BASE/'gfx_pc.c').read_text()
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='F11/gfx_pc.c',tofile='position-id-v1/gfx_pc.c')))
    (OUT/'source.json').write_text(json.dumps(dict(parent='phase9/checks-state-f11',source_sha256={n:sha(OUT/n) for n in inputs},recipe_sha256=sha(Path(__file__)),qualified=False,contract='Only privately packed ELF gives read-only const Vtx arrays canonical exact-XYZ IDs in unused flag. Dynamic/unpacked flags zero use original math. Matrix/aspect epoch prevents stale result reuse. Source ELF remains preserved.'),indent=2)+'\n')
    print(OUT)

def pack():
    elf=OUT/'app.elf';dest=OUT/'packed.elf';assert not dest.exists()
    catalog={};sources={}
    for p in (OUT/'SM64/src/sm64/sm64').rglob('*'):
        if p.suffix not in ('.c','.h') or not p.is_file():continue
        s=p.read_text(errors='replace')
        names=re.findall(r'\bconst\s+Vtx\s+(\w+)\s*\[',s)
        if names:
            sources[str(p.relative_to(OUT/'SM64'))]=sha(p)
            for n in names:catalog[n]=True
    data=bytearray(elf.read_bytes());targets=[];rejected=[]
    with elf.open('rb') as f:
        e=ELFFile(f)
        assert e.elfclass==32 and e.little_endian
        for sym in e.get_section_by_name('.symtab').iter_symbols():
            idx=sym['st_shndx'];size=sym['st_size']
            if sym.name not in catalog or sym['st_info']['type']!='STT_OBJECT' or not isinstance(idx,int) or not size:continue
            sec=e.get_section(idx)
            if (sec['sh_flags']&1) or size%24:
                rejected.append(dict(name=sym.name,reason='writable or unexpected stride',size=size));continue
            offset=sec['sh_offset']+sym['st_value']-sec['sh_addr']
            vertices=[bytes(data[offset+i:offset+i+24]) for i in range(0,size,24)]
            if any(struct.unpack_from('<H',v,12)[0] for v in vertices):
                rejected.append(dict(name=sym.name,reason='original flag not zero'));continue
            if any(not all(math.isfinite(x) for x in struct.unpack_from('<3f',v)) for v in vertices):
                rejected.append(dict(name=sym.name,reason='nonfinite XYZ'));continue
            targets.append((sym['st_value'],offset,sym.name,vertices))
    ids={};edits=[];patched_addresses=set()
    for address,offset,name,vertices in sorted(targets):
        for i,v in enumerate(vertices):
            key=v[:12]
            if key not in ids:ids[key]=len(ids)+1
            index=ids[key];assert 1<=index<=65535
            pos=offset+24*i+12
            assert pos not in patched_addresses
            patched_addresses.add(pos)
            assert data[pos:pos+2]==b'\0\0'
            struct.pack_into('<H',data,pos,index)
        edits.append(dict(name=name,address=address,file_offset=offset,count=len(vertices)))
    dest.write_bytes(data)
    assert len(data)==elf.stat().st_size
    old=elf.read_bytes()
    diff=[i for i,(a,b) in enumerate(zip(old,data)) if a!=b]
    assert all((i in patched_addresses or i-1 in patched_addresses) for i in diff)
    rec=dict(input_elf_sha256=sha(elf),packed_elf_sha256=sha(dest),packer_sha256=sha(Path(__file__)),source_catalog_sha256=sources,arrays=edits,vertices=sum(x['count'] for x in edits),unique_positions=len(ids),changed_bytes=len(diff),unchanged_bytes_except_const_vertex_flags=True,rejected=rejected,qualified=False)
    (OUT/'packing.json').write_text(json.dumps(rec,indent=2)+'\n')
    print(json.dumps({k:v for k,v in rec.items() if k not in ('source_catalog_sha256','arrays','rejected')},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','pack']);a=p.parse_args();globals()[a.action]()
