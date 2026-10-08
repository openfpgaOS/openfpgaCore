#!/usr/bin/env python3
"""Consolidate existing measured acceleration and rejected alternatives."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'selected-performance-summary-v2'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    stages = ('selected-performance-summary-v1', 'load-submit-coupled-v1',
              'load-submit-delivery-v1', 'selected-backend-profile-v2',
              'fallback-observe-v1', 'load-submit-stack-coupled-v2',
              'backend-wire-counters-coupled-v1')
    proofs = [P / s / 'manifest.json' for s in stages]
    for p in proofs:
        d = json.loads(p.read_text())
        for n, h in d.get('files', {}).items():
            assert sha(p.parent / n) == h, (p, n)
        for n, h in d.get('evidence_sha256', {}).items():
            assert sha(ROOT / n) == h, (p, n)
    warm = json.loads((P / 'load-submit-coupled-v1/result-warm.json').read_text())
    cold = json.loads((P / 'load-submit-coupled-v1/result-cold.json').read_text())
    original = 37.82757375
    selected = warm['timing']['overlap']['frame_ms']
    saving = original - selected
    OUT.mkdir(exist_ok=False)
    result = dict(original_busy_window_ms=original, selected_busy_window_ms=selected,
        saving_ms=saving, time_reduction_percent=100 * saving / original,
        modeled_throughput_increase_percent=100 * (original / selected - 1),
        immediate_overlap_saving_ms=warm['frame_saving_ms'],
        selected_cold_window_ms=cold['timing']['overlap']['frame_ms'],
        app_elf_sha256='bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405',
        pocket_fps_measured=False, target_25fps_demonstrated=False,
        physical_timing_qualification_separate=True)
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    report = f'''Selected SM64 performance result

Busy Bob-omb Battlefield window: {original:.8f} -> {selected:.8f} ms/frame.
Saving: {saving:.8f} ms, {result['time_reduction_percent']:.4f}% less time,
or {result['modeled_throughput_increase_percent']:.4f}% greater modeled throughput.
This is a measured simulator comparison, not a Pocket FPS reading or proof
of the25FPS goal. Hardware resource/timing qualification is separate.

The retained changes are GPU span lookahead, a fixed-function LOD5 vertex
transform/reciprocal/one-light engine with immutable CRAM source reuse and
cacheable BRAM results, bounded pool recycling, qualified cache timing
changes, and adjacent LOAD/submission overlap. The final overlap alone saves
{warm['frame_saving_ms']:.8f}ms in the warm window and {cold['frame_saving_ms']:.8f}ms cold.
It submits a prepared batch after starting the next immediately adjacent
vertex LOAD, then observes LOAD completion through the existing driver.
1059/2608 LOADs overlap;0.864305ms of eligible hardware work is hidden.
The total gain is smaller because CPU work and GPU queue waits move around.

The remaining bottleneck is combined CPU triangle preparation/submission
and GPU fragment/memory backpressure. Current indexed backend3.47814ms
includes1.24430ms queue polling and0.63360ms mandatory MMIO copies. It is
not3.48ms of removable geometry arithmetic. The per-PC audit finds no
same-generation duplicate prepared vertices and no duplicate uploads.
The compiler already unrolls all three corner emit paths. Preparation is
about5.4ms; clipping/fallback projection and other renderer work remain.

Rejected after matched evidence: canonical no-copy vertex alias (only0.124ms
gain with443 additional ALUTs); stack-owned overlap (no meaningful gain);
wire-counter removal (0.109/0.157ms slower warm/cold after code-layout changes).
CPU lighting overlap, reduced caches, raw rejected-triangle forwarding and
other earlier regressions remain excluded. No workload, geometry, texture,
audio, clipping or shading quality was reduced to obtain the retained gain.

Actual application instructions execute in the CPU/cache timing model;
GPU, LOAD, render cache and memories execute RTL. Each compared warm/cold
window contains eight actual color/depth frames, all exact, and exact full
scene/cull histories. Separately, five functional modes preserve721 frame
records and eight ordered streams each; these are not721 RTL framebuffers.
The CPU progression denominator excludes the final presentation tail.
GPU counters overlap it and cannot be added to it. Cold frames234..241 are
a different window, measuring{cold['timing']['overlap']['frame_ms']:.8f}ms; they are not
the warm comparison denominator. Native component checks supplement this
model; it is not native full-game execution or a broad-world performance test.

The selected app-only delivery is load-submit-delivery-v1. Its ordinary clean
build from the exact three-file source patch reproduces the qualified ELF.
PROFILE=0 PERF_DIAG=0 must remain explicit. Matching LOD5 hardware is required
for acceleration; absent/failed/unsupported devices use the CPU fallback.
The earlier detailed budget is retained in selected-performance-summary-v1;
its selected app predates the final0.306615ms overlap improvement.
'''
    (OUT / 'REPORT.txt').write_text(report)
    shutil.copy2(__file__, OUT / 'recipe.py')
    manifest = dict(files={p.name: sha(p) for p in OUT.iterdir() if p.is_file()},
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in proofs})
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
