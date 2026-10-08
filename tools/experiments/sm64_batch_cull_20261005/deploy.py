#!/usr/bin/env python3
"""Deploy the qualified SM64 app only; preserve the selected dual core."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from publish import OUT, DEST, sha, load, atomic_copy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = load(OUT / 'qualification.json')
    assert report['qualified'] is True
    subprocess.run(['python3', str(Path(__file__).with_name('publish.py')), '--verify'], check=True)
    saved = load(OUT / 'sd-before.json')
    card = Path(saved['mount'])
    mount = json.loads(subprocess.check_output([
        'findmnt', '-J', '-o', 'TARGET,SOURCE,FSTYPE,UUID,OPTIONS', '-T', str(card)], text=True))
    fs = mount['filesystems'][0]
    assert fs['target'] == str(card) and fs['uuid'] == '995B-1B19' and fs['fstype'] == 'vfat', fs
    rel = 'Assets/sm64/common/sm64.elf'
    expected = report['diagnostic_app_sha256']
    source = DEST / 'build/pocket/sm64' / rel
    assert sha(source) == expected
    already = sha(card / rel) == expected
    for path, value in saved['files'].items():
        assert sha(card / path) == (expected if already and path == rel else value), ('Card file changed', path)
    print(json.dumps(dict(card=str(card), path=rel, before=saved['files'][rel],
                         after=expected, already_current=already), indent=2))
    if not args.apply:
        return
    assert 'rw' in fs['options'].split(','), fs
    backup = OUT / 'sd-backup'
    if not already:
        backup.mkdir(exist_ok=False)
        shutil.copy2(card / rel, backup / 'sm64.elf')
        assert sha(backup / 'sm64.elf') == saved['files'][rel]
        atomic_copy(source, card / rel)
    os.sync()
    for path, value in saved['files'].items():
        assert sha(card / path) == (expected if path == rel else value), ('Readback failed', path)
    (OUT / 'deployed.json').write_text(json.dumps(dict(mount=str(card), uuid=fs['uuid'],
        app_sha256=expected, backup=str(backup), already_current=already,
        protected_files={p: v for p, v in saved['files'].items() if p != rel},
        synced=True, readback_verified=True), indent=2) + '\n')
    print('PASS: SM64 app deployed, synced and verified; core, OS and Quake2 unchanged.')


if __name__ == '__main__':
    main()
