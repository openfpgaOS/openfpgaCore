#!/usr/bin/env python3
"""Independent source/algebra review; no new simulation or CAD."""
import hashlib
import json
from pathlib import Path
import re
import shutil

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
C=P/'cpu-load-sign-v1';O=P/'cpu-load-sign-source-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not O.exists()
    assert sha(C/'manifest.json')=='0ac352f65916e15244afac5c34fdd07a8370f9aa1e5ff10e56fed1cecb153adb'
    m=json.loads((C/'manifest.json').read_text())
    for n,h in m['evidence_sha256'].items():assert sha(R/n)==h,n
    assert sha(C/'baseline.v')=='82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
    assert sha(C/'VexiiRiscv.v')=='06714ce19ff93e1e6189c3f80fb2108ba275da25cc82e20f0e9f0bad5d0c86bd'
    old=(C/'baseline.v').read_text();new=(C/'VexiiRiscv.v').read_text();h=json.loads((C/'hunk.json').read_text())
    assert old.count(h['old'])==new.count(h['new'])==1
    assert old.replace(h['old'],h['new'],1)==new and new.replace(h['new'],h['old'],1)==old
    assert not re.search(r'\b(reg|posedge|negedge)\b',h['new'])
    proof=(C/'literal/proof.v').read_text()
    modules={k:re.search(r'module '+k+r'_dut\(.*?endmodule',proof,re.S).group() for k in ['old','new']}
    for key,body in modules.items():assert h[key] in body
    blocks=re.findall(r'  always @\(\*\) begin\n.*?\n  end\n',modules['old'],re.S)
    for block in blocks:assert block in old and block in new
    assigns=re.findall(r'^  assign .*;\n',modules['old'],re.M)
    for line in assigns:assert line in old
    # The fixture's direct data binding abbreviates these exact two aliases.
    for line in [
        'assign execute_ctrl4_down_LsuL1Plugin_logic_BYPASSED_DATA_lane0 = execute_ctrl4_down_LsuL1Plugin_logic_MUXED_DATA_lane0;',
        'assign execute_ctrl4_down_LsuL1_READ_DATA_lane0 = execute_ctrl4_down_LsuL1Plugin_logic_BYPASSED_DATA_lane0;']:
        assert old.count(line)==new.count(line)==1
    result=json.loads((C/'result.json').read_text());literal=json.loads((C/'literal/result.json').read_text())
    assert result['passed'] and result['literal']==literal and literal['checks']==5521408
    assert literal['arbitrary_controls']==8192 and literal['sc_miss_values']==2
    assert literal['basis_complement_zero_checks']==5259264 and literal['random_checks']==262144
    assert literal['negative_missing_sc_checks']==10359 and literal['negative_priority_checks']==345
    assert '%Error' not in (C/'lint.log').read_text()
    facts=[
        'Exactly one final stage2 DATA assignment changes. Original raw alignment, formatter, architectural valid/write/commit/cancel and all sequential logic remain byte-identical. New functions/nets are combinational, with no added state or cycle.',
        'For width0, byte7 is the sign source and both upper ranges extend it. For width1, raw15 is the upper sign and bits15:8 copy raw. Width2/3 copy all raw bits. Unsigned extension is zero and lane0 suppresses every output contribution. The new raw-copy predicates are exactly width!=0 and width[1].',
        'Cache data is the bitwise OR of independently masked ways, through two unchanged aliases. Byte sign is data[7/15/23/31] selected by address[1:0]; half sign is data[15/31] by address[1]. Extracting a bit commutes with masked OR, so both hits high preserves OR semantics rather than inventing priority.',
        'IO cmdSent suppresses both cache-way contributions and selects IO data. MUL and F2I contribute their original raw bits independently, even when arbitrary source-valid signals overlap. There is no valid-onehot premise.',
        'SC changes only LSU payload[7:0], making its bit7 zero regardless of SC_MISS; candidate excludes the SC byte-sign term. SC leaves LSU bit15 unchanged, so the halfword term correctly retains it. Original low raw byte preserves SC_MISS and overlaps with other sources.',
        'The literal fixed FLOAT=0/LOAD=0 bindings conservatively represent arbitrary effective iwb_valid and ATOMIC&&!LOAD predicates through independent controls. All outputs depend on those effective predicates, not on the removed distinction. The live source keeps actual FLOAT/LOAD equations unchanged.',
        'Literal source contains actual old/new blocks; original source extraction and no-bypass aliases were independently rebound. For fixed controls and SC bit, these output functions are OR-linear selections of160 payload bits, with zero/constant SC contribution. All8192 controls, both SC bits, zero/basis/complement cases plus262144 random inputs pass; this is not enumeration of2^160 payloads nor an X/Z claim.',
        'Initial proof-wrapper keyword failure is preserved, followed by module-name-only correction. Bound full CPU lint and5,521,408 comparisons pass; missing-SC and false-way-priority mutants fail10359 and345 cases. Reviewer ran no duplicate tests.',
        'No blocking source finding. Actual native composition, resource packing and routed timing remain independent gates.'
    ]
    O.mkdir();shutil.copyfile(__file__,O/'recipe.py')
    (O/'review.json').write_text(json.dumps(dict(passed=True,source_only=True,component_manifest_sha256=sha(C/'manifest.json'),
        candidate_sha256=sha(C/'VexiiRiscv.v'),facts=facts,evidence_hashes_checked=len(m['evidence_sha256']),
        no_new_tests=True,no_cad=True,production_modified=False,deployment_ready=False),indent=2)+'\n')
    (O/'REPORT.txt').write_text('Load scalar sign source review: PASS\n\n'+'\n\n'.join(facts)+'\n')
    evidence=dict(m['evidence_sha256']);evidence[str((C/'manifest.json').relative_to(R))]=sha(C/'manifest.json')
    (O/'manifest.json').write_text(json.dumps(dict(passed=True,source_only=True,deployment_ready=False,
        files={p.name:sha(p) for p in O.iterdir() if p.is_file()},evidence_sha256=evidence),indent=2)+'\n')
    print(sha(O/'manifest.json'))


if __name__=='__main__':main()
