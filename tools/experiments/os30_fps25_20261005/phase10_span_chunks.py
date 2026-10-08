#!/usr/bin/env python3
"""Private same-triangle span lookahead across four-record chunk boundaries."""
import difflib
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'span-lookahead-v2'
OUT = P / 'span-lookahead-v3'


def rep(s, old, new):
    assert s.count(old) == 1, (old[:100], s.count(old))
    return s.replace(old, new)


def patch(s):
    s = rep(s, "LA_WAIT=3'd3,LA_CAPTURE=3'd4,LA_READY=3'd5;\nreg [2:0] span_la;",
        "LA_WAIT=3'd3,LA_CAPTURE=3'd4,LA_READY=3'd5,LA_FILL=3'd6;\nreg [2:0] span_la;\nreg span_la_chunk;")
    s = rep(s, '&& !spanprod_direct_affine && spanprod_idx<spanprod_last_idx\n  // Write-only',
        '&& !spanprod_direct_affine && (span_la_chunk || spanprod_idx<spanprod_last_idx)\n  // Write-only')
    s = rep(s, "wire [1:0] spanprod_read_idx = span_la_select ? spanprod_idx+2'd1 : spanprod_idx;",
        "wire [1:0] spanprod_read_idx = span_la_select\n  ? (span_la_chunk ? 2'd0 : spanprod_idx+2'd1) : spanprod_idx;")
    s = rep(s, 'wire               tri_rec_ready = (state == S_TRI_FILL);', '''wire               tri_rec_ready = (state == S_TRI_FILL)
`ifdef INCLUDE_SPAN_LOOKAHEAD
                                 || (state == S_FRAG_PIPE && span_la == LA_FILL)
`endif
                                 ;''')
    s = rep(s, '\n        span_la <= LA_IDLE;\n`endif\n',
        "\n        span_la <= LA_IDLE;\n        span_la_chunk <= 1'b0;\n`endif\n")
    s = rep(s, '\n            span_la <= LA_IDLE;\n`endif\n',
        "\n            span_la <= LA_IDLE;\n            span_la_chunk <= 1'b0;\n`endif\n")
    s = rep(s, '        if(state!=S_FRAG_PIPE)span_la<=LA_IDLE;', '''        if(state!=S_FRAG_PIPE)begin
            span_la<=LA_IDLE;
            span_la_chunk<=1'b0;
        end''')
    s = rep(s, '''            span_la <= cmd_is_tri_walker && spanprod_active && tex_route
              && !spanprod_direct_affine && spanprod_idx<spanprod_last_idx
              && !(spanprod_z_write && !spanprod_z_test)
`ifdef INCLUDE_Z_PREFETCH
              && 1'b0
`endif
              ? LA_SELECT : LA_IDLE;''', '''            span_la<=LA_IDLE;
            span_la_chunk<=1'b0;
            if(cmd_is_tri_walker && spanprod_active && tex_route
              && !spanprod_direct_affine
              && !(spanprod_z_write && !spanprod_z_test)
`ifdef INCLUDE_Z_PREFETCH
              && 1'b0
`endif
              )begin
                if(spanprod_idx<spanprod_last_idx)span_la<=LA_SELECT;
                else if(!tri_walker_done)begin
                    // The current final record has just been snapshotted
                    // into sp_*. All four old record slots are now dead.
                    span_la<=LA_FILL;
                    span_la_chunk<=1'b1;
                    tri_fill_idx<=3'd0;
                end
            end''')
    s = rep(s, '            case(span_la)\n              LA_SELECT:', '''            case(span_la)
              LA_FILL:begin
                // Keep the old visible idx/count until the new source is
                // committed. tri_fill_idx alone owns the pending count.
                if(tri_rec_valid)begin
                    store_u(tri_fill_idx[1:0],tri_rec_u);
                    store_v(tri_fill_idx[1:0],tri_rec_v);
                    store_count(tri_fill_idx[1:0],tri_rec_count);
                    spanprod_cnt_valid[tri_fill_idx[1:0]]<=1'b1;
                    tri_fill_idx<=tri_fill_idx+3'd1;
                    if(tri_fill_idx==3'd3)span_la<=LA_SELECT;
                end else if(tri_walker_done)begin
                    if(tri_fill_idx!=3'd0)span_la<=LA_SELECT;
                    else begin
                        // No next span exists. Retire the old final tail
                        // through its original full-drain path.
                        span_la<=LA_IDLE;
                        span_la_chunk<=1'b0;
                    end
                end
              end
              LA_SELECT:''')
    s = rep(s, '''                spanprod_idx<=spanprod_idx+2'd1;
                span_la<=(spanprod_idx+2'd1)<spanprod_last_idx ? LA_SELECT : LA_IDLE;''', '''                if(span_la_chunk)begin
                    // This atomic handoff makes the pending chunk visible.
                    // In particular walker_done may already be true for a
                    // final partial chunk; its retained count remains live.
                    spanprod_idx<=2'd0;
                    spanprod_record_count<=tri_fill_idx;
                    spanprod_records_left<={13'd0,tri_fill_idx};
                    tri_fill_idx<=3'd0;
                    span_la_chunk<=1'b0;
                    span_la<=tri_fill_idx>3'd1 ? LA_SELECT : LA_IDLE;
                end else begin
                    spanprod_idx<=spanprod_idx+2'd1;
                    if((spanprod_idx+2'd1)<spanprod_last_idx)span_la<=LA_SELECT;
                    else if(!tri_walker_done)begin
                        span_la<=LA_FILL;
                        span_la_chunk<=1'b1;
                        tri_fill_idx<=3'd0;
                    end else span_la<=LA_IDLE;
                end''')
    return s


def prepare():
    OUT.mkdir(exist_ok=False)
    p = BASE / 'gpu_core.v'
    old = p.read_text()
    new = patch(old)
    (OUT / 'gpu_core.v').write_text(new)
    (OUT / 'implementation.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile='span-lookahead-v2/gpu_core.v', tofile='span-lookahead-v3/gpu_core.v')))
    (OUT / 'recipe.py').write_text(Path(__file__).read_text())
    sha = lambda b: hashlib.sha256(b).hexdigest()
    (OUT / 'inputs.json').write_text(json.dumps(dict(private_only=True, baseline=str(p),
        baseline_sha256=sha(p.read_bytes()), candidate_sha256=sha(new.encode()), macro='INCLUDE_SPAN_LOOKAHEAD'), indent=2)+'\n')
    command = [str(OUT / 'gpu_core.v') if x == str(p) else x for x in json.loads((BASE / 'lint-command.json').read_text())]
    (OUT / 'lint-command.json').write_text(json.dumps(command, indent=2)+'\n')
    with (OUT / 'lint.log').open('w') as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    (OUT / 'source-manifest.json').write_text(json.dumps({
        'scope': 'Private source and lint only; functional and timing validation pending.',
        'files': {str(f.relative_to(OUT)): sha(f.read_bytes()) for f in sorted(OUT.iterdir()) if f.is_file()},
    }, indent=2)+'\n')
    print(OUT)
    print('gpu_core.v SHA256', sha(new.encode()))


if __name__ == '__main__':
    prepare()
