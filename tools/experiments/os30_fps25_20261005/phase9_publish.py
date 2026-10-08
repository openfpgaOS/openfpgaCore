#!/usr/bin/env python3
"""Guarded local F11 CPU-preparation cleanup publication over retained F9; no SD action."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase9'
PARENT = OUT.parent / 'phase7/QUALIFIED.json'
PARENT_SHA = 'fe830ac468f083e2be9bc6fe06e9b5a97e901140cf7cc21ae0cffb54eec4b108'
spec = importlib.util.spec_from_file_location('f11_guarded_publisher', Path(__file__).with_name('phase2_publish.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.OUT, m.QUALIFIED = OUT, OUT / 'QUALIFIED.json'
m.SOURCES['of_gpu.h'] = 'src/sdk/include/of_gpu.h'
m.OLD_NORMAL = '73bd7e68c87835cdca6b485277c44c38bd9fb3182aca502755d2cc55f7d7d23a'
m.OLD_FPS = '02782c4a0fb731b358dd4be2d48daa3eae55ac7411351693aaeee924b2e276cf'
m.ELF_DESTINATIONS = (
    ('app.elf', '.obj/sm64/app.elf', m.OLD_NORMAL, 'normal_elf_sha256'),
    ('diagnostic.elf', '.obj/sm64-perfdiag/app.elf', m.OLD_FPS, 'diagnostic_elf_sha256'),
    ('diagnostic.elf', 'build/pocket/sm64/Assets/sm64/common/sm64.elf', m.OLD_FPS, 'diagnostic_elf_sha256'),
)


def selection():
    m.regular(PARENT, OUT.parent)
    m.regular(m.QUALIFIED, OUT)
    m.require(m.sha(PARENT) == PARENT_SHA, 'Frozen F9 qualification changed')
    r, digest = m.read(m.QUALIFIED), m.sha(m.QUALIFIED)
    m.require(r['qualified_for_device_trial'] is True and r['target_verified'] is False,
              'Wrong measurement trial state')
    m.require(r['independent_review_passed'] is True, 'Independent review missing')
    m.require(re.fullmatch(r'[a-z0-9_-]+', r['candidate']), 'Invalid candidate name')
    directory = OUT / r['candidate']
    before = dict(m.read(PARENT)['source_tree_sha256'])
    m.require(len(before) == 6747, 'Unexpected F9 input inventory')
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
    for publisher in (Path(__file__), Path(__file__).with_name('phase2_publish.py')):
        m.require(evidence.get(str(publisher.relative_to(ROOT))) == m.sha(publisher),
                  'Publication guards differ from reviewed evidence')
    for rel, h in evidence.items():
        m.require(m.sha(m.relative_file(ROOT, rel)) == h, ('Evidence changed', rel))
    m.require(m.sha(ROOT / 'src/firmware/api/of_gpu.h') == r['source_sha256']['of_gpu.h'],
              'Canonical SDK hooks differ from the qualified SDK')
    m.require(m.sha(m.QUALIFIED) == digest, 'Qualification changed during selection')
    m.local_retained()
    return r, digest, directory, before, expected


m.selection = selection

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'publish', 'verify'])
    args = parser.parse_args()
    if args.action == 'plan':
        result = m.plan(False)
    elif args.action == 'publish':
        result = m.publish()
    else:
        result = m.verify(False)
    print(json.dumps(result, indent=2))
