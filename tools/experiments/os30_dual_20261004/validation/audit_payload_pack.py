#!/usr/bin/env python3
"""Bind the retained GPU comparison to exact packed-memory proof and sources."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'docs/measurements/os30-dual-20261004'
OUT = BASE / 'validation/fbwq-payload-pack-v1'
SOURCE = BASE / 'fbwq-payload-pack-v1'
RENDER = OUT / 'rendering'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text())


def main():
    proof = read(SOURCE/'manifest.json')
    recipe = read(SOURCE/'recipe.json')
    emitted_path = BASE/'fbwq-payload-pack-emitted-audit-v1/manifest.json'
    emitted = read(emitted_path)
    manifest = read(RENDER/'manifest.json')
    results = read(RENDER/'results.json')
    parent = read(BASE/'validation/texture-offer-v1/qualification.json')
    parent_manifest = read(BASE/'validation/texture-offer-v1/rendering/manifest.json')
    assert results['identical_pixels_cycles_valid_payload_and_axi']
    assert proof['candidate_sha256'] == recipe['candidate_sha256'] == emitted['candidate_sha256'] == manifest['candidate_sha256'] == sha(SOURCE/'gpu_core.v')
    assert proof['reference_sha256'] == recipe['reference_sha256'] == emitted['reference_sha256'] == manifest['baseline_sha256'] == parent['gpu_sha256']
    assert sha(SOURCE/'patch-replay/gpu_core.v') == proof['candidate_sha256']
    assert sha(SOURCE/'pack_fbwq_payload.py') == recipe['script_sha256']
    for name, digest in recipe['evidence'].items():
        assert sha(SOURCE/name) == digest, name
    for name, digest in emitted['hashes'].items():
        assert sha(ROOT/name) == digest, name
    assert emitted['proof_rerun_pass'] and emitted['proof_transitions'] == 819200
    assert emitted['all_other_bytes_unchanged'] and emitted['exact_original_branch_priority_and_unreset_hold_preserved']
    assert proof['same_address_simultaneous_read_write_returns_old_word']
    assert not emitted['packed_no_rw_check']
    for key in ['parameters', 'variant', 'flags', 'cflags']:
        assert manifest[key] == parent_manifest[key], key
    definitions = manifest['variant']['defs'].split()
    assert 'INCLUDE_TEX_QUEUE_RAM' in definitions and 'INCLUDE_TEX_QUEUE_MLAB' not in definitions
    assert manifest['texture_cache_candidate_sha256'] == manifest['texture_cache_baseline_sha256'] == parent['cache_sha256']
    assert manifest['cache_candidate_sha256'] == manifest['cache_baseline_sha256'] == parent['render_cache_sha256']
    assert sha(RENDER/'runner-snapshot.py') == manifest['script_sha256']
    for name, digest in manifest['sources'].items():
        assert sha(RENDER/name) == digest, name
    for name, key in [('baseline-gpu_core.v','baseline_sha256'),('candidate-gpu_core.v','candidate_sha256'),
                      ('candidate-gpu_tex_cache.v','texture_cache_candidate_sha256'),
                      ('candidate-gpu_color_depth_cache.sv','cache_candidate_sha256')]:
        assert sha(RENDER/name) == manifest[key], name
    logs = []
    for variant in ['baseline','candidate']:
        build = read(RENDER/variant/'manifest.json')
        assert sha(RENDER/variant/'obj/Vtb_gpu') == build['simulator_sha256']
        for mode in ['quiet','delayed','gapped']:
            run = results['runs'][variant][mode]
            assert sha(RENDER/variant/(mode+'.log')) == run['log_sha256']
            assert run['passed'] == 79 and len(run['cycles']) == 38
            assert run['texture_offer_monitor']['errors'] == '0'
    for mode in ['quiet','delayed','gapped']:
        a,b = [results['runs'][v][mode] for v in ['baseline','candidate']]
        assert a == b, mode
        logs.append(dict(mode=mode,sha256=a['log_sha256'],entire_logs_identical=True))
    runs = list(results['runs']['candidate'].values())
    totals = lambda field: {key:sum(int(run[field][key]) for run in runs) for key in runs[0][field] if key not in ['payload_hash','axi_hash','hash']}
    fifo = totals('payload_pack_monitor')
    monitor = totals('monitor')
    assert monitor['cycles'] == 42548619 and monitor['hard'] == 387 and monitor['soft'] == 6
    for key in ['reads','writes','simultaneous','read_wrap','write_wrap','start','continue','chain','partial']:
        assert fifo[key] > 0, key
    files = [SOURCE/'manifest.json',SOURCE/'recipe.json',SOURCE/'gpu_core.v',SOURCE/'pack_fbwq_payload.py',
             SOURCE/'patch-replay/gpu_core.v',emitted_path,RENDER/'manifest.json',RENDER/'results.json',
             RENDER/'runner-snapshot.py',BASE/'validation/texture-offer-v1/qualification.json',Path(__file__)]
    files += [SOURCE/name for name in recipe['evidence']]
    files += [RENDER/v/name for v in ['baseline','candidate'] for name in ['manifest.json','build.log','quiet.log','delayed.log','gapped.log']]
    qualification = dict(passed=True,candidate_sha256=proof['candidate_sha256'],reference_sha256=proof['reference_sha256'],
        texture_cache_sha256=parent['cache_sha256'],render_cache_sha256=parent['render_cache_sha256'],
        focused_proof_transitions=819200,independent_emitted_audit=True,
        exact_guarded_replay_and_reversal=True,new_register_bits=0,new_cycles=0,
        same_address_read_write_semantics='Old word, preserved by nonblocking synchronous reads/writes; no no_rw_check override.',
        selected_os30_bram_queue_configuration_exercised=True,all_other_render_configuration_unchanged=True,
        gpu_workloads=38,gpu_modes=3,gpu_pixel_checks=237,gpu_workload_cycle_pairs=114,
        whole_gpu_logs=logs,gpu_monitor_totals=monitor,framebuffer_fifo_totals=fifo,
        texture_monitor_totals=totals('texture_offer_monitor'),
        limitations=['Component cycle/correctness evidence, not game FPS or integrated CPU/GPU execution.',
          'Same-address collisions, swap-priority and arbitrary reset/hold controls are covered by the exhaustive extracted sequential proof; runtime counters state which occurred.',
          'Physical RAM mapping, capacity and timing must be confirmed separately by the full fit.'],
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in files})
    (OUT/'qualification.json').write_text(json.dumps(qualification,indent=2)+'\n')
    report = f'''Packed framebuffer data/strobe RAM qualification: PASS

Candidate: {proof['candidate_sha256']}
Reference: {proof['reference_sha256']}
Texture cache: {parent['cache_sha256']}
Render cache: {parent['render_cache_sha256']}

Only two RAM declarations and their three read/two write pairs change. The
36-bit packed memory retains all 576 storage bits, old-data collisions, original
read/write addresses, branch priority, reset gates and hold semantics. No new
register or pipeline cycle. Guarded replay and independent emitted audit pass;
the exact sequential proof covers 819,200 transitions, including arbitrary
controls, pointers and same-address collisions.

The retained 38 workloads pass in quiet, delayed and gapped memory modes with
the selected os30 BRAM texture queue and unchanged full-feature parameters.
All three complete logs are byte-identical: 114 workload cycle comparisons,
237 pixel acceptance checks, 42,548,619 monitored cycles, 387 hard resets and
6 soft resets per source. Valid-qualified payloads and AXI address/data/strobe/
last/ready activity have identical cycle-indexed hashes. Render-cache and
texture-offer invariants pass on both sides.

Framebuffer FIFO runtime counters (identical on both sides):
{json.dumps(fifo,indent=2)}

Runtime does not cover controls whose counters are zero. In particular, the
extracted proof supplies arbitrary collision/swap/reset qualification beyond
the retained workloads. The memory representation change neither adds a new
collision waiver nor relies on a no-collision state invariant.

These are component correctness/cycle results, not game FPS. The parent owns
physical mapping/capacity/timing qualification. Frozen sources, runner, build
commands, binaries, logs and proof identities are bound in qualification.json.
'''
    (OUT/'REPORT.txt').write_text(report)
    print('PASS: packed GPU proof, exact sources, os30 configuration and 38x3 logs bound.')


if __name__ == '__main__':
    main()
