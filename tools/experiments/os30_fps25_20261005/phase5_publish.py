#!/usr/bin/env python3
"""Publish the separately qualified F7 measurement build over the F6 package."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase5'
PARENT = OUT.parent / 'phase4/release/QUALIFIED.json'
PARENT_SHA = '977cc3c0c1ae91d4e52bd9d585ebc14ec928e6a231d035c4d6bdadde32517a3b'
spec = importlib.util.spec_from_file_location('f7_guarded_publisher', Path(__file__).with_name('phase2_publish.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.OUT, m.QUALIFIED = OUT, OUT / 'QUALIFIED.json'
m.SOURCES['of_gpu.h'] = 'src/sdk/include/of_gpu.h'
m.OLD_NORMAL = 'dd625512215b0971a08b417e7f6ff780dc6e667fbfd835b6d933adbeff791ab0'
m.OLD_FPS = '96bbb96876d6d7d9d990bd7d17831e3048273960d711edf4e20bcd2f73f32b2e'
m.ELF_DESTINATIONS = (
    ('app.elf', '.obj/sm64/app.elf', m.OLD_NORMAL, 'normal_elf_sha256'),
    ('diagnostic.elf', '.obj/sm64-perfdiag/app.elf', m.OLD_FPS, 'diagnostic_elf_sha256'),
    ('diagnostic.elf', 'build/pocket/sm64/Assets/sm64/common/sm64.elf', m.OLD_FPS, 'diagnostic_elf_sha256'),
)


def selection():
    m.require(m.sha(PARENT) == PARENT_SHA, 'Frozen F6 qualification changed')
    r, h = m.read(m.QUALIFIED), m.sha(m.QUALIFIED)
    m.require(r['qualified_for_device_trial'] is True and r['target_verified'] is False,
              'Wrong diagnostic trial state')
    m.require(r['independent_review_passed'] is True, 'Independent review missing')
    m.require(r['normal_elf_sha256'] == m.OLD_NORMAL, 'Normal F6 executable changed')
    m.require(re.fullmatch(r'[a-z0-9_-]+', r['candidate']), 'Invalid candidate name')
    directory = OUT / r['candidate']
    before = dict(m.read(PARENT)['source_tree_sha256'])
    m.require(len(before) == 6747, 'Unexpected F6 input inventory')
    expected = dict(before)
    m.require(set(r['source_sha256']) == set(m.SOURCES), 'Missing reviewed source')
    for name, rel in m.SOURCES.items():
        m.require(m.sha(m.relative_file(directory, name)) == r['source_sha256'][name],
                  ('Candidate changed', name))
        expected[rel] = r['source_sha256'][name]
    m.require(expected == r['source_tree_sha256'], 'Wrong source composition')
    for name, key in [('app.elf', 'normal_elf_sha256'), ('diagnostic.elf', 'diagnostic_elf_sha256')]:
        m.require(m.sha(m.relative_file(directory, name)) == r[key], ('Executable changed', name))
    evidence = r['evidence_sha256']
    m.require(evidence.get(str(PARENT.relative_to(ROOT))) == PARENT_SHA, 'Parent evidence missing')
    m.require(evidence.get(str(Path(__file__).relative_to(ROOT))) == m.sha(Path(__file__)), 'Publisher changed')
    parent_publisher = Path(__file__).with_name('phase2_publish.py')
    m.require(evidence.get(str(parent_publisher.relative_to(ROOT))) == m.sha(parent_publisher),
              'Shared publication guards changed or are not bound')
    for rel, digest in evidence.items():
        m.require(m.sha(m.relative_file(ROOT, rel)) == digest, ('Evidence changed', rel))
    m.require(m.sha(ROOT / 'src/firmware/api/of_gpu.h') == r['source_sha256']['of_gpu.h'],
              'Canonical SDK hook source differs from qualified application SDK')
    m.require(m.sha(m.QUALIFIED) == h, 'Qualification changed during selection')
    m.local_retained()
    return r, h, directory, before, expected


m.selection = selection

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['plan', 'publish', 'deploy', 'verify'])
    p.add_argument('--sd', action='store_true')
    a = p.parse_args()
    if a.action == 'plan':
        result = m.plan(a.sd)
    elif a.action == 'publish':
        result = m.publish()
    elif a.action == 'deploy':
        result = m.deploy_sd()
    else:
        result = m.verify(a.sd)
    print(json.dumps(result, indent=2))
