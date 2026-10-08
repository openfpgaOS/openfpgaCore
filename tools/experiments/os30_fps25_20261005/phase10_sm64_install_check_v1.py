#!/usr/bin/env python3
"""Temporary-directory transaction checks; never accesses the actual SM64 tree."""
import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import traceback
import types

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
H = R / 'tools/experiments/os30_fps25_20261005/phase10_sm64_install_v1.py'
O = P / 'sm64-installer-check-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def expect_failure(fn, contains):
    try:
        fn()
    except RuntimeError as error:
        assert contains in str(error), str(error)
        return
    raise AssertionError('Expected rejection: ' + contains)


def fixture(root):
    module = types.ModuleType('isolated_installer')
    module.__file__ = str(H)
    exec(compile(H.read_bytes(), str(H), 'exec'), module.__dict__)
    module.SIB, module.TX = root / 'SM64', root / 'private-transaction'
    rows, payloads, guards = [], {}, {}
    for index, name in enumerate(module.DESTINATIONS):
        target = module.SIB / name
        mode = 0o755 if index == 2 else 0o644
        if index not in (4, 5):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(('original-' + str(index)).encode()); target.chmod(mode)
            before = module.state(target); guards[name] = before
        else:
            before = None
        contents = ('qualified-payload-' + str(index)).encode(); payloads[name] = contents
        rows.append(dict(path=name, before=before, after=dict(sha256=module.digest(contents), mode=mode),
                         backup='backup/' + str(index) if before else None))
    for name in ['src/sm64/sm64/src/pc/gfx/gfx_geometry.h', 'src/sm64/pocket/perf_pocket.h',
                 'src/sm64/pocket/perf_pocket.c', 'src/sdk/include/of_gpu.h', module.DIAGNOSTIC]:
        target = module.SIB / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(('untouched-' + name).encode()); target.chmod(0o644); guards[name] = module.state(target)
    plan = dict(guard_inputs=guards)
    calls = []
    # Deliberately stub only the external qualified-plan/payload loaders. Actual
    # install, backup, journal, atomic, hash/mode, guard and rollback functions run.
    # This is not a claim that the pending real physical fit passes.
    def ready(ph, dh):
        calls.append((ph, dh)); module.check_preimages(plan, rows)
        return copy.deepcopy(plan), copy.deepcopy(rows), copy.deepcopy(payloads)
    module.readiness = ready
    module.load_plan = lambda ph: copy.deepcopy(plan)
    module.resolve_rows = lambda p, dh: (copy.deepcopy(rows), copy.deepcopy(payloads))
    return module, plan, rows, payloads, calls


def image(module):
    out = {}
    for path in module.SIB.rglob('*'):
        name = str(path.relative_to(module.SIB))
        if path.is_symlink(): out[name] = dict(symlink=os.readlink(path))
        elif path.is_file(): out[name] = module.state(path)
    return out


def check_case(name, root):
    m, plan, rows, payloads, calls = fixture(root)
    original = image(m)
    install = lambda: m.install('reviewed-plan', 'qualified-delivery')
    rollback = lambda: m.rollback('reviewed-plan', 'qualified-delivery')
    if name == 'qualification_rejected_before_any_write':
        def rejected(ph, dh): raise RuntimeError('Fit unqualified')
        m.readiness = rejected
        expect_failure(install, 'Fit unqualified')
        assert not m.TX.exists() and image(m) == original
    elif name == 'initial_guard_drift':
        (m.SIB / m.DIAGNOSTIC).write_bytes(b'preserve later diagnostic')
        before = image(m); expect_failure(install, 'Sibling guard changed')
        assert not m.TX.exists() and image(m) == before
    elif name == 'clean_install_and_rollback':
        install(); assert len(calls) == 1
        assert all(m.state(m.SIB / row['path']) == row['after'] for row in rows)
        assert m.read(m.TX / 'complete.json')['verified_destinations'] == 10
        rollback(); assert image(m) == original
        assert all(not (m.SIB / rows[i]['path']).exists() for i in (4, 5))
    elif name == 'interrupted_after_durable_intent_before_replace':
        journal = m.journal
        def interrupted(row):
            journal(row)
            if row['phase'] == 'intent' and row['index'] == 2: raise RuntimeError('injected intent interruption')
        m.journal = interrupted
        expect_failure(install, 'injected intent interruption')
        m.journal = journal; rollback(); assert image(m) == original
    elif name == 'interrupted_after_replace_and_torn_done':
        atomic = m.atomic; target = m.SIB / rows[6]['path']
        def interrupted(path, contents, mode, expected=m.UNGUARDED):
            atomic(path, contents, mode, expected)
            if path == target: raise RuntimeError('injected replace interruption')
        m.atomic = interrupted
        expect_failure(install, 'injected replace interruption')
        assert m.state(target) == rows[6]['after']
        with (m.TX / 'journal.jsonl').open('ab') as stream: stream.write(b'{"phase":"done","index":6')
        m.atomic = atomic; rollback(); assert image(m) == original
    elif name == 'later_edit_blocks_entire_rollback':
        install(); (m.SIB / rows[3]['path']).write_bytes(b'preserve later Makefile edit')
        before = image(m); expect_failure(rollback, 'Refuse rollback over a later edit')
        assert image(m) == before and not (m.TX / 'rollback-complete.json').exists()
    elif name == 'late_drift_before_atomic_replace':
        journal = m.journal; target = m.SIB / rows[2]['path']
        def changed(row):
            journal(row)
            if row['phase'] == 'intent' and row['index'] == 2: target.write_bytes(b'concurrent edit')
        m.journal = changed
        expect_failure(install, 'Destination changed before atomic replace')
        before = image(m); expect_failure(rollback, 'Refuse rollback over a later edit')
        assert image(m) == before and target.read_bytes() == b'concurrent edit'
    elif name == 'interrupted_rollback_resumes':
        install(); atomic = m.atomic; target = m.SIB / rows[6]['path']
        def interrupted(path, contents, mode, expected=m.UNGUARDED):
            atomic(path, contents, mode, expected)
            if path == target: raise RuntimeError('injected rollback interruption')
        m.atomic = interrupted
        expect_failure(rollback, 'injected rollback interruption')
        assert m.state(target) == rows[6]['before']
        m.atomic = atomic; rollback(); assert image(m) == original
    elif name == 'symlink_target_rejected':
        target = m.SIB / rows[0]['path']; target.unlink()
        outside = root / 'untouched-sentinel'; outside.write_bytes(b'sentinel')
        target.symlink_to(outside); before = image(m)
        expect_failure(install, 'Symlink rejected')
        assert not m.TX.exists() and image(m) == before and outside.read_bytes() == b'sentinel'
    elif name == 'duplicate_journal_record_rejected':
        install(); log = m.TX / 'journal.jsonl'
        lines = log.read_bytes().splitlines(keepends=True); log.write_bytes(b''.join(lines[:2] + [lines[1]] + lines[2:]))
        before = image(m); expect_failure(rollback, 'Invalid completion record'); assert image(m) == before
    else:
        raise AssertionError(name)
    return dict(passed=True, real_transaction_functions=True, temporary_paths_only=True)


def main():
    assert not O.exists(); O.mkdir(); shutil.copy2(__file__, O / 'recipe.py'); shutil.copy2(H, O / 'installer.py')
    cases = ['qualification_rejected_before_any_write', 'initial_guard_drift', 'clean_install_and_rollback',
        'interrupted_after_durable_intent_before_replace', 'interrupted_after_replace_and_torn_done',
        'later_edit_blocks_entire_rollback', 'late_drift_before_atomic_replace', 'interrupted_rollback_resumes',
        'symlink_target_rejected', 'duplicate_journal_record_rejected']
    results = {}; output = io.StringIO()
    try:
        ast.parse(H.read_text())
        with contextlib.redirect_stdout(output):
            for name in cases:
                with tempfile.TemporaryDirectory(prefix='sm64-installer-check-') as temp:
                    results[name] = check_case(name, Path(temp))
        result = dict(passed=True, cases=results, actual_sibling_touched=False, real_qualification_executed=False,
            qualification_stub_scope='Only fixture plan/payload loaders; actual guard/journal/atomic install/rollback methods executed in /tmp.',
            no_actual_installer_CLI_invoked=True, pending_fit_not_qualified=True, installer_sha256=sha(H))
    except Exception:
        result = dict(passed=False, cases=results, error=traceback.format_exc(), installer_sha256=sha(H))
        raise
    finally:
        (O / 'run.log').write_text(output.getvalue())
        (O / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    inputs = [H, Path(__file__), O / 'recipe.py', O / 'installer.py', O / 'run.log', O / 'result.json',
        P / 'sm64-update-plan-v2/manifest.json', P / 'sm64-update-plan-source-review-v2/manifest.json']
    (O / 'manifest.json').write_text(json.dumps(dict(passed=True, temporary_transaction_checks_only=True,
        evidence_sha256={str(x.relative_to(R)):sha(x) for x in inputs}), indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(passed=True, cases=len(cases), installer_sha256=sha(H), manifest_sha256=sha(O / 'manifest.json'))))


if __name__ == '__main__': main()
