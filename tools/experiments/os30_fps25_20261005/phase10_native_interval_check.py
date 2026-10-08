#!/usr/bin/env python3
"""Independent, read-only comparison of the two continuous native intervals."""
import collections
import json
import shutil
import struct
import subprocess
from phase10_native_renderer_check import ROOT, P, WINDOW, ELF, EXPECTED_ELF, sha, read, save

OUT = P / 'native-warm-interval-check-v1'
GUEST = WINDOW / 'guest-warm-v3'
CAPTURE = P / 'native-warm-capture-v4'
SNAP = CAPTURE / 'run-reference/snapshot'


def words(path):
    data = path.read_bytes()
    assert len(data) % 4 == 0
    return struct.unpack('<%dI' % (len(data) // 4), data)


def packets(path):
    data, pos, result = words(path), 0, []
    while pos < len(data):
        size = 1 + (data[pos] & 0xffffff)
        assert 1 < size <= 1000 and pos + size <= len(data), (path, pos, size)
        result.append((pos, data[pos:pos + size]))
        pos += size
    assert pos == len(data)
    return result


def main():
    assert not OUT.exists()
    evidence, checks = {}, 0

    def bind(path):
        evidence[str(path.relative_to(ROOT))] = sha(path)

    def table(base, values):
        nonlocal checks
        for rel, digest in values.items():
            path = base / rel
            assert sha(path) == digest, str(path)
            checks += 1
            bind(path)

    def manifest(path):
        value = read(path)
        table(path.parent, value.get('files', {}))
        for key in ['inputs_sha256', 'evidence_sha256']:
            table(ROOT, value.get(key, {}))
        bind(path)
        return value

    assert sha(ELF) == EXPECTED_ELF
    bind(ELF)
    for path in [CAPTURE / 'manifest.json', GUEST / 'manifest.json',
                 P / 'native-renderer-services-v3/component-manifest.json',
                 P / 'native-renderer-services-v3/manifest.json',
                 P / 'native-warm-guest-source-review-v1/manifest.json',
                 P / 'native-interval-alignment-review-v1/manifest.json',
                 P / 'native-renderer-aligned-check-v1/manifest.json']:
        manifest(path)

    runs = [WINDOW / 'run-interval-baseline-v2', WINDOW / 'run-interval-cache64-v2']
    runners = [WINDOW / 'runner-interval-baseline-v2', WINDOW / 'runner-interval-cache64-v2']
    models = [WINDOW / 'model-v3', WINDOW / 'model-cache64-timing-v1']
    for filename in ['main.cpp', 'recipe.py', 'input.cfg']:
        assert (runners[0] / filename).read_bytes() == (runners[1] / filename).read_bytes()
    host = (runners[0] / 'main.cpp').read_text()
    # The stop is pre-edge: do not retire the next gfx instruction or account
    # for an AXI handshake that has not actually occurred.
    stop = host.index('if(tb->obs_retire&&tb->obs_retire_pc==n("gfx_entry_pc")&&start!=UINT64_MAX)')
    assert host.rfind('half(false);', 0, stop) >= 0
    assert stop < host.index('#define TRACK') < host.index('half(true);++cycles;')
    assert host.index('if(entries==n("stop_entries"))', stop) < host.index('#define TRACK')
    assert 'require(!tb->cpu_run,"live host write")' in host
    assert 'require(!tb->obs_gpu_busy,"GPU not drained at subsequent gfx entry")' in host
    assert 'status=errors||!writes.empty()||!aw.empty()||tb->obs_fence!=n("expected_fence")?3:0;' in host
    assert 'tb->vsync=(start!=UINT64_MAX&&(c-start)%1666667==0);' in host
    assert 'start=c;next_scan=c+30000;next_audio=c+30000;' in host
    cfg = dict(line.split('=', 1) for line in (runners[0] / 'input.cfg').read_text().splitlines())
    assert int(cfg['stop_entries']) == 3 and int(cfg['expected_fence']) == 363
    assert int(cfg['scan_base_halfword']) == 16777216

    prepared = [read(m / 'prepared.json') for m in models]
    for model, obj in zip(models, prepared):
        table(model, obj['source_sha256'])
        bind(model / 'prepared.json'); bind(model / 'build.json')
        assert read(model / 'build.json')['returncode'] == 0
    a, b = prepared
    assert a['defs'] == b['defs'] and a['selected_qsf_sha256'] == b['selected_qsf_sha256']
    assert set(a['source_sha256']) == set(b['source_sha256'])
    delta = {k: {'baseline': a['source_sha256'][k], 'candidate': v}
             for k, v in b['source_sha256'].items() if a['source_sha256'][k] != v}
    assert delta == read(P / 'native-renderer-aligned-check-v1/result.json')['source_delta']
    assert not any('audio_mixer.v' in arg for arg in b['command'])

    results, boundaries = [], []
    for run, model in zip(runs, models):
        manifest_data = manifest(run / 'manifest.json')
        assert manifest_data['model_prepared_sha256'] == sha(model / 'prepared.json')
        result = read(run / 'result.json')
        assert result['status'] == read(run / 'process.json')['returncode'] == 0
        assert result['fence'] == 363 and result['upload_words'] == 46036
        assert all(result[key] == 0 for key in ['write_errors', 'scan_errors', 'cram_errors'])
        assert all(row['errors'] == 0 for row in result['readers'].values())
        assert {k: v['pending'] for k, v in result['readers'].items()} == {
            'I': 1, 'MEM': 0, 'PER': 0, 'M1': 1, 'AUDIO': 0}
        rows = [list(map(int, line.split())) for line in (run / 'boundaries.tsv').read_text().splitlines()]
        assert len(rows) == 3
        assert [r[0] for r in rows] == [1, 2, 3]
        assert [r[3] for r in rows] == [0, 23036, 46036]
        assert [r[4] for r in rows] == [361, 362, 363]
        assert all(r[5] == 0 for r in rows)
        assert rows[0][1] == result['entry_cycle'] and rows[2][1] == result['complete_cycle']
        assert rows[2][2] == result['retired']
        for n, filename in enumerate(['entry-regs.bin', 'entry-2.bin', 'entry-3.bin'], 1):
            assert (run / filename).read_bytes() == (GUEST / 'goldens' / f'entry-{n}.bin').read_bytes()
            assert (run / filename).stat().st_size == 66 * 4
        for n, actual_buffer in [(1, 0), (2, 1)]:
            assert (run / f'frame-{n}-buffer{actual_buffer}.bin').read_bytes() == (GUEST / 'goldens' / f'frame-{n}.bin').read_bytes()
            assert (run / f'frame-{n}-buffer{actual_buffer}.bin').stat().st_size == 307200
        # Retain explicit failures of the fixed-reference-buffer comparison.
        assert (run / 'frame-2.bin').read_bytes() != (GUEST / 'goldens/frame-2.bin').read_bytes()
        assert (run / 'commands.bin').read_bytes() != (GUEST / 'goldens/commands.bin').read_bytes()
        results.append(result); boundaries.append(rows)
    pair_exact = ['commands.bin', 'entry-regs.bin', 'entry-2.bin', 'entry-3.bin', 'frame.bin']
    pair_exact += [f'frame-{n}{suffix}.bin' for n in [1, 2]
                   for suffix in ['', '-buffer0', '-buffer1', '-buffer2']]
    for filename in pair_exact:
        assert (runs[0] / filename).read_bytes() == (runs[1] / filename).read_bytes(), filename

    reference, native = packets(GUEST / 'goldens/commands.bin'), packets(runs[0] / 'commands.bin')
    assert words(runs[0] / 'commands.bin')[:23036] == words(GUEST / 'goldens/commands.bin')[:23036]
    # Only the reference's previously unused buffer2 needs its first-use clear.
    # This is a complete packet omission, not merely an address renaming.
    skipped = [r for r in reference if r[0] == 23036]
    assert skipped == [(23036, (0x11000003, 0x10200000, 0x028000f0, 0x02800000))]
    reference_without_clear = [r for r in reference if r[0] != 23036]
    assert len(reference_without_clear) == len(native) == 5562
    changes = []
    for (ref_pos, ref_words), (native_pos, native_words) in zip(reference_without_clear, native):
        assert len(ref_words) == len(native_words)
        if ref_words == native_words:
            continue
        opcode = ref_words[0] >> 24
        assert ref_words[0] == native_words[0] and ref_pos >= 23036
        differing = [i for i, (x, y) in enumerate(zip(ref_words, native_words)) if x != y]
        assert differing == [1], (ref_pos, native_pos, differing)
        if opcode == 0x4a:
            assert ref_words[1] == 0x10200000 and native_words[1] == 0x10100000
            kind = 'SET_TRI_STATE framebuffer base'
        else:
            assert opcode == 0x42 and ref_words == (0x42000002, 2, 363)
            assert native_words == (0x42000002, 1, 363)
            kind = 'FLIP buffer index'
        changes.append(dict(reference_word=ref_pos + 1, native_word=native_pos + 1,
                            opcode=opcode, field=kind, reference=ref_words[1], native=native_words[1]))
    assert collections.Counter(c['opcode'] for c in changes) == {0x4a: 112, 0x42: 1}
    # Check the first-use-clear guard against source and the actual ELF symbol.
    gpu_source = P / 'renderer-pgo-delivery-v7/sources/src/sm64/sm64/src/pc/gfx/gfx_gpu.c'
    source = gpu_source.read_text(); bind(gpu_source)
    assert 'if (GFX_GPU_CLEAR_COLOR || !(cleared_bufs & (1u << (g_draw_idx & 7)))) {' in source
    assert '#define GFX_GPU_CLEAR_COLOR 0' in source
    nm = subprocess.check_output(['riscv64-elf-nm', '-S', str(ELF)], text=True)
    symbol = [line.split() for line in nm.splitlines() if line.split()[-1:] == ['cleared_bufs.0']]
    assert len(symbol) == 1 and symbol[0][1] == '00000001'
    clear_addr = int(symbol[0][0], 16)
    with (SNAP / 'sdram.bin').open('rb') as handle:
        handle.seek(clear_addr - 0x10000000); clear_mask = handle.read(1)[0]
    assert clear_mask == 3 and clear_mask & 2 and not clear_mask & 4

    timing = []
    for rows in boundaries:
        timing.append(dict(cold_first_interval_cycles=rows[1][1] - rows[0][1],
                           warm_second_interval_cycles=rows[2][1] - rows[1][1],
                           total_cycles=rows[2][1] - rows[0][1],
                           interval_retired=[rows[1][2] - rows[0][2], rows[2][2] - rows[1][2]]))
    changes_timing = {key: dict(cycles_saved=timing[0][key] - timing[1][key],
                              reduction_percent=100 * (1 - timing[1][key] / timing[0][key]))
                      for key in ['cold_first_interval_cycles', 'warm_second_interval_cycles', 'total_cycles']}
    assert [t['total_cycles'] for t in timing] == [8162221, 8011629]
    assert [t['warm_second_interval_cycles'] for t in timing] == [4284150, 4221468]
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    save(OUT / 'command-differences.json', dict(
        native_pair_byte_exact=True, native_words=46036, reference_words=46040,
        first_call_exact_words=23036, reference_packets=len(reference), native_packets=len(native),
        omitted_packet=dict(reference_word=skipped[0][0], words=list(skipped[0][1]),
                            reason='First-use color clear: reference uses uncleared buffer2; both native runs reuse initialized buffer1.'),
        cleared_bufs_symbol=clear_addr, captured_clear_mask=clear_mask,
        changed_payload_fields=changes, all_other_ordered_words_exact=True))
    result = dict(passed=True, checked_input_hashes=checks, selected_elf_sha256=EXPECTED_ELF,
                  source_delta=delta, identical_runner_guest_and_config=True,
                  architectural_words_exact_per_entry=66, exact_entries_per_run=3,
                  exact_rendered_color_depth_bytes_per_frame=307200, exact_frames_per_run=2,
                  raw_native_commands_exact_to_each_other=True, raw_reference_commands_exact=False,
                  actual_native_color_buffers=[0, 1], reference_color_buffers=[0, 2],
                  reference_command_difference_scope='One omitted first-use color-clear packet, 112 framebuffer base fields and one FLIP index; every other ordered packet word exact.',
                  baseline=timing[0], candidate=timing[1], improvement=changes_timing,
                  baseline_readers=results[0]['readers'], candidate_readers=results[1]['readers'],
                  boundary_rows=dict(baseline=boundaries[0], candidate=boundaries[1]),
                  full_frame_FPS_claimed=False, deployment_ready=False,
                  limitations=[
                      'Two continuous captured application intervals only: first renderer begins with cold CPU/GPU caches except bootstrap; second renderer follows actual original caller/game/audio application execution. No stable long-run warm/FPS claim.',
                      'Intervals end before the next gfx_run first instruction retires and include original game/audio/pacing work plus private guest-service instruction cost. They are not isolated renderer timings.',
                      'Guest services preserve captured deterministic logical-time/input/mixer-state semantics, not actual OS scheduling, IRQ timing or PCM output/mixer hardware cost. No native OS/audio-output qualification is claimed.',
                      'Real GPU fence and peripheral buffer roles govern acquisition; raw reference commands differ as explicitly recorded. Both native cache variants make the same choices and issue byte-identical commands.',
                      'Synthetic scan/audio pressure is aligned to first renderer entry. Scanout repeatedly reads fixed buffer0; it does not implement a full display engine following each presented buffer.',
                      'All GPU fences are complete and GPU idle at boundaries. One CPU instruction fetch and matching M1 request remain pending at final pre-edge stop; no global CPU/bus quiescence is claimed.',
                      'Reader statistics include guest initialization and stop at the boundary with that pending request. They count accepted linefill/bus transactions, not retired architectural cache-miss instructions.',
                      'Different polling counts are legitimate; retired instructions, freeze/hazard cycles, and service progress vary with cache/bus scheduling.',
                      'Prior cold native and negative analytic I64/D64 results remain preserved; this adds a bounded continuous native comparison. Cycle improvement does not imply a qualified physical frequency.'
                  ])
    save(OUT / 'result.json', result)
    (OUT / 'REPORT.txt').write_text(
        'Continuous native application interval comparison: PASS\n\n'
        'Both runs execute the exact qualified PGO ELF from gfx entry 360 through entry 362, using identical guest/host/config. '
        'All three 66-word architectural entry snapshots match the reference. The two native command streams are byte-identical (46,036 words), '
        'and all actual native framebuffer dumps match each other. Both rendered color/depth images (307,200 bytes each) match the reference.\n\n'
        'The second native render uses buffer1, while the reference uses buffer2. The complete packet comparison allows exactly 112 tri-state framebuffer addresses, '
        'one FLIP index, and omission of the reference buffer2 first-use color clear. Captured cleared_bufs=3 and the unchanged qualified source establish the clear difference. '
        'No geometry/pixel normalization or broad address substitution was performed.\n\n'
        'Cold-first application interval: 3,878,071 -> 3,790,161 cycles (-87,910, 2.266849%).\n'
        'Second application interval, including warmed renderer after actual game/audio work: 4,284,150 -> 4,221,468 cycles (-62,682, 1.463114%).\n'
        'Total: 8,162,221 -> 8,011,629 cycles (-150,592, 1.844988%).\n\n'
        'The original caller and application game/audio code execute natively. Private guest services retain deterministic reference time/mixer/input semantics, '
        'with real guest cache operations and GPU fence/buffer ownership. This is not native OS/PCM/IRQ or sustained FPS evidence. Synthetic scanout remains fixed-base. '
        'The final stop is pre-edge with one outstanding CPU fetch; GPU is idle, fences are 361/362/363 at the three boundaries, and observed bus errors are zero.\n')
    save(OUT / 'manifest.json', dict(passed=True,
         files={p.name: sha(p) for p in OUT.iterdir() if p.is_file()}, evidence_sha256=evidence))
    print(json.dumps(dict(stage=str(OUT), manifest_sha256=sha(OUT / 'manifest.json'), improvement=changes_timing)))


if __name__ == '__main__':
    main()
