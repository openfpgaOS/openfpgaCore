#!/usr/bin/env python3
"""One private map-only MAX_FANOUT4 screen on the unchanged v7 launch register.

All 115 frozen files and saved seed2 remain byte-identical to compact v7.
Only the generated job QSF adds one exact instance assignment. No fit, CPU
regeneration, functional source change, publication or production write.
"""
import copy
import difflib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import phase10_compact_lod5_fit as common
from phase10_lod5_queues_map import entities

ROOT, P = common.ROOT, common.P
BASE = P / 'compact-lod5-core-v7'
OUT = P / 'compact-lod5-v7-lanefan4-v1'
QSF = 'src/fpga/targets/pocket/ap_core.qsf'
TARGET = 'ic|cpu|cpu|execute_ctrl3_up_LANE_SEL_lane0'
ASSIGNMENT = f'set_instance_assignment -name MAX_FANOUT 4 -to {TARGET}'
BASE_QSF = BASE / 'fit-seed2/jobs/pocket-os30-s2' / QSF
BASE_MAP = BASE_QSF.parent / 'output_files/ap_core.map.rpt'
sha, save = common.sha, common.save


def inventory(directory):
    return {str(p.relative_to(directory)):sha(p)
            for p in sorted(directory.rglob('*')) if p.is_file()}


def verify_evidence(record):
    for name, digest in record.get('evidence_sha256', {}).items():
        assert sha(ROOT / name) == digest, name


def prepare():
    parent = common.checked_stage(BASE)
    assert len(parent['source_sha256']) == 115
    assert parent['seed'] == 2 and not parent['seed_sweep']
    review = json.loads((BASE / 'independent-review.json').read_text())
    assert review['passed_for_resource_fit'] and review['component_and_coupled_proofs_complete']
    assert review['prepared_sha256'] == sha(BASE / 'prepared.json')
    for record in (parent, review, json.loads((BASE / 'final-manifest.json').read_text())):
        verify_evidence(record)
    assert f'reg                 {TARGET.split("|")[-1]};' in (BASE / 'frozen' / common.CPU).read_text()
    assert ASSIGNMENT not in BASE_QSF.read_text()
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    assert inventory(OUT / 'frozen') == parent['source_sha256']
    evidence = [BASE / 'prepared.json', BASE / 'independent-review.json', BASE / 'final-manifest.json',
                BASE / 'map-core/result.json', BASE / 'fit-seed2/inventory.json', BASE_QSF, BASE_MAP,
                P / 'compact-timing-audit-v7/manifest.json', Path(__file__), Path(common.__file__),
                ROOT / 'tools/sweep_target_matrix.py']
    save(OUT / 'prepared.json', dict(scope=__doc__, source_sha256=parent['source_sha256'],
        only_changed_files=[], effective_job_qsf_addition=ASSIGNMENT, exact_target=TARGET,
        parent_prepared_sha256=sha(BASE / 'prepared.json'), seed=2, saved_seed=2,
        seed_sweep=False, exactly_one_map=True, full_fit_authorized=False,
        rtl_byte_identical_to_parent=True, all_frozen_files_byte_identical_to_parent=True,
        cpu_cache_kib=128, instruction_cache_kib=32, configured_mhz=100,
        physical_synthesis_options_unchanged=True, deployment_ready=False,
        recipe_sha256=sha(Path(__file__)),
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence}))
    shutil.copyfile(__file__, OUT / 'recipe.py')
    print(OUT)


def checked():
    prep = common.checked_stage(OUT)
    assert inventory(OUT / 'frozen') == prep['source_sha256'] == common.checked_stage(BASE)['source_sha256']
    assert prep['recipe_sha256'] == sha(Path(__file__)) == sha(OUT / 'recipe.py')
    assert prep['effective_job_qsf_addition'] == ASSIGNMENT
    assert prep['seed'] == 2 and not prep['full_fit_authorized']
    verify_evidence(prep)
    return prep


def map_only():
    prep = checked()
    d = OUT / 'map-core'
    d.mkdir(exist_ok=False)
    sys.path.insert(0, str(ROOT / 'tools'))
    import sweep_target_matrix as sweep
    row = copy.deepcopy(json.loads((BASE / 'fit-seed2/inventory.json').read_text())[0])
    assert row['seed'] == 2 and row['cpu_sha256'] == prep['source_sha256'][common.CPU]
    directory, project = sweep.prepare(OUT / 'frozen', d / 'job', row, 2, 4)
    qsf = directory / (project + '.qsf')
    before = qsf.read_text()
    assert before == BASE_QSF.read_text()
    after = before + '\n' + ASSIGNMENT + '\n'
    qsf.write_text(after)
    assert re.findall(r'^set_global_assignment -name SEED (\d+)$', after, re.M) == ['2']
    for name, digest in prep['source_sha256'].items():
        if name != QSF:
            assert sha(d / 'job' / name) == digest, name
    (OUT / 'effective-qsf.patch').write_text(''.join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), fromfile='v7/' + QSF, tofile='lanefan4/' + QSF)))
    cmd = sweep.docker(d, 'pocket', directory, ['quartus_map', project])
    save(d / 'invocation.json', dict(command=cmd, qsf_sha256=sha(qsf),
        parent_qsf_sha256=sha(BASE_QSF), prepared_sha256=sha(OUT / 'prepared.json'),
        effective_qsf_only_change=ASSIGNMENT, seed=2, fitter_invoked=False))
    with (d / 'map.log').open('x') as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    save(d / 'completion.json', dict(returncode=result.returncode, fitter_invoked=False,
                                    log_sha256=sha(d / 'map.log')))
    assert result.returncode == 0, result.returncode
    print('Map complete; inspect reports before sealing.')


def seal():
    prep = checked()
    d = OUT / 'map-core'
    assert json.loads((d / 'completion.json').read_text())['returncode'] == 0
    qsf = d / 'job' / QSF
    assert qsf.read_text() == BASE_QSF.read_text() + '\n' + ASSIGNMENT + '\n'
    report = qsf.parent / 'output_files/ap_core.map.rpt'
    text = report.read_text(errors='replace')
    old = BASE_MAP.read_text(errors='replace')
    assert 'Analysis & Synthesis was successful' in text
    a, b = entities(BASE_MAP), entities(report)
    delta = {n:{k:int(b[n][k].split()[0].replace(',',''))-int(a[n][k].split()[0].replace(',',''))
              for k in ('aluts','registers','memory_bits','dsp')} for n in sorted(a.keys() & b.keys())}
    name = TARGET.split('|')[-1]
    mentions = [line.strip() for line in text.splitlines() if name in line]
    copies = sorted(set(re.findall(re.escape(name)+r'~SynDup(?:_\d+)?', text)))
    def ignored(s):
        # Preserve complete report sections; source-path prefixes are intentionally visible.
        pattern = r'; (?:Ignored|Unmatched|Illegal) [^\n]*Assignments[^\n]*'
        sections = []
        for match in re.finditer(pattern, s):
            tail = s[match.start():]
            sections.append(tail.split('\n\n', 1)[0])
        return sections
    save(d / 'result.json', dict(passed_map=True, full_fit_attempted=False, deployment_ready=False,
        baseline=a, candidate=b, delta=delta, target=TARGET, assignment=ASSIGNMENT,
        target_report_mentions=mentions, named_synthesis_duplicates=copies,
        baseline_ignored_assignment_sections=ignored(old), candidate_ignored_assignment_sections=ignored(text),
        parent_report_sha256=sha(BASE_MAP), candidate_report_sha256=sha(report),
        prepared_sha256=sha(OUT / 'prepared.json'), qsf_sha256=sha(qsf),
        scope='One exact-source synthesis screen. Named duplicates and warnings require source-specific review; no fitted ALM/LAB or timing claim.'))
    evidence = [OUT / 'prepared.json', OUT / 'recipe.py', OUT / 'effective-qsf.patch',
                d / 'invocation.json', d / 'completion.json', d / 'map.log', d / 'result.json', qsf, report]
    save(OUT / 'manifest.json', dict(passed_map=True, full_fit_attempted=False, deployment_ready=False,
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence}))
    print(json.dumps(dict(delta=delta, duplicates=copies, manifest_sha256=sha(OUT/'manifest.json')), indent=2))


if __name__ == '__main__':
    assert sys.argv[1:] in (['prepare'], ['map'], ['seal'])
    {'prepare':prepare, 'map':map_only, 'seal':seal}[sys.argv[1]]()
