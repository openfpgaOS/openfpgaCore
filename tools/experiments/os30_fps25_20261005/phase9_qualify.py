#!/usr/bin/env python3
"""Bind the reviewed small F11 cleanup for local publication only."""
import json
from pathlib import Path

from phase9_experiment import CAMP, HERE, INPUTS, OUT, ROOT, sha, write

def main():
    parent = CAMP/'phase7/QUALIFIED.json'
    final_path = OUT/'independent-review/FINAL.json'
    final = json.loads(final_path.read_text())
    assert final['passed'] and final['independent_review_passed']
    assert final['candidate'] == 'checks-state-f11' and not final['target_verified']
    comparisons = final['coupled_comparisons']
    assert len(comparisons) == 8
    assert {(r['mode'], r['start'], r['traffic']) for r in comparisons} == {
        (mode, start, traffic) for mode in ('normal', 'diagnostic')
        for start in (360, 600) for traffic in (1, 2)}
    assert all(r['throughput_change_percent'] > 0 for r in comparisons)
    candidate = OUT/final['candidate']
    assert set(final['source_sha256']) == set(INPUTS)
    for name, digest in final['source_sha256'].items():
        assert sha(candidate/name) == digest
    for name, field in (('app.elf','normal_elf_sha256'), ('diagnostic.elf','diagnostic_elf_sha256')):
        assert sha(candidate/name) == final[field]
    for mode in ('normal', 'diagnostic'):
        analysis = json.loads((candidate/f'analysis-{mode}.json').read_text())
        assert analysis['passed'] and all(analysis['scene_geometry_culling_exact'].values())
        assert analysis['all721_command_word_counts_exact']
        assert analysis['all8_publication_boundaries_exact']
        assert analysis['gameplay_frames360_through720']['est_cycles']['saving_percent'] > 0
        assert all(w['saving_percent'] > 0 for w in analysis['windows'])
    tree = dict(json.loads(parent.read_text())['source_tree_sha256'])
    assert len(tree) == 6747
    changes = []
    for name, relative in INPUTS.items():
        if tree[relative] != final['source_sha256'][name]:
            changes.append(relative)
        tree[relative] = final['source_sha256'][name]
    assert set(changes) == {INPUTS[n] for n in ('gfx_pc.c','gfx_gpu.c','perf_pocket.c')}
    evidence = dict(final['evidence_sha256'])
    for path in (parent, final_path, Path(__file__), HERE/'phase9_publish.py', HERE/'phase2_publish.py'):
        evidence[str(path.relative_to(ROOT))] = sha(path)
    for relative, digest in evidence.items():
        path = ROOT/relative
        assert path.is_relative_to(ROOT) and '..' not in Path(relative).parts
        assert sha(path) == digest, relative
    record = dict(candidate=final['candidate'], parent='phase7/combined-v1/F9',
        qualified_for_device_trial=True, independent_review_passed=True,
        target_verified=False, substantial_performance_improvement=False,
        physical_speedup_verified=False, planned_publication_scope='Local ../SM64 only; SD untouched',
        source_sha256=final['source_sha256'], source_tree_sha256=tree,
        changed_source_files=changes, normal_elf_sha256=final['normal_elf_sha256'],
        diagnostic_elf_sha256=final['diagnostic_elf_sha256'],
        numerical_contract=final['numerical_contract'],
        validation=final['validation'], coupled_comparisons=final['coupled_comparisons'],
        evidence_sha256=evidence, limits=final['limits'])
    write(OUT/'QUALIFIED.json', record)
    print(json.dumps(dict(qualified=True,candidate=record['candidate'],
        changed_sources=len(changes),evidence_files=len(evidence),
        qualified_sha256=sha(OUT/'QUALIFIED.json'),target_verified=False),indent=2))

if __name__ == '__main__':
    main()
