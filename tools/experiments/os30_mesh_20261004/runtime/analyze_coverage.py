#!/usr/bin/env python3
"""Summarize diagnostic producer decisions; counts precede CPU/GPU face culling."""
from pathlib import Path
import argparse,json,struct
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('capture',type=Path);p.add_argument('--start',type=int,default=360)
p.add_argument('--frames',type=int,default=8);a=p.parse_args()
def read(frame):return struct.unpack('<280I',(a.capture/f'coverage-{frame:05d}.bin').read_bytes())
before=read(a.start-1);after=read(a.start+a.frames-1)
delta=[(y-x)&0xffffffff for x,y in zip(before,after)]
reasons=['submitted','disabled','no_active_whole_load','guardflag','run_mutable',
         'run_format','run_capacity','run_mixed','material_backend','material_lod',
         'material_shade_xlu','material_fog_alpha','submit_fail','unused13','unused14','unused15']
fields=['calls','triangles','referenced_slots','accepted_slots','triangles_any_accepted',
        'triangles_all_accepted','triangles_current_accepted','pending_slots']
rows={reasons[i]:dict(zip(fields,delta[8*i:8*i+8])) for i in range(16) if any(delta[8*i:8*i+8])}
backend={str(i):delta[128+4*i:128+4*i+4] for i in range(32) if any(delta[128+4*i:128+4*i+4])}
lazy=dict(zip(['loads_deferred','vertices_deferred','vertices_materialized','vertices_discarded',
               'vertices_preserved_on_reload','eager_loads','eager_vertices','materialization_batches'],delta[256:264]))
conclusions=[]
if not any(name in rows for name in ('run_mutable','run_format','run_capacity','run_mixed','material_lod','material_shade_xlu','material_fog_alpha')):
    conclusions.append('No mixed-load, parser, capacity, LOD or shade rejection occurs in this captured window.')
if set(backend)<=set(('4','8')):
    conclusions.append('Accepted fallback faces all use decal or texture-alpha-blend materials.')
if ('run_mixed' not in rows and not rows.get('no_active_whole_load',{}).get('accepted_slots',0)
    and lazy['vertices_materialized']==lazy['vertices_preserved_on_reload']+sum(r['pending_slots'] for name,r in rows.items() if name!='submitted')):
    conclusions.append('Surviving old accepted vertices are eagerly materialized on partial reload despite no later fallback face referring to them.')
result=dict(frames=a.frames,start=a.start,rows=rows,backend_masks=backend,lazy=lazy,
            scope='Diagnostic-only counters before face culling, not performance timings; slot references may repeat across commands.',
            backend_bits={'1':'not truecolor','2':'missing shader','4':'decal','8':'texture-alpha blend','16':'textured C+D'},
            conclusions=conclusions)
(a.capture/'coverage-summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
