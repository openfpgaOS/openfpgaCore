#!/usr/bin/env python3
"""Deploy the user-requested F10 diagnostic trial to one SD-card file.

The previous no-promotion recommendation remains unchanged. This reversible
device test is authorized by the subsequent user request, "Shall we test it".
It does not promote sibling sources, replace FPGA/OS files or assert speedup.
"""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005/phase8'
OUT = CAMP / 'device-trial'
REVIEW = CAMP / 'review/final-review.json'
REVIEW_SHA = '8d539da08a1c32bb517cff58216abbc6b64362d794abefd13a6260d730743d67'
HELPER = Path(__file__).with_name('phase2_publish.py')
HELPER_SHA = '5d8694ab77802d34479ce4336b0fbcd654983a785e49105734653306dd8b2d50'
SOURCE = CAMP / 'kick128-tail-f10/diagnostic.elf'
BEFORE = '02782c4a0fb731b358dd4be2d48daa3eae55ac7411351693aaeee924b2e276cf'
AFTER = 'd5e16a5f76593743fa758067e3be3f591db3805e28215bce6056d44beeef447f'
spec = importlib.util.spec_from_file_location('f10_trial_guards', HELPER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
TARGET = m.CARD / 'Assets/sm64/common/sm64.elf'


def preflight(expected, writable=False):
    m.require(m.sha(HELPER) == HELPER_SHA, 'Copy/rollback guards changed')
    m.require(m.sha(REVIEW) == REVIEW_SHA, 'Independent review changed')
    review = m.read(REVIEW)
    m.require(review['correctness_passed'] and review['passed'], 'Correctness review failed')
    m.require(review['recommended_for_deployment'] is False,
              'Unexpected historical decision; this is an explicit device trial')
    m.require(review['diagnostic_elf_sha256'] == AFTER, 'Wrong reviewed diagnostic')
    for relative, digest in review['evidence_sha256'].items():
        m.require(m.sha(m.relative_file(ROOT, relative)) == digest, ('Reviewed evidence changed', relative))
    m.regular(SOURCE, ROOT)
    m.require(m.sha(SOURCE) == AFTER, 'Trial executable changed')
    for path, digest in review['retained_paths_sha256'].items():
        if Path(path) != TARGET:
            m.require(m.sha(path) == digest, ('Retained file changed', path))
    identity = m.card_identity(require_rw=writable)
    m.regular(TARGET, m.CARD)
    m.require(m.sha(TARGET) == expected, 'Current SD executable differs from the expected trial state')
    return identity


def plan():
    identity = preflight(BEFORE)
    m.require(not OUT.exists(), 'A device-trial attempt already exists; inspect it first')
    return dict(action='plan', read_only=True, mount=identity,
                source=str(SOURCE), destination=str(TARGET), before_sha256=BEFORE,
                after_sha256=AFTER, review_sha256=REVIEW_SHA,
                core_sha256=m.CORE, os_sha256=m.OS, sibling_sources_and_package_unchanged=True,
                user_request='Shall we test it', physical_speedup_unverified=True,
                known_tradeoff='Diagnostic model: about0.4% slower in busy entrance; about2.1–2.2% faster in later window.')


def deploy():
    p = plan()
    identity = preflight(BEFORE, writable=True)
    m.require(identity == p['mount'], 'SD mount changed since preflight')
    OUT.mkdir(exist_ok=False)
    row = dict(source=str(SOURCE), destination=str(TARGET), before_sha256=BEFORE, after_sha256=AFTER)
    m.backup_rows([row], OUT/'sd-backup', m.CARD)
    record = dict(plan=p, file=row, script_sha256=m.sha(Path(__file__)),
                  helper_sha256=HELPER_SHA, review_sha256=REVIEW_SHA,
                  authorization='Subsequent user-requested hardware trial; historical recommendation unchanged.',
                  created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    m.write_record(OUT/'plan.json', record)
    guard = lambda: m.card_guard(identity)
    try:
        m.atomic_copy(SOURCE, TARGET, BEFORE, AFTER, m.CARD, guard)
        os.sync()
        m.require(preflight(AFTER, writable=True) == identity, 'Post-copy mount changed')
        m.require(m.sha(row['backup']) == BEFORE, 'F9 backup changed')
        m.write_record(OUT/'deployed.json', dict(**record, verified=True,
                      sd_app_sha256=AFTER, core_and_os_unchanged=True,
                      sibling_sources_and_package_unchanged=True, physical_speedup_unverified=True))
    except BaseException as error:
        failures = m.restore([row], m.CARD, guard)
        os.sync()
        m.write_record(OUT/'failure.json', dict(error=repr(error), rollback_failures=failures,
                       destination=str(TARGET), backup=row['backup']))
        raise
    return dict(action='deployed', marker='PERF F10 KICK', sd_app_sha256=AFTER,
                backup=row['backup'], core_and_os_unchanged=True,
                sibling_sources_and_package_unchanged=True)


def verify():
    identity = preflight(AFTER)
    record = m.read(OUT/'deployed.json')
    m.require(record['plan']['mount'] == identity, 'Mount differs from deployment record')
    m.require(record['script_sha256'] == m.sha(Path(__file__)), 'Trial deployment script changed')
    m.require(record['review_sha256'] == REVIEW_SHA and record['sd_app_sha256'] == AFTER,
              'Deployment record differs from this trial')
    m.require(m.sha(record['file']['backup']) == BEFORE, 'F9 backup differs')
    return dict(action='verified', sd_app_sha256=AFTER, backup_sha256=BEFORE,
                core_and_os_unchanged=True, sibling_sources_and_package_unchanged=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'deploy', 'verify'])
    args = parser.parse_args()
    print(json.dumps(globals()[args.action](), indent=2))
