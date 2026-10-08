#!/usr/bin/env python3
"""Independent source/output check of the renderer-entry-aligned native pair."""
import difflib
import json
import shutil
import struct
from phase10_native_renderer_check import ROOT, P, WINDOW, SNAP, ELF, EXPECTED_ELF, sha, read, save, check_table

OUT = P / 'native-renderer-aligned-check-v1'


def main():
    assert not OUT.exists()
    evidence = {}
    checked = 0
    def bind(p):
        evidence[str(p.relative_to(ROOT))] = sha(p)
    historical_resolutions = {}
    def manifest(p, frozen_tools=None):
        nonlocal checked
        d = read(p)
        checked += check_table(p.parent, d.get('files', {}))
        for key in ['inputs_sha256', 'evidence_sha256']:
            for rel, digest in d.get(key, {}).items():
                target = ROOT/rel
                if frozen_tools and rel in frozen_tools:
                    target = frozen_tools[rel]
                    historical_resolutions[rel] = str(target.relative_to(ROOT))
                    bind(target)
                assert sha(target) == digest, str(target)
                checked += 1
        bind(p)
        return d
    assert sha(ELF) == EXPECTED_ELF
    for label in ['native-renderer-check-v1', 'native-renderer-method-review-v1',
                  'native-renderer-cache-check-v1', 'cpu-cache64-source-review-v1',
                  'cpu-cache64-rvc19-v1', 'cpu-cache64-rvc19-source-review-v1',
                  'cpu-cache64-rvc19-native-review-v1', 'audio-ramp-flat-v1',
                  'audio-ramp-flat-source-review-v1', 'gpu-recip-write-v1',
                  'gpu-recip-write-review-v2']:
        manifest(P/label/'manifest.json')
    models = [WINDOW/'model-v3', WINDOW/'model-cache64-timing-v1']
    prepared = [read(m/'prepared.json') for m in models]
    for m, d in zip(models, prepared):
        checked += check_table(m, d['source_sha256'])
        for key in d['source_sha256']: bind(m/key)
        bind(m/'prepared.json'); bind(m/'build.json')
        assert read(m/'build.json')['returncode'] == 0
    a, b = prepared
    assert a['defs'] == b['defs'] and a['selected_qsf_sha256'] == b['selected_qsf_sha256']
    assert set(a['source_sha256']) == set(b['source_sha256'])
    delta = {k: {'baseline': a['source_sha256'][k], 'candidate': v}
             for k, v in b['source_sha256'].items() if a['source_sha256'][k] != v}
    expected = {
        'frozen/src/fpga/common/audio_mixer.v': 'f9cbfe37da81ae6d5564df076e75c6d28f46feabb992bb695392d06aa55e0ba4',
        'frozen/src/fpga/common/gpu_core.v': '238f44273b6bb0188447485d1501e898c8df0c1cb9b758841e8af7ceac414adb',
        'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v': '32cf8619ebd6beb9b7c62e593e300ba85486daf9c70c199ad0f978a23b80b3ea',
    }
    assert {k: v['candidate'] for k, v in delta.items()} == expected
    assert not any('audio_mixer.v' in x for x in b['command'])
    runners = [WINDOW/'runner-aligned-baseline-v1', WINDOW/'runner-aligned-candidate-v1']
    for f in ['main.cpp', 'input.cfg', 'recipe.py']:
        assert (runners[0]/f).read_bytes() == (runners[1]/f).read_bytes()
    old = (WINDOW/'runner-v2/main.cpp').read_text()
    new = (runners[0]/'main.cpp').read_text()
    transformed = old
    edits = [
        ('        tb->vsync=(c%1666667==0);\n        if(c>=next_scan&&!scan) {',
         '        // Align synthetic traffic to measured renderer entry, so differing\n        // cache-initializer latency cannot change the comparison\'s phase.\n        tb->vsync=(start!=UINT64_MAX&&(c-start)%1666667==0);\n        if(start!=UINT64_MAX&&c>=next_scan&&!scan) {'),
        ('        if(c>=next_audio&&!tb->inj_audio_valid)tb->inj_audio_valid=1;',
         '        if(start!=UINT64_MAX&&c>=next_audio&&!tb->inj_audio_valid)tb->inj_audio_valid=1;'),
        ('                start=c;std::printf("GFX_ENTRY c=%llu\\n",(unsigned long long)c);',
         '                start=c;next_scan=c+30000;next_audio=c+30000;\n                std::printf("GFX_ENTRY c=%llu\\n",(unsigned long long)c);'),
    ]
    for before, after in edits:
        assert transformed.count(before) == 1
        transformed = transformed.replace(before, after)
    assert transformed == new
    runs = [WINDOW/'run-aligned-baseline-v1', WINDOW/'run-aligned-candidate-v1']
    expected_regs = (SNAP/'cpu-state.bin').read_bytes()[:66*4]
    timing = []
    stats = []
    for run, model in zip(runs, models):
        d = manifest(run/'manifest.json')
        assert d['model_prepared_sha256'] == sha(model/'prepared.json')
        r = read(run/'result.json'); stats.append(r)
        assert read(run/'process.json')['returncode'] == r['status'] == 0
        assert r['fence'] == 362
        assert all(r[k] == 0 for k in ['write_errors', 'scan_errors', 'cram_errors'])
        assert all(v['errors'] == 0 and v['pending'] == 0 for v in r['readers'].values())
        assert (run/'entry-regs.bin').read_bytes() == expected_regs
        assert (run/'commands.bin').read_bytes() == (SNAP/'commands.bin').read_bytes()
        assert (run/'frame.bin').read_bytes() == (SNAP.parent/'frame_00360.bin').read_bytes()
        assert (run/'commands.bin').stat().st_size == 23036*4
        assert (run/'frame.bin').stat().st_size == 307200
        timing.append(dict(entry_cycle=r['entry_cycle'], call_cycles=r['finished_cycle']-r['entry_cycle'],
                           drain_cycles=r['complete_cycle']-r['finished_cycle'],
                           call_and_drain_cycles=r['complete_cycle']-r['entry_cycle']))
    assert [t['call_cycles'] for t in timing] == [2927230, 2788545]
    assert [t['drain_cycles'] for t in timing] == [12737, 15722]
    # Previous timing-only CPU/GPU changes have already been run together against
    # the plain cache64 model using the identical old runner and complete traces.
    for f in ['result.json', 'pc.csv', 'mmio.tsv', 'frame.bin', 'commands.bin', 'entry-regs.bin']:
        x, y = WINDOW/'run-cache64-v1'/f, WINDOW/'run-cache64-timing-v1'/f
        assert x.read_bytes() == y.read_bytes()
        bind(x); bind(y)
    manifest(WINDOW/'run-cache64-timing-v1/manifest.json', {
        'tools/experiments/os30_fps25_20261005/native_renderer_main.cpp': WINDOW/'runner-cache64-timing-v1/main.cpp',
        'tools/experiments/os30_fps25_20261005/phase10_native_renderer_run.py': WINDOW/'runner-cache64-timing-v1/recipe.py',
    })
    change = dict(call_cycles_saved=timing[0]['call_cycles']-timing[1]['call_cycles'],
                  call_reduction_percent=100*(1-timing[1]['call_cycles']/timing[0]['call_cycles']),
                  call_and_drain_cycles_saved=timing[0]['call_and_drain_cycles']-timing[1]['call_and_drain_cycles'],
                  call_and_drain_reduction_percent=100*(1-timing[1]['call_and_drain_cycles']/timing[0]['call_and_drain_cycles']))
    OUT.mkdir()
    shutil.copyfile(__file__, OUT/'recipe.py')
    (OUT/'runner-change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile='release-epoch runner', tofile='renderer-entry-epoch runner')))
    result = dict(passed=True, elf_sha256=EXPECTED_ELF, source_delta=delta,
                  identical_runner_and_runtime_config=True, checked_input_hashes=checked,
                  exact_entry_words=66, exact_command_words=23036, exact_frame_bytes=307200,
                  baseline=timing[0], candidate=timing[1], change=change,
                  readers={'baseline': stats[0]['readers'], 'candidate': stats[1]['readers']},
                  timing_component_trace_equality=True, historical_tool_reference_resolutions=historical_resolutions, deployment_ready=False,
                  limitations=[
                      'One captured renderer call with cold CPU/GPU caches except guest bootstrap effects; no warm multi-frame, game, presentation or hardware FPS claim.',
                      'Synthetic scan/audio launch epochs are now equal relative to first gfx_run retirement. Their later arbitration delays evolve naturally. This does not restore an original real scan/audio phase.',
                      'The candidate includes exact-equivalent CPU bit19 and GPU reciprocal timing rewrites. Prior actual native traces are byte-identical to plain I64/D64, independently of this phase correction.',
                      'The changed audio source is in the frozen source inventory but is not instantiated in this native fixture; actual audio correctness comes from its separate paired mixer proof. Audio memory pressure here is synthetic.',
                      'Reader transaction counters include guest restore and completion drain. They are not exact retired miss-instruction counts. Retired polling counts depend on schedule.',
                      'Prior release-epoch native comparisons and negative analytic I64/D64 screens remain preserved; this report refines the matched cold native comparison only.',
                      'Cycle savings do not establish fitted clock qualification. Milliseconds at configured 100 MHz are experimental until physical timing is qualified.'
                  ])
    save(OUT/'result.json', result)
    (OUT/'REPORT.txt').write_text(
        'Renderer-entry-aligned native comparison: PASS\n\n'
        'The only host change is the common synthetic scan/audio/vsync epoch. Both runs use identical runner/config and original qualified PGO application. '
        'All 66 architectural entry words, 23,036 ordered GPU command words, and 307,200 color/depth bytes match the captured workload.\n\n'
        f"Renderer call: 2,927,230 -> 2,788,545 cycles; {change['call_cycles_saved']:,} saved ({change['call_reduction_percent']:.6f}%). "
        f"Through GPU drain: 2,939,967 -> 2,804,267; {change['call_and_drain_cycles_saved']:,} saved ({change['call_and_drain_reduction_percent']:.6f}%).\n"
        'I linefills: 3,292 -> 1,315; D linefills: 3,752 -> 3,796. These counters include bootstrap/drain. '
        'Candidate CPU uses I64/D64 plus proven bit19 rewrite; GPU8 retains proven reciprocal-write rewrite. '
        'Their prior native result/PC/MMIO/output traces equal the plain cache64 control exactly. Audio RTL is not instantiated here.\n\n'
        'Scope remains one cold renderer invocation, with synthetic shared-bus pressure aligned to renderer entry. '
        'No warm frame, full game, presentation or hardware FPS conclusion. Prior results remain immutable.\n')
    save(OUT/'manifest.json', dict(passed=True, files={f.name:sha(f) for f in OUT.iterdir() if f.is_file()}, evidence_sha256=evidence))
    print(json.dumps(dict(stage=str(OUT), manifest_sha256=sha(OUT/'manifest.json'), change=change)))


if __name__ == '__main__':
    main()
