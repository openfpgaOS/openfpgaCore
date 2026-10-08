#!/usr/bin/env python3
"""Independent source and evidence gate for one compact-v5 resource fit."""
from pathlib import Path
import ast
import json
import re
import shutil
import phase10_compact_v4_review as v4

ROOT, P, sha = v4.ROOT, v4.P, v4.sha
STAGE, BASE = P / 'compact-lod5-core-v5', P / 'compact-lod5-core-v3'
OUT = P / 'compact-remaining-review-v5'
bind, evidence = v4.bind, v4.evidence


def manifest(p):
    d = json.loads(p.read_text())
    if 'inputs' not in d:
        return v4.manifest(p)
    for name, digest in d['inputs'].items():
        assert sha(ROOT / name) == digest, name
    if 'report_sha256' in d:
        assert sha(p.parent / 'REPORT.txt') == d['report_sha256']
    bind(p)
    return d


def main():
    prep = json.loads((STAGE / 'prepared.json').read_text())
    old = json.loads((BASE / 'prepared.json').read_text())
    assert prep['parent_prepared_sha256'] == sha(BASE / 'prepared.json')
    assert prep['source_sha256'].keys() == old['source_sha256'].keys()
    changed = []
    for name, digest in prep['source_sha256'].items():
        assert sha(STAGE / 'frozen' / name) == digest, name
        if digest != old['source_sha256'][name]:
            changed.append(name)
    expected = {
        'src/fpga/common/gpu_core.v': P / 'gpu-paths-v11/gpu_core.v',
        'src/fpga/common/gpu_color_depth_cache.sv': P / 'cache-write-speculate-v2/gpu_color_depth_cache.sv',
        'src/fpga/targets/pocket/io_sdram.v': P / 'sdram-address-mux-v1/io_sdram.v',
        'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v': P / 'cpu-rvc-control-v3/VexiiRiscv.v',
    }
    top = 'src/fpga/targets/pocket/core_top.v'
    assert set(changed) == set(expected) | {top}
    assert sorted(changed) == prep['only_changed_files']
    for name, source in expected.items():
        assert sha(source) == prep['source_sha256'][name]
        bind(source)
    a, b = (BASE / 'frozen' / top).read_text(), (STAGE / 'frozen' / top).read_text()
    needle = '.REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1)'
    assert a.count(needle) == 1
    assert a.replace(needle, needle + ', .SPECULATE_WRITE_HOT0(1)') == b

    # Independently bind the reduced predicate to the actual emitted parent,
    # not merely to the abstract Boolean test module.
    cpu = P / 'cpu-rvc-control-v3'
    recipe = cpu / 'final-recipe.py'
    constants = {}
    for node in ast.parse(recipe.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ('OLD', 'NEW'):
                constants[node.targets[0].id] = ast.literal_eval(node.value)
    parent_cpu = P / 'cpu-rvc-bits-v2/VexiiRiscv.v'
    text = parent_cpu.read_text()
    assert text.count(constants['OLD']) == 1
    assert text.replace(constants['OLD'], constants['NEW']) == (cpu / 'VexiiRiscv.v').read_text()
    for line in [
        "assign AlignerPlugin_logic_usedMask_0 = 4'b0000;",
        "assign AlignerPlugin_logic_extractors_0_first = 1'b1;",
    ]:
        assert line in text
    for source in (recipe, parent_cpu, cpu / 'proof/probe.sv', cpu / 'proof/main.cpp'):
        bind(source)

    for name, digest in prep['evidence_sha256'].items():
        assert sha(ROOT / name) == digest, name
        bind(ROOT / name)
    for name in [
        'cpu-rvc-bits-v2/manifest.json', 'cpu-rvc-control-v3/manifest.json',
        'cpu-os30-repro-v2/manifest.json', 'cpu-os30-regenerate-v2/manifest.json',
        'gpu-paths-review-v11/manifest.json',
        'cache-write-speculate-v1/manifest.json', 'cache-write-review-v1/manifest.json',
        'cache-write-speculate-source-review-v1/manifest.json',
        'cache-write-speculate-v2/manifest.json', 'cache-write-lod5-coupled-v2/manifest.json',
        'sdram-address-review-v1/manifest.json', 'sdram-address-map-review-v1/manifest.json',
        'normal-build-integration-v2/manifest.json',
    ]:
        manifest(P / name)
    for name in [
        'cpu-rvc-control-v3/proof/result.json', 'cpu-rvc-control-v3/native/result.json',
        'gpu-paths-review-v11/result.json', 'gpu-paths-review-v11/paths-result.json',
        'cache-write-speculate-v2/protocol/result.json', 'cache-write-lod5-coupled-v2/result.json',
    ]:
        p = P / name
        assert json.loads(p.read_text())['passed']
        bind(p)
    coupled = json.loads((P / 'cache-write-lod5-coupled-v2/result.json').read_text())

    report = STAGE / 'map-core/result.json'
    r = json.loads(report.read_text())
    assert r['passed_map'] and not r['full_fit_attempted']
    assert r['prepared_sha256'] == sha(STAGE / 'prepared.json')
    mr = STAGE / 'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt'
    assert sha(mr) == r['candidate_report_sha256']
    inv = STAGE / 'map-core/invocation.json'
    i = json.loads(inv.read_text())
    assert i['seed'] == 2 and not i['fitter_invoked']
    qsf = STAGE / 'map-core/job/src/fpga/targets/pocket/ap_core.qsf'
    assert sha(qsf) == i['qsf_sha256']
    assert re.findall(r'^set_global_assignment -name SEED (\d+)$', qsf.read_text(), re.M) == ['2']
    assert prep['seed'] == 2 and not prep['seed_sweep']
    assert prep['cpu_cache_kib'] == 128 and prep['instruction_cache_kib'] == 32
    assert prep['configured_mhz'] == 100
    runner = ROOT / 'tools/experiments/os30_fps25_20261005/phase10_compact_final_paths_fit.py'
    inherited = ROOT / 'tools/experiments/os30_fps25_20261005/phase10_compact_lod5_fit.py'
    assert sha(runner) == prep['stage_recipe_sha256']
    assert sha(inherited) == prep['recipe_sha256']
    assert "review['component_and_coupled_proofs_complete']" in runner.read_text()
    assert "review['passed_for_resource_fit'] and not review['deployment_ready']" in inherited.read_text()
    assert "review['prepared_sha256'] == sha(OUT / 'prepared.json')" in inherited.read_text()
    assert "'--seed-offsets', '0', '--jobs', '1'" in inherited.read_text()
    for p in [STAGE / 'prepared.json', BASE / 'prepared.json', STAGE / 'integration.patch',
              report, mr, inv, qsf, runner, inherited, Path(v4.__file__), Path(__file__)]:
        bind(p)

    OUT.mkdir(exist_ok=False)
    shutil.copyfile(__file__, OUT / 'recipe.py')
    result = dict(
        passed_source_closure=True, passed_for_resource_fit=True,
        component_and_coupled_proofs_complete=True, deployment_ready=False,
        prepared_sha256=sha(STAGE / 'prepared.json'), only_changed_files=changed,
        combined_map_delta=r['delta']['apf_top'],
        findings=[
            'Exactly four RTL files plus one cache instance parameter differ from routed compact-v3. All other sources, clocks, constraints, settings and 128KiB/32KiB CPU caches are unchanged.',
            'CPU scanner priority chooses the first valid slice. The sole extractor has first=1 and usedMask_0=0. Scanner0 consumes saved bit0 and optionally bit1 without requiring a live fetch; scanner1 consumes only saved bit1 and requires live fetch only for a wide instruction; later scanners consume neither saved bit. This derives the new predicate from actual emitted logic, including halt and empty-buffer cases. All4096 binary combinations and six native output/cycle-exact pairs pass. No four-state X-equivalence claim.',
            'Cache full-strobe W_WAIT consumption is safe because all32 data and8 valid/dirty bits are overwritten. The captured slot/address and metadata are already owned in both hot1-hit and post-WCLEAR paths. Partial/zero/absent W waits for the correct data_q. Accepted same-line full beats prefetch the next word, preserving a following partial beat; crossings still enter LOOK_WAIT. AW/AR/flush cannot overtake an owned write. Reset ownership is unchanged.',
            'Cache default parameter0 is cycle-exact with its parent. Twelve poisoned-RAM protocol runs cover hot1/WCLEAR, one/two/seventeen beats, full/partial/zero masks, stalls, line crossing, reset and simultaneous live address changes. Sixteen matched actual color/depth buffers and complete scene histories are exact in coupled warm/cold runs.',
            'Cache v2 removes v1 extra frame cost: matched recycler warm34.988370 to34.957095ms and cold49.227475 to49.214986ms. These are effectively unchanged frame costs, not a substantial standalone speedup.',
            'GPU arithmetic/request updates retain command outputs and cycles with native/boundary/ownership proofs. SDRAM changes only address decode and preserves every consumed address bit, all other outputs and cycles across both all-bank physical schedules.',
            'Checked normal-build source closure and actual isolated Scala regeneration reproduce the exact selected CPU. Other variants and production inputs remain unchanged.',
            'The reviewed runner binds all source/evidence hashes and authorizes one saved seed2 AREA fit. Root owns its invocation.'],
        limits=[
            'No fitter was invoked by this review. Map counts cannot establish fitted area or physical timing.',
            'The unchanged CPU hazard arm and physical placement remain timing risks. The new predicate does not prove the >90MHz target.',
            'Coupled frames use the previously qualified raster/geometry model and modeled CPU/cache, with separate exact zero-cycle bridges for the newer GPU/CPU/SDRAM rewrites. This is not a full current-CPU whole-game RTL simulation or a Pocket FPS measurement.',
            'No production promotion or deployment is authorized by this gate.'],
        evidence_sha256=evidence,
    )
    (OUT / 'review.json').write_text(json.dumps(result, indent=2) + '\n')
    (OUT / 'manifest.json').write_text(json.dumps({p.name: sha(p) for p in OUT.iterdir() if p.is_file()}, indent=2) + '\n')
    gate = dict(result)
    gate['evidence_sha256'] = dict(evidence)
    for p in (OUT / 'review.json', OUT / 'manifest.json'):
        gate['evidence_sha256'][str(p.relative_to(ROOT))] = sha(p)
    with (STAGE / 'independent-review.json').open('x') as f:
        json.dump(gate, f, indent=2)
        f.write('\n')
    print('review', sha(OUT / 'manifest.json'))
    print('gate', sha(STAGE / 'independent-review.json'))


if __name__ == '__main__':
    main()
