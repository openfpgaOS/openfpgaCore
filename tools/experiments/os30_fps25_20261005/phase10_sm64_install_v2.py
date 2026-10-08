#!/usr/bin/env python3
"""Guarded ten-path SM64 transaction. Default is read-only; no SD or core-repo install."""
import sys
# Reviewed helper imports must not create bytecode beside repository sources.
sys.dont_write_bytecode = True

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import types

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
SIB = R.parent / 'SM64'
PLAN = P / 'sm64-update-plan-v2'
DELIVERY = P / 'final-candidate-delivery-v3'
TX = P / 'sm64-update-transaction-v2'
PLAN_HELPER = R / 'tools/experiments/os30_fps25_20261005/phase10_sm64_update_plan_v2.py'
DELIVERY_HELPER = R / 'tools/experiments/os30_fps25_20261005/phase10_final_candidate_delivery_v3.py'
PLAN_SHA = '487dd0652ece68d0c419bf42e982a333d8f2d8071e5dc8f2441e604ddb9e8aa0'
PLAN_HELPER_SHA = 'cc43d7acd875613b001bcba4edbb4cb8ae4bf5bc629a23fad9cd59126be13beb'
DELIVERY_HELPER_SHA = '7ac1abe7a2a514ac0fcf6a7c2900f685bd3315f2f3d9b96e783aa4c76cad35e7'
DIAGNOSTIC = '.obj/sm64-perfdiag/app.elf'
PGO = 'src/sm64/pgo/home/alberto/Repos/openfpgaCore/docs/measurements/os30-fps25-20261005/phase10/renderer-pgo-v1/work/.obj/sm64/sm64/src/pc/gfx/'
DESTINATIONS = (
    'src/sm64/sm64/src/pc/gfx/gfx_pc.c',
    'src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
    'src/sm64/sm64/src/pc/gfx/gfx_gpu.h',
    'src/sm64/Makefile', PGO + 'gfx_pc.gcda', PGO + 'gfx_gpu.gcda',
    '.obj/sm64/app.elf', 'build/pocket/sm64/Assets/sm64/common/sm64.elf',
    'runtime/pocket/os30.rbf_r', 'build/pocket/sm64/Cores/ThinkElastic.SM64/os30.rbf_r',
)
CORE_MEMBER = 'payload/Cores/ThinkElastic.SM64/os30.rbf_r'
UNGUARDED = object()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def safe(base, name):
    name = Path(name)
    require(not name.is_absolute() and name.parts and
            not any(x in ('.', '..', '.git') for x in name.parts), 'Unsafe relative path')
    path = base / name
    for part in reversed([path, *path.parents]):
        if os.path.lexists(part):
            mode = part.lstat().st_mode
            require(not stat.S_ISLNK(mode), 'Symlink rejected: ' + str(part))
            require(stat.S_ISDIR(mode) if part != path else
                    (stat.S_ISREG(mode) or stat.S_ISDIR(mode)), 'Special file rejected: ' + str(part))
    return path


def data(path):
    safe(path.parent, path.name)
    require(path.is_file() and stat.S_ISREG(path.lstat().st_mode), 'Missing regular file: ' + str(path))
    return path.read_bytes()


def digest(contents):
    return hashlib.sha256(contents).hexdigest()


def sha(path):
    return digest(data(path))


def read(path):
    return json.loads(data(path))


def state(path):
    safe(path.parent, path.name)
    if not os.path.lexists(path):
        return None
    return dict(sha256=sha(path), mode=stat.S_IMODE(path.stat().st_mode))


def pinned_module(path, expected, name):
    contents = data(path)
    require(digest(contents) == expected, 'Changed reviewed helper: ' + str(path))
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(contents, str(path), 'exec'), module.__dict__)
    return module


def load_plan(plan_hash):
    require(plan_hash == PLAN_SHA and sha(PLAN / 'manifest.json') == PLAN_SHA, 'Exact reviewed plan hash required')
    require(sha(PLAN_HELPER) == PLAN_HELPER_SHA and sha(DELIVERY_HELPER) == DELIVERY_HELPER_SHA, 'Helper pin changed')
    for name, expected in read(PLAN / 'manifest.json')['files'].items():
        require(sha(safe(PLAN, name)) == expected, 'Private plan changed: ' + name)
    plan = read(PLAN / 'plan.json')
    require(plan['sibling_root'] == str(SIB) and plan['destination_count'] == 10 and
            tuple(row['path'] for row in plan['entries']) == DESTINATIONS, 'Wrong ten-path scope')
    require(plan['diagnostic_guard'] == DIAGNOSTIC and DIAGNOSTIC not in DESTINATIONS, 'Diagnostic must be guard-only')
    require(len(plan['guard_inputs']) == 13 and
            plan['guard_inputs'][DIAGNOSTIC]['sha256'] == plan['diagnostic_sha256'], 'Wrong guard inventory')
    for name, expected in plan['inputs_sha256'].items():
        require(sha(safe(R, name)) == expected, 'Bound plan input changed: ' + name)
    return plan


def resolve_rows(plan, delivery_hash):
    require(re.fullmatch('[0-9a-f]{64}', delivery_hash or '') is not None and
            sha(DELIVERY / 'manifest.json') == delivery_hash, 'Exact explicit delivery hash required')
    dm = read(DELIVERY / 'manifest.json')
    require(dm['helper_sha256'] == DELIVERY_HELPER_SHA and
            dm['elf_sha256'] == plan['expected_normal_elf_sha256'], 'Wrong delivery selection')
    rows, payloads = [], {}
    for index, entry in enumerate(plan['entries']):
        name = entry['path']
        require(entry['mode'] == (entry['before']['mode'] if entry['before'] else 0o644), 'Changed target permissions')
        if entry['kind'] == 'core':
            require(entry['deferred_delivery_member'] == CORE_MEMBER and
                    entry['after_sha256'] is None and entry['payload'] is None, 'Wrong deferred core')
            source, expected = safe(DELIVERY, CORE_MEMBER), dm['reversed_sha256']
        else:
            require(entry['payload'] == 'payload/' + name, 'Wrong source payload path')
            source, expected = safe(PLAN, entry['payload']), entry['after_sha256']
        contents = data(source)
        require(digest(contents) == expected, 'Payload hash changed: ' + name)
        rows.append(dict(path=name, before=entry['before'], after=dict(sha256=expected, mode=entry['mode']),
                         backup='backup/' + str(index) if entry['before'] else None))
        payloads[name] = contents
    return rows, payloads


def check_preimages(plan, rows):
    for name, expected in plan['guard_inputs'].items():
        require(state(safe(SIB, name)) == expected, 'Sibling guard changed: ' + name)
    for row in rows:
        require(state(safe(SIB, row['path'])) == row['before'], 'Destination preimage/absence changed: ' + row['path'])


def readiness(plan_hash, delivery_hash):
    plan = load_plan(plan_hash)
    helper = pinned_module(PLAN_HELPER, PLAN_HELPER_SHA, 'reviewed_sm64_plan_v2')
    # This exact reviewed function rehashes plan/payloads, actual fit/20 timing
    # rows/all capacities, assembler provenance and reversed bytes. No writes.
    with contextlib.redirect_stdout(io.StringIO()):
        helper.verify_ready(plan_hash, delivery_hash)
    rows, payloads = resolve_rows(plan, delivery_hash)
    check_preimages(plan, rows)
    return plan, rows, payloads


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mkdir(path):
    safe(path.parent, path.name)
    missing, current = [], path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for current in reversed(missing):
        current.mkdir(mode=0o755)
        fsync_dir(current.parent)


def atomic(path, contents, mode, expected=UNGUARDED):
    safe(path.parent, path.name)
    mkdir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.sm64-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        safe(path.parent, path.name)
        if expected is not UNGUARDED:
            require(state(path) == expected, 'Destination changed before atomic replace: ' + str(path))
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def put(path, value):
    require(not os.path.lexists(path), 'Preserve existing transaction artifact: ' + str(path))
    atomic(path, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode(), 0o644)


def journal(value):
    path = safe(TX, 'journal.jsonl')
    data(path)
    with path.open('a') as stream:
        stream.write(json.dumps(value, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def guard_only(plan):
    return {name: expected for name, expected in plan['guard_inputs'].items() if name not in DESTINATIONS}


def verify_guards(guards):
    for name, expected in guards.items():
        require(state(safe(SIB, name)) == expected, 'Guard-only input changed: ' + name)


def install(plan_hash, delivery_hash):
    # Every physical/source/preimage gate precedes even private backup writes.
    plan, rows, payloads = readiness(plan_hash, delivery_hash)
    require(not os.path.lexists(TX), 'Existing transaction: preserve and inspect/rollback')
    mkdir(TX)
    for row in rows:
        if row['before']:
            path = safe(SIB, row['path'])
            require(state(path) == row['before'], 'Preimage changed before backup')
            contents = data(path)
            require(digest(contents) == row['before']['sha256'], 'Backup read changed')
            atomic(safe(TX, row['backup']), contents, row['before']['mode'])
            require(state(safe(TX, row['backup'])) == row['before'], 'Backup verification failed')
    # Null before/backup entries are durable absent-path records. The complete
    # original backup set and transaction header exist before any target write.
    put(TX / 'transaction.json', dict(plan_sha256=plan_hash, delivery_sha256=delivery_hash,
        installer_sha256=sha(Path(__file__)), entries=rows, guard_only=guard_only(plan),
        verified_ready_before_mutation=True, sibling_root=str(SIB)))
    atomic(TX / 'journal.jsonl', b'', 0o644)
    check_preimages(plan, rows)
    for index, row in enumerate(rows):
        path = safe(SIB, row['path'])
        require(state(path) == row['before'], 'Concurrent destination change: ' + row['path'])
        require(digest(payloads[row['path']]) == row['after']['sha256'], 'In-memory payload drift')
        journal(dict(phase='intent', index=index, path=row['path']))
        atomic(path, payloads[row['path']], row['after']['mode'], expected=row['before'])
        require(state(path) == row['after'], 'Postwrite mismatch: ' + row['path'])
        journal(dict(phase='done', index=index, path=row['path']))
    for row in rows:
        require(state(safe(SIB, row['path'])) == row['after'], 'Final destination mismatch')
    verify_guards(guard_only(plan))
    put(TX / 'complete.json', dict(passed=True, verified_destinations=10, guard_only_preserved=True,
        diagnostic_preserved=True, no_SD_Quake2_or_hardware_source_writes=True,
        transaction_sha256=sha(TX / 'transaction.json')))
    print(json.dumps(dict(installed=True, destinations=10, transaction=str(TX), transaction_sha256=sha(TX / 'transaction.json'))))


def rollback(plan_hash, delivery_hash):
    # Initial verify_ready cannot run after installation because its preimages
    # intentionally changed. Rollback validates the exact recorded successful
    # readiness selection and every current before/after image instead.
    plan = load_plan(plan_hash)
    rows, _ = resolve_rows(plan, delivery_hash)
    expected = dict(plan_sha256=plan_hash, delivery_sha256=delivery_hash,
        installer_sha256=sha(Path(__file__)), entries=rows, guard_only=guard_only(plan),
        verified_ready_before_mutation=True, sibling_root=str(SIB))
    require(read(TX / 'transaction.json') == expected, 'Wrong recorded transaction')
    require(not os.path.lexists(TX / 'rollback-complete.json'), 'Rollback already completed')
    lines = data(TX / 'journal.jsonl').splitlines(keepends=True)
    records = [json.loads(line) for line in lines if line.endswith(b'\n')]
    intents, pending = [], False
    for record in records:
        index = record['index']
        require(type(index) is int and 0 <= index < 10 and record['path'] == rows[index]['path'] and
                set(record) == {'phase', 'index', 'path'}, 'Invalid journal record')
        if record['phase'] == 'intent':
            require(index == len(intents) and not pending, 'Out-of-order or duplicate intent')
            intents.append(index)
            pending = True
        else:
            require(record['phase'] == 'done' and pending and index == intents[-1], 'Invalid completion record')
            pending = False
    # A torn intent has no following replace; a torn done leaves its durable
    # intent, reconciled by exact hash AND mode. Later edits block all rollback.
    for index, row in enumerate(rows):
        current = state(safe(SIB, row['path']))
        require(current in (row['before'], row['after']) if index in intents else current == row['before'],
                'Refuse rollback over a later edit: ' + row['path'])
        if index in intents and row['before']:
            require(state(safe(TX, row['backup'])) == row['before'], 'Backup changed')
    verify_guards(guard_only(plan))
    for index in reversed(intents):
        row = rows[index]; path = safe(SIB, row['path'])
        current = state(path)
        require(current in (row['before'], row['after']), 'Concurrent rollback edit')
        if current == row['before']:
            continue
        if row['before']:
            atomic(path, data(safe(TX, row['backup'])), row['before']['mode'], expected=row['after'])
        else:
            path.unlink(); fsync_dir(path.parent)
        require(state(path) == row['before'], 'Rollback mismatch')
    check_preimages(plan, rows)
    put(TX / 'rollback-complete.json', dict(passed=True, restored_intents=len(intents),
        empty_created_directories_may_remain=True, no_later_edits_overwritten=True))
    print(json.dumps(dict(rolled_back=True, intents=len(intents))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='check', choices=['check', 'install', 'rollback'])
    parser.add_argument('--plan-manifest-sha256', required=True)
    parser.add_argument('--delivery-manifest-sha256', required=True)
    args = parser.parse_args()
    if args.action == 'check':
        readiness(args.plan_manifest_sha256, args.delivery_manifest_sha256)
        print(json.dumps(dict(read_only_ready=True, destinations=10, execution_performed=False)))
    else:
        globals()[args.action](args.plan_manifest_sha256, args.delivery_manifest_sha256)
