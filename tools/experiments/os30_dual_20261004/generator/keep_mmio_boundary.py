#!/usr/bin/env python3
"""Freeze a private attribute-only SYSREG read-data mux boundary."""
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
REFERENCE = ART / 'fit-rvc-skid-pingpong-peripheral-v1/frozen/src/fpga/common/axi_periph_slave.v'
REFERENCE_SHA = '03269e468c2e1a025897c846e513435cadd6d60267efbacf5dfe52946477b087'
EVIDENCE = ART / 'timing-audit-rvc-skid-pingpong-peripheral-v1-mmio-v2/nodes.tsv'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log, **kwargs):
    with log.open('w') as handle:
        subprocess.run(command, check=True, stdout=handle, stderr=subprocess.STDOUT, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART / 'mmio-read-boundary-v1')
    args = parser.parse_args()
    out = args.output.resolve()
    source = REFERENCE.read_text()
    assert sha(REFERENCE) == REFERENCE_SHA
    old = 'reg [31:0] sysreg_rdata;'
    new = '(* keep = "true" *) reg [31:0] sysreg_rdata;'
    assert source.count(old) == 1
    assert new not in source
    candidate = source.replace(old, new, 1)
    # This exact removal recovers every byte, including preprocessor branches,
    # default cases, state updates, four-state operators and side effects.
    assert candidate.replace(new, old, 1) == source
    assert candidate.count('(* keep = "true" *)') == 1
    assert EVIDENCE.is_file()
    evidence = EVIDENCE.read_text()
    assert 'Mux206~14' in evidence and 'periph_rd_data_r[7]~24' in evidence
    out.mkdir(parents=True, exist_ok=False)
    rtl = out / 'rtl'
    rtl.mkdir()
    target = rtl / 'axi_periph_slave.v'
    target.write_text(candidate)
    patch = out / 'keep-sysreg-rdata.patch'
    patch.write_text(''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True),
                    fromfile='a/axi_periph_slave.v', tofile='b/axi_periph_slave.v')))
    replay = out / 'patch-replay'
    replay.mkdir()
    (replay / target.name).write_text(candidate)
    for label, extra, expected in [('reverse', ['-R'], source), ('replay', [], candidate)]:
        run(['patch', '--batch', '--fuzz=0', *extra, '-p1', '-i', str(patch)],
            out / (label + '.log'), cwd=replay)
        assert (replay / target.name).read_text() == expected
    test = ROOT / 'src/fpga/test'
    common = ROOT / 'src/fpga/common'
    pocket = ROOT / 'src/fpga/targets/pocket'
    dependencies = [test / 'tb_axi_periph.v', test / 'altsyncram_stub.v', target,
                    pocket / 'snac_shifter.v', common / 'sync_fifo.v', pocket / 'apf/common.v']
    command = ['verilator', '--lint-only', '--top-module', 'tb_axi_periph', '-Wno-fatal', '-Wno-BADVLTPRAGMA',
               '+define+INCLUDE_ANALOGIZER', '+define+INCLUDE_LINK',
               '+define+EXCLUDE_SYSREG_DIAG_READBACK', '-GINCLUDE_PARAM_SPAN_LIST=0',
               '-I' + str(common), '-I' + str(pocket / 'third_party/analogizer'),
               *map(str, dependencies)]
    run(command, out / 'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'lint.log').read_text())
    script = out / Path(__file__).name
    shutil.copyfile(__file__, script)
    manifest = dict(
        reference=str(REFERENCE.relative_to(ROOT)), reference_sha256=REFERENCE_SHA,
        candidate_sha256=sha(target), exact_scope='One KEEP synthesis attribute on existing combinational sysreg_rdata[31:0].',
        exact_attribute_removal_restores_reference=True,
        behavioral_source_sha256=hashlib.sha256(candidate.replace(new, old, 1).encode()).hexdigest(),
        preserved='All address values, feature/preprocessor configurations, default and four-state behavior, sequential state, timing, and read side effects are unchanged literally.',
        extra_registers=0, extra_cycles=0, masked_or_or_region_decode_changes=False,
        exact_reverse_patch_and_replay=True, elaboration_lint=True,
        behavioral_simulation='Not repeated: only a synthesis attribute changes; exact behavioral source identity is stronger than finite input testing.',
        mapped_evidence=str(EVIDENCE.relative_to(ROOT)), mapped_evidence_sha256=sha(EVIDENCE),
        measured_path='req_addr[3] -> SYSREG local mux -> Mux206~14 regional merge -> periph_rd_data_r[6], -1.584 ns; 75% routing.',
        purpose='Keep the existing SYSREG word boundary from flattening into the final peripheral-region mux.',
        physical_limitation='Synthesis applicability, added logic/packing cost and setup/hold require actual full-core fitting; no Fmax benefit is claimed by this proof.',
        lint_command=command, dependencies_sha256={str(p.relative_to(ROOT)): sha(p) for p in dependencies},
        frozen_script_sha256=sha(script), patch_sha256=sha(patch))
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'README.txt').write_text('''Private SYSREG read-mux synthesis boundary
========================================

Only a KEEP attribute is added to the existing 32-bit combinational sysreg_rdata.
Removing that exact attribute reproduces the full reference source byte for byte.
No logic expression, register, clock, cycle, address decode, default, or read side
effect changes. This identity includes all preprocessor branches and four-state
semantics, so additional behavioral simulations are intentionally not repeated.
Zero-fuzz reverse/replay and os30 peripheral-harness elaboration pass.

Mapped predecessor evidence confirms that the measured SYSREG chain is merged
with other regional responses at Mux206~14. This attribute preserves a boundary
immediately before that merge. It may consume extra LUTs and affect placement;
only a complete-core fit can qualify area, setup/hold or physical applicability.

Reproduce into a new output directory using the source script:
  python3 tools/experiments/os30_dual_20261004/generator/keep_mmio_boundary.py --output <new-directory>
The manifest records the frozen source, patch, script and measured-cone hashes.
Production source is unchanged.
''')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
