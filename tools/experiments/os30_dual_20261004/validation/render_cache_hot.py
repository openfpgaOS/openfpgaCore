#!/usr/bin/env python3
"""Freeze and run os30 cache scoreboards, including hostile RAM collisions."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from gpu_fbwq_rendering import CACHE_MONITOR

ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-dual-20261004/validation/render-cache-hot-v1/scoreboard'
BASE=ROOT/'docs/measurements/os30-dual-20261004'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(command,log):
    with log.open('w') as f:subprocess.run(list(map(str,command)),check=True,stdout=f,stderr=subprocess.STDOUT,timeout=300)

def main():
    OUT.mkdir(parents=True,exist_ok=False)
    shutil.copy2(Path(__file__),OUT/'runner-snapshot.py')
    harness=ROOT/'tools/tests/gpu_color_depth_cache.cpp'
    shutil.copy2(harness,OUT/'original-main.cpp')
    text=harness.read_text()
    for old,new in [
        ('uint64_t cycles=0, reads=0, writes=0;', 'uint64_t cycles=0, reads=0, writes=0, hard_resets=0, soft_resets=0;'),
        ('void reset() {','void reset() { ++hard_resets;'),
        ('void soft_reset_then_bypass() {','void soft_reset_then_bypass() { ++soft_resets;'),
        ('test.run();','test.run(); printf("RESET_COVERAGE hard=%llu soft=%llu\\n",(unsigned long long)test.hard_resets,(unsigned long long)test.soft_resets); test.t.final();')]:
        assert text.count(old)==1,old
        text=text.replace(old,new)
    (OUT/'main.cpp').write_text(text)
    sources={
        'baseline':BASE/'validation/gpu-fbwq-single-read-v2/common/gpu_color_depth_cache.sv',
        'candidate':BASE/'render-hot-audit-v1/gpu_color_depth_cache.sv'}
    manifest=dict(script_sha256=sha(Path(__file__)),monitor_import_sha256=sha(Path(__file__).with_name('gpu_fbwq_rendering.py')),
                  original_harness=str(harness),original_harness_sha256=sha(harness),harness_sha256=sha(OUT/'main.cpp'),
                  parameters=dict(SET_BITS=6,WORD_BITS=4,WAYS=2,ADDR_W=26,CACHE_ALL=1),
                  poison_collisions=True,source_sha256={k:sha(v) for k,v in sources.items()})
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    def one(label):
        job=OUT/label;job.mkdir();shutil.copy2(sources[label],job/'original.sv')
        text=sources[label].read_text();at=text.rindex('endmodule')
        # Observation-only checks are identical to the complete GPU monitor.
        monitor=CACHE_MONITOR.replace('gpu_render_cache.','')
        (job/'observed.sv').write_text(text[:at]+monitor+'\n'+text[at:])
        command=['verilator','--cc','--exe','--build','--assert','-j','2','-Wno-fatal',
                 '--top-module','gpu_color_depth_cache','--Mdir',job/'obj',
                 '-GSET_BITS=6','-GWORD_BITS=4','-GWAYS=2','-GADDR_W=26','-GCACHE_ALL=1',
                 '+define+CACHE_POISON_COLLISIONS','-CFLAGS',
                 '-std=c++17 -O2 -DSET_BITS_MODEL=6 -DWORD_BITS_MODEL=4 -DWAYS_MODEL=2 -DCACHE_ALL_MODEL=1 -DHAS_BUS_RESET',
                 job/'observed.sv',OUT/'main.cpp']
        run(command,job/'build.log')
        build=dict(command=list(map(str,command)),observed_source_sha256=sha(job/'observed.sv'),simulator_sha256=sha(job/'obj/Vgpu_color_depth_cache'))
        (job/'manifest.json').write_text(json.dumps(build,indent=2)+'\n')
        results=[]
        for seed in [1,2,3]:
            log=job/f'seed{seed}.log';run([job/'obj/Vgpu_color_depth_cache',seed],log);text=log.read_text()
            assert 'PASS cycles=' in text and 'FAIL' not in text,log
            assert 'RESET_COVERAGE hard=2 soft=120' in text,log
            hot=dict(re.findall(r'(\w+)=(\w+)',re.search(r'^CACHEHOT_MON (.+)$',text,re.M)[1]))
            for field in ['hot0','hot1','reads','writes','simultaneous']:assert int(hot[field])>0,(log,field)
            passed=dict(re.findall(r'(\w+)=(\w+)',re.search(r'^PASS (.+)$',text,re.M)[1]))
            results.append(dict(seed=seed,passed=passed,hot_monitor=hot,log_sha256=sha(log)))
            print(label,seed,text.strip(),flush=True)
        return label,results
    with ThreadPoolExecutor(max_workers=2) as pool:results=dict(pool.map(one,['baseline','candidate']))
    assert results['baseline']==results['candidate']
    (OUT/'results.json').write_text(json.dumps(dict(passed=True,exact_logs_match=True,runs=results),indent=2)+'\n')
    print('PASS: all three poisoned-collision cache scoreboards match exactly, including cycles and reset coverage.',flush=True)

if __name__=='__main__':main()
