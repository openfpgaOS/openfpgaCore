#!/usr/bin/env python3
"""Stage worker batching, optionally testing CPU-culling offload, in a private tree."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-worker-20261003'
SOURCE=ROOT.parent/'SM64'
DEST=OUT/'SM64'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--offload-cull',action='store_true')
args=p.parse_args()
DEST.mkdir(exist_ok=False)
for name in ('src','runtime','dist','scripts','tools'):
    shutil.copytree(SOURCE/name,DEST/name,ignore=shutil.ignore_patterns('.git','__pycache__'))
shutil.copytree(SOURCE/'.obj/sm64',DEST/'.obj/sm64')
paths=['src/sm64/sm64/src/pc/gfx/'+n for n in ('gfx_gpu.c','gfx_gpu.h','gfx_pc.c')]
before={p:(DEST/p).read_text() for p in paths}

def replace(path,old,new):
    p=DEST/path;s=p.read_text();assert s.count(old)==1,(path,old);p.write_text(s.replace(old,new))

replace(paths[0],'static int g_geometry;','static int g_geometry;\nstatic int g_geometry_worker;')
replace(paths[0],'int gfx_gpu_geometry_enabled(void) { return g_geometry; }',
    'int gfx_gpu_geometry_enabled(void) { return g_geometry; }\nint gfx_gpu_geometry_worker_enabled(void) { return g_geometry_worker; }')
replace(paths[0],'        printf("[SM64] geometry accelerator=%d\\n", g_geometry);',
    '        g_geometry_worker = g_geometry && ((GPU_STATUS & (1u<<11)) != 0);\n'
    '        printf("[SM64] geometry accelerator=%d worker=%d\\n", g_geometry, g_geometry_worker);')
replace(paths[1],'int gfx_gpu_geometry_enabled(void);','int gfx_gpu_geometry_enabled(void);\nint gfx_gpu_geometry_worker_enabled(void);')
if args.offload_cull:
    replace(paths[2],'static Gfx *gfx_geometry_run(Gfx *first) {\n    Gfx *cmd=first;',
        'static Gfx *gfx_geometry_run(Gfx *first) {\n    Gfx *cmd=first;\n    const bool worker = gfx_gpu_geometry_worker_enabled();')
    replace(paths[2],'            reject[i]=geometry_reject(v[0],v[1],v[2]);',
        '            /* The independent worker overlaps vertex preparation with\n'
        '             * rasterization. Let the existing exact screen-space GPU\n'
        '             * winding test reject these draws, avoiding duplicate CPU\n'
        '             * floating-point culling. Both-sided rejection is trivial. */\n'
        '            reject[i]=worker ? cull==3u : geometry_reject(v[0],v[1],v[2]);')
replace(paths[0],'while(n<3 && i+n<count && upload[i+n].serial==upload[i].serial)n++;',
    'while(n<(g_geometry_worker?32u:3u) && i+n<count && upload[i+n].serial==upload[i].serial)n++;')
patch=''.join(''.join(difflib.unified_diff(before[p].splitlines(True),(DEST/p).read_text().splitlines(True),
    fromfile='a/'+p,tofile='b/'+p)) for p in paths)
(OUT/'sm64-worker.patch').write_text(patch)
(OUT/'app-source-inputs.json').write_text(json.dumps({p:hashlib.sha256(before[p].encode()).hexdigest() for p in paths},indent=2)+'\n')
(OUT/'app-configuration.json').write_text(json.dumps(dict(offload_cull=args.offload_cull, max_batch=32),indent=2)+'\n')
q=OUT/'qsim'
shutil.copytree(ROOT/'docs/measurements/os30-feature-audit-20261003/qsim',q,
    ignore=shutil.ignore_patterns('qsim','build','__pycache__'))
p=q/'gpu.c';s=p.read_text();old='| (getenv("SM64_GEOMETRY_CAP") ? 0x400u : 0)';assert s.count(old)==1
p.write_text(s.replace(old,old+'\n             | (getenv("SM64_WORKER_CAP") ? 0x800u : 0)'))
with (OUT/'build-qsim.log').open('w') as f:
    subprocess.run(['make','-C',str(q),'-j3'],stdout=f,stderr=subprocess.STDOUT,check=True)
print(DEST,flush=True)
