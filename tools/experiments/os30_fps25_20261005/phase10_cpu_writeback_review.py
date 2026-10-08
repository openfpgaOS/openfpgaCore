#!/usr/bin/env python3
"""Independent source/algebra and existing native-proof review; no simulation."""
import ast
import json
import re
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P=CAMP/'phase10';BASE=P/'cpu-aligner-enable-v9';CAND=P/'cpu-writeback-plane-v10'
OUT=P/'cpu-writeback-plane-review-v10'


def main():
    parent=(BASE/'VexiiRiscv.v').read_text();candidate=(CAND/'VexiiRiscv.v').read_text()
    assert sha(CAND/'VexiiRiscv.v')=='482e13475702808f40d5bad7f6b6e6af9331ed5c895d99d7bf4630a3b047a303'
    recipe=Path(__file__).with_name('phase10_cpu_writeback_plane.py')
    constants={}
    for node in ast.parse(recipe.read_text()).body:
        if isinstance(node,ast.Assign):
            for t in node.targets:
                if isinstance(t,ast.Name)and t.id in ('OLD','NEW'):constants[t.id]=ast.literal_eval(node.value)
    assert parent.count(constants['OLD'])==1
    assert candidate==parent.replace(constants['OLD'],constants['NEW'])
    assert constants['NEW'].count('(* keep *) wire')==4
    assert 'always'not in constants['NEW'] and ' reg 'not in constants['NEW']
    # Independently recognize the original formatter's complete defined cases.
    start=parent.index('  assign lane0_IntFormatPlugin_logic_stages_2_hits =')
    end=parent.index('  assign FpuAddSharedPlugin_logic_inserter_portsRs1_0_mode',start)
    formatter=parent[start:end]
    assert formatter==candidate[start:end]
    assert 'WIDTH_ID_lane0 < 2\'b01' in formatter
    assert 'WIDTH_ID_lane0 < 2\'b10' in formatter
    assert 'segments_0_sign_sels_0 = lane0_IntFormatPlugin_logic_stages_2_raw[7]' in formatter
    assert 'segments_1_sign_sels_1 = lane0_IntFormatPlugin_logic_stages_2_raw[15]' in formatter
    component=json.loads((CAND/'manifest.json').read_text())
    assert component['candidate_sha256']==sha(CAND/'VexiiRiscv.v')
    assert component['parent_manifest_sha256']==sha(BASE/'manifest.json')
    for name,digest in component['files'].items():assert sha(CAND/name)==digest,name
    proof=json.loads((CAND/'proof/result.json').read_text())
    assert proof['passed'] and proof['checks']==155904
    assert proof['all_128_hits_width_signed_lane_controls']
    assert proof['negative_missing_halfword_sign']==5403
    native=json.loads((CAND/'result.json').read_text())
    assert native['passed']and native['all10_direct_b511_UART_cycle_comparisons']
    assert len(native['runs'])==10
    checks=0;evidence=[CAND/'manifest.json',BASE/'manifest.json',CAND/'VexiiRiscv.v',
        BASE/'VexiiRiscv.v',CAND/'prepared.json',CAND/'result.json',CAND/'proof/result.json',
        CAND/'proof/probe.sv',CAND/'proof/main.cpp',CAND/'proof/run.log',recipe,Path(__file__)]
    for row in native['runs']:
        a=CAND/row['candidate_log'];b=ROOT/row['reference_log']
        assert b.is_relative_to(BASE)
        assert sha(a)==row['candidate_sha256']and sha(b)==row['reference_sha256']
        at=a.read_text();bt=b.read_text()
        uart=lambda t:t.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
        cycles=lambda t:int(re.search(r'cycles=(\d+) PC=',t)[1])
        assert uart(at)==uart(bt)and cycles(at)==cycles(bt)==row['cycles']
        checks+=int(re.search(r'WRITEBACK_PLANE_COVER checks=(\d+)',at)[1])
        evidence += [a,b]
    assert checks==native['live_checks']['WRITEBACK_PLANE']==14519017
    prepared=json.loads((CAND/'native/prepared.json').read_text())
    assert '--assert' in prepared['command']
    for name,digest in prepared['sources_sha256'].items():assert sha(CAND/'native'/name)==digest,name
    cpupath=CAND/'native/frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    simulated=cpupath.read_text();assert constants['NEW']in simulated
    assert 'assert(writeback_plane_reference==lane0_integer_WriteBackPlugin_logic_stages_2_muxed)'in simulated
    evidence += [CAND/'native/prepared.json',cpupath]
    OUT.mkdir(exist_ok=False)
    write(OUT/'review.json',dict(passed=True,candidate_sha256=sha(CAND/'VexiiRiscv.v'),
        only_final_DATA_assignment_and_four_combinational_masks=True,
        original_formatter_raw_masks_and_architectural_controls_unchanged=True,
        all_widths_and_signs_algebra_exact=True,no_source_onehot_assumption=True,
        literal_control_cases=128,literal_checks=155904,negative_control_mismatches=5403,
        direct_native_uart_and_cycle_pairs=10,live_original_DATA_checks=checks,
        added_registers=0,added_cycles=0,no_new_simulation=True,deployment_ready=False))
    (OUT/'REPORT.txt').write_text('''Independent CPU writeback-plane review: PASS

The exact candidate differs from the selected b511 CPU only in the final
integer writeback DATA assignment and four kept combinational control masks.
The original formatter remains byte-identical for all other readers, including
the whitebox path. Raw multiply/F2I/LSU source masks, merged data, write-valid,
commit, cancellation, reset, stage timing, bypass controls and ISA are unchanged.

For arbitrary raw32 data and binary controls, original width 0 preserves low8
and repeats signed raw7 into bits31:8. Width1 preserves low16 and repeats signed
raw15 into bits31:16. Width2 and width3 preserve all32. Lane-select0 zeroes all
bits. The new masks implement exactly these four disjoint cases: copy_byte is
lane&&(width!=0), copy_upper is lane&&width[1], sign_byte is lane&&signed&&width0,
and sign_half is lane&&signed&&width1. Consequently no output bit ORs incompatible
copy and sign cases. No one-hot assumption about the three raw-data producers
is needed: their masked OR is preserved, including zero and multiple hits.
The existing formatter's unused X defaults never reach a replaced output for
binary width controls; no four-state equivalence claim is required.

The literal RTL probe retains the actual original source masks and formatter
and inserts the actual candidate assignment. All128 control patterns, 96-bit
payload bases/complements and131072 random triplets pass155904 checks. Removing
halfword sign extension produces5403 mismatches. All10 existing native runs
were independently rehashed and compared directly with b511 UART and cycle
logs. The bound simulated CPU contains the candidate and enabled old-DATA
assertion;14519017 live comparisons pass. The first duplicate-monitor fixture
attempt is retained in the component evidence and did not alter source.

No blocking source, arithmetic or ownership finding. This review ran no new
simulation and changed no CPU source. Actual generator reproduction, normal
build closure, map/fit and routed timing remain separate gates. Kept control
masks express the intended earlier decode, but do not promise routed savings.
''')
    write(OUT/'manifest.json',dict(files={str(p.relative_to(OUT)):sha(p)for p in OUT.iterdir()if p.is_file()},
        evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in evidence}))
    print(sha(OUT/'manifest.json'))


if __name__=='__main__':main()
