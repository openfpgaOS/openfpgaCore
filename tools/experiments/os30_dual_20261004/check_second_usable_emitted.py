#!/usr/bin/env python3
"""Independently audit the exact second-usable-mask substitution."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'all-operand-groups-v1/immediate/VexiiRiscv.v'
CAND = ART / 'second-usable-direct-v1/immediate/VexiiRiscv.v'
OUT = ART / 'second-usable-emitted-audit-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(BASE) == 'ffed6f6e299b8e7d3275b781d55d7094ae4d6c086b9f57d6553f794cd545082c'
assert sha(CAND) == '856b139adcaf3147f0a791b2c1dfdd4b6b7b258a64a1b97c1e6a4151c652565a'
old, new = BASE.read_text(), CAND.read_text()
name = 'AlignerPlugin_logic_extractors_1_usableMask'
pattern = r'^  assign ' + name + r' =\s*[^;]+;'
before, after = re.findall(pattern, old, re.M), re.findall(pattern, new, re.M)
assert len(before) == len(after) == 1
assert new.replace(after[0], before[0], 1) == old
V = 'AlignerPlugin_logic_extractors_0_usableMask'
terms = []
for j in range(7, 0, -1):
    adjacent = f'({V}[{j-1}] && !AlignerPlugin_logic_scanners_{j-1}_checker_1_required)'
    prior = f'((|{V}[{j-2}:0]) || {adjacent})' if j > 1 else adjacent
    terms.append(f'({V}[{j}] && {prior})')
assert after[0] == '  assign ' + name + ' = {\n      ' + ',\n      '.join(terms) + '\n  };'
assert "  assign AlignerPlugin_logic_usedMask_0 = 8'h0;" in old
for i in range(8):
    prefix = f'AlignerPlugin_logic_scanners_{i}'
    assert re.search(r'^  wire\s+' + prefix + '_valid;', old, re.M)
    assert re.search(r'^  wire\s+' + prefix + '_checker_1_required;', old, re.M)
    assert f"  assign {prefix}_checker_0_required = 1'b1;" in old
    lines = [f"    {prefix}_usageMask = 8'h0;", f'    {prefix}_usageMask[{i}] = {prefix}_checker_0_required;']
    if i < 7:
        lines.append(f'    {prefix}_usageMask[{i+1}] = {prefix}_checker_1_required;')
    assert '  always @(*) begin\n' + '\n'.join(lines) + '\n  end' in old

# Re-extract from actual immutable whole CPUs. Shared scanner leaves, not an
# assumed correlation between independent valid/mask cutpoints, form the cut.
checker = ROOT / 'tools/check_vexii_os20_timing.py'
spec = importlib.util.spec_from_file_location('audit_cones', checker)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
inputs = [f'AlignerPlugin_logic_scanners_{i}_valid' for i in range(8)]
inputs += [f'AlignerPlugin_logic_scanners_{i}_checker_1_required' for i in range(8)]
outputs = [V, name, 'AlignerPlugin_logic_extractors_1_slicesOh',
           'AlignerPlugin_logic_usedMask_2', 'AlignerPlugin_logic_buffer_readers_1_lastFromBuffer']
cones = []
for src, module in [(old, 'reference'), (new, 'candidate')]:
    cone, bits = mod.cone(src, module, inputs, outputs)
    assert bits == 31
    assert cone == (CAND.parent / (module + '.v')).read_text()
    cones.append(cone)
ports = [f'valids[{i}]' for i in range(8)] + [f'requireds[{i}]' for i in range(8)]
wrapper = '''module second_usable_proof(input [7:0] valids, requireds,
output [30:0] old_observed, new_observed);
reference a(''' + ','.join(ports + ['old_observed']) + ''');
candidate b(''' + ','.join(ports + ['new_observed']) + ''');
endmodule
'''
assert ''.join(cones) + wrapper == (CAND.parent / 'second_usable_proof.v').read_text()

# Separate integer oracle: consume the lowest valid scanner and its optional
# adjacent position; no contiguity, validity or length correlation assumed.
empty = sparse = 0
for valids in range(256):
    for required in range(256):
        selected = (valids & -valids).bit_length() - 1
        consumed = 0 if selected < 0 else ((1 << selected) | (((required >> selected) & 1) << (selected + 1))) & 255
        expected = (valids & ~consumed) >> 1
        actual = 0
        for j in range(1, 8):
            earlier = bool(valids & ((1 << (j-1)) - 1))
            adjacent_short = bool(valids & (1 << (j-1))) and not bool(required & (1 << (j-1)))
            actual |= int(bool(valids & (1 << j)) and (earlier or adjacent_short)) << (j-1)
        assert actual == expected, (valids, required)
        empty += actual == 0
        positions = [i for i in range(8) if valids & (1 << i)]
        sparse += bool(positions) and len(positions) != positions[-1] - positions[0] + 1

OUT.mkdir(exist_ok=False)
run = subprocess.run([str(CAND.parent / 'obj/Vsecond_usable_proof')], check=True, text=True, capture_output=True)
(OUT / 'actual-cone-rerun.log').write_text(run.stdout + run.stderr)
assert 'PASS 65536 actual-cone cases' in run.stdout
manifest = dict(candidate_sha256=sha(CAND), reference_sha256=sha(BASE),
    source_scope='Exactly one combinational assignment; every other byte unchanged.',
    independently_reconstructed_expression=True, scalar_input_widths_checked=True,
    actual_shared_scanner_cut=True, fresh_cones_and_wrapper_match=True,
    actual_cone_binary_rerun=True, independent_mask_math_cases=65536,
    empty_second_cases=empty, sparse_mask_cases=sparse,
    no_contiguity_or_reachability_assumption=True, unchanged_state_and_other_consumers=True,
    qualification='Binary exact source equivalence; retained CPU fixtures and physical timing are separate.',
    inputs={str(p.relative_to(ROOT)):sha(p) for p in [BASE,CAND,checker,
        CAND.parent/'reference.v',CAND.parent/'candidate.v',CAND.parent/'second_usable_proof.v',
        CAND.parent/'second_usable_proof.cpp',CAND.parent/'recipe.json',CAND.parent/'manifest.json']})
(OUT/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print(json.dumps(manifest, indent=2))
