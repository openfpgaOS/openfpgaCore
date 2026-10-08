#!/usr/bin/env python3
"""Validate full-attribute raster input against old-RTL-derived scene results.

Timing is a raster-only measurement with precomputed inputs. It deliberately
does not claim the replacement mesh processor's area, latency or performance.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-parallel-20261004'
PRIOR=ROOT/'docs/measurements/sm64-vertex-perf-20261004'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--edges',action='store_true');args=p.parse_args()
    prefix='edges' if args.edges else 'planes'
    checks='edges' if args.edges else 'plane'
    extract=OUT/(prefix+'-extract/obj/Vtb_gpu')
    backend=OUT/(prefix+'-backend/obj/Vtb_gpu')
    reference=json.loads((PRIOR/'replay-results-final-hidden-final-panel-worker.json').read_text())
    expected={r['name']:r for r in json.loads((ROOT/'docs/measurements/gpu-edge-overflow-20261004/sm64-compact3-results.json').read_text())}
    def run(row):
        name=f"{row['label']}-m{row['mode']}-f{row['frame']}"
        dest=OUT/(checks+'-checks')/name;dest.mkdir(parents=True,exist_ok=False)
        vector=PRIOR/'replays-worker'/row['label']/str(row['mode'])/f"frame_{row['frame']:05}.vec"
        env=os.environ.copy();env['PLANE_VECTOR_OUT']=str(dest/'planes.vec')
        for exe,source,output,label in [(extract,vector,'reference.bin','extract'),
                                        (backend,dest/'planes.vec','backend.bin','backend')]:
            with (dest/(label+'.log')).open('w') as log:
                subprocess.run([str(exe),str(source),str(dest/output),f"+memory_traffic={row['mode']}"],
                               env=env if label=='extract' else None,stdout=log,stderr=subprocess.STDOUT,
                               check=True,timeout=600)
        ref=(dest/'reference.bin').read_bytes();got=(dest/'backend.bin').read_bytes()
        assert hashlib.sha256(ref[:153600]).hexdigest()==expected[name]['framebuffer_sha256'],name
        if row['depth_sha256']:
            assert hashlib.sha256(ref[153612:]).hexdigest()==expected[name]['depth_sha256'],name
        assert ref==got,(name,sum(a!=b for a,b in zip(ref,got)))
        logs={k:(dest/(k+'.log')).read_text() for k in ['extract','backend']}
        cycles={k:int(re.search(r'RESULT cycles=(\d+)',s)[1]) for k,s in logs.items()}
        result=dict(name=name,frame=row['frame'],traffic=row['mode'],exact_memory_and_fence=True,
                    planes=int(re.search(r'PLANES count=(\d+)',logs['extract'])[1]),cycles=cycles,
                    raster_only_gain_percent=100*(cycles['extract']/cycles['backend']-1),
                    source_sha256=sha(vector),planes_sha256=sha(dest/'planes.vec'),
                    output_sha256=sha(dest/'backend.bin'))
        print(name,result['planes'],cycles,flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,reference))
    (OUT/(checks+'-checks.json')).write_text(json.dumps(dict(
        passed=True,scope=__doc__,executable_sha256={str(p):sha(p) for p in [extract,backend]},
        results=results),indent=2)+'\n')


if __name__=='__main__':main()
