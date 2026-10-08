#!/usr/bin/env python3
"""Freeze/build the worker and compare current SM64 renders to retained RTL."""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-worker-20261003'
PRIOR = ROOT / 'docs/measurements/sm64-geometry-fast-20261003'
SOURCE = PRIOR / 'replay-prefetch-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
load = lambda p: json.loads(p.read_text())

def prepare(label,mlab):
    dest = OUT / ('replay-' + label)
    dest.mkdir(exist_ok=False)
    manifest = load(SOURCE / 'manifest.json')
    for rel, digest in manifest['sources'].items():
        assert sha(SOURCE / rel) == digest, rel
    shutil.copytree(SOURCE / 'frozen', dest / 'frozen')
    for name in ('gpu_core.v','gpu_geometry_worker.v'):
        shutil.copy2(ROOT / 'src/fpga/common' / name, dest / 'frozen/common' / name)
    cmd = [a.replace(str(SOURCE), str(dest)) for a in manifest['command']]
    if not mlab:
        cmd = [a for a in cmd if a != '+define+INCLUDE_TEX_QUEUE_MLAB']
    assert cmd.count('-GINCLUDE_GPU_XFORM_MAC=1') == 1
    cmd[cmd.index('-GINCLUDE_GPU_XFORM_MAC=1')] = '-GINCLUDE_GPU_XFORM_MAC=0'
    cmd += ['+define+INCLUDE_GPU_GEOMETRY_WORKER', '+define+EXCLUDE_GPU_XFORM_MAC',
            str(dest / 'frozen/common/gpu_geometry_worker.v')]
    manifest.update(label=label, command=cmd, scope='Independent geometry worker + rasterizer RTL; component cycles, not device FPS',
        sources={str(p.relative_to(dest)):sha(p) for p in (dest / 'frozen').rglob('*') if p.is_file()})
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    with (dest / 'build.log').open('w') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=600)
    return dest

def run(job):
    dest, baseline, capture = job
    mode, frame = baseline['mode'], baseline['frame']
    out = dest / 'runs' / str(mode)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f'frame_{frame:05}'
    vec = PRIOR / 'replays-prefetch/final-candidate' / str(mode) / f'frame_{frame:05}.vec'
    if capture:
        spec=importlib.util.spec_from_file_location('depth_vectors',ROOT/'tools/experiments/sm64_geometry_fast_20261003/replay.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        source=OUT/('capture-'+capture)/'gpuvec'/f'frame_{frame:05}.vec'
        vec=stem.with_suffix('.vec');module.with_depth(source,vec)
    cmd = [str(dest / 'obj/Vtb_gpu'), str(vec), str(stem.with_suffix('.bin')), f'+memory_traffic={mode}']
    with stem.with_suffix('.log').open('w') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=600)
    text = stem.with_suffix('.log').read_text()
    stats = {k:int(v) for k,v in re.findall(r'(\w+)=(\d+)', text)}
    data = stem.with_suffix('.bin').read_bytes()
    assert len(data)==153612+(153600 if baseline['depth_sha256'] else 0)
    fb = hashlib.sha256(data[:153600]).hexdigest()
    depth = hashlib.sha256(data[153612:]).hexdigest() if baseline['depth_sha256'] else None
    row = dict(mode=mode, frame=frame, baseline_cycles=baseline['stats']['cycles'], cycles=stats['cycles'],
        extra_gpu_time_percent=100*(stats['cycles']/baseline['stats']['cycles']-1),
        framebuffer_sha256=fb, depth_sha256=depth, match=(fb==baseline['framebuffer_sha256'] and depth==baseline['depth_sha256']),
        vector_sha256=sha(vec), exe_sha256=sha(dest / 'obj/Vtb_gpu'))
    print(row, flush=True)
    return row

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('label');p.add_argument('--run-only',action='store_true')
    p.add_argument('--capture',help='Replay this private client capture instead of the retained baseline commands')
    p.add_argument('--mlab-queues',action='store_true')
    a=p.parse_args()
    dest=OUT/('replay-'+a.label) if a.run_only else prepare(a.label,a.mlab_queues)
    rows=[r for r in load(PRIOR/'replay-results-final-candidate-prefetch.json') if r['mode'] in (1,2)]
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for r in pool.map(run,[(dest,r,a.capture) for r in rows]):
            results.append(r)
            (dest/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    assert len(results)==16 and all(r['match'] for r in results), 'Rendering mismatch'

if __name__=='__main__':main()
