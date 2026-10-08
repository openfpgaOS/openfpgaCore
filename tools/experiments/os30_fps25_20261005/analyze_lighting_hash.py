#!/usr/bin/env python3
"""Replay two lighting-cache indexes against existing immutable observations."""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / 'docs/measurements/os30-fps25-20261005'
OBS = CAMPAIGN / 'validation/capture-attributes-observed-v2-normal'
OUT = CAMPAIGN / 'phase2/lighting-hash-analysis'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    observation_proof = CAMPAIGN / 'validation/attribute-observer-results.json'
    proof = json.loads(observation_proof.read_text())
    assert proof['passed_noninterference']
    assert sha(OBS / 'attributes.csv') == proof['observation_sha256']
    assert sha(OBS / 'sm64.elf') == proof['baseline_elf_sha256']
    baseline = CAMPAIGN / 'phase2/baseline/gfx_pc.c'
    old_source = ROOT / 'docs/measurements/os30-simple-20261005/simple-v4/gfx_pc.c'
    def lighting_body(path):
        src = path.read_text()
        a = src.index('            const uint32_t normal=gfx_lighting_normal_key(v->cn);')
        b = src.index('            if (rsp.geometry_mode & G_TEXTURE_GEN)', a)
        return src[a:b]
    assert lighting_body(baseline) == lighting_body(old_source)

    elf = (OBS / 'sm64.elf').read_bytes()
    eh = struct.unpack_from('<16sHHIIIIIHHHHHH', elf)
    assert eh[0][:6] == b'\x7fELF\x01\x01'
    sections = [struct.unpack_from('<10I', elf, eh[6] + i * eh[11])
                for i in range(eh[12])]
    strings = sections[eh[13]]
    names = elf[strings[4]:strings[4]+strings[5]]
    named = {names[s[0]:].split(b'\0', 1)[0].decode(): s for s in sections}
    text = named['.text']
    symbols = json.loads((OBS / 'attributes-symbols.json').read_text())
    assert not text[2] & 1
    assert text[3] == symbols['low']
    assert text[3] + text[5] == symbols['high'] == named['.eh_frame'][3]
    def normal(address):
        assert text[3] <= address and address + 3 <= text[3] + text[5]
        off = text[4] + address - text[3]
        return int.from_bytes(elf[off:off+3], 'little')

    with (OBS / 'attributes.csv').open() as f:
        rows = list(csv.DictReader(f))
    first = next(i for i, r in enumerate(rows)
                 if 360 <= int(r['frame']) <= 720 and int(r['mode']) & 0x20000)
    first_epoch = int(rows[first]['epoch'])
    start = next(i for i, r in enumerate(rows) if int(r['epoch']) == first_epoch)
    caches = [[None] * 256 for _ in range(2)]
    stats = [Counter(), Counter()]
    missed = [Counter(), Counter()]
    seen = set()
    epoch = None
    states = {}
    for r in rows[start:]:
        if int(r['frame']) > 720:
            break
        if not int(r['mode']) & 0x20000:
            continue
        assert int(r['eligible']), 'Cannot recover a mutable vertex normal from ELF'
        ep = int(r['epoch'])
        if ep != epoch:
            assert epoch is None or ep > epoch
            seen.clear()
            epoch = ep
        if ep in states:
            assert states[ep] == (r['lights'], r['light_key'])
        states[ep] = (r['lights'], r['light_key'])
        attributes = bytes.fromhex(r['attributes'])
        count = int(r['count'])
        assert len(attributes) == count * 11
        measured = 360 <= int(r['frame']) <= 720
        for i in range(count):
            # GBI_FLOATS: ob[3] at0, flag at12, tc[2] at14, cn[4] at18;
            # the Vtx union rounds this22-byte structure to24 bytes.
            n = normal(int(r['address']) + 24 * i + 18)
            rgb = attributes[11*i+8:11*i+11]
            indices = ((n ^ (n >> 11)) & 255,
                       ((n * 0x9e3779b1) & 0xffffffff) >> 24)
            for j, index in enumerate(indices):
                previous = caches[j][index]
                hit = previous is not None and previous[:2] == (ep, n)
                if hit:
                    assert previous[2] == rgb, 'A cache hit must preserve observed RGB'
                else:
                    caches[j][index] = (ep, n, rgb)
                if measured:
                    st = stats[j]
                    st['lookups'] += 1
                    st['hits' if hit else 'misses'] += 1
                    if not hit:
                        st['slot_epoch_mismatch' if previous is None or previous[0] != ep
                           else 'slot_same_epoch_collision'] += 1
                        st['repeat_key_conflict_miss' if n in seen
                           else 'first_normal_in_epoch'] += 1
                        missed[j][n] += 1
            seen.add(n)
    assert stats[0]['lookups'] == stats[1]['lookups'] == 1255460
    saved = stats[0]['misses'] - stats[1]['misses']
    result = {
        'analysis_passed': True,
        'decision': 'Do not prepare or build a hash candidate: the recoverable collision cost is small and the multiply applies to every lit vertex. No application or hardware changes made.',
        'scope': 'Read-only replay of the complete observed lit-vertex stream for gameplay frames360..720. Every lit source in this window is immutable and recovered from the frozen ELF. This is cache-hit counting, not CPU timing or a rendered candidate.',
        'old_hash': '(normal ^ (normal >> 11)) & 255u',
        'evaluated_hash': '(normal * 0x9e3779b1u) >> 24',
        'old_hash_bit_loss': 'Normal byte0 contributes all8 bits; byte1 contributes bits3..7; byte2 contributes bits0..2. Low3 Y bits and high5 Z bits never affect the old index.',
        'baseline': dict(stats[0]), 'multiplicative': dict(stats[1]),
        'misses_avoided': saved,
        'saved_percent_of_baseline_misses': 100 * saved / stats[0]['misses'],
        'saved_percent_of_lit_lookups': 100 * saved / stats[0]['lookups'],
        'unavoidable_first_normal_percent_of_baseline_misses':
            100 * stats[0]['first_normal_in_epoch'] / stats[0]['misses'],
        'warm_start': {'first_gameplay_lit_epoch': first_epoch,
                       'preceding_rows_replayed_in_same_epoch': first - start,
                       'explanation': 'Begin at the first observation of the initial gameplay epoch so already-seen normals within that epoch are retained. Older epochs cannot hit; no epoch wrap occurs.'},
        'semantics_checks': {'all_lit_sources_immutable': True,
                             'both_hash_hit_colors_equal_observed_rgb': True,
                             'lighting_math_and_memo_body_match_current_baseline': True,
                             'same_epoch_has_identical_light_state': True,
                             'single_variant_evaluated_no_hash_sweep': True},
        'largest_reductions_by_normal': [dict(normal=f'{n:06x}', misses_avoided=v)
                                        for n, v in (missed[0]-missed[1]).most_common(12)],
        'limits': ['No new guest execution, generated target code or actual-A timing was measured.',
                   'Different hashes alter misses but preserve full normal+epoch equality checks. Cost of the new multiply could offset fewer lighting evaluations.',
                   'First-normal-in-epoch misses cannot be fixed by changing a direct-mapped index; total miss reduction must not be mistaken for whole-frame speedup.'],
        'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in
                            (observation_proof, OBS/'attributes.csv', OBS/'sm64.elf',
                             OBS/'attributes-symbols.json', baseline, old_source, Path(__file__))}
    }
    OUT.mkdir(exist_ok=False)
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('baseline', 'multiplicative', 'misses_avoided',
        'saved_percent_of_baseline_misses', 'saved_percent_of_lit_lookups', 'warm_start')}, indent=2))


if __name__ == '__main__':
    main()
