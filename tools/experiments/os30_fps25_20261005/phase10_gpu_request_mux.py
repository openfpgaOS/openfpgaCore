#!/usr/bin/env python3
"""Private GPU request payload selection; unchanged valid/retirement timing."""
import difflib
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'gpu-queue-pack-v8/gpu_core.v'
OUT = P / 'gpu-request-mux-v9'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def prepare():
    original = s = BASE.read_text()
    assert sha(BASE) == '5bd0031d9c66083c72884926e836ce584b9f5a275b106c63934e63f3d3afdfad'
    anchor = 'reg [GPU_ADDR_W-1:0] fbwq_req_addr;'
    assert s.count(anchor) == 1
    s = s.replace(anchor, anchor + '''
// Combinational source code, assigned with blocking assignments below.
// A request payload has four possible sources; validity remains separate.
reg [1:0] fbwq_push_source;
reg [3:0] fbwq_clear_strb;
''')
    old = '''    fbwq_push_addr = {GPU_ADDR_W{1'b0}};
    fbwq_push_data = 32'b0;
    fbwq_push_strb = 4'b0;'''
    assert s.count(old) == 1
    s = s.replace(old, '''    fbwq_push_source = 2'd0;
    fbwq_clear_strb = 4'b0;''')
    counts = {}
    for addr, data, strobe, value in (
        ('z_flush_addr', 'z_flush_data', 'z_flush_strb', 0),
        ('z_acc_addr', 'z_acc_data', 'z_acc_mask', 1),
        ('fb_acc_addr', 'fb_acc_data', 'fb_acc_mask', 2),
    ):
        pattern = (r'(?m)^(\s*)fbwq_push_addr = ' + addr + r';\n\s*'
                   + r'fbwq_push_data = ' + data + r';\n\s*'
                   + r'fbwq_push_strb = ' + strobe + r';')
        s, counts[addr] = re.subn(pattern, lambda m: m[1] + f"fbwq_push_source = 2'd{value};", s)
    assert counts == dict(z_flush_addr=2, z_acc_addr=2, fb_acc_addr=4), counts
    old = '''                fbwq_push_addr = cr_addr & {{(GPU_ADDR_W-2){1'b1}}, 2'b00};
                fbwq_push_data = {cr_color, cr_color, cr_color, cr_color};
                fbwq_push_strb = cr_strobe;'''
    assert s.count(old) == 1
    s = s.replace(old, '''                fbwq_push_source = 2'd3;
                fbwq_clear_strb = cr_strobe;''')
    old = '''            if (fbwq_push_req && fbwq_can_push) begin
                fbwq_req_valid <= 1'b1;
                fbwq_req_addr  <= fbwq_push_addr;
                fbwq_req_data  <= fbwq_push_data;
                fbwq_req_strb  <= fbwq_push_strb;'''
    assert s.count(old) == 1
    s = s.replace(old, '''            // Resolve one source only once, without a zero-data priority
            // cone repeated for every producer. Source 0 is also harmless
            // when no request is published.
            case (fbwq_push_source)
                2'd1: begin
                    fbwq_push_addr = z_acc_addr;
                    fbwq_push_data = z_acc_data;
                    fbwq_push_strb = z_acc_mask;
                end
                2'd2: begin
                    fbwq_push_addr = fb_acc_addr;
                    fbwq_push_data = fb_acc_data;
                    fbwq_push_strb = fb_acc_mask;
                end
                2'd3: begin
                    fbwq_push_addr = cr_addr & {{(GPU_ADDR_W-2){1'b1}}, 2'b00};
                    fbwq_push_data = {cr_color, cr_color, cr_color, cr_color};
                    fbwq_push_strb = fbwq_clear_strb;
                end
                default: begin
                    fbwq_push_addr = z_flush_addr;
                    fbwq_push_data = z_flush_data;
                    fbwq_push_strb = z_flush_strb;
                end
            endcase
            // The request slot can be overwritten iff empty or retiring.
            // An unpublished payload is unowned. Do not put the late
            // producer-valid/address-comparison cone on its register enable.
            if (fbwq_can_push) begin
                fbwq_req_addr  <= fbwq_push_addr;
                fbwq_req_data  <= fbwq_push_data;
                fbwq_req_strb  <= fbwq_push_strb;
            end
            if (fbwq_push_req && fbwq_can_push) begin
                fbwq_req_valid <= 1'b1;''')
    OUT.mkdir(exist_ok=False)
    (OUT / 'gpu_core.v').write_text(s)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), s.splitlines(True),
        fromfile='gpu-v8/gpu_core.v', tofile='gpu-v9/gpu_core.v')))
    (OUT / 'recipe.py').write_text(Path(__file__).read_text())
    (OUT / 'prepared.json').write_text(json.dumps(dict(
        parent_sha256=sha(BASE), candidate_sha256=sha(OUT / 'gpu_core.v'),
        recipe_sha256=sha(Path(__file__)), source_counts=counts,
        new_state_bits=0, performance_or_fit_claim=False,
        note='Only invalid request payload may differ; valid and owned data must match every cycle.'
    ), indent=2) + '\n')
    print(OUT)


if __name__ == '__main__':
    prepare()
