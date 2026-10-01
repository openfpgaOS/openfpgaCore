#!/usr/bin/env python3
"""Coupled SM64 model for comparing GPU implementations (no overlay).

Builds the coupled qsim CPU + GPU/cache/SDRAM RTL model like
sm64_cache_20260924. Use --gpu-manifest with the os30 manifest emitted by
check_gpu_target_matrix.py to select current production parameters/macros;
without it, the model retains its September 25 options. Other differences:

* no colour-clear filter.  SM64 builds since 2026-09-24 clear colour only on a
  buffer's first use, so the harness filter (written for the old binary) would
  drop the per-frame DEPTH clear instead -- same 640x240-byte rectangle -- and
  leave the depth buffer stale;
* --cache-rtl swaps in another render-cache source for A/B runs;
* --common-dir selects a complete common RTL snapshot for A/B runs;
* --counters adds GPU/cache state counters (printed by the RTL's final block
  into run.log) and a CPU profile snapshot at the first coupled frame, which
  analyze.py uses to separate the measured window from boot.
"""
import argparse, importlib.util, json, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT/'build/sm64-bottleneck-20260929'
CACHE = ROOT/'tools/experiments/sm64_cache_20260924/build.py'
spec = importlib.util.spec_from_file_location('cache_build', CACHE)
cache = importlib.util.module_from_spec(spec); spec.loader.exec_module(cache)
PARAMS = cache.VARIANTS['nosrw']   # os30 from 2026-09-25 (swap gate is in gpu_core.v)

COUNTERS = r'''
// ---- sm64_bottleneck_20260929 counters (co-sim only) ----
reg [63:0] bn_total, bn_fp_run, bn_fp_stall, bn_fp_tex, bn_fp_fbss, bn_fp_z, bn_fp_wbuf, bn_fp_infl, bn_fp_other;
reg [63:0] bn_fp_bubble, bn_px, bn_zfill, bn_rd_ar, bn_rd_first;
reg [63:0] bn_state [0:63];
reg [63:0] bn_fbss [0:15];
reg [63:0] bn_cst [0:31];
reg [3:0] bn_fbss_q;
reg bn_wait_first;
integer bn_i;
initial begin
    bn_total=0; bn_fp_run=0; bn_fp_stall=0; bn_fp_tex=0; bn_fp_fbss=0; bn_fp_z=0; bn_fp_wbuf=0; bn_fp_infl=0;
    bn_fp_other=0; bn_fp_bubble=0; bn_px=0; bn_zfill=0; bn_rd_ar=0; bn_rd_first=0; bn_fbss_q=0; bn_wait_first=0;
    for (bn_i=0; bn_i<64; bn_i=bn_i+1) bn_state[bn_i]=0;
    for (bn_i=0; bn_i<16; bn_i=bn_i+1) bn_fbss[bn_i]=0;
    for (bn_i=0; bn_i<32; bn_i=bn_i+1) bn_cst[bn_i]=0;
end
always @(posedge clk) if (reset_n) begin
    bn_total <= bn_total + 1;
    bn_state[gpu.state] <= bn_state[gpu.state] + 1;
    bn_fbss[gpu.fbss] <= bn_fbss[gpu.fbss] + 1;
    bn_cst[render_cache.state] <= bn_cst[render_cache.state] + 1;
    bn_fbss_q <= gpu.fbss;
    if (gpu.fbss == 4'd5 && bn_fbss_q != 4'd5) bn_zfill <= bn_zfill + 1;
    if (gpu.state == 6'd6) begin
        if (gpu.fp_pipe_stall) begin
            bn_fp_stall <= bn_fp_stall + 1;
            if (gpu.p1_valid && !gpu.p1_response_ready) bn_fp_tex <= bn_fp_tex + 1;
            else if (gpu.fbss != 4'd0) bn_fp_fbss <= bn_fp_fbss + 1;
            else if (gpu.p3_valid && !gpu.p3_discard && gpu.p3_z_test) bn_fp_z <= bn_fp_z + 1;
            else if (gpu.fb_write_buffer_stall) bn_fp_wbuf <= bn_fp_wbuf + 1;
            else if (gpu.m_wr_inflight_near_full) bn_fp_infl <= bn_fp_infl + 1;
            else bn_fp_other <= bn_fp_other + 1;
        end else begin
            bn_fp_run <= bn_fp_run + 1;
            if (!gpu.p0_valid) bn_fp_bubble <= bn_fp_bubble + 1;
        end
        if (!gpu.fp_pipe_stall && gpu.p3_valid && !gpu.p3_discard) bn_px <= bn_px + 1;
    end
    if (gpu.m_rd_arvalid && gpu.m_rd_arready) begin bn_rd_ar <= bn_rd_ar + 1; bn_wait_first <= 1; end
    else if (gpu.m_rd_rvalid) bn_wait_first <= 0;
    if (bn_wait_first && !gpu.m_rd_rvalid) bn_rd_first <= bn_rd_first + 1;
end
final begin
    $display("BN total %0d", bn_total);
    for (bn_i=0; bn_i<64; bn_i=bn_i+1) if (bn_state[bn_i] != 0) $display("BN state %0d %0d", bn_i, bn_state[bn_i]);
    for (bn_i=0; bn_i<16; bn_i=bn_i+1) if (bn_fbss[bn_i] != 0) $display("BN fbss %0d %0d", bn_i, bn_fbss[bn_i]);
    for (bn_i=0; bn_i<32; bn_i=bn_i+1) if (bn_cst[bn_i] != 0) $display("BN cstate %0d %0d", bn_i, bn_cst[bn_i]);
    $display("BN fp run %0d bubble %0d px %0d stall %0d tex %0d fbss %0d z %0d wbuf %0d infl %0d other %0d",
             bn_fp_run, bn_fp_bubble, bn_px, bn_fp_stall, bn_fp_tex, bn_fp_fbss, bn_fp_z, bn_fp_wbuf, bn_fp_infl, bn_fp_other);
    $display("BN mem zfills %0d rd_ar %0d rd_first_wait %0d cache_hits %0d cache_misses %0d cache_wb %0d",
             bn_zfill, bn_rd_ar, bn_rd_first, cache_hits, cache_misses, cache_writebacks);
end
'''

def main():
    p = argparse.ArgumentParser()
    p.add_argument('label', help='output directory name under build/sm64-bottleneck-20260929')
    p.add_argument('--cache-rtl', type=Path, default=ROOT/'src/fpga/common/gpu_color_depth_cache.sv')
    p.add_argument('--common-dir', type=Path, default=ROOT/'src/fpga/common',
                   help='RTL source snapshot for reproducible before/after comparisons')
    p.add_argument('--gpu-manifest', type=Path,
                   help='Production parameters/macros from check_gpu_target_matrix.py')
    p.add_argument('--counters', action='store_true')
    p.add_argument('--cache-set-bits', type=int, help='render cache sets = 2**N (os30: 6, 8 KiB)')
    a = p.parse_args()
    params = [x for x in PARAMS if not (a.cache_set_bits and x.startswith('-GCACHE_SET_BITS='))]
    if a.cache_set_bits: params.append(f'-GCACHE_SET_BITS={a.cache_set_bits}')
    manifest = json.loads(a.gpu_manifest.read_text()) if a.gpu_manifest else None
    if manifest:
        overridden = {flag.split('=')[0] for flag in manifest['flags']}
        params = [flag for flag in params if flag.split('=')[0] not in overridden]
        params += manifest['flags']
    out = OUT/a.label
    out.mkdir(parents=True, exist_ok=True)
    if manifest:
        (out/'gpu-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    cache_rtl = a.cache_rtl.resolve().read_text()
    (out/'coupled.cpp').write_text((cache.BASE/'coupled.cpp').read_text())
    (out/'audio.inc').write_text((cache.BASE/'audio.inc').read_text())

    def apply(frozen, integrated):
        cache.apply_cache(frozen, integrated)
        (frozen/'gpu_color_depth_cache.sv').write_text(cache_rtl)
        top = frozen/'tb_gpu_transluc.v'; s = top.read_text()
        if 'bus_reset_n' not in cache_rtl:
            s = cache.rep(s, ' .bus_reset_n(reset_n),', '')
        if a.counters:
            i = s.rindex('endmodule'); s = s[:i] + COUNTERS + s[i:]
        top.write_text(s)

    source = (cache.BASE/'build.py').read_text()
    for o, n in [("OUT = ROOT/'build/sm64-coupled-20260922'", 'OUT = PRIVATE_OUT'),
                 ("'--top-module','tb_gpu_transluc',", "'--top-module','tb_gpu_transluc',*PARAMS,"),
                 ("HERE/'coupled.cpp'", "OUT/'coupled.cpp'"),
                 ("    top = frozen/'tb_gpu_transluc.v'; s = top.read_text()",
                  "    COPY_COMMON(frozen)\n"
                  "    APPLY_CACHE(frozen, True)\n"
                  "    top = frozen/'tb_gpu_transluc.v'; s = top.read_text()"),
                 ("    rtl = [top, frozen/'sdram_model_full.v',", "    rtl = [top, frozen/'gpu_color_depth_cache.sv', frozen/'sdram_model_full.v',"),
                 ("    sources = 'cpu fpu cache prof qelf lint gpu app env main'.split()",
                  "    SNAPSHOT(q)\n    sources = 'cpu fpu cache prof qelf lint gpu app env main'.split()"),
                 ("cfg['variant']['defs'].split()", "(GPU_DEFS if GPU_DEFS is not None else cfg['variant']['defs'].split())")]:
        assert source.count(o) == 1, o
        source = source.replace(o, n)

    def snapshot(q):
        # Profile snapshot on the first coupled frame (window start) as well.
        if not a.counters: return
        m = (q/'main.c').read_text()
        old = "if (E.sm64_profile && profile && nrows % 60 == 0) {"
        assert m.count(old) == 1
        (q/'main.c').write_text(m.replace(old,
            "if (E.sm64_profile && profile && (nrows % 60 == 0 || (getenv(\"COUPLED_START_FRAME\") && "
            "nrows == (size_t)atoll(getenv(\"COUPLED_START_FRAME\"))))) {"))

    def copy_common(frozen):
        shutil.copytree(a.common_dir.resolve(), frozen/'common', dirs_exist_ok=True)

    ns = dict(__file__=str(cache.BASE/'build.py'), __name__='private_build', PRIVATE_OUT=out,
              PARAMS=params, APPLY_CACHE=apply, SNAPSHOT=snapshot, COPY_COMMON=copy_common,
              GPU_DEFS=manifest['variant']['defs'].split() if manifest else None)
    exec(compile(source, str(cache.BASE/'build.py'), 'exec'), ns)
    ns['main']()

if __name__ == '__main__': main()
