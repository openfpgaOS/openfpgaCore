#!/usr/bin/env python3
"""Remove unowned live AW addresses from the registered read data-RAM lookup."""
import difflib
import inspect
import json
import shutil
import sys
from pathlib import Path
import phase10_cache_full_write as parent

P = parent.P
BASE = P / 'cache-write-speculate-v2/gpu_color_depth_cache.sv'
OUT = P / 'cache-owned-lookup-v3'
rep, sha, save = parent.rep, parent.sha, parent.save


def prepare():
    assert sha(BASE) == 'e42f06c03b8b2aa8cfc69d330c937f51fe7283bc15d9f1b034b657dbeed7afe0'
    old = BASE.read_text()
    new = rep(old, '        R_ACCEPT: data_ra={hot_slot,word_index};', '''        // R_ACCEPT owns addr. Do not route the live IDLE address mux
        // through this RAM input: hot_slot is equivalent in this state,
        // but includes an otherwise unowned upstream AW tag comparison.
        R_ACCEPT: data_ra={(hot0_valid && hot0_line==addr[ADDR_W-1:LINE_BITS])
                           ? hot0_slot : hot1_slot,word_index};''')
    OUT.mkdir(exist_ok=False)
    (OUT / 'gpu_color_depth_cache.sv').write_text(new)
    shutil.copy2(BASE, OUT / 'baseline.sv')
    shutil.copy2(__file__, OUT / 'recipe.py')
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True),
        fromfile='cache-write-v2/cache', tofile='cache-owned-v3/cache')))
    save(OUT / 'prepared.json', dict(source_sha256=sha(OUT / 'gpu_color_depth_cache.sv'),
        parent_sha256=sha(BASE), new_register_bits=0, new_states=0, new_ram_bits=0,
        proof='R_ACCEPT!=IDLE implies lookup_addr=addr. Therefore old hot_slot and new expression are identical in the only changed case.',
        all_cycle_behavior_unchanged=True, deployment_ready=False))


def tests():
    fn = inspect.getsource(parent.v1.tests)
    fn = rep(fn, "'reset();staged_read_ownership();hazard_directed();speculative_write_ownership();')",
        "'reset();staged_read_ownership();hazard_directed();speculative_write_ownership();full_wait_ownership();')")
    fn = rep(fn, "        if name=='baseline.sv':s=rep(s,'reg [4:0] state;','localparam SPECULATE_WRITE_HOT0=0;\\nreg [4:0] state;')\n", '')
    fn = rep(fn, "configurations=[('baseline',0,1),('default',0,1),('enabled',1,1),('ranged',1,0)]",
        "configurations=[('baseline',0,1),('default',0,1),('enabled',1,1),('ranged',1,0),('old-enabled',1,1),('old-ranged',1,0)]")
    fn = rep(fn, "'baseline.sv' if name=='baseline' else", "'baseline.sv' if name=='baseline' or name.startswith('old-') else")
    fn = rep(fn, "    assert results['baseline']==results['default']",
        "    assert results['baseline']==results['default']\n    assert results['enabled']==results['old-enabled']\n    assert results['ranged']==results['old-ranged']")
    monitor = parent.MONITOR + '''
always @(posedge clk) if(reset_n && state==R_ACCEPT) begin
    assert(lookup_addr==addr);
    assert(data_ra=={hot_slot,word_index});
end
'''
    ns = dict(parent.v1.__dict__, OUT=OUT, BASE=BASE, MONITOR=monitor,
        DIRECTED=parent.v1.DIRECTED + parent.DIRECTED, __file__=__file__)
    exec(fn, ns)
    ns['tests']()
    result = json.loads((OUT / 'protocol/result.json').read_text())
    save(OUT / 'result.json', dict(passed=result['passed'],
        all_three_modes_cycle_identical=True, component_runs=18,
        source_sha256=sha(OUT / 'gpu_color_depth_cache.sv'),
        no_performance_cycle_change=True, no_register_or_ram_change=True,
        limits='Protocol scoreboard and literal case identity. Routed timing/area are not yet measured.'))


if __name__ == '__main__':
    for action in sys.argv[1:]:
        {'prepare': prepare, 'tests': tests}[action]()
