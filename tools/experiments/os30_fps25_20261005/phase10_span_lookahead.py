#!/usr/bin/env python3
"""Private same-triangle one-span-ahead setup and ordered source handoff."""
import hashlib,json,subprocess,difflib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'render-line32-v1/baseline'
OUT=P/'span-lookahead-v2'
def rep(s,a,b):
    assert s.count(a)==1,(a[:100],s.count(a));return s.replace(a,b)
def patch(s):
    s=rep(s,'wire spanprod_select = reset_n && !soft_reset && (state == S_SPANPROD_SELECT);','''// Private lookahead owns only the next record within this unchanged triangle.
// Cur/start registers are dead after EMIT; the live raster uses sp_* snapshots.
`ifdef INCLUDE_SPAN_LOOKAHEAD
`ifndef INCLUDE_SPAN_DEDICATED_MULT
    initial $fatal(1,"Span lookahead requires dedicated span multipliers");
`endif
localparam LA_IDLE=3'd0,LA_SELECT=3'd1,LA_SETUP=3'd2,
           LA_WAIT=3'd3,LA_CAPTURE=3'd4,LA_READY=3'd5;
reg [2:0] span_la;
wire span_la_select = state==S_FRAG_PIPE && span_la==LA_SELECT;
wire span_la_setup = state==S_FRAG_PIPE && span_la==LA_SETUP;
wire span_la_capture = state==S_FRAG_PIPE && span_la==LA_CAPTURE;
wire span_la_owned = span_la!=LA_IDLE;
wire span_la_eligible = cmd_is_tri_walker && spanprod_active && tex_route
  && !spanprod_direct_affine && spanprod_idx<spanprod_last_idx
  // Write-only depth can overtake old color when FB/Z aliases. Keep serial.
  && !(sp_z_write_enable && !sp_z_test_enable)
`ifdef INCLUDE_Z_PREFETCH
  && 1'b0 // Its EMIT reuses p3_z_addr and requires an empty old tail.
`endif
  ;
wire [1:0] spanprod_read_idx = span_la_select ? spanprod_idx+2'd1 : spanprod_idx;
`else
wire span_la_select=1'b0,span_la_setup=1'b0,span_la_capture=1'b0;
wire span_la_owned=1'b0;
wire [1:0] spanprod_read_idx=spanprod_idx;
`endif
wire spanprod_setup_phase = state==S_SPANPROD_SETUP || span_la_setup;
wire spanprod_capture_phase = state==S_SPANPROD_CAPTURE || span_la_capture;
wire spanprod_select = reset_n && !soft_reset
  && (state==S_SPANPROD_SELECT || span_la_select);''')
    # Only the synchronous SELECT port uses the speculative read index.
    a=s.index('    if (spanprod_select) begin');b=s.index('\nend\nassign spanprod_cur_count',a)
    s=s[:a]+s[a:b].replace('[spanprod_idx]','[spanprod_read_idx]')+s[b:]
    s=rep(s,'wire [3:0] spanprod_plane_launch_addr = (state == S_SPANPROD_SETUP)',
        'wire [3:0] spanprod_plane_launch_addr = spanprod_setup_phase')
    s=rep(s,'wire [3:0] spanprod_plane_origin_addr = (state == S_SPANPROD_CAPTURE)',
        'wire [3:0] spanprod_plane_origin_addr = spanprod_capture_phase')
    s=rep(s,'wire span_mul_fb = (state == S_SPANPROD_SETUP);','wire span_mul_fb = spanprod_setup_phase;')
    # One capture task preserves the exact existing result arithmetic/slices.
    a=s.index('            case (spanprod_calc_step)',s.index('        S_SPANPROD_CAPTURE: begin'))
    b=s.index('            endcase',a)+len('            endcase')
    body=s[a:b]
    s=s[:a]+'            spanprod_capture_step;'+s[b:]
    task='task spanprod_capture_step;\n    begin\n'+body+'\n    end\nendtask\n\n'
    s=rep(s,'task spanprod_load_generated_span;\n',task+'task spanprod_load_generated_span;\n')
    # State storage is owned by the existing main sequential process.
    main=s.index('always @(posedge clk) begin : main_fsm')
    a=s.index('    if (!reset_n) begin',main)+len('    if (!reset_n) begin')
    s=s[:a]+'\n`ifdef INCLUDE_SPAN_LOOKAHEAD\n        span_la <= LA_IDLE;\n`endif'+s[a:]
    s=rep(s,'        if (soft_reset) begin\n            state',
      '        if (soft_reset) begin\n`ifdef INCLUDE_SPAN_LOOKAHEAD\n            span_la <= LA_IDLE;\n`endif\n            state')
    # Cancel ownership outside its precise command lifetime. EMIT below wins.
    s=rep(s,'        case (state)\n\n        // ============================================================\n        // IDLE',
      '''`ifdef INCLUDE_SPAN_LOOKAHEAD
        if(state!=S_FRAG_PIPE)span_la<=LA_IDLE;
`endif
        case (state)

        // ============================================================
        // IDLE''')
    s=rep(s,'        S_SPANPROD_EMIT: begin\n            spanprod_load_generated_span;',
      '''        S_SPANPROD_EMIT: begin
            spanprod_load_generated_span;
`ifdef INCLUDE_SPAN_LOOKAHEAD
            // Eligibility below uses command constants for the initial EMIT,
            // not old sp_* values from a preceding surface.
            span_la <= cmd_is_tri_walker && spanprod_active && tex_route
              && !spanprod_direct_affine && spanprod_idx<spanprod_last_idx
              && !(spanprod_z_write && !spanprod_z_test)
`ifdef INCLUDE_Z_PREFETCH
              && 1'b0
`endif
              ? LA_SELECT : LA_IDLE;
`endif''')
    # Existing full-drain branch is the exact fallback for final/chunk/zero.
    # New handoff is its mutually exclusive higher-priority branch, placed
    # after PSS/source updates so EMIT assignments win on this edge.
    marker='\t\t            if (src_done && !stream_pending && !p0a_valid && !p0_valid && !p1_valid && !p2_valid && !p2b_valid'
    assert s.count(marker)==1
    block='''`ifdef INCLUDE_SPAN_LOOKAHEAD
            // Background math cannot pause FBSS, R-beat collection or the old
            // fragment tail. Both dedicated span multipliers are independent
            // of the current span's PSS DSPs.
            case(span_la)
              LA_SELECT:span_la<=LA_SETUP;
              LA_SETUP:begin
                if(!spanprod_cur_nonzero)span_la<=LA_IDLE;
                else begin
                  spanprod_calc_step<=4'd0;
                  spanprod_launch_step<=spanprod_next_calc(4'd0);
                  span_la<=LA_WAIT;
                end
              end
              LA_WAIT:begin
                spanprod_launch_step_mul(spanprod_launch_step);
                spanprod_launch_step<=spanprod_next_calc(spanprod_launch_step);
                span_la<=LA_CAPTURE;
              end
              LA_CAPTURE:begin
                spanprod_launch_step_mul(spanprod_launch_step);
                spanprod_launch_step<=spanprod_next_calc(spanprod_launch_step);
                spanprod_capture_step;
                if(spanprod_next_calc(spanprod_calc_step)==SPANPROD_STEP_NONE)
                  span_la<=LA_READY;
                else spanprod_calc_step<=spanprod_next_calc(spanprod_calc_step);
              end
              default:;
            endcase
            if(span_la==LA_READY && span_la_eligible && src_done
               && !p0a_valid && !p0_valid && !persp_active
               && !persp_swap_pending && persp_pss==PSS_IDLE)begin
                // FIFO order preserves all older texels/fragments. Material,
                // masks, blend/depth modes and plane steps are identical in
                // this command; only the next source coordinates restart.
                // No write accumulator, p1..p3, FBSS or R owner is cleared.
                spanprod_load_generated_span;
                spanprod_idx<=spanprod_idx+2'd1;
                span_la<=(spanprod_idx+2'd1)<spanprod_last_idx ? LA_SELECT : LA_IDLE;
            end else
`endif
'''
    s=s.replace(marker,block+marker)
    s=rep(s,'\t\t                         && !persp_active) begin\n\t                src_done <= 0;',
      '\t\t                         && !persp_active && !span_la_owned) begin\n\t                src_done <= 0;')
    return s

def prepare():
    OUT.mkdir(exist_ok=False)
    p=BASE/'model/frozen/common/gpu_core.v';old=p.read_text();new=patch(old)
    (OUT/'gpu_core.v').write_text(new)
    (OUT/'implementation.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='retained/gpu_core.v',tofile='private/gpu_core.v')))
    (OUT/'recipe.py').write_text(Path(__file__).read_text())
    sha=lambda b:hashlib.sha256(b).hexdigest()
    (OUT/'inputs.json').write_text(json.dumps(dict(private_only=True,baseline=str(p),baseline_sha256=sha(p.read_bytes()),candidate_sha256=sha(new.encode()),macro='INCLUDE_SPAN_LOOKAHEAD'),indent=2)+'\n')
    cmd=json.loads((BASE/'build-command.json').read_text());args=[]
    for x in cmd:
      if x.startswith('+define+') or x.startswith('-G') or x.startswith('-I') or x.endswith(('.v','.sv')):args.append(str(OUT/'gpu_core.v') if x==str(p) else x)
    command=['verilator','--lint-only','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_gpu','+define+INCLUDE_SPAN_LOOKAHEAD']+args
    (OUT/'lint-command.json').write_text(json.dumps(command,indent=2)+'\n')
    with (OUT/'lint.log').open('w') as log:subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    print(OUT)
if __name__=='__main__':prepare()
