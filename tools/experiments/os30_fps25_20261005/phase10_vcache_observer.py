#!/usr/bin/env python3
"""Passive full-176-bit cache and derive-staging checks on actual narrow GPU."""
import hashlib,json,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/vcache-narrow-v1'
DEST=OUT/'observer'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
DEST.mkdir(exist_ok=False)
frozen=DEST/'frozen';shutil.copytree(OUT/'model/frozen',frozen)
p=frozen/'tb_gpu.v';s=p.read_text();assert s.count('endmodule')==1
monitor='''
// Passive independently wide golden cache. It samples the preexisting
// projection fields at the exact old packed-write point, not the new serial
// cache's data. It checks all 176 stored bits and all three derive vertices.
reg [175:0] vc_gold [0:31];
reg [31:0] vc_gold_valid;
reg vc_check_pending;
reg [4:0] vc_check_slot;
reg [5:0] vc_prev_state;
integer vc_checked_writes, vc_checked_triangles;
always @(posedge clk) begin
    if (!reset_n || gpu.soft_reset) begin
        vc_gold_valid <= 0;
        vc_check_pending <= 0;
        vc_prev_state <= 0;
        vc_checked_writes <= 0;
        vc_checked_triangles <= 0;
    end else begin
        vc_prev_state <= gpu.state;
        vc_check_pending <= 0;
        if (gpu.state == gpu.S_XFORM && gpu.xf_state == gpu.XF_PROJ_LAUNCH &&
            (gpu.SINGLE_CLIP_VERTEX || gpu.SM64_GEOMETRY || gpu.xf_to_cache)) begin
            vc_gold[gpu.xf_load_slot] <= {gpu.vt_brow[0],gpu.vt_lrow[0],gpu.vt_rrow[0],
                (gpu.xf_clip ? gpu.xf_load_depth : gpu.vt_depth[0]),
                gpu.dv_tzi[0],gpu.dv_szi[0],gpu.vt_zi[0],gpu.xf_sy[0],gpu.xf_sx[0]};
            vc_gold_valid[gpu.xf_load_slot] <= 1;
        end
        if (gpu.state == gpu.S_VCWRITE && gpu.vcw_cnt == 5) begin
            vc_check_pending <= 1;
            vc_check_slot <= gpu.xf_load_slot;
        end
        if (vc_check_pending) begin
            if (!vc_gold_valid[vc_check_slot]) $fatal(1,"unmodeled cache write");
            if ({gpu.vc_mem[{3'd5,vc_check_slot}][15:0],
                 gpu.vc_mem[{3'd4,vc_check_slot}],gpu.vc_mem[{3'd3,vc_check_slot}],
                 gpu.vc_mem[{3'd2,vc_check_slot}],gpu.vc_mem[{3'd1,vc_check_slot}],
                 gpu.vc_mem[{3'd0,vc_check_slot}]} !== vc_gold[vc_check_slot])
                $fatal(1,"narrow cache 176-bit write mismatch slot=%0d",vc_check_slot);
            vc_checked_writes <= vc_checked_writes + 1;
        end
        if (vc_prev_state == gpu.S_VCREAD && gpu.state == gpu.S_TRI_DERIVE) begin
'''
for i in range(3):
 monitor+=f'''            if (!vc_gold_valid[gpu.vc_i{i}]) $fatal(1,"read uninitialized golden slot");
            if ({{gpu.vt_brow[{i}],gpu.vt_lrow[{i}],gpu.vt_rrow[{i}],gpu.vt_depth[{i}],
                  gpu.dv_tzi[{i}],gpu.dv_szi[{i}],gpu.vt_zi[{i}],gpu.tri_v{i}_y,gpu.tri_v{i}_x}} !== vc_gold[gpu.vc_i{i}])
                $fatal(1,"narrow cache 176-bit derive mismatch vertex={i} slot=%0d",gpu.vc_i{i});
'''
monitor+='''            vc_checked_triangles <= vc_checked_triangles + 1;
        end
    end
end
final $display("VC_OBSERVER writes=%0d triangles=%0d",vc_checked_writes,vc_checked_triangles);
'''
p.write_text(s.replace('endmodule',monitor+'\nendmodule'))
command=json.loads((OUT/'model/build.json').read_text())['command'];command=[x.replace(str(OUT/'model/frozen'),str(frozen)).replace(str(OUT/'model/obj'),str(DEST/'obj')) for x in command]
with (DEST/'build.log').open('x') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
exe=DEST/'obj/Vtb_gpu';save(DEST/'build.json',dict(command=command,simulator_sha256=sha(exe),gpu_source_sha256=sha(frozen/'common/gpu_core.v'),tb_source_sha256=sha(p)))
rows=[]
for label,source,reference in [('normal',OUT/'replays/frame_00360.vec',OUT/'replays/narrow/0/frame_00360.bin'),('shadow',OUT/'slot-tests/shadow.vec',OUT/'slot-tests/shadow-narrow-0.bin')]:
 dest=DEST/label;command=[str(exe),str(source),str(dest.with_suffix('.bin')),'+memory_traffic=0','+tagged_tex=1']
 with dest.with_suffix('.log').open('x') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
 text=dest.with_suffix('.log').read_text();print(text[-1000:])
 assert dest.with_suffix('.bin').read_bytes()==reference.read_bytes()
 counts=re.findall(r'VC_OBSERVER writes=(\d+) triangles=(\d+)',text)
 # Existing replay exits without top.final(); absence of final summary does
 # not disable fatal assertions, but exact command counts remain bound by
 # the independently parsed slot-tests inputs / original vector.
 rows.append(dict(label=label,output_exact=True,observer_counts=counts,output_sha256=sha(dest.with_suffix('.bin')),log_sha256=sha(dest.with_suffix('.log')),source_sha256=sha(source),command=command))
save(DEST/'result.json',dict(passed=True,scope='Passive assertions on all176 cache bits and three derive staging vertices; original source write expression as golden.',runs=rows,recipe_sha256=sha(Path(__file__))))
