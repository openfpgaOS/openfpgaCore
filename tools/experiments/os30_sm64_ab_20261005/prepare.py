#!/usr/bin/env python3
"""Freeze three verified bitstream-only overlays for a same-app Pocket test."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-sm64-ab-20261005'
PACKAGE = OUT / 'package'
GAME = ROOT.parent / 'SM64/build/pocket/sm64'
BITSTREAM = 'Cores/ThinkElastic.SM64/os30.rbf_r'

CHOICES = {
    'A-single': {
        'description': 'Previous single-issue CPU, geometry disabled',
        'geometry': 0,
        'source': 'docs/measurements/os30-dual-20261004/copy-to-sd-20261005T131016Z/backups/ThinkElastic.SM64/os30.rbf_r',
        'sha256': 'd3d90e20c7116304108a2f94b65d3a69335347603fab388b905cafc3fc6f5f9f',
    },
    'B-dual': {
        'description': 'Current dual-issue CPU, geometry disabled; restores the current core',
        'geometry': 0,
        'source': 'docs/measurements/os30-dual-20261004/copy-to-games-20261005T125923Z/os30.rbf_r',
        'sha256': 'b4d55466283320c798047effa1dd9895d990fd6f297857e52ad99ec9bbf3778d',
    },
    'C-geometry': {
        'description': 'Earlier single-issue CPU with geometry acceleration and worker enabled',
        'geometry': 1,
        'source': 'docs/measurements/os30-worker-20261003/deploy-worker-test/Cores/ThinkElastic.SM64/os30.rbf_r',
        'sha256': 'a950b6d7a8343d610cd46adb1cd85676ad3f83c6e80985522ba6c29c682295a0',
    },
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not PACKAGE.exists(), 'Package already frozen; do not overwrite evidence.'
    expected = {
        'Assets/sm64/common/sm64.elf': '990ba23281d6793a479df4c9094cb0cd600894ca3ab5548df360a62c016f2984',
        'Assets/sm64/common/os.bin': '054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7',
        'Assets/sm64/common/bank.ofsf': '605fc6c6df4c3f7250854744475e9ed0a9c30e80353edab1454695f7ba8584c6',
        'Cores/ThinkElastic.SM64/loader.bin': 'fc1f8d37eb0fb322006b4428f0b59d2801c9b74978bd3f24103f5f9bbfca3c99',
    }
    for rel, value in expected.items():
        assert sha(GAME / rel) == value, ('Published baseline changed', rel)
    protected = dict(expected)
    for p in (GAME / 'Cores/ThinkElastic.SM64').iterdir():
        if p.is_file() and p.suffix == '.json':
            protected[str(p.relative_to(GAME))] = sha(p)
    rel = 'Assets/sm64/ThinkElastic.SM64/sm64.json'
    protected[rel] = sha(GAME / rel)
    for key, choice in CHOICES.items():
        source = ROOT / choice['source']
        assert sha(source) == choice['sha256'], ('Bitstream changed', key)
        destination = PACKAGE / key / BITSTREAM
        destination.parent.mkdir(parents=True)
        shutil.copy2(source, destination)
        assert sha(destination) == choice['sha256']
    record = dict(bitstream_path=BITSTREAM, choices=CHOICES, protected_files=protected,
                  cpu_configured_mhz=100, initial_choice='B-dual',
                  first_trial='A-single', app_source=str(GAME / 'Assets/sm64/common/sm64.elf'),
                  contains_rom_assets=False, rebuilt=False, fpga_sweep=False,
                  comparison='Complete hardware configurations with the same published P3 app; not issue width alone.')
    (PACKAGE / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    (PACKAGE / 'SHA256SUMS').write_text(''.join(
        f"{c['sha256']}  {key}/{BITSTREAM}\n" for key, c in CHOICES.items()))
    shutil.copy2(Path(__file__).with_name('swap.py'), PACKAGE / 'swap.py')
    print(json.dumps(dict(package=str(PACKAGE), choices=list(CHOICES),
                          protected_files=len(protected)), indent=2))


if __name__ == '__main__':
    main()
