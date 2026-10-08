#!/usr/bin/env python3
"""Read-only three-way native cache comparison; no models or runs launched."""
from pathlib import Path
import json
import shutil
from phase10_native_renderer_check import ROOT, P, WINDOW, sha, read, save, check_table
from phase10_native_renderer_cache_check import aggregate

OUT=P/'native-renderer-render32-check-v1'
TOP='frozen/docs/measurements/os30-dual-20261004/validation/baseline128/tb_irq.v'


def main():
    assert not OUT.exists()
    prior=P/'native-renderer-cache-check-v1'
    check_table(prior,read(prior/'manifest.json')['files'])
    check_table(ROOT,read(prior/'manifest.json')['evidence_sha256'])
    a,b=WINDOW/'model-cache64-v1',WINDOW/'model-cache64-render32-v1'
    ap,bp=read(a/'prepared.json'),read(b/'prepared.json')
    assert set(ap['source_sha256'])==set(bp['source_sha256'])
    assert [k for k in ap['source_sha256'] if ap['source_sha256'][k]!=bp['source_sha256'][k]]==[TOP]
    old=(a/TOP).read_text();new=(b/TOP).read_text()
    needle='.WAYS(2), .SET_BITS(6), .WORD_BITS(4)'
    assert old.count(needle)==1 and old.replace(needle,'.WAYS(2), .SET_BITS(8), .WORD_BITS(4)')==new
    assert ap['defs']==bp['defs'] and ap['cpu_sha256']==bp['cpu_sha256']
    check_table(b,bp['source_sha256']);assert read(b/'build.json')['returncode']==0
    for f in ['main.cpp','input.cfg']:
        assert (WINDOW/'runner-cache64-v1'/f).read_bytes()==(WINDOW/'runner-cache64-render32-v1'/f).read_bytes()
    runs=[WINDOW/x for x in ['run-v2','run-cache64-v1','run-cache64-render32-v1']]
    names=['i32_d128_gpu8','i64_d64_gpu8','i64_d64_gpu32']
    for f,n in [('entry-regs.bin',264),('commands.bin',92144),('frame.bin',307200)]:
        payload=(runs[0]/f).read_bytes();assert len(payload)==n
        assert all((r/f).read_bytes()==payload for r in runs)
    timing={}
    for name,r in zip(names,runs):
        v=read(r/'result.json');assert v['status']==0 and v['fence']==362
        assert all(v[k]==0 for k in ['write_errors','scan_errors','cram_errors'])
        assert all(x['errors']==0 for x in v['readers'].values())
        timing[name]=dict(call_cycles=v['finished_cycle']-v['entry_cycle'],
                         drained_cycles=v['complete_cycle']-v['entry_cycle'],
                         drain_cycles=v['complete_cycle']-v['finished_cycle'],
                         retired=v['retired'],freeze_cycles=v['freeze_cycles'],hazard_cycles=v['hazard_cycles'],
                         gpu_busy_cycles=v['gpu_busy_cycles'],readers=v['readers'])
    assert timing[names[2]]['call_cycles']==2691126
    x,y,z=[timing[n] for n in names]
    saving=dict(incremental_cycles=y['call_cycles']-z['call_cycles'],
                incremental_percent=100*(1-z['call_cycles']/y['call_cycles']),
                total_cycles=x['call_cycles']-z['call_cycles'],
                total_percent=100*(1-z['call_cycles']/x['call_cycles']))
    aa,bb=aggregate(runs[1]),aggregate(runs[2])
    rows=[dict(symbol=k,baseline_retired=aa[k][0],candidate_retired=bb[k][0],baseline_gap=aa[k][1],candidate_gap=bb[k][1],gap_saved=aa[k][1]-bb[k][1]) for k in set(aa)|set(bb)]
    rows.sort(key=lambda x:-x['gap_saved'])
    OUT.mkdir();shutil.copyfile(__file__,OUT/'recipe.py')
    save(OUT/'profile-delta.json',dict(scope='Retirement-gap attribution only; not causal/exclusive stalls.',symbols=rows))
    save(OUT/'result.json',dict(passed=True,only_model_change='gpu_render_cache SET_BITS6→8; 8KiB→32KiB, 2way64B lines unchanged',
        exact_other_source_count=len(bp['source_sha256'])-1,exact_entry_words=66,exact_command_words=23036,exact_color_bytes=153600,exact_depth_bytes=153600,
        timing=timing,saving=saving,deployment_ready=False,
        limitations=['One cold native renderer call; no warm/full-frame/presentation or FPGA timing claim.',
                    'Prior analytic GPU32 experiment on the older CPU configuration was slower/unfittable. This result applies to actual native I64/D64 with spare BRAM and does not replace that result.',
                    'Synthetic fixed scan/audio traffic starts at fixture phase; not an exact phase restore of the running reference system.',
                    'Reader counters cover release through DONE; freeze/hazard/GPU busy overlap and are not additive.']))
    (OUT/'REPORT.txt').write_text('''Three-way cold native cache comparison: PASS

Actual renderer entry→gfx_end_frame return cycles:
  I32/D128 + GPU8KiB: 2,924,576
  I64/D64 + GPU8KiB:  2,786,449
  I64/D64 + GPU32KiB: 2,691,126
The render-cache increase alone saves95,323cycles (0.95323ms at configured100MHz,3.421%). The combined difference is233,450cycles (2.33450ms,7.982%). Final GPU drain is18,602cycles for GPU32KiB.

Only the existing render-cache SET_BITS parameter changes6→8 between the last two models. CPU, every other RTL source, macro, runner, config, overlay, captured input and PGO ELF remain exact. All three runs match66 entry words,23,036 command words and the full307,200 color/depth bytes; no checked bus/physical-memory error.

This is one cold native renderer invocation, not warm native FPS or presentation speed. Earlier negative analytic cache screens remain valid for their respective models/configurations. Physical resource/timing feasibility is a separate pending gate.
''')
    evidence={}
    def bind(f):evidence[str(f.relative_to(ROOT))]=sha(f)
    for d in [runs[2],WINDOW/'runner-cache64-render32-v1']:
        for f in d.rglob('*'):
            if f.is_file():bind(f)
    for rel in bp['source_sha256']:bind(b/rel)
    for f in [b/'prepared.json',b/'build.json',b/'build.log',b/'recipe-prepare.py',prior/'manifest.json',prior/'result.json',runs[0]/'result.json',runs[1]/'result.json']:bind(f)
    save(OUT/'manifest.json',dict(passed=True,files={f.name:sha(f) for f in OUT.iterdir() if f.is_file()},evidence_sha256=evidence))
    print(json.dumps(dict(manifest_sha256=sha(OUT/'manifest.json'),saving=saving)))


if __name__=='__main__':main()
