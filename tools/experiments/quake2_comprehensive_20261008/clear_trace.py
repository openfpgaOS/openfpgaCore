#!/usr/bin/env python3
"""Host-only all-command observer and removed-only-clear semantic comparison."""
import argparse
import csv
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import clear_reuse as recipe

R, P = recipe.R, recipe.P
S = R / 'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
O = P / 'command-trace-v1'
def put(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def prepare():
    original = json.loads((S / 'prepared.json').read_text())
    assert sha(S / 'qsim/qsim') == original['qsim_sha256']
    assert not O.exists()
    O.mkdir()
    for rel, expected in original['inputs'].items():
        if not rel.startswith('qsim/'):
            continue
        assert sha(S / rel) == expected, rel
        target = O / rel
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(S / rel, target)
    path = O / 'qsim/env.c'
    old = path.read_text()
    anchor = '''    uint32_t n = hdr & 0x1FFF, op = hdr >> 24;
    uint8_t b[4];'''
    assert old.count(anchor) == 1
    new = old.replace(anchor, '''    uint32_t n = hdr & 0x1FFF, op = hdr >> 24;
    /* Passive host observer: no guest memory, time, schedule or state writes. */
    static FILE *command_trace;
    static int trace_checked;
    if (!trace_checked) {
        const char *path = getenv("QSIM_COMMAND_TRACE");
        if (path) {
            command_trace = fopen(path, "wb");
            if (!command_trace) { perror("command trace"); exit(2); }
        }
        trace_checked = 1;
    }
    if (command_trace) {
        uint64_t frame = E->flips;
        if (fwrite(&frame, 8, 1, command_trace) != 1 ||
            fwrite(&hdr, 4, 1, command_trace) != 1 ||
            fwrite(p, 4, n, command_trace) != n) {
            perror("command trace write"); exit(2);
        }
    }
    uint8_t b[4];''', 1)
    path.write_text(new)
    (O / 'source.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile='a/env.c', tofile='b/env.c')))
    with (O / 'build.log').open('w') as log:
        subprocess.run(['make', '-j2'], cwd=O / 'qsim', stdout=log, stderr=subprocess.STDOUT, check=True)
    shutil.copy2(__file__, O / 'recipe.py')
    put(O / 'prepared.json', dict(parent_prepared_sha256=sha(S / 'prepared.json'),
        parent_qsim_sha256=sha(S / 'qsim/qsim'), observer_qsim_sha256=sha(O / 'qsim/qsim'),
        changed_sources=['env.c'], sources={p.name:sha(p) for p in (O / 'qsim').iterdir() if p.suffix in ('.c', '.h')},
        scope='Read-only callback host FILE output, 64-bit frame then 32-bit header and payload. No guest/model mutation. Statistics equivalence is required separately for each run.'))
    print('observer ready', sha(O / 'qsim/qsim'))

def run(label):
    assert re.fullmatch(r'[A-Za-z0-9_-]+', label)
    prep = json.loads((O / 'prepared.json').read_text())
    assert sha(O / 'qsim/qsim') == prep['observer_qsim_sha256']
    for name, expected in prep['sources'].items():
        assert sha(O / 'qsim' / name) == expected
    old_report = json.loads((S / (label + '-result.json')).read_text())
    assert old_report['passed'] and sha(S / (label + '.elf')) == old_report['elf_sha256']
    dest = O / label
    assert not dest.exists()
    env = {k:v for k,v in os.environ.items() if not k.startswith(('SM64_', 'QSIM_'))}
    env['QSIM_COMMAND_TRACE'] = str(O / (label + '.commands'))
    command = [str(O / 'qsim/qsim'), str(S / (label + '.elf')), '--out', str(dest),
               '--app', str(S / 'quake2.app'), '--data-dir', str(S / 'data'), '--profile',
               '--frames', '362', '--max-insns', '4e9', '--gpu-sched', 'eager', '--mem',
               'flat', '--no-normalize-build-stamp', '--label', label]
    with (O / (label + '.log')).open('w') as log:
        subprocess.run(command, cwd=S, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    assert (dest / 'frames.csv').read_bytes() == (S / label / 'frames.csv').read_bytes()
    before = json.loads((S / label / 'summary.json').read_text())
    after = json.loads((dest / 'summary.json').read_text())
    for value in (before, after):
        del value['wall_seconds'], value['guest_mips']
        value['env'].pop('gpuvec_captured')
    assert before == after, [k for k in before if before[k] != after.get(k)]
    put(O / (label + '-result.json'), dict(passed=True, command=command,
        elf_sha256=old_report['elf_sha256'], prepared_sha256=sha(O / 'prepared.json'),
        trace_sha256=sha(O / (label + '.commands')), trace_bytes=(O / (label + '.commands')).stat().st_size,
        frames_csv_sha256=sha(dest / 'frames.csv'), all_frame_columns_identical=True,
        all_guest_summary_fields_identical=True,
        excluded_summary_fields=['wall_seconds', 'guest_mips', 'env.gpuvec_captured']))
    print(label, 'observer matches every CSV column and guest summary field')

def trace(path):
    data = path.read_bytes()
    pos = 0
    result = []
    while pos < len(data):
        assert pos + 12 <= len(data)
        frame, header = struct.unpack_from('<QI', data, pos)
        n = header & 8191
        assert pos + 12 + 4*n <= len(data)
        command = struct.unpack_from('<' + 'I'*(n+1), data, pos+8)
        result.append((frame, command))
        pos += 12 + 4*n
    assert pos == len(data)
    return result

def clear_range(command):
    assert command[0] == 0x11000003 and len(command) == 4
    start, packed, style = command[1:]
    width, height, stride = packed >> 16, packed & 65535, style >> 16
    assert width and height and stride >= width
    start &= 0x3ffffff
    return start, start+(height-1)*stride+width

def safe_duplicate(commands, index):
    command = commands[index][1]
    if command[0] != 0x11000003:
        return None
    start, end = clear_range(command)
    # Q2's fixed RGB565 framebuffer pool only; no Z or arbitrary-memory elision.
    assert start in (0, 0x100000, 0x200000)
    assert command[2] == (640 << 16) | 240 and command[3] >> 16 == 640
    between = []
    for earlier in range(index-1, -1, -1):
        other = commands[earlier][1]
        if other == command:
            return dict(command_index=index, previous_identical_index=earlier,
                        frame=commands[index][0], clear=list(command),
                        intervening_commands=between[::-1], bytes_elided=640*240)
        op = other[0] >> 24
        if op == 0x11:
            a, b = clear_range(other)
            if a < end and start < b:
                return None
        elif op not in (0x02, 0x23, 0x4a):
            return None
        between.append(list(other))
    return None

def prove(before, after):
    removed = []
    j = 0
    for i, entry in enumerate(before):
        if j < len(after) and entry == after[j]:
            j += 1
        else:
            proof = safe_duplicate(before, i)
            assert proof is not None, ('not a proven duplicate clear', i, entry, after[j] if j < len(after) else None)
            removed.append(proof)
    assert j == len(after), ('added or reordered commands', j, len(after))
    return removed

def compare(candidate, baseline):
    dest = O / (candidate + '-vs-' + baseline)
    assert not dest.exists()
    commands = {}
    for label in (baseline, candidate):
        result = json.loads((O / (label + '-result.json')).read_text())
        assert result['passed'] and sha(O / (label + '.commands')) == result['trace_sha256']
        commands[label] = trace(O / (label + '.commands'))
        # The observer includes any commands after the last presented frame.
        # Bind all presented-frame counts/hashes to the immutable normal run.
        grouped = {}
        for frame, command in commands[label]:
            grouped.setdefault(frame, bytearray()).extend(struct.pack('<'+'I'*len(command), *command))
        rows = list(csv.DictReader((S / label / 'frames.csv').open()))
        assert len(rows) == 362
        for row in rows:
            data = grouped.get(int(row['frame']), b'')
            assert len(data)//4 == int(row['gpu_cmd_words'])
            h = 0xcbf29ce484222325
            for b in data:
                h = ((h ^ b)*0x100000001b3) & 0xffffffffffffffff
            assert f'{h:016x}' == row['hash_cmd']
    removed = prove(commands[baseline], commands[candidate])
    assert removed
    dest.mkdir()
    put(dest / 'result.json', dict(passed=True, baseline=baseline, candidate=candidate,
        baseline_commands=len(commands[baseline]), candidate_commands=len(commands[candidate]),
        removed_clear_count=len(removed), removed_bytes=sum(p['bytes_elided'] for p in removed),
        normal_frame_count_hash_bindings=362, only_identical_untouched_framebuffer_clears_removed=True,
        all_other_commands_and_frame_assignment_exact=True, depth_clears_preserved=True,
        proof=removed, limits=['CPU color writes are outside the command stream; source lifecycle invariant and differential tests cover those writes.',
            'Functional qsim does not rasterize Q2 geometry. Four selected actual RTL samples remain required.']))
    paths = [O / 'prepared.json', O / 'source.patch', O / 'recipe.py', Path(__file__), dest / 'result.json']
    for label in (baseline, candidate):
        paths += [O / (label + '-result.json'), O / (label + '.commands'), O / label / 'frames.csv', O / label / 'summary.json']
    put(dest / 'manifest.json', dict(passed=True, evidence_sha256={str(p.relative_to(R)):sha(p) for p in paths}))
    print('PROVED', len(removed), 'duplicate color clears removed, all other commands exact')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'compare'])
    parser.add_argument('--label')
    parser.add_argument('--baseline', default='pass2_trio')
    args = parser.parse_args()
    if args.action == 'prepare': prepare()
    elif args.action == 'run': run(args.label)
    else: compare(args.label, args.baseline)
