#!/usr/bin/env python3
"""Independent existing-evidence review; no CAD command or simulation."""
import hashlib
import json
import re
from pathlib import Path

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
C = P / 'cache64-timing-compose-v2'
B = P / 'cpu-cache64-resource-v1'
F = P / 'cache64-timing-fit-v1'
O = P / 'cache64-timing-source-review-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def inventory(root):
    return {str(p.relative_to(root)): sha(p) for p in root.rglob('*') if p.is_file()}


def manifest(p):
    d = read(p)
    for name, expected in d.get('files', {}).items():
        assert sha(p.parent / name) == expected, (p, name)
    for name, expected in d.get('evidence_sha256', {}).items():
        assert sha(R / name) == expected, (p, name)


def hierarchy(path):
    d = {}
    for line in path.read_text(errors='replace').splitlines():
        c = [v.strip() for v in line.split(';')]
        if len(c) == 12 and c[8].startswith('|apf_top') and c[3].split()[0].isdigit():
            d[c[8]] = c[3]
    return d


def write(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def main():
    assert sha(C / 'manifest.json') == 'e74508b1a97b0373bbae9717feabd9ee4b770cd22f63a09edcd1829a42778b7b'
    manifest(C / 'manifest.json')
    comp = read(C / 'prepared.json')
    result = read(C / 'result.json')
    assert result['passed_map'] and result['total_delta']['registers'] == 1
    assert result['total_delta']['logic_aluts'] == -21
    assert result['total_delta']['block_memory_bits'] == result['total_delta']['dsps'] == 0
    assert inventory(C / 'frozen') == comp['source_sha256']
    assert inventory(B / 'frozen') == comp['parent_source_sha256']
    changed = sorted(n for n in comp['source_sha256'] if comp['source_sha256'][n] != comp['parent_source_sha256'][n])
    expected = ['src/fpga/common/audio_mixer.v', 'src/fpga/common/gpu_core.v', 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
    assert changed == expected == comp['changed_sources']
    selected = {
        expected[0]: P / 'audio-ramp-flat-v1/audio_mixer.v',
        expected[1]: P / 'gpu-recip-write-v1/gpu_core.v',
        expected[2]: P / 'cpu-cache64-rvc19-v1/VexiiRiscv.v',
    }
    for name, file in selected.items():
        assert sha(file) == comp['source_sha256'][name]
    qsf = Path('src/fpga/targets/pocket/ap_core.qsf')
    assert (C / 'map-core/job' / qsf).read_bytes() == (B / 'map-core/job' / qsf).read_bytes()
    assert (C / 'frozen/src/fpga/targets/pocket/core_top.v').read_text().count('.WAYS(2), .SET_BITS(6), .WORD_BITS(4)') == 1
    reports = [x / 'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt' for x in [B, C]]
    h0, h1 = map(hierarchy, reports)
    regdiff = {k: {'old': h0[k], 'new': h1[k]} for k in h0 if h0[k] != h1[k]}
    assert set(regdiff) == {'|apf_top', '|apf_top|core_top:ic', '|apf_top|core_top:ic|cram1_controller:psram1', '|apf_top|core_top:ic|cram1_controller:psram1|cram1_phy:phy'}
    assert regdiff['|apf_top|core_top:ic|cram1_controller:psram1|cram1_phy:phy'] == {'old': '133 (133)', 'new': '134 (134)'}
    removed = []
    for report in reports:
        removed.append(set(' '.join(line.split()) for line in report.read_text(errors='replace').splitlines() if 'cram1_controller:psram1' in line and any(k in line for k in ['Merged', 'Removed', 'Lost', 'Constant', 'Stuck'])))
    merge = '; core_top:ic|cram1_controller:psram1|cram1_phy:phy|cram_ub_n ; Merged with core_top:ic|cram1_controller:psram1|cram1_phy:phy|cram_lb_n ;'
    assert removed[0] - removed[1] == {merge} and not removed[1] - removed[0]
    fp = read(F / 'prepared.json')
    assert sha(F / 'prepared.json') == 'da51a16263d08d4e8852d73db8dcfafacfce4d8cfb1f60abebfc3b14a23b0763'
    helper = R / 'tools/experiments/os30_fps25_20261005/phase10_cache64_timing_fit.py'
    assert sha(helper) == fp['recipe_sha256'] == sha(F / 'recipe.py')
    assert inventory(C / 'map-core/job') == fp['mapped_job_sha256'] == inventory(F / 'job')
    assert fp['component_manifest_sha256'] == sha(C / 'manifest.json')
    assert sha(F / 'job' / qsf) == fp['qsf_sha256']
    q = (F / 'job' / qsf).read_text()
    assert re.findall(r'^set_global_assignment -name SEED (\d+)$', q, re.M) == ['2']
    loc = [line for line in q.splitlines() if line.startswith('set_location_assignment ')]
    assert len(loc) == 224 and all('PIN_' in line for line in loc)
    assert not (F / 'fit-seed2').exists()
    assert not list((F / 'job').rglob('*.fit.rpt')) and not list((F / 'job').rglob('*.sta.rpt'))
    assert not (F / 'job' / qsf.parent / 'timing').exists()
    refs = [C / 'manifest.json', C / 'result.json', C / 'prepared.json', F / 'prepared.json', helper] + reports
    for label in ['gpu-recip-write-source-review-v1', 'cache64-timing-native-review-v1', 'audio-ramp-flat-source-review-v1', 'cpu-cache64-rvc19-source-review-v1', 'cpu-cache64-rvc19-native-review-v1']:
        m = P / label / 'manifest.json'
        manifest(m)
        refs.append(m)
    O.mkdir(exist_ok=False)
    (O / 'recipe.py').write_bytes(Path(__file__).read_bytes())
    write(O / 'review.json', dict(passed=True, passed_for_one_fit=True, deployment_ready=False,
        changed_sources=changed, exact_frozen_file_count=len(comp['source_sha256']),
        map_delta=result['total_delta'], mapped_register_hierarchy_delta=regdiff,
        one_register_reason='Unchanged CRAM1 PHY cram_ub_n no longer merged with cram_lb_n; no added RTL state.',
        adapter_review=dict(exact_map_database_copy=True, no_resynthesis=True, ordinary_timing_driven_fit=True,
            seed=2, internal_locations=0, pins=224, source_and_clock_unchanged=True,
            fresh_four_corner_twenty_numeric_checks_required=True, strict_known_corner_failure_only=True),
        findings=[], new_tests_or_CAD_actions=False))
    (O / 'REPORT.txt').write_text(
        'PASS for one ordinary seed-2 resource/timing fit; deployment remains unqualified.\n'
        'Exactly three source files differ from the separately generated I64/D64 parent: CPU compressed bit19, audio ramp function and GPU reciprocal write DATA/enable. All match independently reviewed components. GPU cache stays 8 KiB, QSF/defines/clock constraints and remaining frozen files are unchanged. The combined native renderer outputs and cycles are exact; actual audio proof is separately bound.\n'
        'Map: -21 ALUT, +1 mapped register, +1 estimated ALM, no memory/DSP delta. The sole register delta is unchanged CRAM1 PHY 133 to 134: the prior report merged cram_ub_n with cram_lb_n; the new report retains both. None of the changed components adds architectural state. Mapping estimates do not establish fitted area or timing.\n'
        'The adapter copies the exact fresh mapped job, performs only fit/assembly/STA/corner reporting, preserves seed2 and 224 original pin constraints with no internal locations, and rechecks source inputs. Initial timing products are absent. Qualification requires 20 fresh numeric rows across slow/fast 0/85 C and setup/hold/recovery/removal/pulse width; nonzero corners exit is allowed only for the exact expected timing-failure wrapper. Both slow main-clock Fmax values must exceed90 MHz and hold/recovery/removal/pulse width stay positive. The configured100 MHz setup deficit is reported separately. No fit, source mutation or simulation was performed by this review.\n')
    evidence = {str(p.relative_to(R)): sha(p) for p in refs}
    write(O / 'manifest.json', dict(passed=True, deployment_ready=False,
        files={p.name: sha(p) for p in O.iterdir() if p.is_file()}, evidence_sha256=evidence))
    write(F / 'independent-review.json', dict(passed_for_one_fit=True, prepared_sha256=sha(F / 'prepared.json'),
        deployment_ready=False, exactly_one_fit=True, seed=2, seed_sweep=False,
        component_and_native_proofs_complete=True, source_and_map_binding_passed=True,
        evidence_sha256={str(O.relative_to(R) / 'manifest.json'): sha(O / 'manifest.json'), **evidence}))
    print('SOURCE_REVIEW', sha(O / 'manifest.json'))
    print('FIT_GATE', sha(F / 'independent-review.json'))


if __name__ == '__main__':
    main()
