#!/usr/bin/env python3
"""Compose disjoint FP RS1 grouping and direct aligner last-slice classification."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
PATHS = {
    'base': ART / 'aligner-trap-direct-v1/immediate/VexiiRiscv.v',
    'fp': ART / 'fp-rs1-priority-groups-v1/immediate/VexiiRiscv.v',
    'last': ART / 'aligner-last-direct-v1/immediate/VexiiRiscv.v',
}
HASHES = {
    'base': 'ec69d8b6b09f60fe60f03227bb1bad4c19ea841a28e874d8025b6970b1d639c3',
    'fp': 'a0afc808335895240d5034d5b94d26d6c9393c293b383a9cf164e8fc917ff145',
    'last': 'ef386e8e95557fdc39bcac98d179ee4db693180f0fafc49af5aff7f7833bf3c4',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log, **kwargs):
    with log.open('w') as handle:
        subprocess.run(command, check=True, stdout=handle, stderr=subprocess.STDOUT, **kwargs)


def apply(source, changes, reverse=False):
    for a, b in (reversed(changes) if reverse else changes):
        old, new = (b, a) if reverse else (a, b)
        assert source.count(old) == 1
        source = source.replace(old, new, 1)
    return source


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ART / 'fp-last-combined-v1/immediate')
    args = ap.parse_args()
    sources = {}
    for key, path in PATHS.items():
        assert sha(path) == HASHES[key]
        sources[key] = path.read_text()
    base, fp, last = [sources[key] for key in ('base', 'fp', 'last')]
    last_changes = []
    for lane in range(2):
        name = f'AlignerPlugin_logic_buffer_readers_{lane}_lastFromBuffer'
        old = re.findall(r'^  assign ' + name + r' = [^;]+;', base, re.M)
        new = re.findall(r'  // The first usable instruction stays in the buffer unless it starts\n'
                         r'  // beyond it or its boundary slice requires one slice from fetch\.\n'
                         r'  assign ' + name + r' =[^;]+;', last)
        assert len(old) == len(new) == 1
        last_changes.append((old[0], new[0]))
    assert apply(base, last_changes) == last
    dest = 'execute_ctrl1_down_float_RS1_lane0'
    old = re.findall(r'^  assign ' + dest + r' = [^;]+;', base, re.M)
    new = re.findall(r'^  \(\* keep = "true" \*\) wire \[31:0\] fp_rs1_priority_early_data;\n'
                     r'.*?^  assign ' + dest + r' = [^;]+;', fp, re.M | re.S)
    assert len(old) == len(new) == 1
    fp_changes = [(old[0], new[0])]
    assert apply(base, fp_changes) == fp
    candidate = apply(fp, last_changes)
    assert candidate == apply(last, fp_changes)
    assert apply(candidate, last_changes, reverse=True) == fp
    assert apply(candidate, fp_changes, reverse=True) == last
    assert apply(apply(candidate, last_changes, reverse=True), fp_changes, reverse=True) == base
    assert re.findall(r'^  always @\(posedge clk.*?^  end', base, re.M | re.S) == re.findall(
        r'^  always @\(posedge clk.*?^  end', candidate, re.M | re.S)
    # Re-extract the composed FP cone; it must be identical to its qualified
    # component, not merely produce a generic matching scalar example.
    check = PATHS['fp'].parent / 'check_vexii_os20_timing.py'
    assert sha(check) == '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
    spec = importlib.util.spec_from_file_location('actual_cones', check)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    normalized = candidate
    for name in ('fp_rs1_priority_early_data', 'fp_rs1_priority_late_data'):
        normalized = normalized.replace(f'  (* keep = "true" *) wire [31:0] {name};', f'  wire [31:0] {name};', 1)
    inputs = ['execute_lane0_bypasser_float_RS1_bypassEnables'] + [
        f'execute_ctrl{stage}_down_lane0_float_WriteBackPlugin_logic_DATA_lane0' for stage in range(4, 13)]
    inputs.append('execute_lane0_bypasser_float_RS1_port_data')
    cone, width = checker.cone(normalized, 'candidate', inputs, ['execute_lane0_bypasser_float_RS1_sel', dest])
    assert width == 42 and cone == (PATHS['fp'].parent / 'candidate.v').read_text()
    blocks = json.loads((PATHS['last'].parent / 'extracted-blocks.json').read_text())
    assert len(blocks) == 69
    for block in blocks:
        transformed = block
        for a, b in last_changes:
            transformed = transformed.replace(a, b)
        assert candidate.count(transformed) == 1
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    target = out / 'VexiiRiscv.v'
    target.write_text(candidate)
    for key in ('base', 'fp', 'last'):
        patch = out / ('from-' + key + '.patch')
        patch.write_text(''.join(difflib.unified_diff(sources[key].splitlines(True), candidate.splitlines(True),
                        fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
        replay = out / ('replay-' + key)
        replay.mkdir()
        (replay / target.name).write_text(candidate)
        for label, extra, expected in [('reverse', ['-R'], sources[key]), ('replay', [], candidate)]:
            run(['patch', '--batch', '--fuzz=0', *extra, '-p1', '-i', str(patch)],
                out / (key + '-' + label + '.log'), cwd=replay)
            assert (replay / target.name).read_text() == expected
    # Apply the ORIGINAL component patches in both orders as an additional
    # external check of commutation and line-offset robustness.
    patches = {'fp': PATHS['fp'].parent / 'fp-rs1-priority-groups.patch',
               'last': PATHS['last'].parent / 'last-direct.patch'}
    for order in [('fp', 'last'), ('last', 'fp')]:
        replay = out / ('order-' + '-'.join(order))
        replay.mkdir()
        (replay / target.name).write_text(base)
        for key in order:
            run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patches[key])],
                out / ('order-' + '-'.join(order) + '-' + key + '.log'), cwd=replay)
        assert (replay / target.name).read_text() == candidate
    run([str(PATHS['fp'].parent / 'obj/Vfp_rs1_proof')], out / 'fp-proof-rerun.log')
    run([str(PATHS['last'].parent / 'obj/Vlast_proof')], out / 'last-proof-rerun.log')
    run(['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', str(target)], out / 'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'lint.log').read_text())
    shutil.copyfile(__file__, out / Path(__file__).name)
    manifest = dict(reference_sha256=HASHES['base'], component_sha256=HASHES,
        candidate_sha256=sha(target), exact_scope='Two lastFromBuffer RHS plus FP RS1 data mux/two kept combinational words; all other source unchanged.',
        source_composition_orders_identical=True, original_patch_orders_identical=True,
        reversals_restore_each_parent_exactly=True, full_sequential_source_unchanged=True,
        composed_fp_actual_cone_identical=True, composed_last_actual69_blocks_identical=True,
        focused_fp_cases_rerun=1048576, focused_last_cases_rerun=393216,
        new_register_bits=0, new_pipeline_cycles=0, exact_patch_replay=True, full_netlist_lint=True,
        whole_cpu_qualification='Pending retained14-case suite on this final combined source.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (out / 'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out / Path(__file__).name),
        source_hashes=HASHES, candidate_sha256=sha(target),
        component_recipe_or_manifest={key: sha(PATHS[key].parent / ('recipe.json' if key == 'fp' else 'manifest.json')) for key in ('fp','last')},
        patch_sha256={p.name:sha(p) for p in out.glob('*.patch')}),indent=2)+'\n')
    (out / 'README.txt').write_text('''Combined FP RS1 local priority and direct frontend last-slice classification
=========================================================================

Base ec69 retains the sampled LSU-hit register and direct trap classification.
This candidate changes only two lastFromBuffer expressions and the RS1 data
mux. RS1 sources0-3 and4-9 resolve priority locally; the later group is masked
once by absence of any earlier source. Exactly two combinational32-bit word
boundaries are kept. All original bypass gates/selectors and every FF remain.

Both source-transform orders and both original external patch orders produce
the exact same candidate. Each reverse restores its expected parent exactly.
The actual composed FP cone equals the qualified component; every actual
last-slice proof block also remains exact. Their1,048,576 and393,216 case
proofs rerun successfully. Complete-netlist lint passes. Qualification of the
final fourteen retained CPU cases is recorded separately after completion.

No registers/cycles/features are added or removed. Physical benefit and
placement cost require a complete-core fit; production is unchanged.
Reproduce with generator/compose_fp_last.py --output <new-directory>.
''')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
