#!/usr/bin/env python3
"""Independent v7 plus final-fetch-only composition gate. Never invokes Quartus."""
import ast
import json
from pathlib import Path
import re
import shutil
from phase10_compact_v7_review import ROOT, P, sha, bind, verify, evidence

OUT = P / 'compact-lod5-core-v14'
BASE = P / 'compact-lod5-core-v7'
COMP = P / 'cpu-fetch-v7-v12'
NORMAL = P / 'normal-build-integration-v12'
CPU = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
EXPECTED = 'ca6a2cb822978bf3ad6223397bc5622f0149a550237fb8096178a5087bc76d40'


def removed_registers(path):
    text = path.read_text().split('; Registers Removed During Synthesis', 1)[1]
    text = text.split('; Removed Registers Triggering', 1)[0]
    result = {}
    for line in text.splitlines():
        fields = [x.strip() for x in line.split(';')[1:-1]]
        if len(fields) >= 2 and '|' in fields[0]:
            result[fields[0]] = fields[1:]
    return result


def main():
    assert not (OUT / 'independent-review.json').exists()
    assert not (OUT / 'fit-seed2').exists()
    prep = bind(OUT / 'prepared.json')
    parent = bind(BASE / 'prepared.json')
    assert set(prep['source_sha256']) == set(parent['source_sha256'])
    for name, digest in prep['source_sha256'].items():
        assert sha(OUT / 'frozen' / name) == digest, name
        assert sha(BASE / 'frozen' / name) == parent['source_sha256'][name], name
    for name, digest in prep['evidence_sha256'].items():
        assert sha(ROOT / name) == digest, name
    changed = sorted(name for name, digest in prep['source_sha256'].items()
                     if digest != parent['source_sha256'][name])
    assert changed == prep['only_changed_files'] == [CPU]
    assert prep['source_sha256'][CPU] == sha(COMP / 'VexiiRiscv.v') == EXPECTED
    assert parent['source_sha256'][CPU] == '8fca28712f731dab4b8c81c3e57ddf2529c6bf4222c292f120e4108c758689f1'
    assert prep['parent_prepared_sha256'] == sha(BASE / 'prepared.json')
    assert prep['seed'] == 2 and not prep['seed_sweep']
    assert prep['configured_mhz'] == 100 and prep['cpu_cache_kib'] == 128 and prep['instruction_cache_kib'] == 32

    proofs = [
        'compact-lod5-core-v7/independent-review.json', 'compact-lod5-core-v7/final-manifest.json',
        'compact-timing-audit-v7/manifest.json', 'cpu-fetch-v7-v12/manifest.json',
        'cpu-fetch-v7-source-review-v12/manifest.json', 'cpu-os30-repro-v10/manifest.json',
        'cpu-os30-regenerate-v10/manifest.json', 'normal-build-integration-v12/manifest.json',
        'cache-write-lod5-coupled-v2/manifest.json', 'load-submit-coupled-v1/manifest.json',
        'load-submit-delivery-v1/manifest.json', 'load-submit-delivery-review-v1/manifest.json',
    ]
    for name in proofs:
        verify(P / name)
    for name in [
        'cpu-fetch-v7-v12/proof/result.json', 'cpu-fetch-v7-v12/native/result.json',
        'cpu-fetch-v7-v12/native/extra/result.json', 'cpu-fetch-v7-v12/result.json',
        'cpu-fetch-v7-source-review-v12/review.json', 'cpu-os30-repro-v10/checks/result.json',
        'cpu-os30-regenerate-v10/result.json', 'normal-build-integration-v12/result.json',
        'cache-write-lod5-coupled-v2/result.json', 'load-submit-coupled-v1/result.json',
    ]:
        assert bind(P / name)['passed'], name
    assert bind(P / 'cpu-os30-regenerate-v10/result.json')['output_sha256'] == EXPECTED
    source_review = bind(P / 'cpu-fetch-v7-source-review-v12/review.json')
    assert source_review['exact_v7_plus_final_fetch_only']
    assert source_review['candidate_sha256'] == EXPECTED
    assert source_review['original_dispatch_and_sequential_statements_byte_identical']

    mapped = bind(OUT / 'map-core/result.json')
    invocation = bind(OUT / 'map-core/invocation.json')
    assert mapped['passed_map'] and not mapped['full_fit_attempted']
    assert not invocation['fitter_invoked'] and invocation['seed'] == 2
    assert mapped['prepared_sha256'] == sha(OUT / 'prepared.json') == invocation['prepared_sha256']
    qsf = OUT / 'map-core/job/src/fpga/targets/pocket/ap_core.qsf'
    assert sha(qsf) == invocation['qsf_sha256']
    bind(qsf)
    assert re.findall(r'^set_global_assignment -name SEED (\d+)$', qsf.read_text(), re.M) == ['2']
    parent_qsf = BASE / 'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf'
    assert qsf.read_bytes() == parent_qsf.read_bytes()
    bind(parent_qsf)
    report = OUT / 'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt'
    parent_report = BASE / 'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/output_files/ap_core.map.rpt'
    assert sha(report) == mapped['candidate_report_sha256']
    assert sha(parent_report) == mapped['baseline_report_sha256']
    bind(report)
    bind(parent_report)
    old_removed, new_removed = removed_registers(parent_report), removed_registers(report)
    gpu_differences = {name: dict(parent=old_removed.get(name), candidate=new_removed.get(name))
                       for name in sorted(old_removed.keys() | new_removed.keys())
                       if '|gpu_core:gpu|' in name and old_removed.get(name) != new_removed.get(name)}
    assert gpu_differences == {'core_top:ic|gpu_core:gpu|spanprod_header_supported':
                               {'parent': ['Lost fanout'], 'candidate': None}}
    assert mapped['delta']['VexiiRiscv']['registers'] == 0
    assert mapped['delta']['gpu_core']['registers'] == mapped['delta']['apf_top']['registers'] == 1

    normal = bind(NORMAL / 'checks/selected-binding.json')
    normal_prep = bind(NORMAL / 'prepared.json')
    normal_result = bind(NORMAL / 'result.json')
    assert normal['mapped_qsf_sha256'] == sha(qsf)
    assert normal['selected_prepared_sha256'] == sha(OUT / 'prepared.json')
    assert len(normal_prep['overrides']) == 6
    assert normal_result['other_variants_unchanged'] and normal_result['normal_and_frozen_generators_agree']
    assert normal_prep['proposed_sha256'][CPU] == EXPECTED
    for name in normal_prep['proposed_sha256']:
        assert not name.endswith(('/os30.seed', '/os30.seed.src'))

    stage = Path(__file__).with_name('phase10_compact_fetch_only_fit.py')
    generic = Path(__file__).with_name('phase10_compact_lod5_fit.py')
    mapper = Path(__file__).with_name('phase10_compact_datapath_fit.py')
    for path in (stage, generic, mapper, Path(__file__).with_name('phase10_compact_v7_review.py'), Path(__file__)):
        bind(path)
    assert prep['recipe_sha256'] == sha(generic)
    assert prep['stage_recipe_sha256'] == sha(stage)
    assert (OUT / 'stage-recipe.py').read_bytes() == stage.read_bytes()
    stage_source = stage.read_text()
    assert 'f.BASE, f.OUT = BASE, OUT' in stage_source
    assert "review['component_and_coupled_proofs_complete']" in stage_source
    assert "review['prepared_sha256'] == sha(OUT / 'prepared.json')" in generic.read_text()
    tree = ast.parse(generic.read_text())
    fitrun = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'fitrun')
    argv = next(n.value for n in ast.walk(fitrun) if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
        and t.value.id == 'sys' and t.attr == 'argv' for t in n.targets))
    strings = [n.value for n in ast.walk(argv) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert strings == ['--target', 'pocket', '--variant', 'os30', '--seed-offsets', '0', '--jobs', '1', '--processors', '4', '--output', 'fit-seed2']
    assert (OUT / 'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes() == (BASE / 'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()

    proof_dir = OUT / 'independent-review-proof'
    proof_dir.mkdir(exist_ok=False)
    (proof_dir / 'mapped-register-delta.json').write_text(json.dumps(dict(
        passed=True, added_architectural_state=False, gpu_source_unchanged=True,
        gpu_removed_register_differences=gpu_differences, map_delta=mapped['delta'],
        explanation='The same declared spanprod_header_supported register was removed for Lost fanout in v7 and is retained in v14. CPU/source sequential logic is unchanged.'), indent=2) + '\n')
    shutil.copy2(__file__, OUT / 'independent-review-recipe.py')
    for path in (proof_dir / 'mapped-register-delta.json', OUT / 'independent-review-recipe.py', OUT / 'stage-recipe.py', OUT / 'integration.patch'):
        bind(path)
    evidence.update(prep['evidence_sha256'])
    review = dict(
        passed_for_resource_fit=True, component_and_coupled_proofs_complete=True,
        exact_source_binding_passed=True, deployment_ready=False,
        reviewer='triangle_eligibility_impl', prepared_sha256=sha(OUT / 'prepared.json'),
        parent_review_sha256=sha(BASE / 'independent-review.json'), only_changed_files=changed,
        seed=2, seed_sweep=False, exactly_one_fit=True, physical_options_unchanged=True,
        configured_clock_mhz=100, required_routed_mhz_greater_than=90,
        cpu_instruction_cache_kib=32, cpu_data_cache_kib=128, map_summary=mapped['delta'],
        findings=[
            'Only CPU ca6a2cb8 changes from exact compact-v7. Reversing one final-fetch hunk reproduces CPU8fca. All other source closure entries, including GPU, geometry, cache, SDRAM, wrapper, clocks, QSF and constraints, remain byte-identical.',
            'The direct factor distributes the original final-fetch Boolean equation. Its two new kept wires are combinational; every original dispatch/fire/issue/hazard/reservation equation, register update, data/trap capture and reset/flush priority remains unchanged. Later enable, reservation, writeback and other v8-v13 revisions are excluded.',
            'Literal original/new RTL matches all32768 independent control combinations; dropping the hazard detects7 mismatches. All10 direct v7 native UART/cycle pairs match and14,519,017 live original fetch checks pass, alongside inherited ready/arithmetic/decoder/forwarding checks.',
            'Checked os30-only reproduction and actual isolated offline Scala generation produce exact ca6a2cb8. Normal-build-v12 uses six private os30 overrides and the original saved seed2, matches the actual mapped QSF/source/defines/constraints and keeps other variants unchanged.',
            'The map passes with CPU-14ALUT/0FF and whole-4ALUT/+1FF. The unchanged GPU has+39ALUT/+1FF; the one register is existing spanprod_header_supported retained instead of its old Lost fanout removal. This is synthesis optimization variation, not new declared state or a fitted-resource claim.',
            'The effective map QSF is byte-identical to the actual parent v7 seed2 QSF. The minimal adapter explicitly uses v7 inventory for map and fit; the inherited runner rehashes prepared/review evidence and selects one seed-offset0 job, with no clock or physical-option change.',
            'Existing GPU/LOAD/memory coupled proofs and qualified zero-cycle component bridges remain bound. Selected software remains the tested load-submit-overlap-v1 normal ELF; no new full-native-game or Pocket FPS claim is made.',
        ],
        limits=[
            'Authorize only the parent-selected single saved-seed2 AREA resource/timing fit. This reviewer invoked no Quartus, fitter, simulation or generation.',
            'Both routed slow corners must exceed90MHz for the comparative target; configured100MHz timing closure is a separate condition. No timing gain or deployment recommendation follows from synthesis or functional proofs.',
            'Proof scopes are arbitrary binary combinational controls and bounded native/coupled corpora, not universal four-state or whole-game native equivalence.',
            'No production promotion, sibling source change or SD write is authorized.',
        ], evidence_sha256=evidence)
    with (OUT / 'independent-review.json').open('x') as fp:
        json.dump(review, fp, indent=2)
        fp.write('\n')
    print(sha(OUT / 'independent-review.json'))


if __name__ == '__main__':
    main()
