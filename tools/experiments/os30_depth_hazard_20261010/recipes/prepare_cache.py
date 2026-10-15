#!/usr/bin/env python3
"""Create isolated disjoint-burst read-priority cache experiment."""
from pathlib import Path
import difflib
import hashlib
import json

HERE = Path(__file__).resolve().parent
P = HERE.parents[1]
BASE = P / 'cached-cpu-v1/native-sdram-v2'
REL = 'frozen/src/fpga/common/gpu_color_depth_cache.sv'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
bound = json.loads((BASE / 'prepared.json').read_text())
assert sha(BASE / REL) == bound['source_sha256'][REL]
before = (BASE / REL).read_text()
text = before


def replace(old, new):
    global text
    assert text.count(old) == 1, old
    text = text.replace(old, new)


replace('''assign s_awready=state==IDLE && !flush_req;
assign s_arready=state==IDLE && !flush_req && !s_awvalid;''', '''// The optional depth-hazard experiment may present AR before unrelated
// GPU writes drain. Admit that read ahead of an unaccepted AW only when the
// COMPLETE bursts occupy disjoint cache lines. Same-line aliases (including
// partial masks), boundary crossings and longer bursts keep AW priority.
// Restrict the feature to registered reads: IDLE can retain its hot-write
// speculation, while the accepted AR is looked up from owned addr in R_ACCEPT.
`ifdef INCLUDE_GPU_CACHE_READ_PRIORITY
localparam ADMIT_SUM_BITS = ((WORD_BITS > 8) ? WORD_BITS : 8) + 1;
wire [ADMIT_SUM_BITS-1:0] admit_read_last =
    ADMIT_SUM_BITS'(s_araddr[2+:WORD_BITS]) + ADMIT_SUM_BITS'(s_arlen);
wire [ADMIT_SUM_BITS-1:0] admit_write_last =
    ADMIT_SUM_BITS'(s_awaddr[2+:WORD_BITS]) + ADMIT_SUM_BITS'(s_awlen);
wire cache_prefer_read = REGISTER_READ_LOOKUP && s_arvalid && s_awvalid
    && s_araddr[1:0]==0 && s_awaddr[1:0]==0
    && admit_read_last < ADMIT_SUM_BITS'(WORDS)
    && admit_write_last < ADMIT_SUM_BITS'(WORDS)
    && s_araddr[ADDR_W-1:LINE_BITS] != s_awaddr[ADDR_W-1:LINE_BITS];
`else
wire cache_prefer_read = 1'b0;
`endif
assign s_awready=state==IDLE && !flush_req && !cache_prefer_read;
assign s_arready=state==IDLE && !flush_req && (!s_awvalid || cache_prefer_read);''')
replace('''                else if(s_awvalid) begin
                    // The accepted AW owns this candidate slot.''', '''                else if(s_awvalid && !cache_prefer_read) begin
                    // The accepted AW owns this candidate slot.''')

target = HERE / 'gpu_color_depth_cache.sv'
assert not target.exists()
target.write_text(text)
patch = HERE / 'cache-read-priority.patch'
patch.write_text(''.join(difflib.unified_diff(before.splitlines(keepends=True),
    text.splitlines(keepends=True), fromfile='a/src/fpga/common/gpu_color_depth_cache.sv',
    tofile='b/src/fpga/common/gpu_color_depth_cache.sv')))
notes = dict(status='RTL_READY_FOR_LINT_AND_DIRECTED_TESTS',
    baseline_model=str(BASE), baseline_prepared_sha256=sha(BASE / 'prepared.json'),
    baseline_source_sha256=sha(BASE / REL), candidate_source=str(target),
    candidate_source_sha256=sha(target), patch_sha256=sha(patch),
    recipe_sha256=sha(Path(__file__)), defines=['INCLUDE_GPU_CACHE_READ_PRIORITY'],
    paired_gpu_source=str(HERE.parent / 'work/gpu_core.v'),
    paired_gpu_sha256=sha(HERE.parent / 'work/gpu_core.v'),
    paired_gpu_define='INCLUDE_GPU_DEPTH_SELECTIVE_WAIT',
    required_parameter='REGISTER_READ_LOOKUP=1; otherwise priority remains unchanged.',
    changed_logic=['s_awready suppresses acceptance when disjoint pending AR is preferred.',
                   's_arready accepts exactly that read instead.',
                   'IDLE AW branch uses the same predicate, otherwise AR branch captures owned addr.'],
    added_state_bits=0, added_ram_bits=0,
    limitations=['No resource inference, timing, fit or hardware claim.',
                 'Cross-line and longer bursts conservatively retain AW priority.',
                 'This combines with the new queue tag gate; that gate alone assumes AW priority for all popped writes.',
                 'Posted downstream writes remain protected by the unchanged cache line scoreboard and fill hazard.'])
(HERE / 'rtl-notes.json').write_text(json.dumps(notes, indent=2) + '\n')
print(json.dumps(notes, indent=2))
