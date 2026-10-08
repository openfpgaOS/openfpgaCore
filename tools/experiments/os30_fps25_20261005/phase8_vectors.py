#!/usr/bin/env python3
"""Independently compare GPU work, retaining texture flushes and MMIO order.

Only CMDS publication boundaries are normalized. MEM1 snapshots contain CPU
memory changes and executable relocations; those require separate physical
replay. TEX1 bytes are reported separately for the existing bounded padding
check. This check does not by itself qualify rendering or performance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
BASE = CAMP / 'phase7/combined-v1/capture-normal'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse(data):
    pos, tokens, batches, textures, palettes = 0, [], [], [], []
    memory_records = 0
    while pos < len(data):
        start = pos
        tag = data[pos:pos+4]
        pos += 4
        if tag in (b'MEM1', b'TEX1'):
            address, count = struct.unpack_from('<II', data, pos)
            pos += 8
            body = data[pos:pos+count]
            assert len(body) == count
            pos += (count+3) & ~3
            if tag == b'MEM1':
                memory_records += 1
            else:
                textures.append(dict(address=address, bytes=count, sha256=sha(body)))
                tokens.append(data[start:start+12])
        elif tag == b'CMDS':
            count, = struct.unpack_from('<I', data, pos)
            pos += 4
            limit = pos + count*4
            batches.append(count)
            while pos < limit:
                header, = struct.unpack_from('<I', data, pos)
                end = pos + 4*(1+(header & 8191))
                assert end <= limit, 'Publication splits an incomplete command'
                tokens.append(b'CMD1' + data[pos:end])
                pos = end
            assert pos == limit
        elif tag in (b'MMIO', b'DUMP'):
            pos += 8
            tokens.append(data[start:pos])
        elif tag == b'RAND':
            pos += 12
            tokens.append(data[start:pos])
        elif tag == b'PALB':
            palettes.append(struct.unpack_from('<I', data, pos)[0])
            pos += 4
            tokens.append(tag)  # Base relocation reported separately.
        elif tag == b'TLUT':
            pos += 32768
            tokens.append(data[start:pos])
        else:
            assert tag in (b'FLSH', b'FENC', b'HASH', b'END.'), tag
            tokens.append(tag)  # FLSH is a texture invalidation, never omitted.
            if tag == b'END.':
                assert pos == len(data)
        assert pos <= len(data)
    assert tokens and tokens[-1] == b'END.'
    return dict(tokens=tokens, batches=batches, textures=textures,
                palettes=palettes, memory_records=memory_records)


def digest(tokens):
    h = hashlib.sha256()
    for token in tokens:
        h.update(struct.pack('<I', len(token)))
        h.update(token)
    return h.hexdigest()


def stats(parsed):
    batches = parsed['batches']
    return dict(publications=len(batches), command_words=sum(batches),
                smallest_words=min(batches), largest_words=max(batches),
                mean_words=sum(batches)/len(batches),
                texture_flushes=parsed['tokens'].count(b'FLSH'),
                ordered_work_sha256=digest(parsed['tokens']),
                texture_preloads=parsed['textures'], palette_bases=parsed['palettes'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate')
    args = p.parse_args()
    directory = CAMP / 'phase8' / args.candidate
    assert directory.parent == CAMP / 'phase8' and directory.is_dir()
    current = directory / 'capture-normal'
    paths = sorted((BASE/'gpuvec').glob('*.vec'))
    assert len(paths) == 8
    rows = []
    for path in paths:
        target = current/'gpuvec'/path.name
        a, b = parse(path.read_bytes()), parse(target.read_bytes())
        exact = a['tokens'] == b['tokens']
        mismatch = next((i for i, (x,y) in enumerate(zip(a['tokens'], b['tokens'])) if x != y), None)
        rows.append(dict(frame=int(path.stem.split('_')[1]), ordered_work_exact=exact,
                         first_mismatch_index=mismatch, baseline=stats(a), candidate=stats(b),
                         baseline_sha256=sha(path.read_bytes()), candidate_sha256=sha(target.read_bytes())))
    result = dict(passed=all(r['ordered_work_exact'] for r in rows), vectors=rows,
                  script_sha256=sha(Path(__file__).read_bytes()),
                  scope='Command contents/order, texture invalidations, and MMIO ordering; only publication boundaries normalized. TEX1 payload and SDRAM ownership require separate checks.')
    with (directory/'independent-vectors.json').open('x') as f:
        json.dump(result, f, indent=2)
        f.write('\n')
    print(json.dumps(dict(passed=result['passed'], frames=len(rows),
                         publications=[(r['frame'],r['baseline']['publications'],r['candidate']['publications']) for r in rows])))
    assert result['passed'], 'GPU-visible ordering differs; inspect before qualification'


if __name__ == '__main__':
    main()
