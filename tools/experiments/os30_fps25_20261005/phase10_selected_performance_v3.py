#!/usr/bin/env python3
"""Bound the retained PGO improvement to actual matched RTL windows."""
import json,shutil
from pathlib import Path
from phase10_experiment import ROOT,CAMP,sha,write
P=CAMP/'phase10'; OUT=P/'selected-performance-summary-v3'
def main():
    proofs=[P/s/'manifest.json'for s in('selected-performance-summary-v2','renderer-pgo-v1','renderer-pgo-coupled-v1','renderer-pgo-source-review-v1')]
    for p in proofs:
        d=json.loads(p.read_text())
        for n,h in d.get('files',{}).items():assert sha(p.parent/n)==h,(p,n)
        for n,h in d.get('evidence_sha256',{}).items():assert sha(ROOT/n)==h,(p,n)
    a=json.loads((P/'renderer-pgo-coupled-v1/result-warm.json').read_text());b=json.loads((P/'renderer-pgo-coupled-v1/result-cold.json').read_text());assert a['passed']and b['passed']
    original=37.82757375;selected=a['timing']['pgo']['frame_ms'];saving=original-selected
    OUT.mkdir(exist_ok=False)
    result=dict(original_warm_ms=original,selected_warm_ms=selected,saving_ms=saving,time_reduction_percent=100*saving/original,
        modeled_throughput_increase_percent=100*(original/selected-1),pgo_warm_saving_ms=a['saving_ms'],pgo_cold_saving_ms=b['saving_ms'],
        original_cold_baseline_not_claimed=True,immediate_cold_baseline_ms=b['timing']['baseline']['frame_ms'],selected_cold_ms=b['timing']['pgo']['frame_ms'],
        selected_elf_sha256='fff132a5ee34d625a279fbf612e4abe54faa122fe519b386130f5ee230dfe63b',actual16_buffers_exact=True,
        in_sample_pgo=True,gcov_zero_summary_unmodified=True,pocket_fps_measured=False,target25fps_demonstrated=False,
        portable_delivery_qualification_separate=True,physical_timing_qualification_separate=True,deployed=False)
    write(OUT/'result.json',result)
    (OUT/'REPORT.txt').write_text(f'''Retained SM64 compiler improvement

Matched warm Bob-omb window: {original:.8f} -> {selected:.8f} ms/frame.
Total saving {saving:.8f} ms, {result['time_reduction_percent']:.4f}% less modeled time,
or {result['modeled_throughput_increase_percent']:.4f}% greater modeled throughput.
This is not a Pocket FPS reading or demonstration of the25FPS goal.

Renderer-only GCC edge PGO improves the immediately preceding selected app
from {a['timing']['baseline']['frame_ms']:.8f} to {selected:.8f} ms warm,
saving {a['saving_ms']:.8f} ms. Cold234..241 improves from
{b['timing']['baseline']['frame_ms']:.8f} to {b['timing']['pgo']['frame_ms']:.8f} ms,
saving {b['saving_ms']:.8f} ms. Cold is a separate eight-frame window; its
immediate baseline is the load-submit app, not a claimed original F11 cold run.

All16 actual color/depth buffers and complete scene/geometry/culling histories
match. Each pair reuses the exact same GPU/LOAD/cache/SDRAM/CRAM RTL binary,
assets, synthetic traffic and CPU model settings. Only the uninstrumented
profile-use ELF differs. Total GPU command words are unchanged; publication
splits and polling counts can change as the CPU reaches the ring earlier.

Training instruments only gfx_pc.c and gfx_gpu.c edge counters. Real measured
counts are exported after timing reports through GCC's freestanding gcov-info
serializer. All136 function identities/checksums and1,357 counters were audited;
842 counters are nonzero. No value-profile stub, invented histogram or summary
correction was used. GCC's stream omits the object summary; merge-stream leaves
runs=0,sum_max=0 and profile-use sets hotness threshold0. These original bytes
are retained. Compiler profile-use can change inlining, unrolling and layout;
source arithmetic, ISA/FPU/fast-math flags and game/asset content stay unchanged.

The same deterministic721-frame Bob-omb run trains and screens the app. This
is an in-sample performance result, not generalization across levels. That
CPU-only screen saves0.88530795ms/gameplay frame and preserves all721 records,
command counts and eight ordered renderer streams. Qsim's ordinary3D command
handlers do not render those streams, so their framebuffer hashes are not
renderer evidence. The separate16 coupled RTL buffers above provide the
actual rendering comparisons. Existing fallback/component proofs are inherited
source evidence; the optimized ELF has not independently rerun all fault modes.

The retained baseline features and closed alternatives remain documented in
selected-performance-summary-v2. PGO adds no hardware resources. CPU/cache
costs remain modeled; the progression denominator excludes final presentation
tail and is not native full-game execution. Hardware routing/timing and portable
ordinary-build reproduction are separate gates. No production, sibling or SD
files were changed, and no deployment or manual device trial is claimed.
''')
    shutil.copy2(__file__,OUT/'recipe.py')
    write(OUT/'manifest.json',dict(files={f.name:sha(f)for f in OUT.iterdir()if f.is_file()},evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in proofs}))
    print(json.dumps(result,indent=2));print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
