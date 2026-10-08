#!/usr/bin/env python3
"""Read the isolated DSP proof/report; do not compile, simulate, or fit."""
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'dsp-modadd-component-v3'
OUT = P / 'dsp-modadd-independent-review-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def integer(report, label):
    match = re.search(r';\s*' + re.escape(label) + r'\s*;\s*(\d+)', report)
    assert match, label
    return int(match[1])


def main():
    assert not OUT.exists()
    manifest = json.loads((BASE / 'manifest.json').read_text())
    evidence = {}
    checked = 0
    for key, base in (('files', BASE), ('evidence_sha256', ROOT)):
        for name, digest in manifest.get(key, {}).items():
            assert sha(base / name) == digest, name
            checked += 1
    assert checked > 0
    metrics = {}
    for variant in ('fabric', 'dsp'):
        directory = BASE / variant
        for stage in ('map', 'fit', 'sta'):
            assert json.loads((directory / (stage + '-exit.json')).read_text())['returncode'] == 0
        fit = (directory / 'output_files/ap_core.fit.rpt').read_text(encoding='latin1')
        mapped = (directory / 'output_files/ap_core.map.rpt').read_text(encoding='latin1')
        sta = (directory / 'output_files/ap_core.sta.rpt').read_text(encoding='latin1')
        frequencies = {}
        for temperature in (0, 85):
            part = sta.split(f'; Slow 1100mV {temperature}C Model Fmax Summary', 1)[1]
            match = re.search(r';\s*([\d.]+) MHz\s*;[^\n]+;\s*core\s*;', part)
            assert match
            frequencies[str(temperature)] = float(match[1])
        metrics[variant] = {
            'mapped_aluts': integer(mapped, 'Combinational ALUT usage for logic'),
            'mapped_alm_estimate': integer(mapped, 'Estimate of Logic utilization (ALMs needed)'),
            'needed_alms': integer(fit, 'ALMs needed [=A-B+C]'),
            'placed_alms': integer(fit, '[A] ALMs used in final placement [=a+b+c+d]'),
            'labs': integer(fit, 'Total LABs:  partially or completely used'),
            'registers': integer(fit, 'Total registers'),
            'dsps': integer(fit, 'Total DSP Blocks'),
            'co_packed_logic_and_register_alms': integer(fit, '[a] ALMs used for LUT logic and registers'),
            'register_only_alms': integer(fit, '[c] ALMs used for registers'),
            'slow_fmax_mhz': frequencies,
        }
        for name in ('ap_core.v', 'ap_core.qsf', 'output_files/ap_core.fit.rpt',
                     'output_files/ap_core.map.rpt', 'output_files/ap_core.sta.rpt'):
            path = directory / name
            evidence[str(path.relative_to(ROOT))] = sha(path)
    assert metrics['fabric']['needed_alms'] == 33 and metrics['dsp']['needed_alms'] == 35
    assert metrics['fabric']['placed_alms'] == 41 and metrics['dsp']['placed_alms'] == 44
    assert metrics['fabric']['registers'] == metrics['dsp']['registers'] == 86
    assert metrics['fabric']['dsps'] == 0 and metrics['dsp']['dsps'] == 2
    assert metrics['fabric']['mapped_aluts'] == 44 and metrics['dsp']['mapped_aluts'] == 1
    assert metrics['fabric']['mapped_alm_estimate'] == metrics['dsp']['mapped_alm_estimate'] == 44

    primitive = (BASE / 'phase10_dsp_modadd.v').read_text()
    for required in ('.operation_mode("m27x27")', '.operand_source_max("coef")',
                     '.operand_source_may("preadder")', '.preadder_subtract_a("false")',
                     '.signed_max("true")', '.signed_may("false")',
                     '.output_clock("none")', 'assign sum=product[WIDTH-1:0];'):
        assert required in primitive
    assert re.findall(r'\.[a-z_]+_clock\("([^"]+)"\)', primitive) == ['none'] * 13
    assert len(re.findall(r'\.coef_a_[0-7]\(27\x27sd1\)', primitive)) == 8
    assert primitive.count("{{(26-WIDTH){1'b0}}") == 2
    assert 'always' not in primitive
    sim_log = (BASE / 'vendor-simulation/simulation.log').read_text()
    assert 'Unable to checkout a license' in sim_log
    assert 'Invalid license environment' in sim_log
    gpu = P / 'compact-lod5-core-v7/frozen/src/fpga/common/gpu_core.v'
    assert sha(gpu) == '3c171d36bce942735040d3cdc2d96a5f9959b46b2b5c4896455d1c4763ffa4d1'
    for path in (BASE / 'manifest.json', BASE / 'phase10_dsp_modadd.v',
                 BASE / 'vendor-simulation/compile.log', BASE / 'vendor-simulation/simulation.log',
                 BASE / 'vendor-simulation/tb.sv', gpu, Path(__file__)):
        evidence[str(path.relative_to(ROOT))] = sha(path)
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    result = dict(
        independent_review_passed=True, recommended_for_gpu_integration=False,
        resource_gate_passed=False, vendor_arithmetic_simulation_passed=False,
        source_modulo_equivalence=True, adds_pipeline_state=False,
        full_gpu_area_or_timing_claim=False, deployment_ready=False,
        tools_or_production_actions_invoked=False, rehashed_component_files=checked,
        metrics=metrics, evidence_sha256=evidence)
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text('''Independent DSP modular-adder review: reject this integration screen.

The exact coefficient-one Cyclone V primitive is accepted by Quartus as two
unregistered 27x27 hard DSP blocks. Both inputs are zero-extended low 21/22-bit
values; their unsigned sum fits 23 bits. Multiplying that sum by signed +1 and
retaining low N bits is addition modulo 2^N, including all negative-step bit
patterns. All internal clocks are bypassed. No added cycle or state is proposed.

The six selected GPU accumulator outputs observe only low 21/22 bits. Their
remaining reads are their own recurrences. Induction over original load,
hold, and advance edges proves low-bit equivalence; high bits cannot carry
down. Keeping original external registers and priority would therefore avoid
reintroducing previously pruned high bits. This is a source-level argument,
not a successful vendor arithmetic simulation or a tested GPU integration.

The actual isolated comparison does not show area relief: ALMs needed 33->35,
placed 41->44, LABs 7->9, registers 86->86, DSPs 0->2. Mapped ALUTs 44->1 conceal
unchanged estimated 44 ALMs. Fabric co-packs 22 logic/register ALMs; the DSP
version instead has 43 register-only ALMs. This explains the negative result.
The isolated timing results are bound separately and cannot establish timing
in the near-full GPU. This probe does not prove every possible GPU placement
would lose, but it fails the required evidence gate for spending a full fit.

The actual vendor wrapper/model compilation succeeds; simulation stops before
execution because the required license is unavailable. No behavioral sum stub
is treated as vendor arithmetic verification. The evidence warrants stopping
this candidate without GPU edits, GPU replay, or a full-core fitter run.
This review only read existing sources and reports; it invoked none of those
tools and did not touch production or device state.
''')
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        files={p.name: sha(p) for p in OUT.iterdir() if p.is_file()},
        evidence_sha256=evidence), indent=2) + '\n')
    print(json.dumps(dict(manifest_sha256=sha(OUT / 'manifest.json'), metrics=metrics)))


if __name__ == '__main__':
    main()
