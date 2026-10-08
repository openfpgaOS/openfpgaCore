#!/usr/bin/env python3
"""Bind real producer, reference consumer and conservative whole-load coverage."""
import csv
import json
import struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,sha,write
OUT=CAMP/'phase10/raw-consumer-capture';BASE=CAMP/'phase9/checks-state-f11'

def main():
    # Prove passive consumer capture did not affect any original guest work.
    proof={};a=CAMP/'phase10/raw-producer-v2/capture-normal';b=OUT/'capture-normal'
    for p in a.rglob('*'):
        if not p.is_file():continue
        if p.name in ('frames.csv','scene.csv','geometry.csv','culling.csv') or p.suffix in ('.vec','.tsv'):
            rel=p.relative_to(a);assert p.read_bytes()==(b/rel).read_bytes(),rel;proof[str(rel)]=sha(p)
    with (BASE/'app.elf').open('rb') as f:
        e=ELFFile(f);t=e.get_section_by_name('.text');low=t['sh_addr'];data=t.data()
    def source(a,n):
        assert low<=a and a+n<=low+len(data);return data[a-low:a-low+n]
    blob=(OUT/'consumer-input.bin').read_bytes();num=struct.unpack_from('<I',blob)[0];o=4;loads=[]
    for k in range(num):
        h=struct.unpack_from('<4I',blob,o);o+=16
        matrix=struct.unpack_from('<17I',blob,o);o+=68
        light=struct.unpack_from('<22I',blob,o);o+=88
        ctrl=struct.unpack_from('<3I',blob,o);o+=12+24*h[2]
        d=blob[o:o+36*h[2]];o+=len(d)
        inside=all(not d[36*i+32] and 0<struct.unpack_from('<I',d,36*i+12)[0]<0x46fffe00 for i in range(h[2]))
        attrs=not(ctrl[0]&0x40000) and (not(ctrl[0]&0x20000) or 1<=light[21]<=3)
        loads.append(dict(header=h,inside=inside,attrs=attrs))
    assert o==len(blob)
    rows=list(csv.DictReader((CAMP/'phase10/raw-frontend-observer/capture-normal/raw-frontend.csv').open()))
    materials=list(csv.DictReader((CAMP/'phase10/raw-material-observer-v2/capture-normal/raw-material.csv').open()))
    mr=iter(r for r in materials if r['kind']=='indexed');li=iter(loads);inside_mask=attr_mask=0;selected=[]
    for row in rows:
        r={k:(v if k=='kind' else int(v)) for k,v in row.items()}
        if r['kind']=='vertex':
            count,first=r['count'],r['first'];mask=((1<<count)-1)<<first
            inside_mask&=~mask;attr_mask&=~mask
            if r['immutable'] and first<32 and count<=32-first:
                load=next(li);assert (r['frame'],count,first)==(load['header'][0],load['header'][2],load['header'][3])
                if load['inside']:inside_mask|=mask
                if load['attrs']:attr_mask|=mask
        else:
            material={k:(v if k=='kind' else int(v)) for k,v in next(mr).items()};assert material['frame']==r['frame'] and material['triangles']==r['triangles']
            if not 360<=r['frame']<=720:continue
            used=0
            if r['immutable']:
                raw=source(r['address'],r['count']*8)
                for c in range(r['count']):
                    w0,w1=struct.unpack_from('<2I',raw,c*8);assert w0>>24 in (5,6)
                    for w in [w0] if w0>>24==5 else [w0,w1]:
                        for shift in [16,8,0]:used|=1<<((w>>shift&255)//2)
            inside=r['immutable'] and r['all_vertices_immutable'] and used&inside_mask==used
            attrs=used&attr_mask==used
            fully_supported=inside and (not material['ready'] or (attrs and material['material_supported'] and not material['w_fallback'] and not material['clip_fallback']))
            selected.append(dict(**r,wholeload_inside=inside,fully_supported=fully_supported,material=material))
    try:next(li);raise AssertionError('remaining LOADs')
    except StopIteration:pass
    try:next(mr);raise AssertionError('remaining RUNs')
    except StopIteration:pass
    summary={}
    for key,group in [('all_indexed_runs',selected),('wholeload_inside',[r for r in selected if r['wholeload_inside']]),('fully_supported',[r for r in selected if r['fully_supported']])]:
        summary[key]=dict(calls=len(group),triangles=sum(r['triangles'] for r in group),original_inclusive_cycles=sum(r['inclusive_cycles'] for r in group),original_ms_per_frame=sum(r['inclusive_cycles'] for r in group)/361/1e5)
    mt=[{k:(v if k=='kind' else int(v)) for k,v in r.items()} for r in materials if 360<=int(r['frame'])<=720]
    keys=['triangles','immutable','attributes_supported','inside','trivial_clip','w_fallback','clip_fallback']
    full={k:sum(r[k] for r in mt) for k in keys}
    full['immutable_rejected_before_material']=sum(r['immutable'] for r in mt if not r['ready'])
    full['conservative_per_triangle_supported']=full['inside']+full['trivial_clip']+full['immutable_rejected_before_material']
    full['per_triangle_fraction']=full['conservative_per_triangle_supported']/full['triangles']
    summary['all_input_triangles']=full
    write(OUT/'frontend-coverage.json',dict(passed_passive_capture=True,noninterference_sha256=proof,whole_load_run_coverage=summary,elf_sha256=sha(BASE/'app.elf'),material_observation_sha256=sha(CAMP/'phase10/raw-material-observer-v2/capture-normal/raw-material.csv'),producer_input_sha256=sha(OUT/'consumer-input.bin'),helper_sha256=sha(Path(__file__)),limits=['All input triangles include scalar rectangle/material fallbacks; immutable run subset excludes those.','Entire call must pass for fully_supported; mixed/material/clip/equal-W fallbacks conservatively reject it.','Cycle costs are original inclusive CPU work, not a projected frame speedup.','Readback, submission transport, material state copy and hardware throughput are not measured by this coverage record.']))
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
