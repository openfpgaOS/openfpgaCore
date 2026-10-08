#!/usr/bin/env python3
"""Read-only independent check of completed resident-texture replay evidence."""
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PHASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase4'
OUT = PHASE / 'texmem'
PRIOR = ROOT / 'docs/measurements/os30-fps25-20261005/validation/replays-single-A/phase2-exact-v1-fps/2'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

manifest = json.loads((OUT / 'inputs.json').read_text())
for rel, expected in manifest['sources'].items():
    assert digest(OUT / rel) == expected, rel
for name, entry in manifest['added'].items():
    assert digest(ROOT / entry['origin']) == entry['sha256'], name
    assert digest(OUT / 'frozen' / name) == entry['sha256'], name

mapping = {int(a): b for a, b in manifest['page_map'].items()}
assert len(set(mapping.values())) == len(mapping)
assert all((a | b) & 16383 == 0 for a, b in mapping.items())
assert sorted(mapping.values()) == list(range(0, len(mapping) * 16384, 16384))
assert len(mapping) * 16384 == manifest['pool_bytes'] <= 0x200000
rows = []
for frame in (360, 600, 720):
    stem = f'frame_{frame:05d}'
    source = (PRIOR / (stem + '.vec')).read_bytes()
    expected = bytearray(source)
    pos, packets, changes = 0, 0, []
    while pos < len(source):
        tag = source[pos:pos + 4]
        pos += 4
        if tag == b'MEM1':
            address, count = struct.unpack_from('<II', source, pos)
            pos += 8 + ((count + 3) & ~3)
        elif tag == b'CMDS':
            count, = struct.unpack_from('<I', source, pos)
            pos += 4
            end = pos + count * 4
            while pos < end:
                header, = struct.unpack_from('<I', source, pos)
                opcode, length = header >> 24, header & 0xffffff
                payload = struct.unpack_from(f'<{length}I', source, pos + 4)
                assert opcode in (0x11, 0x4a, 0x50, 0x56, 0x54, 0x4e, 0x42)
                if opcode == 0x4a:
                    assert length == 17 and payload[6] & 128
                    old = payload[3] & 0x3ffffff
                    new = mapping[old & ~16383] + (old & 16383)
                    assert old & 16383 == new & 16383
                    w, mask = payload[4], payload[5] >> 16
                    h = (payload[10] >> 16) + 1 if mask == 65535 else mask + 1
                    size = w * h * 2
                    assert 0 < size and new + size <= 0x200000
                    # Every crossed page remains contiguous, not merely the base.
                    for a in range(old & ~16383, (old + size + 16383) & ~16383, 16384):
                        assert mapping[a] == new + a - old
                    offset = pos + 16
                    struct.pack_into('<I', expected, offset, new)
                    changes.append(dict(offset=offset, before=payload[3], after=new))
                    packets += 1
                pos += (length + 1) * 4
            assert pos == end
        elif tag == b'TLUT':
            pos += 32768
        elif tag == b'PALB':
            pos += 4
        elif tag == b'DUMP':
            pos += 8
        elif tag == b'RAND':
            pos += 12
        else:
            assert tag in (b'FLSH', b'FENC', b'HASH', b'END.')
        assert pos <= len(source)
    assert bytes(expected) == (OUT / 'vectors' / (stem + '.vec')).read_bytes()
    assert changes == manifest['frames'][str(frame)]['changes']
    baseline = (PRIOR / (stem + '.bin')).read_bytes()
    assert len(baseline) == 307212
    checks = []
    for extra in (0, 2):
        reference = None
        for route in ('sdram', 'fast'):
            directory = OUT / f'model-extra{extra}' / route
            result = next(x for x in json.loads((directory / 'results.json').read_text()) if x['frame'] == frame)
            assert (directory / (stem + '.bin')).read_bytes() == baseline
            assert digest(directory / (stem + '.bin')) == result['output_sha256']
            assert digest(directory / (stem + '.log')) == result['log_sha256']
            executable = directory.parent / 'obj/Vtb_gpu'
            assert digest(executable) == result['simulator_sha256']
            stats = result['stats']
            assert stats['cram_errors'] == 0
            if route == 'sdram':
                reference = stats
                old = next(x for x in json.loads((ROOT / 'docs/measurements/os30-fps25-20261005/phase3/arbiter/threshold8/results.json').read_text()) if x['frame'] == frame)
                assert all(stats[k] == v for k, v in old['stats'].items())
            else:
                assert stats['fills'] == reference['fills']
                checks.append(dict(refresh_extra=extra, baseline_cycles=reference['cycles'], fast_cycles=stats['cycles'], fills=stats['fills']))
    rows.append(dict(frame=frame, tri_state_packets=packets, only_texture_address_words_changed=True, full_output_bytes=len(baseline), runs=checks))

concurrency = json.loads((OUT / 'extended/checks/results.json').read_text())
for entry in concurrency:
    binary = ROOT / entry['command'][2]
    assert digest(binary) == entry['output_sha256']
    assert binary.read_bytes() == (PRIOR / 'frame_00360.bin').read_bytes()
assert concurrency[1]['stats']['uploads_while_fill'] > 0
assert concurrency[1]['stats']['fills_while_upload'] > 0
assert concurrency[1]['stats']['readback_words'] == 256

result = dict(passed=True, source_manifest_sha256=digest(OUT / 'inputs.json'), report_sha256=digest(OUT / 'REPORT.json'),
              audit_script_sha256=digest(Path(__file__)), sources_hash_exact=True, bijective_page_mapping=True,
              cache_index_and_offset_bits_preserved=14, mapped_pool_bytes=len(mapping) * 16384,
              model_tested_address_limit=0x200000, rows=rows, concurrent_disjoint_upload_output_exact=True,
              limitations=['Resident page preloads exclude upload and CPU costs.',
                           'Only first 2 MiB of die0 tested; no die1 or 8 MiB boundary proof.',
                           'Synthetic CPU traffic is acceptance-relative, not application execution.',
                           'Application allocator/polling and producer overlap need separate combined-candidate qualification.'])
(PHASE / 'texmem-remap-review.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'passed': True, 'frames': len(rows), 'replay_runs': 12, 'pool_bytes': len(mapping) * 16384}))
