#!/usr/bin/env python3
"""Replay the opened F4 panel only to check layout/ABI, never device FPS."""
from common import *
import csv,subprocess,struct
from PIL import Image
src=OUT/'capture-render-v5-panel-open';m=read(src/'inputs.json')
assert m['success'] and m['panel'] and not m['geometry_capability'] and m['audio']
for rel,h in m['results_sha256'].items():assert sha(src/rel)==h
rows=list(csv.DictReader((src/'diagnostic.csv').open()));r=rows[640]
assert r['frame']=='640' and r['stat0']=='1' and r['stat18']=='4' and r['stat19']=='0'
assert all(r['stat'+str(i)]=='0' for i in range(20,39))
dest=OUT/'panel-smoke';dest.mkdir(exist_ok=True);assert not (dest/'panel.bin').exists()
vec=next((src/'gpuvec').glob('*.vec'));assert int(vec.stem.split('_')[1])==640
counts,depth=load('replay').with_depth(vec,dest/'panel.vec')
exe=PREVIOUS/'replay-single-A/obj/Vtb_gpu';hardware=read(PREVIOUS/'replay-single-A/manifest.json')
assert sha(exe)==hardware['simulator_sha256']
with (dest/'replay.log').open('w') as f:subprocess.run([str(exe),str(dest/'panel.vec'),str(dest/'panel.bin'),'+memory_traffic=2'],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
a=(dest/'panel.bin').read_bytes();assert len(a)==153612+153600
rgb=[]
for v in struct.unpack('<76800H',a[:153600]):rgb.extend((((v>>11)*255+15)//31,(((v>>5)&63)*255+31)//63,((v&31)*255+15)//31))
im=Image.frombytes('RGB',(320,240),bytes(rgb));im.save(dest/'panel.png')
record={'passed_abi':True,'image':str((dest/'panel.png').relative_to(ROOT)),'snapshot_version':4,'detail_samples':0,'note':'Synthetic-clock simulator output for panel layout/ABI only, not Pocket FPS. Visual inspection required.','elf_sha256':sha(src/'sm64.elf'),'inputs_sha256':sha(src/'inputs.json'),'simulator_sha256':sha(exe),'vector_sha256':sha(vec),'image_sha256':sha(dest/'panel.png')}
(OUT/'panel-smoke.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
