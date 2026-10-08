#!/usr/bin/env python3
"""Bind resident texture replay evidence without selecting a device candidate."""
import importlib.util,json
from pathlib import Path
S=importlib.util.spec_from_file_location('resident',Path(__file__).with_name('run.py'))
R=importlib.util.module_from_spec(S);S.loader.exec_module(R)

def main():
    hashes={}
    def bind(p):
        hashes[str(p.relative_to(R.ROOT))]=R.sha(p)
        return json.loads(p.read_text())
    inputs=bind(R.OUT/'inputs.json')
    for rel,h in inputs['sources'].items():assert R.sha(R.OUT/rel)==h,rel
    for rel,h in inputs['scripts'].items():assert R.sha(R.ROOT/rel)==h,rel
    rows=[]
    for extra in (0,2):
        model=R.OUT/f'model-extra{extra}';build=bind(model/'build.json')
        assert R.sha(model/'obj/Vtb_gpu')==build['simulator_sha256']
        a=bind(model/'sdram/results.json');b=bind(model/'fast/results.json')
        assert [x['frame'] for x in a]==list(R.FRAMES)==[x['frame'] for x in b]
        for baseline,fast in zip(a,b):
            frame=baseline['frame'];s=baseline['stats'];t=fast['stats']
            assert s['fills']==t['fills'] and s['beats']==t['beats']==4*t['fills']
            assert baseline['output_sha256']==fast['output_sha256']
            assert baseline['exact_color_depth_fence_memoryhash'] and fast['exact_color_depth_fence_memoryhash']
            for kind,item in [('sdram',baseline),('fast',fast)]:
                for suffix,key in [('bin','output_sha256'),('log','log_sha256')]:
                    p=model/kind/f'frame_{frame:05d}.{suffix}';assert R.sha(p)==item[key]
                    hashes[str(p.relative_to(R.ROOT))]=item[key]
                assert item['stats']['cram_errors']==0 and item['stats']['scan_latency_max']<6361
            rows.append(dict(frame=frame,refresh_extra_cycles=extra,
                sdram_cycles=s['cycles'],fast_cycles=t['cycles'],cycles_saved=s['cycles']-t['cycles'],
                gpu_completion_reduction_percent=100*(s['cycles']-t['cycles'])/s['cycles'],
                equivalent_ms_saved_at_100MHz=(s['cycles']-t['cycles'])/100000,
                texture_fills=s['fills'],texture_request_wait_sdram=s['request_wait'],texture_request_wait_fast=t['request_wait'],
                texture_service_sdram=s['service_sum'],texture_service_fast=t['service_sum'],
                cpu_queue_mean_sdram=s['cpu_queue_sum']/s['cpu_queue_n'],cpu_queue_mean_fast=t['cpu_queue_sum']/t['cpu_queue_n'],
                cpu_total_mean_sdram=s['cpu_total_sum']/s['cpu_total_n'],cpu_total_mean_fast=t['cpu_total_sum']/t['cpu_total_n'],
                cpu_queue_p95_sdram=s['cpu_queue_p95'],cpu_queue_p95_fast=t['cpu_queue_p95'],
                cpu_queue_max_sdram=s['cpu_queue_max'],cpu_queue_max_fast=t['cpu_queue_max'],
                cpu_completed_sdram=s['cpu_completed'],cpu_completed_fast=t['cpu_completed'],
                cpu_completions_per_cycle_sdram=s['cpu_completed']/s['cycles'],cpu_completions_per_cycle_fast=t['cpu_completed']/t['cycles'],
                audio_total_p95_sdram=s['audio_total_p95'],audio_total_p95_fast=t['audio_total_p95'],
                audio_total_max_sdram=s['audio_total_max'],audio_total_max_fast=t['audio_total_max'],
                scan_latency_max_sdram=s['scan_latency_max'],scan_latency_max_fast=t['scan_latency_max']))
    ext=R.OUT/'extended';extended=bind(ext/'inputs.json')
    for rel,h in extended['sources'].items():assert R.sha(ext/rel)==h,rel
    assert R.sha(Path(__file__).with_name('extend.py'))==extended['script_sha256']
    build=bind(ext/'model/build.json');assert R.sha(ext/'model/obj/Vtb_gpu')==build['simulator_sha256']
    checks=bind(ext/'checks/results.json');assert [r['kind'] for r in checks]==['control','concurrent']
    for item in checks:
        for suffix,key in [('bin','output_sha256'),('log','log_sha256')]:
            p=ext/'checks'/(item['kind']+'.'+suffix);assert R.sha(p)==item[key];hashes[str(p.relative_to(R.ROOT))]=item[key]
    control,concurrent=checks
    assert control['output_sha256']==concurrent['output_sha256']
    u=concurrent['stats'];assert u['words']==u['readback_words']==256 and u['uploads_while_fill'] and u['fills_while_upload']
    limits=[
        'GPU replay with synthetic CPU M1/audio/scanout traffic; actual application CPU is not running in this timing model.',
        'CPU request schedule is acceptance-relative (next request=accept+97); completion counts vary with elapsed replay time. Compare rates and latency distributions, not counts alone.',
        'Resident case backdoors current texture pages before each CMDS batch with zero simulated upload cost. Persistent address mapping is tested; allocator/lifetime behavior and real app producer overlap are not.',
        'Original low14 address bits are preserved, including texture-cache set index. Identical fill counts separate memory routing from cache-placement effects.',
        'TEX1/MMIO extension supports real future live uploads; each CMDS currently waits idle, so it does not reproduce arbitrary CPU submission overlap. Separate deliberate concurrent upload test covers both controller overlap directions.',
        'Upload cycles are real GPU/controller/model minimum wall time with one-cycle host MMIO polling. Actual CPU instructions and bus polling overhead are not included.',
        'Actual retained-A controller clock configuration100MHz and BCR0x241F used. Logical chip model checks protocol/timing; this is not physical board timing closure or a guarantee at100MHz.',
        'Model is restricted to first2MiB of die0; both dies initialized but neither die1 access nor8MiB bank boundary is exercised.',
        'Only F5frames360/600/720 and one1KiB upload schedule covered. No actualFPS result,25FPS claim, FPGA fit, production edit, full sweep, or deployment.'
    ]
    report=dict(passed=True,selected_for_device=False,target25fps_verified=False,
        scope='Dedicated CRAM1 resident-texture feasibility with actual retained-A GPU, adapter, controller, PHY and physical chip model.',
        pool_bytes=inputs['pool_bytes'],texture_extent_sum=inputs['referenced_extent_sum'],preserved_cache_index_bits=14,
        original_route_noninterference='All prior phase3 stats and entire307212byte color/depth/fence/fullSDRAMhash output exact for each baseline case.',
        rows=rows,
        concurrency=dict(uploaded_words=u['words'],upload_bytes=4*u['words'],verified_words=u['readback_words'],
            upload_busy_cycles=u['upload_cycles'],mean_cycles_per_word=u['upload_cycles']/u['words'],max_cycles_per_word=u['word_cycles_max'],
            uploads_started_during_fill=u['uploads_while_fill'],fills_started_during_upload=u['fills_while_upload'],
            upload_span=u['concurrent_span'],gpu_completion_delta=u['cycles']-control['stats']['cycles'],
            texture_fill_service_delta=u['service_sum']-control['stats']['service_sum'],exact_color_depth_and_memory=True),
        conclusion='Positive but modest: nominal GPU completion improves2.56–2.80%, CPU queue mean improves~1cycle. Does not demonstrate the~5ms frame improvement needed for25FPS.',
        limits=limits,evidence_sha256=hashes,report_script_sha256=R.sha(Path(__file__)))
    R.write(R.OUT/'REPORT.json',report)
    lines=['Retained-A dedicated texture memory replay feasibility','',report['scope'],'',report['original_route_noninterference'],
        f"Persistent mapping pool: {inputs['pool_bytes']} bytes; referenced extent sum: {inputs['referenced_extent_sum']} bytes.",
        'Cache placement: low14bits preserved; exact texture-fill counts in all comparisons.','',
        'frame WAITextra SDRAMcycles CRAMcycles reduction GPUmsSaved@100MHz CPUqueueMeanOld/New']
    for x in rows:
        lines.append(f"{x['frame']:5} {x['refresh_extra_cycles']:9} {x['sdram_cycles']:11} {x['fast_cycles']:10} {x['gpu_completion_reduction_percent']:8.4f}% {x['equivalent_ms_saved_at_100MHz']:.5f} {x['cpu_queue_mean_sdram']:.3f}/{x['cpu_queue_mean_fast']:.3f}")
    lines+=['',f"Concurrent GPU-MMIO upload: {4*u['words']} bytes, {u['readback_words']} exact readbacks; {u['uploads_while_fill']} uploads began during fills, {u['fills_while_upload']} fills began during uploads.",
        f"Actual controller minimum upload span: {u['concurrent_span']} cycles, mean {u['upload_cycles']/u['words']:.3f} cycles/word, maximum {u['word_cycles_max']}. CPU polling overhead excluded.",
        f"Concurrent F5frame360: identical full output; GPU completion delta {u['cycles']-control['stats']['cycles']} cycles, texture fill service delta {u['service_sum']-control['stats']['service_sum']} cycles.",'',report['conclusion'],'','Limits:']
    lines += ['- '+x for x in limits]
    lines += ['','Reproduce: run.py prepare; run.py build; run.py run; run.py run --fast; repeat build/run with --extra2 (as --extra 2). extend.py prepare; extend.py build. Existing evidence directories intentionally refuse overwrite.',
        'Extended replay: extended/model/obj/Vtb_gpu VECTOR OUTPUT +memory_traffic=2 +tagged_tex=1.',
        'Concurrent replay: same executable with vectors/frame_00360.vec OUTPUT +memory_traffic=2 +fast_tex=1 +concurrent_upload=1.',
        'REPORT.json binds source manifests, simulators, logs, results and output hashes. No production candidate selected.']
    (R.OUT/'REPORT.txt').write_text('\n'.join(lines)+'\n')
    print(report['conclusion'])

if __name__=='__main__':main()
