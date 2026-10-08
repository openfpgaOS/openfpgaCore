#!/usr/bin/env python3
"""Guarded F9 triangle optimization publication over the current F8 trial."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase7'
PARENT = OUT.parent / 'phase6/QUALIFIED.json'
PARENT_SHA = '7ba3033b008dcc117867d6854cf767f7dd4c3b417c3ded2b1c89d63d82de0b69'
spec = importlib.util.spec_from_file_location('f9_guarded_publisher', Path(__file__).with_name('phase2_publish.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.OUT, m.QUALIFIED = OUT, OUT / 'QUALIFIED.json'
m.SOURCES['of_gpu.h'] = 'src/sdk/include/of_gpu.h'
m.OLD_NORMAL = 'dd625512215b0971a08b417e7f6ff780dc6e667fbfd835b6d933adbeff791ab0'
m.OLD_FPS = 'aba8b93e1582cf833accebde0dd596ffdcc500a8902109591ab308549d2be247'
m.ELF_DESTINATIONS = (
    ('app.elf', '.obj/sm64/app.elf', m.OLD_NORMAL, 'normal_elf_sha256'),
    ('diagnostic.elf', '.obj/sm64-perfdiag/app.elf', m.OLD_FPS, 'diagnostic_elf_sha256'),
    ('diagnostic.elf', 'build/pocket/sm64/Assets/sm64/common/sm64.elf', m.OLD_FPS, 'diagnostic_elf_sha256'),
)


def selection():
    m.require(m.sha(PARENT) == PARENT_SHA, 'Frozen F8 qualification changed')
    r, digest = m.read(m.QUALIFIED), m.sha(m.QUALIFIED)
    m.require(r['qualified_for_device_trial'] is True and r['target_verified'] is False,
              'Wrong measurement trial state')
    m.require(r['independent_review_passed'] is True, 'Independent review missing')
    m.require(re.fullmatch(r'[a-z0-9_-]+', r['candidate']), 'Invalid candidate name')
    directory = OUT / r['candidate']
    before = dict(m.read(PARENT)['source_tree_sha256'])
    m.require(len(before) == 6747, 'Unexpected F8 input inventory')
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
    parser.add_argument('action', choices=['plan', 'publish', 'deploy', 'verify'])
    parser.add_argument('--sd', action='store_true')
    args = parser.parse_args()
    m.require(not args.sd or args.action in ('plan', 'verify'), '--sd applies to plan/verify')
    if args.action == 'plan':
        result = m.plan(args.sd)
    elif args.action == 'publish':
        result = m.publish()
    elif args.action == 'deploy':
        result = m.deploy_sd()
    else:
        result = m.verify(args.sd)
    print(json.dumps(result, indent=2))
