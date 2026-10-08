#!/usr/bin/env python3
"""Private retained-A vertex cache repack; no production changes."""
import argparse
import concurrent.futures
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT/'docs/measurements/os30-fps25-20261005'
OUT = CAMP/'phase10/vcache-narrow-v1'
BASE = CAMP/'phase4/texmem/extended'
GPU = BASE/'frozen/common/gpu_core.v'
OLD_SHA = '2aec324292a0deebb8f3d728dfe7fea29b486e4024fc8f43ea29dafd4da3df5b'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, obj): p.write_text(json.dumps(obj, indent=2)+'\n')
def replace(s, old, new):
    assert s.count(old) == 1, (s.count(old), old[:90])
    return s.replace(old, new)


def prepare():
    OUT.mkdir(exist_ok=False)
    assert sha(GPU) == OLD_SHA
    old = GPU.read_text()
    s = old
    s = replace(s, 'localparam S_PAY_WAIT         = 6\'d18;', 'localparam S_VCWRITE          = 6\'d19; // Serialize six packed vertex words\nlocalparam S_PAY_WAIT         = 6\'d18;')
    start = s.index('localparam VC_SLOTS = ')
    stop = s.index('reg        xf_to_cache;', start)
    s = s[:start] + '''// Six 32-bit planes, each containing all 32 slots.  The last plane's
// upper 16 bits are unused.  192x32 fits one Cyclone V M10K (vs five for
// 32x176); externally the cache still has the original 32 vertices.
localparam VC_WORDS = (INCLUDE_VTX_CACHE != 0) ? 192 : 1;
(* ramstyle = "M10K, no_rw_check" *) reg [31:0] vc_mem [0:VC_WORDS-1];
reg [31:0] vc_q;
reg [7:0] vc_raddr; // {word 0..5, vertex slot 0..31}
reg [4:0] vcr_cnt;  // one prime + eighteen word captures
reg [2:0] vcw_cnt;
reg vcw_explicit_depth;
''' + s[stop:]
    s = replace(s, '''                if (GEOMETRY_WORKER && INCLUDE_VTX_CACHE && tri_state_valid)
                    vc_mem[xf_load_slot] <= {vt_brow[0],vt_lrow[0],vt_rrow[0],
                        xf_load_depth,dv_tzi[0],dv_szi[0],vt_zi[0],xf_sy[0],xf_sx[0]};
                state <= S_IDLE;''', '''                if (GEOMETRY_WORKER && INCLUDE_VTX_CACHE && tri_state_valid) begin
                    vc_mem[{3'd0, xf_load_slot}] <= {xf_sy[0], xf_sx[0]};
                    vcw_cnt <= 3'd1;
                    vcw_explicit_depth <= 1'b1;
                    state <= S_VCWRITE;
                end else state <= S_IDLE;''')
    s = replace(s, '''                    vc_raddr <= vc_i0;
                    vcr_cnt  <= 2'd0;''', '''                    vc_raddr <= {3'd0, vc_i0};
                    vcr_cnt  <= 5'd0;''')
    first = s.index('                        vc_mem[xf_load_slot] <= { vt_brow[0]')
    end = s.index('                    end else if (xf_behind', first)
    original_write = s[first:end]
    assert 'state <= S_PAY_DATA;' in original_write
    s = s[:first] + '''                        vc_mem[{3'd0, xf_load_slot}] <= {xf_sy[0], xf_sx[0]};
                        vcw_cnt <= 3'd1;
                        vcw_explicit_depth <= xf_clip;
                        state <= S_VCWRITE;
''' + s[end:]
    first = s.index('        // T3: sequential 3-vertex cache read for 0x54 DRAW_INDEXED_TRI.')
    end = s.index('        // Triangle record fill', first)
    old_read = s[first:end]
    launch_start = old_read.index('                    spanprod_idx ')
    launch_end = old_read.index('\n                end\n            endcase', launch_start)
    launch = old_read[launch_start:launch_end]
    lines = ['''        // Serialize the remainder of a cache write from the existing projection
        // staging registers.  No next command is consumed until all six words
        // are committed, preserving immediate LOAD -> DRAW and repeated slots.
        S_VCWRITE: begin
            case (vcw_cnt)
                3'd1: vc_mem[{3'd1, xf_load_slot}] <= vt_zi[0];
                3'd2: vc_mem[{3'd2, xf_load_slot}] <= dv_szi[0];
                3'd3: vc_mem[{3'd3, xf_load_slot}] <= dv_tzi[0];
                3'd4: vc_mem[{3'd4, xf_load_slot}] <= vcw_explicit_depth ? xf_load_depth : vt_depth[0];
                default: vc_mem[{3'd5, xf_load_slot}] <= {16'd0, vt_brow[0], vt_lrow[0], vt_rrow[0]};
            endcase
            if (vcw_cnt == 3'd5) begin
                state <= S_IDLE;
                if (SM64_GEOMETRY && cmd_class == CMDCLS_GEOMETRY && pay_remaining != 0) begin
                    pay_idx <= 6'd0;
                    ring_rdptr <= ring_rdptr + 1'b1;
                    ring_rd_addr <= ring_rdptr + 1'b1;
                    state <= S_PAY_DATA;
                end
            end else vcw_cnt <= vcw_cnt + 3'd1;
        end

        // Eighteen sequential read captures use existing derive staging.  The
        // registered RAM read has one prime edge; the next address is queued
        // one capture ahead.  Read and write commands remain mutually exclusive.
        S_VCREAD: begin : s_vcread_blk
            if ((INCLUDE_VTX_CACHE != 0) && (INCLUDE_XFORM_RGB != 0)) begin
            case (vcr_cnt)''']
    for count in range(19):
        lines.append(f"                5'd{count}: begin")
        if count:
            v, w = divmod(count-1, 6)
            assigns = [f'tri_v{v}_x <= vc_q[15:0]; tri_v{v}_y <= vc_q[31:16];',
                       f'vt_zi[{v}] <= vc_q;', f'dv_szi[{v}] <= vc_q;',
                       f'dv_tzi[{v}] <= vc_q;', f'vt_depth[{v}] <= vc_q;',
                       f'vt_rrow[{v}] <= vc_q[4:0]; vt_lrow[{v}] <= vc_q[10:5]; vt_brow[{v}] <= vc_q[15:11];']
            lines.append('                    '+assigns[w])
        if count < 17:
            v, w = divmod(count+1, 6)
            lines.append(f"                    vc_raddr <= {{3'd{w}, vc_i{v}}};")
        if count == 18:
            lines.append(launch)
        else:
            lines.append(f"                    vcr_cnt <= 5'd{count+1};")
        lines.append('                end')
    lines.extend(['''                default: state <= S_IDLE;
            endcase
            end
        end

        // ============================================================
'''])
    s = s[:first] + '\n'.join(lines) + s[end:]
    # Each cache write is now a single 32-bit port.  No packed writes survive.
    assert 'VC_W' not in s.replace('VC_WORDS', '').replace('S_VCWRITE', '')
    assert 'vc_mem[xf_load_slot]' not in s
    (OUT/'gpu_core.v').write_text(s)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True), s.splitlines(True), fromfile='retained-A/gpu_core.v', tofile='narrow/gpu_core.v')))
    save(OUT/'prepared.json', dict(original_sha256=sha(GPU), candidate_sha256=sha(OUT/'gpu_core.v'), recipe_sha256=sha(Path(__file__)), scope='Only private gpu_core source; unchanged packet ABI/32 slots; 5 extra write cycles per vertex and 15 extra read cycles per indexed triangle expected'))
    print(sha(OUT/'gpu_core.v'))


def build():
    assert sha(OUT/'gpu_core.v') == json.loads((OUT/'prepared.json').read_text())['candidate_sha256']
    model = OUT/'model'
    model.mkdir(exist_ok=False)
    frozen = model/'frozen'
    shutil.copytree(BASE/'frozen', frozen)
    shutil.copy2(OUT/'gpu_core.v', frozen/'common/gpu_core.v')
    before = {str(p.relative_to(BASE/'frozen')): sha(p) for p in (BASE/'frozen').rglob('*') if p.is_file()}
    after = {str(p.relative_to(frozen)): sha(p) for p in frozen.rglob('*') if p.is_file()}
    assert before.keys() == after.keys()
    assert [p for p in before if before[p] != after[p]] == ['common/gpu_core.v']
    save(model/'inputs.json', after)
    command = json.loads((BASE/'model/build.json').read_text())['command']
    command = [x.replace(str(BASE/'frozen'), str(frozen)).replace(str(BASE/'model/obj'), str(model/'obj')) for x in command]
    with (model/'build.log').open('x') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    save(model/'build.json', dict(command=command, simulator_sha256=sha(model/'obj/Vtb_gpu'), inputs_sha256=sha(model/'inputs.json')))
    print('build passed')


def replay(frames):
    spec = importlib.util.spec_from_file_location('depth_adapter', Path(__file__).with_name('phase4_combined_validate.py'))
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    resultdir = OUT/'replays'
    resultdir.mkdir(exist_ok=False)
    sims = {'baseline': BASE/'model/obj/Vtb_gpu', 'narrow': OUT/'model/obj/Vtb_gpu'}
    for mode, exe in sims.items():
        p = BASE/'model/build.json' if mode == 'baseline' else OUT/'model/build.json'
        assert sha(exe) == json.loads(p.read_text())['simulator_sha256']
    inputs = {}
    for frame in frames:
        source = CAMP/'phase9/checks-state-f11/capture-normal/gpuvec'/f'frame_{frame:05}.vec'
        dest = resultdir/f'frame_{frame:05}.vec'
        commands, depth, tags = adapter.with_depth(source, dest)
        assert depth
        inputs[str(frame)] = dict(source_sha256=sha(source), vector_sha256=sha(dest), commands=commands, tags=tags)
    save(resultdir/'inputs.json', inputs)
    def one(job):
        mode, traffic, frame = job
        exe = sims[mode]
        dest = resultdir/mode/str(traffic)
        dest.mkdir(parents=True, exist_ok=True)
        stem = dest/f'frame_{frame:05}'
        command = [str(exe), str(resultdir/f'frame_{frame:05}.vec'), str(stem.with_suffix('.bin')), f'+memory_traffic={traffic}', '+tagged_tex=1']
        with stem.with_suffix('.log').open('x') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
        raw = stem.with_suffix('.bin').read_bytes()
        assert len(raw) == 307212
        stats = {k:int(v) for k,v in re.findall(r'(\w+)=(\d+)', stem.with_suffix('.log').read_text())}
        assert stats['cram_errors'] == 0 and stats['words'] == stats['readback_words']
        return dict(mode=mode, traffic=traffic, frame=frame, stats=stats, output_sha256=sha(stem.with_suffix('.bin')), log_sha256=sha(stem.with_suffix('.log')), simulator_sha256=sha(exe), command=command)
    jobs = [(mode,t,f) for mode in sims for t in (0,1,2) for f in frames]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(one,jobs))
    save(resultdir/'runs.json', rows)
    results = []
    bykey = {(r['mode'],r['traffic'],r['frame']):r for r in rows}
    for traffic in (0,1,2):
        for frame in frames:
            old = (resultdir/'baseline'/str(traffic)/f'frame_{frame:05}.bin').read_bytes()
            new = (resultdir/'narrow'/str(traffic)/f'frame_{frame:05}.bin').read_bytes()
            b = bykey['baseline',traffic,frame]['stats']
            c = bykey['narrow',traffic,frame]['stats']
            results.append(dict(traffic=traffic, frame=frame, color_exact=old[:153600] == new[:153600], depth_exact=old[153612:] == new[153612:], fence_exact=old[153600:153604] == new[153600:153604], baseline_cycles=b['cycles'], narrow_cycles=c['cycles'], cycle_change_percent=100*(c['cycles']/b['cycles']-1)))
    save(OUT/'replay-results.json', dict(passed=all(r['color_exact'] and r['depth_exact'] and r['fence_exact'] for r in results), comparisons=results, limitation='Serial GPU command replay with retained real memory models; excludes CPU producer overlap, not application FPS.'))
    assert all(r['color_exact'] and r['depth_exact'] and r['fence_exact'] for r in results)
    print(json.dumps(results))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','build','replay'])
    parser.add_argument('--frames', default='40,120,200,360,400,500,600,720')
    args = parser.parse_args()
    if args.action == 'prepare': prepare()
    elif args.action == 'build': build()
    else: replay([int(x) for x in args.frames.split(',')])
