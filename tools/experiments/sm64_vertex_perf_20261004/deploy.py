#!/usr/bin/env python3
"""Replace only the Pocket's cache app with the validated P3 vertex diagnostic, retaining its installed worker core/OS."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-vertex-perf-20261004'
SD = Path('/run/media/alberto/POCKETDEV')
APP = 'Assets/sm64/common/sm64.elf'
EXPECTED = {
    APP: '1ac8e33fa0d63e17d75764a85067459e387b52b4f69e6e0c5fa535ace0bd44e8',
    'Cores/ThinkElastic.SM64/os30.rbf_r': 'a950b6d7a8343d610cd46adb1cd85676ad3f83c6e80985522ba6c29c682295a0',
    'Assets/sm64/common/os.bin': '054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7',
}
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = json.loads((OUT / 'results.json').read_text())
    assert report['paired_framebuffer_depth_comparisons'] == 10
    source = ROOT.parent / 'SM64/build/pocket/sm64' / APP
    assert sha(source) == report['diagnostic_app_sha256']
    mounts = json.loads(subprocess.check_output(['findmnt', '-J', '-T', str(SD),
        '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS,UUID'], text=True))['filesystems']
    assert len(mounts) == 1
    mount = mounts[0]
    assert mount['uuid'] == '995B-1B19' and mount['target'] == str(SD), mount
    for rel, digest in EXPECTED.items():
        assert sha(SD / rel) == digest, ('Unexpected installed file', rel)
    print('Verified Pocket UUID, current P2 CACHE app, worker bitstream and OS.')
    if not args.apply:
        return
    assert 'rw' in mount['options'].split(','), mount
    backup = OUT / ('pocket-deploy-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(exist_ok=False)
    destination = SD / APP
    with (backup / 'sm64-before.elf').open('xb') as f:
        f.write(destination.read_bytes()); f.flush(); os.fsync(f.fileno())
    assert sha(backup / 'sm64-before.elf') == EXPECTED[APP]
    record = dict(uuid=mount['uuid'], mount=str(SD), before_sha256=EXPECTED,
                  after_sha256={**EXPECTED, APP: report['diagnostic_app_sha256']},
                  backup=str(backup / 'sm64-before.elf'), source=str(source),
                  verified=False, safely_unmounted=False)
    record_path = backup / 'deployment.json'
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    temporary = destination.with_name('sm64.elf.p3-tmp')
    with temporary.open('xb') as f:
        f.write(source.read_bytes()); f.flush(); os.fsync(f.fileno())
    assert sha(temporary) == report['diagnostic_app_sha256']
    os.replace(temporary, destination)
    subprocess.run(['sync', '-f', str(SD)], check=True)
    for rel, digest in record['after_sha256'].items():
        assert sha(SD / rel) == digest, ('Readback mismatch', rel)
    record['verified'] = True
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    print('P3 VERT deployed and read back successfully; worker bitstream and OS unchanged.')
    print(record_path)

if __name__ == '__main__':
    main()
