#!/usr/bin/env python3
"""Collect existing FBWQ GPU logs, qualifying intentionally invalid payloads."""
import argparse
import hashlib
import json
from pathlib import Path
import re

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory',type=Path)
args=parser.parse_args()
dest=args.directory.resolve()
manifest=json.loads((dest/'manifest.json').read_text())
assert sha(dest/'runner-snapshot.py')==manifest['script_sha256']
for name,digest in manifest['sources'].items(): assert sha(dest/name)==digest,name
for label in ['baseline','candidate']:
    assert sha(dest/(label+'-gpu_core.v'))==manifest[label+'_sha256']
if 'cache_candidate_sha256' in manifest:
    assert sha(dest/'candidate-gpu_color_depth_cache.sv')==manifest['cache_candidate_sha256']
    assert sha(Path(manifest['cache_candidate_original']))==manifest['cache_candidate_sha256']
    assert sha(dest/'common/gpu_color_depth_cache.sv')==manifest['cache_baseline_sha256']
results={}
for label in ['baseline','candidate']:
    job=dest/label
    build=json.loads((job/'manifest.json').read_text())
    assert sha(job/'obj/Vtb_gpu')==build['simulator_sha256']
    results[label]={}
    for mode in ['quiet','delayed','gapped']:
        log=job/(mode+'.log');text=log.read_text()
        match=re.search(r'Acceptance Results: (\d+) passed, (\d+) failed',text)
        assert match and tuple(map(int,match.groups()))==(79,0),log
        cycles={k:int(v) for k,v in re.findall(r'^CYCLES (\w+) (\d+)$',text,re.M)}
        assert len(cycles)==38,log
        monitor=dict(re.findall(r'(\w+)=(\w+)',re.search(r'^FBWQ_MON (.+)$',text,re.M)[1]))
        for key in ['hard_nonzero','soft_nonzero','observed','aw','w']:assert int(monitor[key])>0,(log,key)
        results[label][mode]=dict(passed=79,cycles=cycles,monitor=monitor,log_sha256=sha(log))
        if 'cache_candidate_sha256' in manifest:
            hot=dict(re.findall(r'(\w+)=(\w+)',re.search(r'^CACHEHOT_MON (.+)$',text,re.M)[1]))
            for key in ['hot0','hot1','reads','writes']:assert int(hot[key])>0,(log,key)
            results[label][mode]['cache_monitor']=hot
        if manifest.get('ring_monitor'):
            ring=dict(re.findall(r'(\w+)=(\w+)',re.search(r'^RING_MON (.+)$',text,re.M)[1]))
            assert int(ring['cpu'])>0,(log,'no accepted CPU ring writes')
            results[label][mode]['ring_monitor']=ring
for mode in ['quiet','delayed','gapped']:
    for key in ['passed','cycles']:assert results['baseline'][mode][key]==results['candidate'][mode][key],(mode,key)
    for key,value in results['baseline'][mode]['monitor'].items():
        # These counters deliberately inspect unqualified stale payload data.
        # Reset removal may change them, including at the next reset edge.
        if key not in ['invalid_nonzero','hard_nonzero','soft_nonzero']:
            assert value==results['candidate'][mode]['monitor'][key],(mode,key)
    if 'cache_candidate_sha256' in manifest:
        assert results['baseline'][mode]['cache_monitor']==results['candidate'][mode]['cache_monitor']
    if manifest.get('ring_monitor'):
        assert results['baseline'][mode]['ring_monitor']==results['candidate'][mode]['ring_monitor']
report=dict(identical_pixels_cycles_valid_payload_and_axi=True,runs=results,
            collector_sha256=sha(Path(__file__)),identities_verified=True,
            unqualified_payload_counters=['invalid_nonzero','hard_nonzero','soft_nonzero'])
(dest/'results.json').write_text(json.dumps(report,indent=2)+'\n')
(dest/'collector-snapshot.py').write_bytes(Path(__file__).read_bytes())
print('PASS:',dest.name,'38 workloads x 3 modes; pixels, cycles, valid request/snoop and AXI activity match.')
