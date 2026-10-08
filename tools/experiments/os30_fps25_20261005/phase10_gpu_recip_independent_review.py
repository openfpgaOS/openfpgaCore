#!/usr/bin/env python3
"""Read existing reciprocal and native evidence; never build or simulate."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text())


def check_manifest(p):
    m = read(p)
    count = 0
    for key in ('files', 'evidence_sha256', 'inputs_sha256'):
        for name, expected in m.get(key, {}).items():
            q = (p.parent if key == 'files' else ROOT) / name
            assert sha(q) == expected, q
            count += 1
    return count


def once(s, old, new=''):
    assert s.count(old) == 1, old
    return s.replace(old, new)


def seal(label, review, report, refs):
    out = P / label
    out.mkdir(exist_ok=False)
    (out / 'review.json').write_text(json.dumps(review, indent=2) + '\n')
    (out / 'REPORT.txt').write_text(report)
    (out / 'recipe.py').write_bytes(Path(__file__).read_bytes())
    evidence = {str(p.relative_to(ROOT)): sha(p) for p in refs}
    manifest = dict(passed=True, deployment_ready=False,
                    files={p.name: sha(p) for p in out.iterdir() if p.is_file()},
                    evidence_sha256=evidence)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(label, sha(out / 'manifest.json'))
    return out / 'manifest.json'


def main():
    c = P / 'gpu-recip-write-v1'
    replay = P / 'gpu-recip-write-review-v2'
    assert sha(c / 'manifest.json') == 'a5fb30e7e45927b7e636b1030f178248e1f39ee3d7aade09c8a3290623ecdf1e'
    assert sha(c / 'gpu_core.v') == '238f44273b6bb0188447485d1501e898c8df0c1cb9b758841e8af7ceac414adb'
    assert sha(c / 'baseline.v') == '3c171d36bce942735040d3cdc2d96a5f9959b46b2b5c4896455d1c4763ffa4d1'
    counts = {str(p.relative_to(P)): check_manifest(p) for p in [c / 'manifest.json', replay / 'manifest.json']}
    old = (c / 'baseline.v').read_text()
    new = (c / 'gpu_core.v').read_text()
    assert len(re.findall(r'\brecip_q16_r\s*<=', old)) == 4
    assert len(re.findall(r'\brecip_q16_r\s*<=', new)) == 1
    start = new.index('// Default the reciprocal write DATA')
    end = new.index('reg signed [31:0] nr_two_minus_xy;', start)
    data = new[start:end]
    new = once(new, data)
    new = once(new, '            if (persp_active && recip_q16_write_enable)\n                recip_q16_r <= recip_q16_write_data;\n')
    old = once(old, '                        recip_q16_r <= $signed(pss_recip_cache_value);\n')
    old = once(old, '                    reg [5:0]  recip_shamt;\n                    reg [44:0] recip_funnel;\n')
    begin = old.index('                    recip_shamt  = pss_use_q29 ?')
    end = old.index('                    persp_pss <= PSS_NR_MUL_X;', begin)
    seed = old[begin:end]
    old = once(old, seed)
    old = once(old, '                    reg signed [63:0] recip_shifted;\n')
    begin = old.index('                    if (pss_use_q29) begin\n                        recip_shifted = dsp_p >>> 16;')
    end = old.index('                    persp_pss <= PSS_MUL;', begin)
    nr = old[begin:end]
    old = once(old, nr)
    assert old == new, 'Unrelated source or control change'
    probe = (c / 'literal/probe.v').read_text()
    assert data in probe
    assert seed.replace('recip_q16_r <=', 'old_value =') in probe
    assert nr.replace('recip_q16_r <=', 'old_value =') in probe
    result = read(c / 'result.json')
    assert result['passed'] and result['literal']['checks'] == 7291456
    assert result['exact_cycle_pixel_pairs'] == 16
    assert result['native']['totals']['cycles'] == 30829640
    assert result['native']['totals']['q29'] == result['native']['totals']['hits'] == 0
    assert all(read(replay / f)['passed'] for f in ['result.json', 'recip-result.json', 'boundaries-v3/result.json'])
    component_review = seal('gpu-recip-write-source-review-v1', dict(
        passed=True, exact_source_scope=True, findings=[], deployment_ready=False,
        parent_sha256=sha(c / 'baseline.v'), candidate_sha256=sha(c / 'gpu_core.v'),
        literal_checks=7291456, native_pairs=16, live_next_state_checks=30829640,
        existing_manifest_bindings_checked=counts, added_registers=0, added_cycles=0,
        native_q29_coverage=False, literal_q29_control_and_data_coverage=True,
        new_tests_run=False, area_or_timing_gain_proven=False),
        'PASS: reciprocal write ownership and arithmetic are preserved.\n'
        'Removing only the new DATA/enable block and the four old assignment sites/local combinational temporaries reconstructs identical source. The same enclosing reset, soft-reset and S_FRAG_PIPE priorities apply; the new write additionally requires the original persp_active ownership. TOP8 writes only on the Q29 cache hit; SHIFT and NR write on exactly their original edges. No original reset assignment existed, so reset and every no-write state still hold.\n'
        'The seed remains a 45-bit left-shift funnel sliced [44:13]. Q29 refinement remains signed 64-bit arithmetic right shift followed by low-32 truncation. Ordinary refinement remains dsp_p[47:16] plus exactly the old constant-Z one-LSB bias. The three state codes are distinct, so the default seed and the two overrides cannot change a simultaneously owned case.\n'
        'All author evidence was rehashed. The literal probe embeds the exact new DATA block and old expressions; 7,291,456 checks include exhaustive LUT/CLZ/format and control cases. Sixteen actual GPU pairs preserve colors, depths, fences and cycle counts; 30,829,640 live checks use an original-expression next-state oracle independent of the new DATA wire. Live selected Q29/cache-hit counts are zero; Q29 arithmetic/control coverage is literal only. The preserved initial random-input failure was testbench width masking, corrected without RTL changes.\n'
        'This review runs no simulations or Quartus. It supports composition, not deployment or a timing-gain claim.\n',
        [c / 'manifest.json', c / 'result.json', c / 'candidate.patch', replay / 'manifest.json'])

    w = P / 'native-renderer-window-v1'
    run = w / 'run-cache64-timing-v1'
    base = w / 'run-cache64-v1'
    assert sha(run / 'manifest.json') == '60a429bebea2ce592cca87b397bf43b1f165f5cb4532f8b3ca84b8c6ff8c1480'
    native_bindings = check_manifest(run / 'manifest.json')
    ma, mb = w / 'model-cache64-v1', w / 'model-cache64-timing-v1'
    a, b = read(ma / 'prepared.json'), read(mb / 'prepared.json')
    for d, m in [(a, ma), (b, mb)]:
        for name, expected in d['source_sha256'].items():
            assert sha(m / name) == expected, m / name
    assert a['source_sha256'].keys() == b['source_sha256'].keys()
    diff = {n: {'old': a['source_sha256'][n], 'new': b['source_sha256'][n]} for n in a['source_sha256'] if a['source_sha256'][n] != b['source_sha256'][n]}
    assert set(diff) == {'frozen/src/fpga/common/gpu_core.v', 'frozen/src/fpga/common/audio_mixer.v', 'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'}
    assert diff['frozen/src/fpga/common/gpu_core.v']['new'] == sha(c / 'gpu_core.v')
    assert diff['frozen/src/fpga/common/audio_mixer.v']['new'] == sha(P / 'audio-ramp-flat-v1/audio_mixer.v')
    assert diff['frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']['new'] == sha(P / 'cpu-cache64-rvc19-v1/VexiiRiscv.v')
    assert a['defs'] == b['defs'] and a['selected_qsf_sha256'] == b['selected_qsf_sha256']
    assert a['cache_layout'] == b['cache_layout'] == 'I64/D64' and b['gpu_cache_kib'] == 8
    assert [v.replace(str(ma), '@MODEL@') for v in a['command']] == [v.replace(str(mb), '@MODEL@') for v in b['command']]
    exact = {}
    for name in ['result.json', 'pc.csv', 'mmio.tsv', 'frame.bin', 'commands.bin', 'entry-regs.bin']:
        assert (run / name).read_bytes() == (base / name).read_bytes(), name
        exact[name] = sha(run / name)
    for name in ['main.cpp', 'input.cfg']:
        assert (w / 'runner-cache64-timing-v1' / name).read_bytes() == (w / 'runner-cache64-v1' / name).read_bytes()
    nr = read(run / 'result.json')
    assert nr['status'] == 0 and nr['upload_words'] == 23036
    assert (run / 'frame.bin').stat().st_size == 307200
    assert (run / 'entry-regs.bin').stat().st_size == 66 * 4
    assert all(nr[k] == 0 for k in ['write_errors', 'scan_errors', 'cram_errors'])
    assert all(r['errors'] == r['pending'] == 0 for r in nr['readers'].values())
    assert nr['finished_cycle'] - nr['entry_cycle'] == 2786449
    assert nr['complete_cycle'] - nr['finished_cycle'] == 18377
    proofrefs = [component_review, P / 'cpu-cache64-rvc19-native-review-v1/manifest.json',
                 P / 'audio-ramp-flat-source-review-v1/manifest.json', P / 'native-renderer-method-review-v1/manifest.json']
    for ref in proofrefs:
        check_manifest(ref)
    seal('cache64-timing-native-review-v1', dict(
        passed=True, deployment_ready=False, findings=[], source_changes=diff,
        model_frozen_files_checked=len(a['source_sha256']), existing_run_bindings_checked=native_bindings,
        exact_outputs_sha256=exact, renderer_cycles=2786449, drain_cycles=18377,
        gpu_cache_kib=8, cpu_cache_layout='I64/D64', audio_mixer_executed=False,
        audio_proof='Separate actual dual-mixer exhaustive/component evidence',
        cold_cpu_caches=True, native_full_frame_fps_claimed=False, new_tests_run=False),
        'PASS: combined native replay is byte-exact to plain I64/D64, GPU8.\n'
        'All 165 files in each frozen model were rehashed; exactly CPU bit19, GPU reciprocal DATA/enable and audio ramp source differ. Defines, QSF, remaining source bytes and model build command after output-root normalization are identical. Runner and input configuration are identical.\n'
        'Result JSON, complete PC trace, MMIO trace, 23,036 command words, 307,200-byte framebuffer and 66 entry-state words are byte-identical. Renderer retirement spans 2,786,449 cycles and final GPU drain 18,377 cycles. Every memory/checker error and pending reader count is zero.\n'
        'The actual renderer topology drives synthetic audio M3 traffic; audio_mixer is neither compiled nor executed by this replay despite being present in the frozen source inventory. Its behavioral qualification remains the separate exhaustive and actual dual-mixer component proof. CPU bit19 and GPU reciprocal are active in the native model.\n'
        'This is one captured frame-360 renderer call with cold CPU caches and an explicit guest service bridge, not game/audio execution or Pocket FPS. No new simulations, synthesis, fitting or source changes were performed in this review.\n',
        [run / 'manifest.json', ma / 'prepared.json', mb / 'prepared.json', base / 'result.json'] + proofrefs)


if __name__ == '__main__':
    main()
