#!/usr/bin/env python3
"""Independent source closure and proof audit for geometry scan and FP RS3 mux cuts."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
G=P/'unified-load-components-v3';C=P/'cpu-fp-rs3-mux-v1';OUT=P/'timing-mux-independent-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
evidence={}
def bind(p):evidence[str(p.relative_to(ROOT))]=sha(p)
def manifest(p):
 j=json.loads(p.read_text());entries=j.get('evidence_sha256',j)
 assert isinstance(entries,dict)
 for k,h in entries.items():
  if not isinstance(h,str)or len(h)!=64:continue
  f=ROOT/k if (ROOT/k).is_file() else p.parent/k
  assert sha(f)==h,k
 bind(p)
# Whole CPU differs only in the reviewed assignment; generator/ISA/control untouched.
b=(C/'baseline.v').read_text();c=(C/'VexiiRiscv.v').read_text()
old=(C/'original-assignment.txt').read_text().rstrip('\n');new=(C/'candidate-assignment.txt').read_text().rstrip('\n')
assert b.count(old)==1 and b.replace(old,new)==c
assert sha(C/'baseline.v')==sha(P/'compact-lod5-core-v2/frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
probe=(C/'probe.v').read_text();prefix='execute_lane0_bypasser_float_RS3';output='execute_ctrl1_down_float_RS3_lane0'
a=b.index('  assign _zz_'+prefix+'_bypassEnables_bools_0 =');z=b.index(old,a)+len(old)
assert b[a:z].replace('assign '+output+' =','assign old_result =')in probe
assert new.replace('assign '+output+' =','assign new_result =')in probe
for line in re.findall(r'^  assign _zz_'+output+r'(?:_\d+)? = .*?;$',b,re.M):assert line in probe
r=json.loads((C/'combinational/result.json').read_text());assert r['passed']and r['selector_vectors']==1024 and r['bit_basis_per_selector']==320 and r['checks']==355328
# Only expose existing synchronous XYZW result and replace its SCAN consumers.
parent=P/'unified-load-components-v2/frozen'
g0=(parent/'geometry_transform_light.v').read_text();g1=(G/'frozen/geometry_transform_light.v').read_text()
expected=g0.replace('output wire [31:0] output_data,output wire [23:0] output_rgb','output wire [31:0] output_data,output wire [31:0] output_position_data,output wire [23:0] output_rgb')
addition=' // Raw clip SCAN only reads XYZW, so expose the same synchronous RAM result\n // without the idle inverse-view mux. No new port on the underlying RAM.\n assign output_position_data=acc_q;\n'
expected=expected.replace(' assign output_data=output_inverse?inv_q:acc_q;\n',' assign output_data=output_inverse?inv_q:acc_q;\n'+addition);assert expected==g1
r0=(parent/'geometry_raw_load.v').read_text();r1=(G/'frozen/geometry_raw_load.v').read_text()
expected=r0.replace('wire [31:0] compute_data;','wire [31:0] compute_data,compute_position_data;').replace('.output_data(compute_data),.output_rgb(compute_rgb)', '.output_data(compute_data),.output_position_data(compute_position_data),.output_rgb(compute_rgb)').replace('float_lt(compute_data,neg_w),float_lt(clip_w,compute_data)','float_lt(compute_position_data,neg_w),float_lt(clip_w,compute_position_data)').replace('0:clip_w<=compute_data;','0:clip_w<=compute_position_data;')
assert r1.startswith(expected[:expected.rfind('endmodule')])
assert r1[r1.rfind('`ifdef GEOMETRY_SCAN_CHECKS'):]=='''`ifdef GEOMETRY_SCAN_CHECKS
 always @(posedge clk) if(!reset && state==SCAN && read_valid) begin
  assert(!compute_busy);
  assert(compute_position_data==compute_data);
 end
`endif
endmodule
'''
assert "state==SCAN?{1'b0,scan_coordinate,global_scan_vertex}:output_addr"in r1
assert 'read_valid<=scan_issue;read_row<=scan_row;read_vertex<=scan_vertex;'in r1
assert 'output_inverse<=output_addr[7];'in g1
assert 'acc_q<=acc[busy?{row,job_dest+vertex}:output_addr[6:0]];'in g1
wr=json.loads((G/'wrapper/result.json').read_text());assert wr['passed']and wr['cycle_and_output_counts_equal_v2']and wr['assert_unmuxed_scan_equivalent']
cmd=json.loads((G/'wrapper/build-command.json').read_text());assert '--assert'in cmd and '+define+GEOMETRY_SCAN_CHECKS'in cmd
manifest(G/'manifest.json');manifest(C/'manifest.json');manifest(P/'lod5-live-native-v2/manifest.json')
for p in [C/'baseline.v',C/'VexiiRiscv.v',C/'original-assignment.txt',C/'candidate-assignment.txt',C/'probe.v',C/'combinational/main.cpp',C/'combinational/result.json',G/'frozen/geometry_transform_light.v',G/'frozen/geometry_raw_load.v',G/'wrapper/result.json',G/'wrapper/build-command.json',G/'arithmetic/result.json',Path(__file__)]:bind(p)
OUT.mkdir(exist_ok=False)
report=dict(passed=True,deployment_ready=False,scope='Read-only independent source/proof review; actual timing/placement remains a separate gate.',cpu_sha256=sha(C/'VexiiRiscv.v'),geometry_sha256={n:sha(G/'frozen'/n)for n in['geometry_transform_light.v','geometry_raw_load.v']},
 findings=['CPU source is exactly the original queued CPU with one RS3 assignment replaced by combinational wires. Matching, writeback payloads, pipeline state, enables and all other source are unchanged.','Original selector implements lowest-index enabled stage. Each new3-input group chooses that same local minimum; outer group priority chooses the global minimum, with original register-file and all-zero behavior.','For fixedbinary enables these expressions are bitwise selections, so320 independent payloadbit bases plus all1024 selectors prove binary data equivalence; specialfloatpayload patterns require no numerical assumption.','Geometry adds no RAM port, register, arithmetic or cycle. It exposes existing acc_q; rawSCAN consumes it only after arithmetic done.','Every SCAN request forces output_addr[7]=0. output_inverse and acc_q are registered on the same edge, while rawread_valid is delayed once. Thus first, steady, and last valid scan reads equal the old inverse-mux output. Reset clears rawread_valid, and no external alias port is consumed until the existing completion boundary.','Wrapper proof enables runtime unmuxed/muxed equality and idle assertions; all wrapper cycles/output counts equal v2. Independent native/random arithmetic and context/lifetime proofs are bound.','Prior actualCPU/liveLOD5 proof is bound as selected-v2 integration evidence; a fresh combined nominal run is required to bind the upcoming composed source.'],limits=['Two-state valid hardware data equivalence; no claim of identical X-propagation in four-state simulation.','This review authorizes source composition, not production or device deployment.','No area or clock claim follows from the combinational factoring alone.'],evidence_sha256=evidence)
(OUT/'review.json').write_text(json.dumps(report,indent=2)+'\n');print(sha(OUT/'review.json'))
