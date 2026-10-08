#!/usr/bin/env python3
"""Current-SM64 cache-size A/B using retained Pocket RTL; no hardware FPS claim."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-feature-audit-20261003'
PRIOR = ROOT / 'docs/measurements/sm64-geometry-fast-20261003'
SOURCE = PRIOR / 'replay-prefetch-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
load = lambda p: json.loads(p.read_text())

def prepare(label):
    dest = OUT / ('gpu-' + label)
    dest.mkdir(exist_ok=False)
    manifest = load(SOURCE / 'manifest.json')
    for rel, digest in manifest['sources'].items():
        assert sha(SOURCE / rel) == digest, rel
    shutil.copytree(SOURCE / 'frozen', dest / 'frozen')
    cmd = [a.replace(str(SOURCE), str(dest)) for a in manifest['command']]
    if label == 'texture8k':
        assert cmd.count('-GGPU_TEX_CACHE_SET_BITS=10') == 1
        cmd[cmd.index('-GGPU_TEX_CACHE_SET_BITS=10')] = '-GGPU_TEX_CACHE_SET_BITS=9'
    elif label == 'render4k':
        p = dest / 'frozen/tb_gpu.v'
        s = p.read_text()
        old = '.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4)'
        assert s.count(old) == 1
        p.write_text(s.replace(old, old.replace('.SET_BITS(6)', '.SET_BITS(5)')))
    else:
        raise ValueError(label)
    manifest.update(label=label, command=cmd,
        sources={str(p.relative_to(dest)):sha(p) for p in (dest / 'frozen').rglob('*') if p.is_file()})
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (dest / 'build.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    return dest

def run(job):
    dest, baseline = job
    mode, frame = baseline['mode'], baseline['frame']
    out = dest / 'runs' / str(mode)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f'frame_{frame:05}'
    vec = PRIOR / 'replays-prefetch/final-candidate' / str(mode) / f'frame_{frame:05}.vec'
    assert vec.is_file()
    cmd = [str(dest / 'obj/Vtb_gpu'), str(vec), str(stem.with_suffix('.bin')), f'+memory_traffic={mode}']
    with stem.with_suffix('.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    log = stem.with_suffix('.log').read_text()
    stats = {k:int(v) for k,v in re.findall(r'(\w+)=(\d+)', log)}
    data = stem.with_suffix('.bin').read_bytes()
    fb = hashlib.sha256(data[:153600]).hexdigest()
    depth = hashlib.sha256(data[153612:]).hexdigest() if baseline['depth_sha256'] else None
    assert fb == baseline['framebuffer_sha256'], (dest.name, mode, frame, 'color')
    assert depth == baseline['depth_sha256'], (dest.name, mode, frame, 'depth')
    return dict(case=dest.name, mode=mode, frame=frame, baseline_cycles=baseline['stats']['cycles'],
        cycles=stats['cycles'], extra_gpu_time_percent=100*(stats['cycles']/baseline['stats']['cycles']-1),
        framebuffer_sha256=fb, depth_sha256=depth, vector_sha256=sha(vec),
        exe_sha256=sha(dest / 'obj/Vtb_gpu'))

def main():
    rows = [r for r in load(PRIOR / 'replay-results-final-candidate-prefetch.json') if r['mode'] in (1,2)]
    results = []
    for label in ('texture8k', 'render4k'):
        dest = prepare(label)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for row in pool.map(run, [(dest, r) for r in rows]):
                results.append(row)
                print(row['case'], row['mode'], row['frame'], round(row['extra_gpu_time_percent'], 3), flush=True)
        (OUT / 'gpu-screen.json').write_text(json.dumps(dict(
            scope='Independent RTL GPU replay; SDRAM textures and synthetic background traffic; not full-game FPS',
            source=str(SOURCE), results=results), indent=2) + '\n')

if __name__ == '__main__':
    main()
