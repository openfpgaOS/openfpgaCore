#!/usr/bin/env python3
"""Independent F11/selected-software/selected-hardware interval comparison."""
import shutil
from phase10_native_renderer_check import ROOT,P,WINDOW,sha,read,save

OUT=P/'native-f11-selected-comparison-v1'

def main():
    assert not OUT.exists()
    evidence={};checked=0
    def bind(path):evidence[str(path.relative_to(ROOT))]=sha(path)
    def table(base,rows):
        nonlocal checked
        for n,h in rows.items():
            assert sha(base/n)==h,str(base/n);checked+=1;bind(base/n)
    def manifest(path):
        data=read(path);table(path.parent,data.get('files',{}))
        for key in ['inputs_sha256','evidence_sha256']:table(ROOT,data.get(key,{}))
        bind(path);return data
    for label in ['native-f11-guest-source-review-v1','native-warm-interval-check-v1',
                  'native-rvcpipe-render32-check-v1','render-cache-storage-source-review-v2']:
        manifest(P/label/'manifest.json')
    for guest in ['guest-f11-warm-v1','guest-warm-v3']:manifest(WINDOW/guest/'manifest.json')
    models=[WINDOW/'model-v3',WINDOW/'model-cache64-rvcpipe-render32-v1',
            WINDOW/'model-cache64-rvcpipe-render32-storage-v2']
    mp=[read(p/'prepared.json')for p in models]
    for p,m in zip(models,mp):
        table(p,m['source_sha256']);bind(p/'prepared.json');bind(p/'build.json')
        assert read(p/'build.json')['returncode']==0
    a,b=mp[1:]
    assert set(a['source_sha256'])==set(b['source_sha256'])
    delta={n:{'before':a['source_sha256'][n],'after':h}for n,h in b['source_sha256'].items()if a['source_sha256'][n]!=h}
    cache='frozen/src/fpga/common/gpu_color_depth_cache.sv'
    assert delta=={cache:{'before':'2f68b6571a5fbaa88ec016d571d84f0403acaf0b000efab70daa9b4ed2f12aea',
                          'after':'13fa29b4d44202be88ca22fd89ca2b1c901c86049e9e75c828de77a2e651bdb8'}}
    assert a['defs']==b['defs'] and a['cpu_sha256']==b['cpu_sha256'] and a['rvc_pipeline']==b['rvc_pipeline']
    assert a['gpu_cache_kib']==b['gpu_cache_kib']==32
    descriptions=[('F11_selected_v7','interval-f11-selected-v7-v1',models[0],'guest-f11-warm-v1'),
                  ('PGO_selected_v7','interval-baseline-v2',models[0],'guest-warm-v3'),
                  ('PGO_cache64_pipeline_GPU32','interval-render32-storage-v2',models[2],'guest-warm-v3')]
    records={};runs=[];runners=[];configs=[]
    for name,label,model,guest in descriptions:
        run=WINDOW/('run-'+label);runner=WINDOW/('runner-'+label)
        m=manifest(run/'manifest.json');assert m['model_prepared_sha256']==sha(model/'prepared.json')
        r=read(run/'result.json');assert r['status']==read(run/'process.json')['returncode']==0
        assert r['fence']==363 and r['upload_words']==46036
        assert all(r[k]==0 for k in ['write_errors','scan_errors','cram_errors'])
        assert all(x['errors']==0 for x in r['readers'].values())
        rows=[list(map(int,x.split()))for x in (run/'boundaries.tsv').read_text().splitlines()]
        assert [x[0]for x in rows]==[1,2,3]
        assert [x[3:]for x in rows]==[[0,361,0],[23036,362,0],[46036,363,0]]
        assert rows[0][1]==r['entry_cycle'] and rows[-1][1]==r['complete_cycle']
        assert {k:v['pending']for k,v in r['readers'].items()}=={'I':1,'MEM':0,'PER':0,'M1':1,'AUDIO':0}
        for observed,golden in [('entry-regs.bin','entry-1.bin'),('entry-2.bin','entry-2.bin'),('entry-3.bin','entry-3.bin')]:
            assert (run/observed).read_bytes()==(WINDOW/guest/'goldens'/golden).read_bytes()
        for i,buffer in [(1,0),(2,1)]:
            assert (run/f'frame-{i}-buffer{buffer}.bin').read_bytes()==(WINDOW/guest/'goldens'/f'frame-{i}.bin').read_bytes()
        cfg=dict(line.split('=',1)for line in (runner/'input.cfg').read_text().splitlines())
        for n in ['main.cpp','input.cfg','recipe.py','prepared.json','build.json']:bind(runner/n)
        assert read(runner/'build.json')['returncode']==0
        records[name]=dict(first_interval=rows[1][1]-rows[0][1],second_interval=rows[2][1]-rows[1][1],
            total=rows[2][1]-rows[0][1],boundaries=rows,readers=r['readers'],retired=r['retired'],
            model_prepared_sha256=m['model_prepared_sha256'])
        runs.append(run);runners.append(runner);configs.append(cfg)
    assert all((runners[0]/'main.cpp').read_bytes()==(p/'main.cpp').read_bytes()for p in runners[1:])
    assert configs[1]==configs[2]
    diff={k for k in configs[0]if configs[0][k]!=configs[1][k]}
    assert diff=={'gfx_entry_pc','gfx_end_pc','return_pc','snapshot','segment0_path','segment1_path'}
    exact=['commands.bin','frame.bin']+[f'frame-{n}{suffix}.bin'for n in [1,2]for suffix in ['', '-buffer0','-buffer1','-buffer2']]
    for name in exact:assert all((runs[0]/name).read_bytes()==(p/name).read_bytes()for p in runs[1:]),name
    # Storage rewrite itself has no observed transaction, execution, or cycle change.
    control=WINDOW/'run-interval-rvcpipe-render32-v1'
    manifest(control/'manifest.json')
    for name in ['result.json','boundaries.tsv','pc.csv','mmio.tsv',*exact,'entry-regs.bin','entry-2.bin','entry-3.bin']:
        assert (control/name).read_bytes()==(runs[2]/name).read_bytes(),name
    expected=[(4201145,4629250,8830395),(3878071,4284150,8162221),(3722274,4146763,7869037)]
    assert [(r['first_interval'],r['second_interval'],r['total'])for r in records.values()]==expected
    def compare(before,after):
        a,b=records[before],records[after];keys=['first_interval','second_interval','total']
        return dict(saved_cycles={k:a[k]-b[k]for k in keys},reduction_percent={k:100*(a[k]-b[k])/a[k]for k in keys})
    sw=compare('F11_selected_v7','PGO_selected_v7')
    hw=compare('PGO_selected_v7','PGO_cache64_pipeline_GPU32')
    total=compare('F11_selected_v7','PGO_cache64_pipeline_GPU32')
    OUT.mkdir();shutil.copyfile(__file__,OUT/'recipe.py')
    save(OUT/'result.json',dict(passed=True,checked_input_hashes=checked,cases=records,
        software_on_same_selected_core=sw,hardware_under_same_selected_PGO=hw,defined_combined_comparison=total,
        storage_source_delta=delta,storage_zero_cycle_native_equivalence=True,
        cross_app_byte_exact_outputs=exact,per_ELF_entry_goldens_exact=True,
        deployment_ready=False,full_frame_FPS_claimed=False,
        limitations=[
            'The reference is preserved F11 normal on selected-v7 model-v3; it is not the historical deployed core or the F10 diagnostic SD executable.',
            'The same-core software difference includes three renderer changes, LOD5 usage and compiler PGO. It is not a PGO-only speedup or software-only arithmetic optimization.',
            'The combined candidate adds I64/D64, a registered RVC stage, GPU32, and qualified equivalent timing/storage factors. This result does not establish physical fit/clock qualification.',
            'There are two renderer-entry intervals, with cold initial CPU caches and one warmed renderer following original game/audio CPU work. No sustained all-level/FPS claim.',
            'Each ELF matches its own reference architecture; cross-ELF register or memory-image identity is neither required nor asserted.',
            'Cross-app native command/buffer bytes are exact; inherited analytic reference differences are explicitly classified buffer-role/address and first-use-clear changes.',
            'Bounded guest services and fixed-base synthetic scan/audio traffic do not qualify native OS interrupts, PCM mixing or physical presentation.',
            'Counters include bootstrap where noted and pending final instruction fetch; only entry-to-entry cycles are used for gains.',
            'Independent review ran no new simulation, compilation, FPGA tool or deployment.'
        ]))
    (OUT/'REPORT.txt').write_text('Defined F11 / selected-software / hardware native comparison: PASS\n\n'
        'All three runs have the identical native host. F11 and PGO use the exact same model-v3; F11 config differences are only its own entry/return addresses and snapshot/guest paths. '
        'All three per-ELF entry snapshots match their own goldens, all46,036 raw command words match across applications, and all buffer dumps match. '
        'The second frame is correctly in actual buffer1, versus reference buffer2, as already classified. Every observed bus/oracle error is zero.\n\n'
        'Case                                      first interval  warm second   total cycles\n'
        'F11 normal on selected v7                       4201145       4629250        8830395\n'
        'Selected PGO app on the same v7                  3878071       4284150        8162221\n'
        'Selected PGO + cache64/RVC pipeline/GPU32        3722274       4146763        7869037\n\n'
        f'Same-core software saving:668,174 cycles ({sw["reduction_percent"]["total"]:.6f}%). '
        'This includes the three renderer changes and hardware-LOAD usage as well as PGO; it is not PGO alone.\n'
        f'Additional hardware saving under the same app:293,184 cycles ({hw["reduction_percent"]["total"]:.6f}%).\n'
        f'Defined combined saving:961,358 cycles ({total["reduction_percent"]["total"]:.6f}%); warmed second interval {total["reduction_percent"]["second_interval"]:.6f}% less.\n\n'
        'The storage rewrite itself preserves every observed cycle, PC sample, MMIO record and output relative to the prior GPU32 run. '
        'The historical deployed hardware is not reconstructed here, physical timing remains separate, and this bounded guest/synthetic-traffic window is not a native OS or FPS measurement.\n')
    save(OUT/'manifest.json',dict(passed=True,files={p.name:sha(p)for p in OUT.iterdir()if p.is_file()},evidence_sha256=evidence))
    print(sha(OUT/'manifest.json'))

if __name__=='__main__':main()
