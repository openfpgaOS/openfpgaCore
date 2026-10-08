#!/usr/bin/env python3
"""Compare the new walker against retained SM64 framebuffer/depth/cycle results."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/gpu-edge-overflow-20261004'
BASE = ROOT / 'docs/measurements/sm64-vertex-perf-20261004'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='')
    args = parser.parse_args()
    assert not args.label or re.fullmatch(r'[a-z0-9_-]+', args.label)
    prefix = args.label + '-' if args.label else ''
    exe = OUT / f'replay-{prefix}parallel0/obj/Vtb_gpu'
    baseline = json.loads((BASE / 'replay-results-final-hidden-final-panel-worker.json').read_text())
    exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()

    def run(row):
        name = f"{row['label']}-m{row['mode']}-f{row['frame']}"
        dest = OUT / ('sm64-' + args.label if args.label else 'sm64') / name
        dest.mkdir(parents=True, exist_ok=True)
        vector = (BASE / 'replays-worker' / row['label'] / str(row['mode']) /
                  f"frame_{row['frame']:05}.vec")
        with (dest / 'replay.log').open('w') as log:
            subprocess.run([str(exe), str(vector), str(dest / 'output.bin'),
                            f"+memory_traffic={row['mode']}"],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        data = (dest / 'output.bin').read_bytes()
        assert len(data) == 153612 + (153600 if row['depth_sha256'] else 0)
        fb = hashlib.sha256(data[:153600]).hexdigest()
        depth = hashlib.sha256(data[153612:]).hexdigest() if row['depth_sha256'] else None
        cycles = int(re.search(r'RESULT cycles=(\d+)', (dest / 'replay.log').read_text())[1])
        result = dict(name=name, framebuffer_match=fb == row['framebuffer_sha256'],
                      depth_match=depth == row['depth_sha256'], cycles=cycles,
                      before_cycles=row['stats']['cycles'],
                      framebuffer_sha256=fb, depth_sha256=depth,
                      executable_sha256=exe_sha,
                      vector_sha256=hashlib.sha256(vector.read_bytes()).hexdigest())
        print(name, result['framebuffer_match'], result['depth_match'],
              'cycles', result['before_cycles'], '->', cycles, flush=True)
        return result

    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for row in pool.map(run, baseline):
            results.append(row)
            (OUT / f'sm64-{prefix}results.json').write_text(json.dumps(results, indent=2) + '\n')
    assert all(row['framebuffer_match'] and row['depth_match'] for row in results), \
        'Inspect any rendering changes against the corrected edge coverage'
    assert all(row['cycles'] == row['before_cycles'] for row in results), \
        'Investigate a replay performance change'


if __name__ == '__main__':
    main()
