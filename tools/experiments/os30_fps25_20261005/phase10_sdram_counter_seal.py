#!/usr/bin/env python3
"""Seal exact counter factoring as a private, unfitted fallback candidate."""
import json
import shutil
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write
P=CAMP/'phase10';OUT=P/'sdram-counter-factor-v2'
def main():
 assert not(OUT/'manifest.json').exists()
 assert sha(OUT/'io_sdram.v')=='a87ef738b646c538f9c74551643f5c1e8653e4c76e1b4ba10024ba4b2176fe32'
 literal=json.loads((OUT/'literal/result.json').read_text());native=json.loads((OUT/'bank-test/result.json').read_text())
 assert literal['passed'] and native['passed']
 assert all(v['banks']==15 for v in native['cases'].values())
 cycles=sum(v['cycles']for v in native['cases'].values())
 report=f'''Private SDRAM delay-counter factoring

PASS. The candidate replaces 23 identical zero overrides of the existing
four-bit dc register with one OR of their exact control predicates and one
increment-or-clear assignment. No other register writer, command output,
request, state transition or timing constant changes. No flip-flops, FSM states
or cycles are added. Parent is the already-proved address-mux source 402fbc16.

The proof preserves two easy-to-miss details: request precharge clear requires
!req_row_hit before req_need_prechg, and ST_RESET does not clear dc. Its normal
increment remains active there, as in the original. FAST_166-only burst-write
clears retain their original conditions; BANK_ROW_TRACK changes no dc rule.
All comparisons use the old counter value and the original Verilog widths.

The literal test instantiates actual complete old and new modules, with both
FAST_166 and BANK_ROW_TRACK each 0/1. Before each tested edge it forces equal
state, dc and clear-predicate inputs, then compares the resulting dc. It covers
all 64 encoded/unencoded states, all 16 counter values and all 64 combinations
of row-hit, precharge need, nonzero pending refresh, nonzero open row, terminal
boot count and reset input: {literal['actual_RTL_next_counter_comparisons']:,}
actual RTL next-counter comparisons. The equality/nonzero abstraction is complete
for these predicates; arbitrary other nonterminal/nonzero values have the same
clear result. These forced states are a counter truth-table proof, not complete
legal transaction histories or analog/X initialization coverage.

The existing physical SDRAM/mixed-master/scanout test was independently replayed
with exact old/new controllers under both bank-tracking configurations and
addresses spread over all four banks. It compares every output each observed
cycle, every dc value, and ALL address pins (including otherwise ignored pins).
The inherited result.json phrase about ignored address pins is conservative;
the new frozen wrapper contains the stronger unconditional equality assertion.
  BANK_ROW_TRACK=1: {native['cases']['1']['cycles']:,} checked cycles
  BANK_ROW_TRACK=0: {native['cases']['0']['cycles']:,} checked cycles
  Total: {cycles:,} checked cycles
Both complete with zero chip-model errors and no lost/corrupted write beat.
This continuous physical timing scoreboard uses FAST_166=0. FAST_166=1 counter
equivalence is covered by the literal actual-module test and unchanged remaining
FSM, not a new 166 MHz chip-timing qualification.

The first native preparation inferred the workspace root from the relocated
recipe and stopped before building a simulator. Its recipe and failure note
are preserved. The corrected recipe binds the explicit workspace path. Candidate
RTL stayed unchanged. The successful recipe, both simulators and logs are bound.

This is a separately proved fallback candidate. It has not been composed into
v7, mapped, fitted or deployed. No timing improvement is claimed. No production,
sibling application or device file was changed.
'''
 (OUT/'REPORT.txt').write_text(report);shutil.copy2(__file__,OUT/'seal-recipe.py')
 files=[p for p in OUT.rglob('*')if p.is_file()and not any(x.startswith('obj')for x in p.relative_to(OUT).parts)]
 files += [OUT/'literal/obj/Vtb_counter',OUT/'bank-test/obj0/Vtb_sdram_cont',OUT/'bank-test/obj1/Vtb_sdram_cont',P/'sdram-address-mux-v1/io_sdram.v',P/'residual-timing-options-v6/manifest.json']
 h=Path(__file__).resolve().parent
 files += [h/'phase10_sdram_counter_factor.py',h/'phase10_sdram_counter_seal.py',h/'phase10_sdram_address_mux.py']
 write(OUT/'manifest.json',dict(passed=True,passed_for_resource_screen=True,deployment_ready=False,composed=False,map_or_fit_run=False,candidate_sha256=sha(OUT/'io_sdram.v'),parent_sha256=sha(OUT/'baseline.v'),literal_checks=literal['actual_RTL_next_counter_comparisons'],native_exact_cycles=cycles,added_FF=0,added_states=0,added_cycles=0,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(files))if p.is_file()}))
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
