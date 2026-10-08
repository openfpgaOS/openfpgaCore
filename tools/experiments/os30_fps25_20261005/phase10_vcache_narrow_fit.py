#!/usr/bin/env python3
"""One private retained-A os30 seed2 fit of the validated narrow vertex cache."""
import copy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tools'))
import sweep_target_matrix as fit
BASE = ROOT/'docs/measurements/os30-dual-20261004/fit-single128-v1'
CANDIDATE = ROOT/'docs/measurements/os30-fps25-20261005/phase10/vcache-narrow-v1'
OUT = CANDIDATE/'fit-seed2'
GPU = 'src/fpga/common/gpu_core.v'
CPU = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
CPU_A = '33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'


def inventory(**kwargs):
    rows = json.loads((BASE/'inventory.json').read_text())
    assert len(rows) == 1
    row = rows[0]
    assert (row['target'], row['variant'], row['seed'], row['cpu_sha256']) == ('pocket','os30',2,CPU_A)
    return copy.deepcopy(rows)


def freeze(out, rows):
    expected = json.loads((BASE/'frozen-sources.json').read_text())
    for path,digest in expected.items():
        assert fit.digest(BASE/'frozen'/path) == digest, path
    assert expected[CPU] == CPU_A
    validation = json.loads((CANDIDATE/'replay-results.json').read_text())
    assert validation['passed'] and len(validation['comparisons']) == 24
    prepared = json.loads((CANDIDATE/'prepared.json').read_text())
    assert fit.digest(CANDIDATE/'gpu_core.v') == prepared['candidate_sha256']
    dest = out/'frozen'
    shutil.copytree(BASE/'frozen', dest)
    shutil.copy2(CANDIDATE/'gpu_core.v', dest/GPU)
    actual = {str(p.relative_to(dest)):fit.digest(p) for p in dest.rglob('*') if p.is_file()}
    assert set(expected) == set(actual)
    assert [p for p in expected if expected[p] != actual[p]] == [GPU]
    fit.save(out/'inventory.json',rows)
    fit.save(out/'frozen-sources.json',actual)
    fit.save(out/'experiment.json',dict(scope=__doc__, seed=2, seed_sweep=False,
        configured_mhz=100, production_modified=False, only_changed_file=GPU,
        before_sha256=expected[GPU],after_sha256=actual[GPU],
        original_manifest_sha256=fit.digest(BASE/'frozen-sources.json'),
        validation_sha256=fit.digest(CANDIDATE/'replay-results.json'),
        cpu_sha256=CPU_A, expected_vertex_cache_m10k_before=5,expected_after=1,
        limitations=['Private resource/timing proof only; no deployment.', 'No hardware FPS claim or raw geometry frontend included.']))
    return dest


if __name__ == '__main__':
    assert sys.argv[1:] == ['run']
    fit.inventory=inventory
    fit.freeze=freeze
    sys.argv=[sys.argv[0],'--target','pocket','--variant','os30','--seed-offsets','0','--jobs','1','--processors','4','--output',str(OUT)]
    fit.main()
