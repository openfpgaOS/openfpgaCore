#!/usr/bin/env python3
"""Apply a reviewed, hash-guarded local Quake2 deployment plan."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def sha(path):
    if not path.exists():
        return None
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + target.name + '.q2-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(source.read_bytes())
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, (target if target.exists() else source).stat().st_mode & 0o777)
        assert sha(Path(temp)) == sha(source)
        os.replace(temp, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temp).unlink(missing_ok=True)


def main():
    plan_path = Path(sys.argv[1]).resolve()
    expected_plan_hash = sys.argv[2]
    assert sha(plan_path) == expected_plan_hash, 'plan changed'
    plan = json.loads(plan_path.read_text())
    destination = Path(plan['destination'])
    evidence = plan_path.parent
    assert not (evidence / 'complete.json').exists(), 'already deployed'
    if plan.get('sd_uuid'):
        mount = subprocess.check_output(
            ['findmnt', '-J', '-M', str(destination), '-o', 'TARGET,UUID,FSTYPE,OPTIONS'], text=True)
        entry = json.loads(mount)['filesystems'][0]
        assert entry['target'] == str(destination)
        assert entry['uuid'] == plan['sd_uuid'] and entry['fstype'] == 'vfat'
        assert 'rw' in entry['options'].split(',')
    changed_paths = {entry['path'] for entry in plan['files']}
    assert len(changed_paths) == len(plan['files'])
    for rel, digest in plan['guards'].items():
        assert sha(destination / rel) == digest, 'guard changed: ' + rel
    for entry in plan['files']:
        target = destination / entry['path']
        assert target.resolve().is_relative_to(destination.resolve())
        assert not target.is_symlink()
        assert sha(target) == entry['before'], 'target changed: ' + str(target)
        assert sha(Path(entry['source'])) == entry['after'], 'source changed'
    for path, digest in plan['evidence'].items():
        assert sha(Path(path)) == digest, 'evidence changed: ' + path
    backup = evidence / 'backup'
    backup.mkdir(exist_ok=False)
    for entry in plan['files']:
        if entry['before'] is not None:
            saved = backup / entry['path']
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination / entry['path'], saved)
            assert sha(saved) == entry['before']
    changed = []
    try:
        for entry in plan['files']:
            changed.append(entry)
            atomic_copy(Path(entry['source']), destination / entry['path'])
            assert sha(destination / entry['path']) == entry['after']
            write_json(evidence / 'journal.json', {'completed': [e['path'] for e in changed]})
        for rel, digest in plan['guards'].items():
            assert sha(destination / rel) == digest, 'preservation failed: ' + rel
        for entry in plan['files']:
            assert sha(destination / entry['path']) == entry['after']
        subprocess.run(['sync', '-f', str(destination)], check=True)
    except BaseException:
        for entry in reversed(changed):
            target = destination / entry['path']
            if entry['before'] is None:
                target.unlink(missing_ok=True)
            else:
                atomic_copy(backup / entry['path'], target)
        raise
    write_json(evidence / 'complete.json', {
        'passed': True, 'plan_sha256': expected_plan_hash,
        'destination': str(destination), 'files': plan['files'],
        'unchanged_guard_count': len(plan['guards']),
        'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    print(json.dumps({'passed': True, 'updated': len(changed), 'preserved': len(plan['guards']),
                      'result': str(evidence / 'complete.json')}))


if __name__ == '__main__':
    main()
