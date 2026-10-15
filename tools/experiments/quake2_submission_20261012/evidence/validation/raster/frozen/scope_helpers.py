#!/usr/bin/env python3
"""Read-only instrumentation workflow: exact frame-tagged command scope.

The original output-validation gpuvec includes the next frame's eager color
clear. Remove only that final command and require all remaining command words
to exactly match the app's independently frame-tagged command trace. No RTL
or app code changes. Full current-frame RGB565/depth must remain byte-exact.
"""
from pathlib import Path
import collections, concurrent.futures, hashlib, json, re, struct, subprocess

ROOT = Path(__file__).resolve().parent
OLD = Path('/tmp/quake2-gpu-20261011/rtl-tests')
EXE = OLD / 'timing-replay/obj/Vtb_gpu'
FRAMES = [20, 80, 180, 300]

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def put(p, d):
    p.write_text(json.dumps(d, indent=2) + '\n')

def trace():
    b = (OLD / 'captures/final-v6/commands.bin').read_bytes()
    pos = 0
    result = collections.defaultdict(list)
    while pos < len(b):
        frame, header = struct.unpack_from('<QI', b, pos)
        n = (header & 8191) + 1
        if frame in FRAMES:
            result[frame].append(list(struct.unpack_from('<' + 'I'*n, b, pos+8)))
        pos += 8 + 4*n
    assert pos == len(b)
    return result

def filter_vector(source, dest, expected):
    b = source.read_bytes()
    pos = 0
    records = []
    commands = []
    while pos < len(b):
        start = pos
        tag = b[pos:pos+4]
        pos += 4
        if tag in (b'MEM1', b'TEX1'):
            address, n = struct.unpack_from('<II', b, pos)
            pos += 8 + ((n+3) & ~3)
        elif tag == b'CMDS':
            n, = struct.unpack_from('<I', b, pos)
            pos += 4
            words = list(struct.unpack_from('<'+'I'*n, b, pos))
            pos += 4*n
            i = 0
            packet = []
            while i < n:
                size = (words[i] & 8191) + 1
                command = words[i:i+size]
                assert len(command) == size
                packet.append(command)
                commands.append(command)
                i += size
            records.append((tag, packet))
            continue
        elif tag == b'TLUT':
            pos += 32768
        elif tag == b'PALB':
            pos += 4
        elif tag in (b'MMIO', b'DUMP'):
            pos += 8
        elif tag == b'RAND':
            pos += 12
        elif tag in (b'FLSH', b'FENC', b'HASH', b'END.'):
            pass
        else:
            raise AssertionError((pos, tag))
        records.append((tag, b[start:pos]))
    assert pos == len(b)
    assert commands[:-1] == expected
    assert commands[-1][0] == 0x11000003
    assert commands[-2][0] >> 24 == 2
    removed = commands[-1]
    last = max(i for i, (tag, value) in enumerate(records) if tag == b'CMDS')
    assert records[last][1][-1] == removed
    records[last][1].pop()
    parts = []
    for tag, value in records:
        if tag != b'CMDS':
            parts.append(value)
        elif value:
            words = [word for command in value for word in command]
            parts.append(tag + struct.pack('<I', len(words)) + struct.pack('<'+'I'*len(words), *words))
    dest.write_bytes(b''.join(parts))
    return removed

def run(frame, expected):
    old = OLD / 'replays/final-v6-timing-worker' / f'frame_{frame:05}'
    stem = ROOT / f'frame_{frame:05}'
    removed = filter_vector(old.with_suffix('.vec'), stem.with_suffix('.vec'), expected)
    command = [str(EXE), '+fast_tex=1', '+tagged_tex=1', '+memory_traffic=0',
               str(stem.with_suffix('.vec')), str(stem.with_suffix('.bin'))]
    with stem.with_suffix('.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=360)
    result = stem.with_suffix('.bin').read_bytes()
    reference = old.with_suffix('.bin').read_bytes()
    assert len(result) == len(reference) == 307212
    assert result[:153600] == reference[:153600]
    assert result[153612:] == reference[153612:]
    log = re.sub(r'rtl cycles=\d+\n?', '', stem.with_suffix('.log').read_text())
    tables = {name: {int(k): int(v) for k, v in re.findall(r'CRITICAL_'+name+r' (\d+) (\d+)', log)}
              for name in ['MAIN', 'DRV', 'PSS', 'FRAG', 'DRAIN', 'SPAN']}
    result_line = re.search(r'^RESULT (.*)$', log, re.M).group(1)
    stats = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', result_line)}
    assert sum(tables['MAIN'].values()) == stats['cycles']
    assert sum(tables['FRAG'].values()) == tables['MAIN'][6]
    assert sum(tables['DRV'].values()) == tables['MAIN'][15]
    row = dict(frame=frame, command=command, executable_sha256=sha(EXE),
               source_vector=str(old.with_suffix('.vec')), source_sha256=sha(old.with_suffix('.vec')),
               filtered_vector_sha256=sha(stem.with_suffix('.vec')),
               log_sha256=sha(stem.with_suffix('.log')),
               frame_tagged_commands_exact=True, removed_next_frame_clear=removed,
               color_depth_exact=True, result=stats, critical=tables)
    put(stem.with_suffix('.json'), row)
    print(frame, stats, flush=True)
    return row

if __name__ == '__main__':
    expected = trace()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda f: run(f, expected[f]), FRAMES))
    put(ROOT / 'frame-scope-results.json', dict(passed=True, frames=rows,
        trace_path=str(OLD/'captures/final-v6/commands.bin'),
        trace_sha256=sha(OLD/'captures/final-v6/commands.bin'),
        rationale=__doc__))
