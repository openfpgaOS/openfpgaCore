#!/usr/bin/env python3
"""Prepare the fixed private candidate; integrate only explicitly, into this repo."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
NORMAL = P / 'normal-build-control-cones-v1'
SOFTWARE = P / 'renderer-pgo-delivery-v7'
MAP = P / 'control-cones-compose-map-v1'
FIT = P / 'control-cones-fit-v1'
OUT = P / 'final-candidate-delivery-v2'
JOB = FIT / 'fit-seed2/job'
TARGET = Path('src/fpga/targets/pocket')
ELF_SHA = 'fff132a5ee34d625a279fbf612e4abe54faa122fe519b386130f5ee230dfe63b'
FIXED = {
    NORMAL / 'manifest.json': 'cca9d9717e9cb54268fc2796ea473018713ed852cac9b7aa7306eca9f9070cfa',
    SOFTWARE / 'manifest.json': 'd923e397b89aab6f94e00405be78cc017dccd9b18a7a615854f15c3782c7e218',
    MAP / 'manifest.json': '8bdf2e43925b0f4543e4f41671009f3028edecfeade7c7bd510df8524c5e7ae4',
    FIT / 'prepared.json': '8accf008f8a43e6dbf7b0d6f6c7939fdd2864a63d09335eedb9a2e75ff804549',
}


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def safe(base, name):
    """Reject traversal and any symlink, including existing parent components."""
    name = Path(name)
    require(not name.is_absolute() and name.parts and
            not any(x in ('.', '..', '.git') for x in name.parts), 'Unsafe relative path')
    path = base
    for part in name.parts:
        path = path / part
        if os.path.lexists(path):
            mode = path.lstat().st_mode
            require(not stat.S_ISLNK(mode), 'Symlink rejected: ' + str(path))
            require(stat.S_ISDIR(mode) if path != base / name else
                    (stat.S_ISREG(mode) or stat.S_ISDIR(mode)), 'Special path rejected: ' + str(path))
    return path


def regular(path):
    safe(ROOT, path.relative_to(ROOT))
    require(path.is_file() and stat.S_ISREG(path.lstat().st_mode), 'Missing regular file: ' + str(path))
    return path


def sha(path):
    return hashlib.sha256(regular(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(regular(path).read_text())


def state(path):
    safe(ROOT, path.relative_to(ROOT))
    if not os.path.lexists(path):
        return None
    regular(path)
    return {'sha256': sha(path), 'mode': stat.S_IMODE(path.stat().st_mode)}


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mkdir(path):
    safe(ROOT, path.relative_to(ROOT))
    missing = []
    cur = path
    while not cur.exists():
        missing.append(cur)
        cur = cur.parent
    for cur in reversed(missing):
        cur.mkdir(mode=0o755)
        fsync_dir(cur.parent)


def atomic(path, data, mode=0o644):
    safe(ROOT, path.relative_to(ROOT))
    mkdir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        safe(ROOT, path.relative_to(ROOT))
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def put(path, value):
    require(not os.path.lexists(path), 'Preserve existing artifact: ' + str(path))
    atomic(path, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())


def manifest(path, plain=False):
    d = read(path)
    tables = [(path.parent, d)] if plain else [(path.parent, d.get('files', {})),
                                             (ROOT, d.get('evidence_sha256', {}))]
    for base, rows in tables:
        for name, digest in rows.items():
            require(sha(safe(base, name)) == digest, 'Artifact drift: ' + name)
    return d


def qualification():
    for path, digest in FIXED.items():
        require(sha(path) == digest, 'Selected input changed: ' + str(path))
    manifest(NORMAL / 'manifest.json', plain=True)
    manifest(SOFTWARE / 'manifest.json')
    manifest(MAP / 'manifest.json')
    fit_manifest = manifest(FIT / 'manifest.json')
    result, complete, prep = read(FIT / 'result.json'), read(FIT / 'fit-seed2/complete.json'), read(FIT / 'prepared.json')
    for path in [FIT / 'result.json', FIT / 'prepared.json', FIT / 'fit-seed2/complete.json']:
        require(fit_manifest['evidence_sha256'].get(str(path.relative_to(ROOT))) == sha(path), 'Unbound fit result')
    require(result['comparative_physical_gate_pass'] and complete['valid'] and complete['state'] == 'complete', 'Fit unqualified')
    adapter = ROOT / 'tools/experiments/os30_fps25_20261005/phase10_control_cones_fit.py'
    require(sha(adapter) == prep['recipe_sha256'] == sha(FIT / 'fit-seed2/recipe.py') and
            fit_manifest['evidence_sha256'].get(str(adapter.relative_to(ROOT))) == sha(adapter), 'Fit adapter changed')
    require(complete['directory'] == str(JOB / TARGET) and
            prep['component_manifest_sha256'] == sha(MAP / 'manifest.json'), 'Wrong fitted job/component')
    run = read(FIT / 'fit-seed2/prepared.json')
    require(run['prepared_sha256'] == sha(FIT / 'prepared.json') and
            run['review_sha256'] == sha(FIT / 'independent-review.json'), 'Execution selection drift')
    require(result['seed'] == prep['seed'] == complete['seed'] == 2 and result['full_fit_count'] == 1, 'Wrong fit selection')
    require(result['configured_mhz'] == 100 and not result['seed_sweep'], 'Changed clock or seed scope')
    require(complete['returncodes'] == result['phase_returncodes'], 'Phase result mismatch')
    require(all(complete['returncodes'].get(k) == 0 for k in ['fit', 'asm', 'sta']) and
            complete['returncodes'].get('corners') in [0, 3], 'Incomplete physical phases')
    rows = complete['timing']
    keys = {(m, t, c) for m in ['slow', 'fast'] for t in ['0', '85']
            for c in ['setup', 'hold', 'recovery', 'removal', 'pulse_width']}
    require(len(rows) == 20 and {(x['model'], x['temperature'], x['check']) for x in rows} == keys, 'Incomplete 20-corner/check rows')
    require(all(math.isfinite(float(x['slack'])) for x in rows), 'Non-finite timing')
    require(all(float(x['slack']) > 0 for x in rows if x['check'] != 'setup'), 'Nonpositive non-setup timing')
    require(all(result['worst_timing'][c] == min(float(x['slack']) for x in rows if x['check'] == c)
                for c in ['setup', 'hold', 'recovery', 'removal', 'pulse_width']), 'Timing summary mismatch')
    freq = result['slow_main_clock_fmax_mhz']
    require(set(freq) == {'0', '85'} and all(math.isfinite(v) and v > 90 for v in freq.values()), 'Both slow corners must exceed 90 MHz')
    resources = result['resources']
    require(all(isinstance(v, int) and v > 0 for v in resources.values()), 'Incomplete resources')
    require(resources['m10ks'] <= 308 and resources['labs'] <= 1848 and resources['dsps'] <= 66 and
            resources['needed_alms'] <= 18480 and resources['placed_alms'] <= 18480, 'Physical capacity exceeded')
    expected = read(MAP / 'prepared.json')
    require(read(NORMAL / 'prepared.json')['selected_prepared_sha256'] == sha(MAP / 'prepared.json'), 'Normal selection drift')
    binding = read(NORMAL / 'checks/selected-binding.json')
    require(binding['mapped_qsf_sha256'] == sha(JOB / TARGET / 'ap_core.qsf') == prep['qsf_sha256'], 'Fit QSF differs from normal closure')
    for name, digest in prep['source_input_sha256'].items():
        require(sha(safe(JOB, name)) == digest, 'Fitted source drift: ' + name)
    for name, digest in expected['source_sha256'].items():
        # Frozen master QSF is expanded by the reviewed map preparation.
        if name != str(TARGET / 'ap_core.qsf'):
            require(sha(safe(JOB, name)) == digest, 'Selected source differs: ' + name)
    raw = JOB / TARGET / 'output_files/ap_core.rbf'
    require(sha(raw) == complete['hashes']['rbf'] == fit_manifest['evidence_sha256'][str(raw.relative_to(ROOT))], 'Raw assembler provenance mismatch')
    require(raw.stat().st_size > 0, 'Empty bitstream')
    return result, raw


def preimages():
    guards = read(NORMAL / 'production-before.json')
    proposed = read(NORMAL / 'prepared.json')['proposed_sha256']
    require(len(proposed) == 36, 'Unexpected hardware payload count')
    for name, digest in guards.items():
        require(sha(safe(ROOT, name)) == digest, 'Production preimage changed: ' + name)
    for name in proposed:
        target = safe(ROOT, name)
        require(name in guards or not os.path.lexists(target), 'New target already exists: ' + name)
    return guards, proposed


def prepare():
    result, raw = qualification()  # All gates precede output creation.
    guards, proposed = preimages()
    require(not os.path.lexists(OUT), 'Preserve existing private delivery')
    mkdir(OUT)
    entries = []
    for name, digest in proposed.items():
        source, dest = safe(NORMAL / 'proposed', name), safe(OUT / 'hardware', name)
        require(sha(source) == digest, 'Proposal drift')
        before = state(safe(ROOT, name))
        mode = before['mode'] if before else stat.S_IMODE(source.stat().st_mode)
        atomic(dest, source.read_bytes(), mode)
        entries.append(dict(path=name, before=before, after=state(dest)))
    atomic(OUT / 'raw/ap_core.rbf', raw.read_bytes())
    c = ROOT / 'tools/reverse_bits.c'
    mkdir(OUT / 'tools')
    subprocess.run(['cc', '-O2', str(c), '-o', str(OUT / 'tools/reverse_bits')], check=True)
    reverse = OUT / 'payload/Cores/ThinkElastic.SM64/os30.rbf_r'
    mkdir(reverse.parent)
    subprocess.run([str(OUT / 'tools/reverse_bits'), str(OUT / 'raw/ap_core.rbf'), str(reverse)], check=True)
    table = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))
    require(reverse.read_bytes() == raw.read_bytes().translate(table) and
            reverse.read_bytes().translate(table) == raw.read_bytes(), 'Bit-reversal check failed')
    app = SOFTWARE / 'payload/Assets/sm64/common/sm64.elf'
    require(sha(app) == ELF_SHA, 'Wrong software ELF')
    atomic(OUT / 'payload/Assets/sm64/common/sm64.elf', app.read_bytes())
    archive = OUT / 'sm64-incremental-local.zip'
    members = ['Cores/ThinkElastic.SM64/os30.rbf_r', 'Assets/sm64/common/sm64.elf']
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in members:
            info = zipfile.ZipInfo(name, (2026, 10, 7, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (OUT / 'payload' / name).read_bytes())
    with zipfile.ZipFile(archive) as z:
        require(z.namelist() == members and all(z.read(n) == (OUT / 'payload' / n).read_bytes() for n in members), 'ZIP mismatch')
    # Self-contained source inputs; no copying into the sibling game checkout.
    software_files = read(SOFTWARE / 'manifest.json')['files']
    names = [n for n in software_files if n.startswith('sources/')] + [
        'integration.patch', 'README.txt', 'prepared.json', 'rebuild-proof.json']
    for name in names:
        source = safe(SOFTWARE, name)
        require(sha(source) == software_files[name], 'Software source drift')
        atomic(safe(OUT / 'software', name), source.read_bytes(), stat.S_IMODE(source.stat().st_mode))
    atomic(OUT / 'software/manifest.json', regular(SOFTWARE / 'manifest.json').read_bytes())
    atomic(OUT / 'recipe.py', Path(__file__).read_bytes(), 0o755)
    atomic(OUT / 'README.txt', (
        'Private selected candidate. The ZIP contains only the exact core and normal SM64 PGO app.\n'
        'hardware/ contains the 36 reviewed repository outputs; software/ contains the pinned F11 patch and measured profiles.\n'
        'No source integration, SD installation, sibling write, diagnostic overwrite or public distribution has occurred.\n'
        'Configured clock remains100MHz. Passing >90MHz both slow corners with negative100MHz setup is experimental, not100MHz closure.\n'
        'Native evidence is two bounded Bob-omb intervals, not sustained Pocket FPS or native OS/IRQ/PCM qualification.\n'
        'The separate integrate command modifies only this repository after all captured preimages and absent new targets are checked.\n'
        'Do not edit target files concurrently. Rollback restores only unchanged recorded transaction outputs.\n').encode())
    inputs = {str(p.relative_to(ROOT)): sha(p) for p in list(FIXED) + [FIT / 'manifest.json', c, Path(__file__)]}
    put(OUT / 'manifest.json', dict(private_only=True, integrated=False, helper_sha256=sha(Path(__file__)),
        qualification=result, inputs_sha256=inputs, production_guards=guards, entries=entries,
        raw_sha256=sha(raw), reversed_sha256=sha(reverse), elf_sha256=ELF_SHA,
        bit_table_and_involution_passed=True,
        files={str(p.relative_to(OUT)): state(p) for p in sorted(OUT.rglob('*')) if p.is_file()}))
    print('PREPARED', OUT, sha(OUT / 'manifest.json'))


def payload(digest):
    require(re.fullmatch('[0-9a-f]{64}', digest or '') is not None, 'Exact reviewed manifest SHA required')
    require(sha(OUT / 'manifest.json') == digest, 'Wrong payload manifest')
    d = read(OUT / 'manifest.json')
    require(d['helper_sha256'] == sha(Path(__file__)), 'Helper changed after preparation')
    for name, expected in d['files'].items():
        require(state(safe(OUT, name)) == expected, 'Payload changed: ' + name)
    for name, expected in d['inputs_sha256'].items():
        require(sha(safe(ROOT, name)) == expected, 'Bound input changed: ' + name)
    require(len(d['entries']) == 36 and len({r['path'] for r in d['entries']}) == 36, 'Wrong output set')
    return d


def journal(path, value):
    regular(path)
    with path.open('a') as f:
        f.write(json.dumps(value, sort_keys=True) + '\n')
        f.flush(); os.fsync(f.fileno())


def integrate(digest):
    d = payload(digest)
    qualification()
    guards, proposed = preimages()
    require(d['production_guards'] == guards and {r['path'] for r in d['entries']} == set(proposed), 'Guard/output set changed')
    for row in d['entries']:
        require(state(safe(ROOT, row['path'])) == row['before'], 'Target mode/preimage changed')
    tx = OUT / 'integration'
    require(not os.path.lexists(tx), 'Existing transaction; preserve it and inspect/rollback')
    mkdir(tx)
    put(tx / 'transaction.json', dict(manifest_sha256=digest, helper_sha256=sha(Path(__file__))))
    changed = [r for r in d['entries'] if r['before'] != r['after']]
    # Back up every existing changed target before the first repository write.
    for i, row in enumerate(changed):
        if row['before']:
            original = safe(ROOT, row['path'])
            require(state(original) == row['before'], 'Preimage drift during backup')
            atomic(tx / 'backup' / str(i), original.read_bytes(), row['before']['mode'])
    atomic(tx / 'journal.jsonl', b'')
    for i, row in enumerate(changed):
        target = safe(ROOT, row['path'])
        require(state(target) == row['before'], 'Concurrent destination change')
        journal(tx / 'journal.jsonl', dict(phase='intent', index=i, path=row['path']))
        atomic(target, safe(OUT / 'hardware', row['path']).read_bytes(), row['after']['mode'])
        require(state(target) == row['after'], 'Postwrite mismatch')
        journal(tx / 'journal.jsonl', dict(phase='done', index=i, path=row['path']))
    for row in d['entries']:
        require(state(safe(ROOT, row['path'])) == row['after'], 'Final output mismatch')
    for name, expected in guards.items():
        if name not in proposed:
            require(sha(safe(ROOT, name)) == expected, 'Unrelated guarded file changed')
    put(tx / 'complete.json', dict(passed=True, source_closure_exact=True, changed_files=len(changed),
        guarded_outputs=36, no_sibling_or_SD_writes=True, no_git_operations=True))
    print('INTEGRATED', len(changed), 'repository files; backup:', tx)


def rollback(digest):
    d = payload(digest)
    tx = OUT / 'integration'
    require(read(tx / 'transaction.json') == dict(manifest_sha256=digest, helper_sha256=sha(Path(__file__))), 'Wrong transaction')
    changed = [r for r in d['entries'] if r['before'] != r['after']]
    lines = regular(tx / 'journal.jsonl').read_text().splitlines(keepends=True)
    # A torn final append cannot precede a replace: its intent was not durable.
    # A torn final done still leaves the preceding intent for hash reconciliation.
    records = [json.loads(line) for line in lines if line.endswith('\n')]
    intents = []
    for record in records:
        i = record['index']
        require(isinstance(i, int) and 0 <= i < len(changed) and record['path'] == changed[i]['path'] and
                record['phase'] in ['intent', 'done'], 'Invalid journal record')
        if record['phase'] == 'intent':
            require(i not in intents, 'Duplicate intent')
            intents.append(i)
    # Reconcile an interrupted replace by exact before/after hash AND mode.
    for i in intents:
        row = changed[i]
        require(state(safe(ROOT, row['path'])) in [row['before'], row['after']], 'Refuse rollback over later edit')
        if row['before']:
            require(state(tx / 'backup' / str(i)) == row['before'], 'Backup changed')
    for i in reversed(intents):
        row = changed[i]; target = safe(ROOT, row['path'])
        current = state(target)
        require(current in [row['before'], row['after']], 'Concurrent rollback edit')
        if current == row['before']:
            continue
        if row['before']:
            atomic(target, (tx / 'backup' / str(i)).read_bytes(), row['before']['mode'])
        else:
            target.unlink(); fsync_dir(target.parent)
        require(state(target) == row['before'], 'Rollback mismatch')
    put(tx / 'rollback-complete.json', dict(passed=True, restored_intents=len(intents), directories_may_remain_empty=True))
    print('ROLLED BACK', len(intents), 'recorded intents')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='prepare', choices=['prepare', 'integrate', 'rollback'])
    parser.add_argument('--manifest-sha256')
    args = parser.parse_args()
    if args.action == 'prepare':
        require(args.manifest_sha256 is None, 'Prepare has no prior manifest')
        prepare()
    else:
        globals()[args.action](args.manifest_sha256)
