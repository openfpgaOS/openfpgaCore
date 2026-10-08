#!/usr/bin/env python3
"""Private passive render-cache instrumentation, qualified against four old samples."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

R=Path(__file__).resolve().parents[3]
T=Path(__file__).resolve().parent
P=R/'docs/measurements/quake2-batch-memory-20261008'
O=P/'cache-observer-v1'
OLD=R/'docs/measurements/quake2-optimize-20261008/rtl-demo-depth-v2'
BUILD_PARENT=OLD.parent/'rtl-demo-v1'
LABEL='thorough_combined6'
FRAMES=(20,80,180,300)
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def put(path,value): path.write_text(json.dumps(value,indent=2)+'\n')

NAMES='window_cycles cache_busy_cycles cache_live_cycles gpu_idle_ring_empty_cycles gpu_active_ring_empty_cycles lookup_hits write_lookup_hits read_lookup_hits hot_write_hits hot_read_hits tag_hits lookup_misses read_lookup_misses write_lookup_misses empty_allocations dirty_replacements clean_replacements flush_empty_slots flush_dirty_lines flush_clean_lines flush_starts maintenance_cycles flush_completions new_line_fills partial_line_refills fill_hazard_cycles posted_write_capacity_cycles gpu_read_requests gpu_read_requested_bytes gpu_read_returned_bytes gpu_write_requests gpu_write_requested_beat_bytes gpu_write_beats gpu_write_valid_bytes memory_read_requests memory_read_requested_bytes memory_read_returned_bytes memory_write_requests memory_write_requested_beat_bytes maintenance_writeback_chunks replacement_writeback_chunks bypass_write_chunks memory_write_beats memory_write_valid_bytes maintenance_writeback_beat_bytes maintenance_writeback_valid_bytes replacement_writeback_beat_bytes replacement_writeback_valid_bytes bypass_write_beat_bytes bypass_write_valid_bytes memory_read_address_wait_cycles memory_write_address_wait_cycles memory_write_data_wait_cycles gpu_read_accept_wait_cycles gpu_write_accept_wait_cycles gpu_write_data_wait_cycles fill_response_gap_cycles write_allocation_clear_cycles lookup_pipeline_cycles barrier_wait_cycles idle_flush_request_cycles texture_flush_request_cycles partial_read_word_triggers cache_idle_without_request_cycles'.split()
assert len(NAMES)==64

def prepare():
    prep=json.loads((OLD/'prepared.json').read_text())
    built=json.loads((OLD/'built.json').read_text())
    assert sha(OLD/'prepared.json')==built['prepared_sha256']
    assert sha(OLD/'obj/Vtb_gpu')==built['executable_sha256']
    for rel,value in prep['files'].items():assert sha(OLD/rel)==value,rel
    O.mkdir(exist_ok=False)
    shutil.copytree(OLD/'frozen',O/'frozen')
    path=O/'frozen/tb_gpu.v';old=path.read_text()
    anchor='.hits(), .misses(), .writebacks(), .protocol_error(gpu_cache_protocol_error)'
    assert old.count(anchor)==1 and old.count('endmodule')==1
    new=old.replace(anchor,'.hits(audit_real_hits), .misses(audit_real_misses), .writebacks(audit_real_writebacks), .protocol_error(gpu_cache_protocol_error)',1)
    fragment=(T/'cache_observer.sv').read_text()
    new=new.replace('endmodule',fragment+'\nendmodule',1)
    path.write_text(new)
    shutil.copy2(T/'cache_observer.sv',O/'cache_observer.sv')
    (O/'source.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/frozen/tb_gpu.v',tofile='b/frozen/tb_gpu.v')))
    command=[v.replace(str(BUILD_PARENT),str(O)) for v in json.loads((BUILD_PARENT/'command.json').read_text())]
    put(O/'command.json',command)
    shutil.copy2(__file__,O/'recipe.py')
    files={str(p.relative_to(O)):sha(p) for p in (O/'frozen').rglob('*') if p.is_file()}
    changed=[rel for rel in prep['files'] if files[rel]!=prep['files'][rel]]
    assert changed==['frozen/tb_gpu.v']
    put(O/'schema.json',dict(counter_names=NAMES,flush_mask_bits=['barrier','idle','texture'],
        region_names=['framebuffer_pool','active_depth_allocation','other_or_initialization'],
        burst_bucket='0 unused; 1..15 exact 32-bit beats;16 means16-or-more',
        definitions={'lookup_hits':'Cache line lookup decisions, not pixel samples or all burst beats.',
                     'lookup_misses':'Line allocation decisions; partial word fills in a matching line do not increment this.',
                     'writebacks':'Completed dirty burst chunks, not distinct dirty cache lines.',
                     'flush_cause':'Mask sampled when the cache accepts maintenance; causes can coincide.',
                     'byte_counts':'Valid byte lanes and transferred 32-bit beat bytes reported separately; upstream repeated writes differ from eventual merged dirty bytes.'}))
    put(O/'prepared.json',dict(passed=True,parent_prepared_sha256=sha(OLD/'prepared.json'),
        parent_executable_sha256=built['executable_sha256'],parent_command_sha256=sha(BUILD_PARENT/'command.json'),
        files=files,changed_sources=changed,observer_fragment_sha256=sha(T/'cache_observer.sv'),
        recipe_sha256=sha(Path(__file__)),scope='Only private testbench passive wires/counters added. Functional GPU/cache/controller RTL and replay producer unchanged.'))
    print('prepared private testbench observer')

def build():
    prep=json.loads((O/'prepared.json').read_text())
    for rel,value in prep['files'].items():assert sha(O/rel)==value
    assert not (O/'built.json').exists()
    start=time.monotonic()
    with (O/'build.log').open('w') as log: subprocess.run(json.loads((O/'command.json').read_text()),cwd=R,stdout=log,stderr=subprocess.STDOUT,check=True)
    put(O/'built.json',dict(passed=True,prepared_sha256=sha(O/'prepared.json'),
        command_sha256=sha(O/'command.json'),executable_sha256=sha(O/'obj/Vtb_gpu'),build_seconds=time.monotonic()-start))
    print('built',sha(O/'obj/Vtb_gpu'))

def legacy_lines(text):
    text=re.sub(r'rtl cycles=\d+\n?','',text)
    return sorted(line for line in text.splitlines() if line and not line.startswith('CACHE_'))

def parse(log):
    values={int(a):int(b) for a,b in re.findall(r'^CACHE_AUDIT (\d+) (\d+)$',log,re.M)}
    assert set(values)==set(range(64))
    result=dict(zip(NAMES,[values[i] for i in range(64)]))
    hits,misses,writebacks=map(int,re.search(r'^CACHE_REAL hits (\d+) misses (\d+) writeback_chunks (\d+)$',log,re.M).groups())
    assert hits==result['lookup_hits'] and misses==result['lookup_misses']
    assert writebacks==result['maintenance_writeback_chunks']+result['replacement_writeback_chunks']
    assert hits==result['hot_write_hits']+result['hot_read_hits']+result['tag_hits']
    assert misses==result['empty_allocations']+result['dirty_replacements']+result['clean_replacements']
    assert result['memory_read_requested_bytes']==result['memory_read_returned_bytes']
    assert result['memory_write_requested_beat_bytes']==result['memory_write_beats']*4
    assert result['memory_write_valid_bytes']==sum(result[k] for k in ('maintenance_writeback_valid_bytes','replacement_writeback_valid_bytes','bypass_write_valid_bytes'))
    assert result['memory_write_requested_beat_bytes']==sum(result[k] for k in ('maintenance_writeback_beat_bytes','replacement_writeback_beat_bytes','bypass_write_beat_bytes'))
    result['real_outputs']=dict(hits=hits,misses=misses,writeback_chunks=writebacks)
    result['flush_masks']={int(a):dict(starts=int(b),cycles=int(c)) for a,b,c in re.findall(r'^CACHE_FLUSH (\d+) (\d+) (\d+)$',log,re.M)}
    result['barrier_commands']={int(a):int(b) for a,b in re.findall(r'^CACHE_BARRIER (\d+) (\d+)$',log,re.M)}
    result['burst_histogram']={int(a):dict(read=int(b),write=int(c)) for a,b,c in re.findall(r'^CACHE_BURST (\d+) (\d+) (\d+)$',log,re.M)}
    result['regions']={int(a):dict(hits=int(b),misses=int(c)) for a,b,c in re.findall(r'^CACHE_REGION (\d+) (\d+) (\d+)$',log,re.M)}
    result['main_states']={int(a):int(b) for a,b in re.findall(r'^CRITICAL_MAIN (\d+) (\d+)$',log,re.M)}
    result['fragment_blockers']={int(a):int(b) for a,b in re.findall(r'^CRITICAL_FRAG (\d+) (\d+)$',log,re.M)}
    assert sum(v['starts'] for v in result['flush_masks'].values())==result['flush_starts']
    assert sum(result['main_states'].values())==result['window_cycles']
    return result

def run():
    built=json.loads((O/'built.json').read_text())
    assert sha(O/'obj/Vtb_gpu')==built['executable_sha256']
    output=O/'qualified-samples';output.mkdir(exist_ok=False)
    def one(frame):
        name=f'frame_{frame:05}';source=OLD/LABEL/(name+'.vec');old_output=OLD/LABEL/(name+'.bin')
        report=json.loads((OLD/LABEL/(name+'.json')).read_text())
        filt=json.loads((OLD/LABEL/(name+'.filter.json')).read_text())
        assert sha(source)==filt['filtered_sha256'] and sha(old_output)==report['output_sha256']
        command=[str(O/'obj/Vtb_gpu'),'+fast_tex=1','+tagged_tex=1','+memory_traffic=0',str(source),str(output/(name+'.bin'))]
        with (output/(name+'.log')).open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=300,check=True)
        assert (output/(name+'.bin')).read_bytes()==old_output.read_bytes(),('complete output mismatch',frame)
        log=(output/(name+'.log')).read_text();old_log=(OLD/LABEL/(name+'.log')).read_text()
        assert legacy_lines(log)==legacy_lines(old_log),('existing counters changed',frame)
        counters=parse(log)
        cycles=int(re.search(r'^RESULT cycles=(\d+)',log,re.M).group(1))
        assert counters['window_cycles']==cycles
        result=dict(passed=True,frame=frame,command=command,input_sha256=sha(source),
            full_output_sha256=sha(old_output),color_sha256=report['color_sha256'],depth_sha256=report['depth_sha256'],
            complete_output_and_legacy_counters_exact=True,cycles=cycles,counters=counters)
        put(output/(name+'.json'),result)
        print('qualified',frame,cycles,'hits',counters['lookup_hits'],'misses',counters['lookup_misses'],flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(one,FRAMES))
    put(output/'result.json',dict(passed=True,baseline_label=LABEL,observer_executable_sha256=built['executable_sha256'],frames=results,
        limitations=['Saturated producer, memory_traffic=0, depth snapshot drain and initial/next-frame clears remain unchanged.',
                     'These four selected samples are GPU work measurements, not Pocket FPS or coupled CPU/GPU contention.']))
    evidence=[O/'prepared.json',O/'built.json',O/'command.json',O/'source.patch',O/'schema.json',O/'cache_observer.sv',Path(__file__)]+[p for p in output.iterdir() if p.is_file()]
    put(O/'qualification-manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}))
    print('all four passive samples qualified')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','build','run']);a=p.parse_args()
    globals()[a.action]()
