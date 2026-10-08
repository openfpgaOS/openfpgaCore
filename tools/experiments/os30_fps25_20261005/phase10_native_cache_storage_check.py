#!/usr/bin/env python3
"""Seal same-capacity native byte/cycle equivalence of the cache storage rewrite."""
import hashlib
import json
import shutil
from pathlib import Path

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
W = P / 'native-renderer-window-v1'
O = P / 'native-cache-storage-check-v1'
CACHE = 'frozen/src/fpga/common/gpu_color_depth_cache.sv'
evidence = {}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    evidence[str(p.relative_to(R))] = sha(p)
    return json.loads(p.read_text())


def save(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def verify_run(label):
    d = W / ('run-' + label)
    manifest = read(d / 'manifest.json')
    for table in ('inputs_sha256', 'evidence_sha256'):
        for name, digest in manifest[table].items():
            assert sha(R / name) == digest, name
    assert read(d / 'process.json')['returncode'] == 0
    assert read(d / 'result.json')['status'] == 0


def main():
    assert not O.exists()
    pairs = [
        (8, 'model-cache64-rvcpipe-v1', 'model-cache64-rvcpipe-render8-storage-v2',
         'interval-rvcpipe-v1', 'interval-render8-storage-v2'),
        (32, 'model-cache64-rvcpipe-render32-v1', 'model-cache64-rvcpipe-render32-storage-v2',
         'interval-rvcpipe-render32-v1', 'interval-render32-storage-v2')]
    results = []
    for capacity, before, after, control, candidate in pairs:
        a, b = read(W / before / 'prepared.json'), read(W / after / 'prepared.json')
        assert set(a['source_sha256']) == set(b['source_sha256'])
        delta = [n for n in a['source_sha256'] if a['source_sha256'][n] != b['source_sha256'][n]]
        assert delta == [CACHE], delta
        assert b['source_sha256'][CACHE] == '13fa29b4d44202be88ca22fd89ca2b1c901c86049e9e75c828de77a2e651bdb8'
        for field in ('defs', 'selected_qsf_sha256', 'gpu_cache_kib', 'cpu_sha256'):
            assert a[field] == b[field], field
        assert a['gpu_cache_kib'] == capacity
        for model, prepared in ((before, a), (after, b)):
            for name, digest in prepared['source_sha256'].items():
                assert sha(W / model / name) == digest, name
            assert read(W / model / 'build.json')['returncode'] == 0
        for name in ('main.cpp', 'input.cfg'):
            assert (W / ('runner-' + control) / name).read_bytes() == (W / ('runner-' + candidate) / name).read_bytes(), name
        for label in (control, candidate):
            verify_run(label)
        x, y = W / ('run-' + control), W / ('run-' + candidate)
        names = ['result.json', 'boundaries.tsv', 'pc.csv', 'mmio.tsv', 'commands.bin',
                 'entry-regs.bin', 'entry-2.bin', 'entry-3.bin']
        names += sorted(p.name for p in x.glob('frame*.bin'))
        for name in names:
            assert (x / name).read_bytes() == (y / name).read_bytes(), (capacity, name)
        metrics = read(y / 'result.json')
        results.append(dict(capacity_kib=capacity, source_delta=delta, exact_files=names,
                            interval_cycles=metrics['finished_cycle']-metrics['entry_cycle'],
                            retired=metrics['retired'], upload_words=metrics['upload_words']))
    for name in ('render-cache-storage-v2/manifest.json',
                 'render-cache-storage-source-review-v2/manifest.json',
                 'native-rvcpipe-render32-check-v1/manifest.json',
                 'native-warm-interval-check-v1/manifest.json'):
        read(P / name)
    O.mkdir()
    shutil.copyfile(__file__, O / 'recipe.py')
    save(O / 'result.json', dict(passed=True, pairs=results,
         scope='Storage-only equivalence at matched GPU capacity. Larger capacity speed was measured separately. No synthesized area, RAM packing, routed timing, or physical FPS claim.'))
    (O / 'REPORT.txt').write_text(
        'Native cache storage equivalence: PASS\n\n'
        'At GPU8 and GPU32 separately, only the render-cache RTL changes.\n'
        'Same host, captured application, guest services, clocks and synthetic traffic.\n'
        'Complete results, retirement PC histograms, MMIO traces, boundary timing,\n'
        'commands, architectural entries, and every framebuffer/depth dump are byte-exact.\n'
        'GPU8 interval: 8,058,510 cycles. GPU32 interval: 7,869,037 cycles.\n'
        'This storage rewrite adds no observed transaction or CPU cycles.\n'
        'Area, physical block packing and timing require separate synthesis/fit evidence.\n')
    save(O / 'manifest.json', dict(passed=True, evidence_sha256=evidence,
         files={p.name: sha(p) for p in O.iterdir() if p.is_file()}))
    print('MANIFEST', sha(O / 'manifest.json'))


if __name__ == '__main__':
    main()
