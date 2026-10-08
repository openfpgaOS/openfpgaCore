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


def check_manifest(p, relocations=None):
    m = read(p)
    count = 0
    for key in ('files', 'evidence_sha256', 'inputs_sha256'):
        for name, expected in m.get(key, {}).items():
            q = (relocations or {}).get(name, (p.parent if key == 'files' else ROOT) / name)
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
    component_review = P / 'gpu-recip-write-source-review-v1/manifest.json'
    w = P / 'native-renderer-window-v1'
    run = w / 'run-cache64-timing-v1'
    base = w / 'run-cache64-v1'
    assert sha(run / 'manifest.json') == '60a429bebea2ce592cca87b397bf43b1f165f5cb4532f8b3ca84b8c6ff8c1480'
    relocations = {
        'tools/experiments/os30_fps25_20261005/native_renderer_main.cpp': w / 'runner-cache64-timing-v1/main.cpp',
        'tools/experiments/os30_fps25_20261005/phase10_native_renderer_run.py': w / 'runner-cache64-timing-v1/recipe.py',
    }
    manifest = read(run / 'manifest.json')
    for old, frozen in relocations.items():
        assert manifest['inputs_sha256'][old] == manifest['evidence_sha256'][str(frozen.relative_to(ROOT))]
    native_bindings = check_manifest(run / 'manifest.json', relocations)
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
        historical_tool_input_relocations={k:str(v.relative_to(ROOT)) for k,v in relocations.items()},
        gpu_cache_kib=8, cpu_cache_layout='I64/D64', audio_mixer_executed=False,
        audio_proof='Separate actual dual-mixer exhaustive/component evidence',
        cold_cpu_caches=True, native_full_frame_fps_claimed=False, new_tests_run=False),
        'PASS: combined native replay is byte-exact to plain I64/D64, GPU8.\n'
        'Two historical live tool input paths were resolved only to their exact-SHA frozen runner copies already bound in the same manifest; all other bindings were checked at their original paths. The initial strict-path audit stopped on the mutable runner, not on a source/model/output discrepancy.\n'
        'All 165 files in each frozen model were rehashed; exactly CPU bit19, GPU reciprocal DATA/enable and audio ramp source differ. Defines, QSF, remaining source bytes and model build command after output-root normalization are identical. Runner and input configuration are identical.\n'
        'Result JSON, complete PC trace, MMIO trace, 23,036 command words, 307,200-byte framebuffer and 66 entry-state words are byte-identical. Renderer retirement spans 2,786,449 cycles and final GPU drain 18,377 cycles. Every memory/checker error and pending reader count is zero.\n'
        'The actual renderer topology drives synthetic audio M3 traffic; audio_mixer is neither compiled nor executed by this replay despite being present in the frozen source inventory. Its behavioral qualification remains the separate exhaustive and actual dual-mixer component proof. CPU bit19 and GPU reciprocal are active in the native model.\n'
        'This is one captured frame-360 renderer call with cold CPU caches and an explicit guest service bridge, not game/audio execution or Pocket FPS. No new simulations, synthesis, fitting or source changes were performed in this review.\n',
        [run / 'manifest.json', ma / 'prepared.json', mb / 'prepared.json', base / 'result.json'] + proofrefs)


if __name__ == '__main__':
    main()
