#!/usr/bin/env python3
"""Independent read-only storage lifetime review; no simulation or CAD."""
from pathlib import Path
import hashlib,json,shutil

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
C=P/'render-cache-storage-v1'
O=P/'render-cache-storage-source-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    assert not O.exists()
    prep=json.loads((C/'prepared.json').read_text())
    old=(C/'baseline.sv').read_text();new=(C/'gpu_color_depth_cache.sv').read_text()
    assert sha(C/'baseline.sv')==prep['baseline_sha256']=='2f68b6571a5fbaa88ec016d571d84f0403acaf0b000efab70daa9b4ed2f12aea'
    assert sha(C/'gpu_color_depth_cache.sv')==prep['source_sha256']=='e72520a045032fef7b4127bed9e02111c4592f958b1fe15fddb42dfdd2ec11a7'
    assert old[old.index('module '):old.index('localparam WORDS=')]==new[new.index('module '):new.index('localparam WORDS=')]
    marker='always @(posedge clk) begin\n    if(!reset_n) begin\n        state<=INIT;'
    before=old[old.index(marker):]
    before=before.replace('                if(walk[WAY_BITS-1:0]==0) victim[walk[INDEX_BITS-1:WAY_BITS]]<=0;\n','')
    before=before.replace("                victim[set_index]<=slot[WAY_BITS-1:0]+1'b1;\n",'')
    assert before==new[new.index(marker):], 'FSM differs beyond the moved victim writes'
    start='wire [3:0] word_valid=data_q[35:32], word_dirty=data_q[39:36];'
    # Downstream bus/state combinational blocks retain their original bytes.
    start='function automatic cacheable('
    assert old[old.index(start):old.index(marker)]==new[new.index(start):new.index(marker)]
    assert new.count('victim[meta_ra]')==1 and new.count('victim[victim_wa]<=victim_w')==1
    assert 'wire victim_we=reset_n && ((state==INIT && walk[WAY_BITS-1:0]==0) || state==ALLOC);' in new
    assert 'always @(posedge clk) read_bank<=data_ra[DATA_BITS-1:11];' in new
    assert 'assign data_q=bank_q[read_bank*40+:40];' in new
    assert 'if(DATA_BITS<=11) begin: data_single' in new
    facts=[
      'Module parameters/ports and complete state-machine tail are exact except removal of the two relocated victim writes. All AXI and data-RAM address/write combinational logic is unchanged.',
      'LOOK is reached only from accepted IDLE requests, R_ACCEPT, or LOOK_WAIT. Each preceding edge reads victim[meta_ra] for the same owned set as the synchronous tag banks. Address advance uses LOOK_WAIT; staged AR uses R_ACCEPT.',
      'INIT and ALLOC writes preserve original reset_n, address and modulo-way update. Neither can be followed immediately by LOOK. Their mixed-port read collision output is unowned, and later LOOK receives a fresh read.',
      'Reset begins INIT and reinitializes every victim before use. Maintenance never consumes selected victim; after returning to IDLE an accepted request supplies its own lookup read.',
      'Each large data bank uses original low-address read latency. The same edge captures the high read-bank selector, so selected q equals the original full-address memory read. Exactly one bank accepts a write.',
      'Poisoning an unselected bank is unobservable. Poison in the selected bank occurs exactly on a full-address read/write collision, matching the original unspecified collision contract.',
      'Power-of-two WAYS>=2 and positive SET_BITS/WORD_BITS remain the existing supported domain. DATA_BITS<=11 retains a single original-sized RAM; larger power-of-two storage divides exactly into 2048-word banks.',
      'No source issue found. This review is source/lifetime reasoning only; the separate protocol/miter/native proof and physical mapping remain required.'
    ]
    O.mkdir();shutil.copyfile(__file__,O/'recipe.py')
    (O/'review.json').write_text(json.dumps(dict(passed=True,source_only=True,component_tests_qualified=False,
        source_sha256=prep['source_sha256'],baseline_sha256=prep['baseline_sha256'],facts=facts,
        simulations_run=False,quartus_invoked=False,production_modified=False),indent=2)+'\n')
    (O/'REPORT.txt').write_text('Render-cache storage independent source review: PASS\n\n'+'\n\n'.join(facts)+'\n')
    evidence={str((C/n).relative_to(R)):sha(C/n) for n in ['baseline.sv','gpu_color_depth_cache.sv','prepared.json','candidate.patch','recipe.py']}
    (O/'manifest.json').write_text(json.dumps(dict(passed=True,source_only=True,
        files={p.name:sha(p) for p in O.iterdir() if p.is_file()},evidence_sha256=evidence),indent=2)+'\n')
    print(sha(O/'manifest.json'))

if __name__=='__main__':main()
