#!/usr/bin/env python3
"""Emit a private, hash-guarded tag mux derivative of the qualified skid CPU."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
REFERENCE = ART / 'fpu-match-v1/full-skid/VexiiRiscv.v'
REFERENCE_SHA = 'e2dde9e2ba382fe0ff08358c0149fdcc2ce8ca799675395647cd80f432ad186e'
PROOF_REFERENCE_SHA = '80b6f51d805e4909d8814ee742d1041c387e2c9a66438985062e40271428697f'
P = 'LsuL1Plugin_logic_'
C = P + 'lsu_ctrl_'
E = 'execute_ctrl4_down_'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def comb_blocks(text):
    for start in re.finditer(r'^  always @\(\*\) begin\n', text, re.M):
        level = 0
        for token in re.finditer(r'\bbegin\b|\bend\b', text[start.start():]):
            level += 1 if token[0] == 'begin' else -1
            if level == 0:
                yield text[start.start():start.start()+token.end()]+'\n'
                break

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['full-alu','immediate','address'], default='full-alu')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    reference,reference_sha = REFERENCE,REFERENCE_SHA
    default_output = ART/'tag-select-v1/full-skid-fpu'
    if args.profile == 'immediate':
        reference = ART/'fpu-match-v2/immediate-skid/VexiiRiscv.v'
        reference_sha = 'be212e95b7486ebb8cbadac0240398bb2ef6bb714a16126e0f51f6d1920f1e60'
        default_output = ART/'tag-select-v2/immediate-skid-fpu'
    if args.profile == 'address':
        reference = ART/'fpu-match-v3/address-skid/VexiiRiscv.v'
        reference_sha = 'f92407db03c971a735567577b0068877cb990263e49331eb5fec391414962c9f'
        default_output = ART/'tag-select-v3/address-skid-fpu'
    src = reference.read_text()
    assert digest(src.encode()) == reference_sha, 'FPU reference changed'
    proof = json.loads((ART/'tag-select-audit-v1/manifest.json').read_text())
    assert proof['cpu_sha256'] == PROOF_REFERENCE_SHA
    assert proof['all_enabled_noninitializer_tag_bits_equal']
    assert proof['initializer_loaded_remains_false']
    assert proof['control_states'] == 524288
    proof_src = (ROOT/proof['cpu']).read_text()
    assert digest(proof_src.encode()) == PROOF_REFERENCE_SHA
    targets = [P+'waysWrite_tag_'+x for x in ['loaded','address','fault']]
    replacements = []
    for target in targets:
        pattern = r'\b'+re.escape(target)+r'\s*='
        blocks = [b for b in comb_blocks(src) if re.search(pattern,b)]
        original = [b for b in comb_blocks(proof_src) if re.search(pattern,b)]
        assert len(blocks) == len(original) == 1
        block = blocks[0]
        assert block == original[0], 'Proven tag-data cone changed'
        assert block.count('if('+C+'doCbm)') == 1
        assert block.count('if('+C+'doFlush)') == 1
        new = block.replace('if('+C+'doCbm)', 'if(tag_early_cbm)')
        new = new.replace('if('+C+'doFlush)', 'if(tag_early_flush)')
        replacements.append((block,new))
    # The proof's exact expressions are the only additional combinational logic.
    early = proof['early_expressions']
    assert early.count('wire ') == 2 and 'tag_early_cbm' not in src
    candidate = src
    for old,new in replacements:
        assert candidate.count(old) == 1
        candidate = candidate.replace(old,new)
    candidate = candidate.replace(replacements[0][1], early+'\n'+replacements[0][1],1)
    # Reverse the entire transformation: this proves all control, mask, address,
    # refill, arbitration, and sequential logic outside those data muxes is exact.
    restored = candidate.replace(early+'\n','',1)
    for old,new in replacements:
        restored = restored.replace(new,old,1)
    assert restored == src
    out = (args.output or default_output).resolve()
    out.mkdir(parents=True,exist_ok=False)
    (out/'VexiiRiscv.v').write_text(candidate)
    shutil.copy2(__file__,out/'tag_select.py')
    manifest = dict(profile=args.profile,reference=str(reference.relative_to(ROOT)),
                    reference_sha256=reference_sha,candidate=str(out/'VexiiRiscv.v'),
                    candidate_sha256=digest(candidate.encode()),
                    script_sha256=digest(Path(__file__).read_bytes()),
                    proof_reference_sha256=PROOF_REFERENCE_SHA,
                    proof_manifest_sha256=digest((ART/'tag-select-audit-v1/manifest.json').read_bytes()),
                    changed_tag_data_muxes=targets,additional_register_bits=0,
                    pipeline_cycles_added=0,all_other_rtl_exactly_unchanged=True,
                    early_expressions=early,whole_cpu_validation='pending',
                    initializer_caveat='Tag address/fault bits may differ only when initializer forces loaded=false; they are unobserved invalid-tag payload.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))

if __name__ == '__main__':
    main()
