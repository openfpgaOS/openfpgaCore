#!/usr/bin/env python3
"""Read-only source feasibility and Boolean proof; creates no candidate RTL."""
import hashlib,itertools,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v1/frozen'
OUT=P/'remaining-timing-source-audit-v1'
CPU=BASE/'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
SDRAM=BASE/'src/fpga/targets/pocket/io_sdram.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    OUT.mkdir(exist_ok=False)
    cpu=CPU.read_text();sdram=SDRAM.read_text()
    assert 'assign AlignerPlugin_logic_usedMask_0 = 4\'b0000;' in cpu
    assert 'AlignerPlugin_logic_buffer_readers_0_lastFromBuffer = ({AlignerPlugin_logic_extractors_0_usageMask[3],AlignerPlugin_logic_extractors_0_usageMask[2]} == 2\'b00)' in cpu
    assert 'if(row_open_v != 4\'d0)' in sdram
    cases=0
    # Independent scanner validity and instruction width cover all reachable
    # cases plus combinations excluded by scanner construction.
    for valid in itertools.product((0,1),repeat=4):
        for full in itertools.product((0,1),repeat=4):
            oh=[bool(valid[i] and not any(valid[:i])) for i in range(4)]
            usage=0
            for i in range(4):
                if oh[i]:usage|=(1<<i)|((1<<(i+1)) if full[i] and i<3 else 0)
            first_old=oh[0] or oh[1]
            last_old=(usage&12)==0
            first_new=bool(valid[0] or valid[1])
            uses_fetch_new=bool(not valid[0] and ((valid[1] and full[1]) or (not valid[1] and (valid[2] or valid[3]))))
            assert first_old==first_new and (not last_old)==uses_fetch_new
            for trap_buffer,trap_fetch in itertools.product((0,1),repeat=2):
                old=bool(first_old and trap_buffer or not last_old and trap_fetch)
                new=bool(first_new and trap_buffer or uses_fetch_new and trap_fetch)
                assert old==new
                cases+=1
    result=dict(read_only=True,created_candidate_rtl=False,map_run=False,fit_run=False,
        cpu_trap_boolean_cases=cases,cpu_mismatches=0,added_cpu_state_bits=0,
        proposed_sdram_state_bits_added=0,sdram_command_cycle_change=False,
        sdram_nop_address_can_change=True,
        qualification='Source reasoning and Boolean equivalence only; selected new-fit timing not yet available')
    (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    (OUT/'REPORT.txt').write_text('''Read-only remedies for remaining compact-v1 timing paths

No RTL, generated CPU, compiler setting, map, placement or fitter was changed by this audit. These are contingent remedies for actual new-fit paths, not selected optimizations or timing predictions.

1. SDRAM row-open to A10

The current slow85_global report shows row_open_v[3] -> dc~0 -> Mux44~3/~4/~12/~13 -> phy_a[10]. Data delay is12.358ns, of which10.022ns is routing. The source-origin bit has fanout11, so this is not simply a huge fanout net. The longest individual routes are2.812ns to the reduction/control node,2.290ns back to the address mux, and3.973ns from the mux to the output register. Setup slack−1.524ns limits this placement to86.78MHz. No safe extra register may be inserted on the command/address outputs without shifting SDRAM command timing.

The source assigns A10=1 during ST_IDLE refresh only inside row_open_v!=0. A minimal command-equivalent rewrite would hoist that assignment one lexical level outward, into refresh_pending!=0. With open banks, PRECHARGE and A10 remain exactly the same edge/value. With all banks closed, the state machine still emits NOP and proceeds directly to AUTOREFRESH; only ignored A10 during that NOP becomes1. AUTOREFRESH ignores the address. The original controller has no internal reader of phy_a, and every address-bearing PRECHARGE/ACTIVATE/READ/WRITE/LMR command explicitly writes A10 or the complete address. Command order, timing counters, row-open tracking, refresh bookkeeping, queue grants, data and status are unchanged. This requires0 extra FF and removes the row_open reduction from the A10 write-enable cone. It does not promise a better physical result and is not pin-identical during NOP.

If selected, validation should compare every cycle's command/CKE/BA/data/masks and address on commands that use it, plus all transaction responses, using the real SDRAM model. Include refresh with0/1/multiple open banks, pending reads/writes/scanout, row crossings, both BANK_ROW_TRACK modes and reset. Do not require equality of address during NOP/AUTOREFRESH; those are the intentional don't-care difference. Full timing still needs a routed build. No implementation was made.

2. CPU aligner trap/PC hold

The slow85 CPU path is buffer_mask[1] -> scanner0_valid (fanout43) -> priority-selector logic -> Equal222 (fanout50) -> ctx_trap -> holdRequest -> PC output fire -> fetch-up-valid. It has8 logic levels,11.109ns data and8.125ns routing. Setup slack−1.361ns gives88.02MHz in this placement. Trap/hold retiming is not safe: it would allow an extra fetch on a synchronous trap and alter redirect/flush behavior.

A purely combinational alternative exists for ctx_trap. Define vi=scanner_i_valid, f1=(slice_data_1[1:0]==3), tb=buffer_trap and tf=fetch_down_TRAP. The original one-hot priority selector picks the lowest valid scanner; firstFromBuffer=oh0|oh1; usageMask upper bits say whether the selected instruction reaches the live fetch word. Algebraically:

  first_from_buffer = v0 || v1
  uses_fetch_word = !v0 && ((v1 && f1) || (!v1 && (v2 || v3)))
  ctx_trap = (tb && first_from_buffer) || (tf && uses_fetch_word)

The proposed change is only the ctx_trap assignment. Other selector/usageMask consumers remain unchanged. This bypasses the priority-onehot -> usageMask[3:2] reduction -> trap path and may reduce fanout on the shared selector/reduction. It adds0 state bits and preserves same-cycle trap value for every Boolean input combination. Exhaustive proof checks all16 scanner-valid vectors,16 instruction-width vectors and4 trap pairs,1024 total combinations, including combinations unreachable from valid scanners. The source has usedMask_0=0; that exact single-extractor assumption is bound below. This is not a general multi-issue-alignment rewrite.

Quartus may already rediscover an equivalent expression or duplicate other cones; no ALUT/timing gain is asserted. If selected, implement the guarded specialization in the generator or a private, hash-bound generated-RTL experiment, prove direct ctx_trap equality cycle-by-cycle and test instruction-fetch faults, compressed instructions crossing a fetch boundary, prediction redirects, interrupts, reset and pipeline halt. Native game arithmetic alone is insufficient trap proof.

Avoided options: no extra trap pipeline stage, no timing exceptions, no unconstrained duplicate FF, no SDRAM command-cycle change and no blind register retiming. Prefer the SDRAM assignment hoist if its exact path remains relevant; use the CPU algebra only if current STA still identifies that cone and the required trap-focused verification is worthwhile.
''')
    sources={str(p.relative_to(ROOT)):sha(p) for p in (CPU,SDRAM,P/'compact-timing-audit-v1/reports/slow_85_global.rpt',P/'compact-timing-audit-v1/reports/slow_85_cpu.rpt',Path(__file__))}
    (OUT/'sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    (OUT/'recipe.py').write_bytes(Path(__file__).read_bytes())
    files={p.name:sha(p) for p in sorted(OUT.iterdir())}
    (OUT/'manifest.json').write_text(json.dumps(files,indent=2)+'\n')
    print(json.dumps(result,indent=2));print(sha(OUT/'manifest.json'))

if __name__=='__main__':main()
