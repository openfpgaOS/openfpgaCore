#!/usr/bin/env python3
"""Compose the qualified direct aligner-trap change on the original ping-pong CPU."""
from pathlib import Path
import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
PATHS = {
    'pingpong': ART / 'decode-pingpong-v1/immediate/VexiiRiscv.v',
    'sampled': ART / 'lsu-hit-sampled-v1/immediate/VexiiRiscv.v',
    'trap_sampled': ART / 'aligner-trap-direct-v1/immediate/VexiiRiscv.v',
}
HASHES = {
    'pingpong': 'd471c5da61b3f5f67ce3c78eb0bf34d98cb828407ba0da15b63ac1044ed50f86',
    'sampled': '4b01fdaa39fce594a8e0bb46cb756cd55d6613b8775c72207b5c7be3c77ecf09',
    'trap_sampled': 'ec69d8b6b09f60fe60f03227bb1bad4c19ea841a28e874d8025b6970b1d639c3',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log, **kwargs):
    with log.open('w') as handle:
        subprocess.run(command, check=True, stdout=handle, stderr=subprocess.STDOUT, **kwargs)


def apply(source, changes, reverse=False):
    for old, new in (reversed(changes) if reverse else changes):
        a, b = (new, old) if reverse else (old, new)
        assert source.count(a) == 1
        source = source.replace(a, b, 1)
    return source


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ART / 'aligner-trap-no-sampled-hit-v1/immediate')
    args = ap.parse_args()
    sources = {}
    for name, path in PATHS.items():
        assert sha(path) == HASHES[name]
        sources[name] = path.read_text()
    base, sampled, combined = [sources[k] for k in ('pingpong', 'sampled', 'trap_sampled')]
    up = 'execute_ctrl4_up_LsuL1Plugin_logic_WAYS_HITS_lane0'
    down = 'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0'
    incoming = 'execute_ctrl3_down_LsuL1Plugin_logic_WAYS_HITS_lane0'
    hit = 'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HIT_lane0'
    shadow = 'execute_ctrl4_up_LsuL1Plugin_logic_WAYS_HIT_lane0'
    decl = '  reg        [1:0]    ' + up + ';'
    capture = '      ' + up + ' <= ' + incoming + ';'
    original_or = f'  assign {hit} = (|{down});'
    hit_changes = [
        (decl, '  reg                 ' + shadow + ';\n' + decl),
        (original_or, f'  assign {hit} = {shadow};'),
        (capture, f'      {shadow} <= (|{incoming});\n' + capture),
    ]
    assert apply(base, hit_changes) == sampled
    assert apply(sampled, hit_changes, reverse=True) == base
    trap_changes = []
    for lane in range(2):
        name = f'AlignerPlugin_logic_extractors_{lane}_ctx_trap'
        old = re.findall(r'^  assign ' + name + r' = [^;]+;', sampled, re.M)
        new = re.findall(r'  // Classify the first usable instruction directly, before its one-hot mux\.\n'
                         r'  // A boundary instruction touches fetch only when it needs a second slice\.\n'
                         r'  assign ' + name + r' =[^;]+;', combined)
        assert len(old) == len(new) == 1
        trap_changes.append((old[0], new[0]))
    assert apply(sampled, trap_changes) == combined
    candidate = apply(combined, hit_changes, reverse=True)
    assert candidate == apply(base, trap_changes)
    assert apply(candidate, hit_changes) == combined
    assert apply(candidate, trap_changes, reverse=True) == base
    assert not re.search(r'\b' + shadow + r'\b', candidate)
    # Only the aggregate producer is restored. Its five consumers, vector
    # capture, exact down alias and all sequential source are the old design.
    consumers = [line for line in base.splitlines() if re.search(r'\b' + hit + r'\b', line)
                 and line != original_or and not re.match(r'^  wire\s+', line)]
    assert len(consumers) == 5 and all(candidate.count(line) == 1 for line in consumers)
    assert candidate.count(capture) == 1
    assert candidate.count(f'  assign {down} = {up};') == 1
    assert re.findall(r'^  always @\(posedge clk.*?^  end', base, re.M | re.S) == re.findall(
        r'^  always @\(posedge clk.*?^  end', candidate, re.M | re.S)
    # Exact source cuts from the independently qualified trap proof carry over;
    # nothing in the hit-register reversal occurs in any of its 69 blocks.
    blocks = json.loads((PATHS['trap_sampled'].parent / 'extracted-blocks.json').read_text())
    assert len(blocks) == 69
    for block in blocks:
        assert base.count(block) == 1
        transformed = block
        for a, b in trap_changes:
            transformed = transformed.replace(a, b)
        assert candidate.count(transformed) == 1
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    target = out / 'VexiiRiscv.v'
    target.write_text(candidate)
    for filename, old_source in [('trap-only.patch', base), ('remove-sampled-hit.patch', combined)]:
        (out / filename).write_text(''.join(difflib.unified_diff(
            old_source.splitlines(True), candidate.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    replay = out / 'patch-replay'
    replay.mkdir()
    for filename, original in [('trap-only.patch', base), ('remove-sampled-hit.patch', combined)]:
        (replay / target.name).write_text(candidate)
        for direction, extra, expected in [('reverse', ['-R'], original), ('replay', [], candidate)]:
            run(['patch', '--batch', '--fuzz=0', *extra, '-p1', '-i', str(out / filename)],
                out / (filename + '.' + direction + '.log'), cwd=replay)
            assert (replay / target.name).read_text() == expected
    run([str(PATHS['trap_sampled'].parent / 'obj/Vtrap_proof')], out / 'proof-rerun.log')
    run(['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', str(target)], out / 'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'lint.log').read_text())
    shutil.copyfile(__file__, out / Path(__file__).name)
    manifest = dict(reference=str(PATHS['pingpong'].relative_to(ROOT)),
        reference_sha256=HASHES['pingpong'], controlled_comparison_sha256=HASHES['trap_sampled'],
        source_hashes=HASHES, candidate_sha256=sha(target),
        exact_scope='Only two ctx_trap equations differ from d471; only the three sampled-hit edits are reversed from ec69.',
        commuting_composition_exact=True, reverse_each_component_exact=True,
        sampled_hit_vector_and_five_consumers_unchanged=True, sampled_shadow_register_absent=True,
        all_sequential_source_identical_to_d471=True, actual_trap_cone_blocks_identical=69,
        qualified_trap_cases_rerun=393216, exact_patch_replay=True, lint=True,
        extra_cycles=0, register_change_vs_ec69=-1, register_change_vs_d471=0,
        whole_cpu_qualification='Pending fourteen retained fixtures with full skid/pingpong monitoring.',
        physical_limitation='A controlled placement comparison; removing the locally well-timed shadow is not claimed to improve timing without a full fit.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    recipe = dict(script_sha256=sha(out / Path(__file__).name), source_hashes=HASHES,
                  candidate_sha256=sha(target), patches={p.name: sha(p) for p in out.glob('*.patch')},
                  trap_proof_sha256=sha(PATHS['trap_sampled'].parent / 'trap_proof.v'),
                  independent_trap_audit_sha256=sha(ART / 'aligner-trap-emitted-audit-v1/manifest.json'))
    (out / 'recipe.json').write_text(json.dumps(recipe, indent=2) + '\n')
    (out / 'README.txt').write_text('''Controlled direct-trap CPU without sampled any-hit register
==========================================================

The direct-trap transform is applied to the unchanged d471 ping-pong CPU.
The result is byte-identical to ec69 with only the three sampled-hit edits
reversed: declaration, capture and aggregate producer. The original two-way
hit vector/reduction, five consumers and all d471 sequential source remain.
Applying either transform first commutes exactly; both component reversals
and zero-fuzz external patch replay restore their exact expected parents.

All69 actual selector/trap proof blocks are unchanged by the reversal; the
393,216-case proof binary reruns successfully. Full-netlist lint passes.
This is a physical packing comparison, with no new cycles or architectural
change. The sampled hit path itself met timing in the other fit; only actual
full-core fitting can determine whether omitting the register helps placement.

Reproduce with generator/without_sampled_hit.py --output <new-directory>.
Whole-CPU qualification is recorded separately after the14 retained cases.
Production is unchanged.
''')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
