#!/usr/bin/env python3
"""Compose zero-cycle timing fixes over compact v2; map before one reviewed fit."""
import copy
import difflib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
import phase10_compact_lod5_fit as fit
from phase10_lod5_queues_map import entities

ROOT, P = fit.ROOT, fit.P
BASE = P / 'compact-lod5-core-v2'
OUT = P / 'compact-lod5-core-v3'
COMP = P / 'unified-load-components-v3'
CPU_COMP = P / 'cpu-fp-rs3-mux-v1'
GPU_PROOF = P / 'gpu-request-review-v10d/manifest.json'
SOURCES = {
    fit.CPU: CPU_COMP / 'VexiiRiscv.v',
    'src/fpga/common/gpu_core.v': P / 'gpu-request-mux-v10/gpu_core.v',
    'src/fpga/common/geometry_raw_load.v': COMP / 'frozen/geometry_raw_load.v',
    'src/fpga/common/geometry_transform_light.v': COMP / 'frozen/geometry_transform_light.v',
}


def prepare():
    parent = fit.checked_stage(BASE)
    assert json.loads((BASE / 'result.json').read_text())['whole_core_fit_successful']
    gpu = json.loads(GPU_PROOF.read_text())
    assert gpu['passed_for_map_only']
    assert gpu['candidate_sha256'] == fit.sha(SOURCES['src/fpga/common/gpu_core.v'])
    for n, h in gpu['evidence_sha256'].items():
        assert fit.sha(ROOT / n) == h, n
    for n, h in json.loads((COMP / 'manifest.json').read_text()).items():
        assert fit.sha(COMP / n) == h, n
    assert json.loads((COMP / 'wrapper/result.json').read_text())['passed']
    assert all(v['passed'] for v in json.loads((COMP / 'arithmetic/result.json').read_text()).values())
    cpu = json.loads((CPU_COMP / 'prepared.json').read_text())
    assert cpu['original_cpu_sha256'] == parent['source_sha256'][fit.CPU]
    assert cpu['candidate_cpu_sha256'] == fit.sha(SOURCES[fit.CPU])
    assert json.loads((CPU_COMP / 'combinational/result.json').read_text())['passed']
    evidence = [BASE / 'final-manifest.json', GPU_PROOF,
                P / 'gpu-request-mux-v10/divider-proof.json',
                COMP / 'manifest.json', CPU_COMP / 'prepared.json',
                CPU_COMP / 'combinational/result.json',
                P / 'lod5-live-native-v2/manifest.json',
                Path(__file__), Path(fit.__file__)]
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    for n, src in SOURCES.items():
        shutil.copyfile(src, OUT / 'frozen' / n)
    sources = {str(p.relative_to(OUT / 'frozen')): fit.sha(p)
               for p in sorted((OUT / 'frozen').rglob('*')) if p.is_file()}
    changed = sorted(n for n, h in sources.items() if parent['source_sha256'][n] != h)
    assert changed == sorted(SOURCES), changed
    prep = dict(parent, scope=__doc__, source_sha256=sources,
                only_changed_files=changed,
                parent_prepared_sha256=fit.sha(BASE / 'prepared.json'),
                evidence_sha256={str(p.relative_to(ROOT)):fit.sha(p) for p in evidence},
                recipe_sha256=fit.sha(Path(fit.__file__)),
                stage_recipe_sha256=fit.sha(Path(__file__)),
                cpu_native_manifest_required_before_fit=True)
    fit.save(OUT / 'prepared.json', prep)
    shutil.copyfile(__file__, OUT / 'stage-recipe.py')
    shutil.copyfile(fit.__file__, OUT / 'recipe.py')
    patch = ''.join(''.join(difflib.unified_diff(
        (BASE / 'frozen' / n).read_text().splitlines(True),
        (OUT / 'frozen' / n).read_text().splitlines(True),
        fromfile='compact-v2/' + n, tofile='compact-v3/' + n)) for n in changed)
    (OUT / 'integration.patch').write_text(patch)
    print(OUT)


def map_only():
    prep = fit.checked_stage(OUT)
    for n, h in prep['evidence_sha256'].items():
        assert fit.sha(ROOT / n) == h, n
    directory = OUT / 'map-core'
    directory.mkdir(exist_ok=False)
    sys.path.insert(0, str(ROOT / 'tools'))
    import sweep_target_matrix as sweep
    row = copy.deepcopy(json.loads((BASE / 'fit-seed2/inventory.json').read_text())[0])
    row['cpu_sha256'] = prep['source_sha256'][fit.CPU]
    job, project = sweep.prepare(OUT / 'frozen', directory / 'job', row, 2, 4)
    qsf = job / (project + '.qsf')
    assert re.findall(r'^set_global_assignment -name SEED (\d+)$', qsf.read_text(), re.M) == ['2']
    command = sweep.docker(directory, 'pocket', job, ['quartus_map', project])
    fit.save(directory / 'invocation.json', dict(command=command,
        prepared_sha256=fit.sha(OUT / 'prepared.json'), fitter_invoked=False,
        qsf_sha256=fit.sha(qsf), seed=2))
    with (directory / 'map.log').open('x') as f:
        subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True)
    report = job / 'output_files/ap_core.map.rpt'
    baseline = next((BASE / 'fit-seed2/jobs').rglob('ap_core.map.rpt'))
    a, b = entities(baseline), entities(report)
    delta = {n:{k:int(b[n][k].split()[0].replace(',', '')) - int(a[n][k].split()[0].replace(',', ''))
                 for k in ['aluts', 'registers', 'memory_bits', 'dsp']}
             for n in sorted(a.keys() & b.keys())}
    fit.save(directory / 'result.json', dict(passed_map=True, full_fit_attempted=False,
        baseline=a, candidate=b, delta=delta,
        baseline_report_sha256=fit.sha(baseline), candidate_report_sha256=fit.sha(report),
        prepared_sha256=fit.sha(OUT / 'prepared.json'),
        limits='Synthesis counts do not establish fitted ALMs, LABs or Fmax.'))
    print(json.dumps(delta, indent=2))


def fitrun():
    assert json.loads((OUT / 'map-core/result.json').read_text())['passed_map']
    review = json.loads((OUT / 'independent-review.json').read_text())
    # A native-CPU result is deliberately not required to run synthesis, but
    # must be sealed and bound by the independent review before placement.
    n = str((CPU_COMP / 'manifest.json').relative_to(ROOT))
    assert review['evidence_sha256'][n] == fit.sha(CPU_COMP / 'manifest.json')
    fit.OUT = OUT
    fit.fitrun()


if __name__ == '__main__':
    assert sys.argv[1:] in (['prepare'], ['map_only'], ['fit'])
    {'prepare':prepare, 'map_only':map_only, 'fit':fitrun}[sys.argv[1]]()
