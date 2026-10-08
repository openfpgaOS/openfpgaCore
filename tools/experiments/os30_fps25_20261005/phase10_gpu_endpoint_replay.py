#!/usr/bin/env python3
"""Replay the combined GPU with the literal original step-writer shadow."""
from pathlib import Path
import hashlib,json,re,shutil,sys
import phase10_gpu_queue_finality_v18 as q
import phase10_gpu_span_step_shadow_v3 as shadow
R=q.R;P=q.P;O=P/'gpu-endpoint-replay-v1';C=P/'gpu-endpoint-control-v1'
BASE=P/'gpu-queue-finality-review-v18/model'
sha=q.sha;put=q.save
q.REVIEW=O

BLEND_PROBE='''reg [63:0] endpoint_blend_checks=0;
wire [31:0] endpoint_blend_mask={{8{gpu.fb_acc_mask[3]}},{8{gpu.fb_acc_mask[2]}},{8{gpu.fb_acc_mask[1]}},{8{gpu.fb_acc_mask[0]}}};
wire [31:0] endpoint_blend_old_word=gpu.cbw_p2_acc_same_w
 ? ((gpu.cbw_p2_word_w & ~endpoint_blend_mask) | (gpu.fb_acc_data & endpoint_blend_mask))
 : gpu.cbw_p2_word_w;
wire [15:0] endpoint_blend_old=gpu.p2_fb_addr[1] ? endpoint_blend_old_word[31:16] : endpoint_blend_old_word[15:0];
always @(posedge clk)if(reset_n&&!gpu.soft_reset)begin
 if(gpu.cbw_p2_dst_w!==endpoint_blend_old)$fatal(1,"blend halfword overlay differs");
 endpoint_blend_checks<=endpoint_blend_checks+1;
end
final $display("ENDPOINT_BLEND_AUDIT checks=%0d",endpoint_blend_checks);
'''

def prepare():
 assert sha(C/'manifest.json')=='2e6690eaf44158ff8ca4cc654473235ba7a7bfff03a67b897910623fa136e213'
 assert sha(C/'gpu_core.v')=='5e9dd586e64034ba7b9f2bb97d9f4b8cba20c772da21d263f38ad74049269e5a'
 assert sha(P/'gpu-span-step-enable-v1/manifest.json')=='6d77ccefbe528425adcafe4ae1ea468c85cfc8f2f3e463d41710f94d5ef5e025'
 O.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',O/'model/frozen')
 gpu=O/'model/frozen/common/gpu_core.v'
 # The selected lint source already has exactly all functional changes and
 # the retained original DSP-A monitor; instrument only the two step registers.
 gpu.write_text(shadow.instrument_shadow((C/'lint/gpu_core.v').read_text()))
 tb=O/'model/frozen/tb_gpu.v';s=tb.read_text();assert s.count('endmodule')==1
 tb.write_text(s.replace('endmodule',shadow.PROBE+'\n'+BLEND_PROBE+'\nendmodule'))
 command=[x.replace(str(BASE),str(O/'model')) for x in json.loads((BASE/'command.json').read_text())]
 put(O/'model/command.json',command);shutil.copyfile(__file__,O/'recipe.py')
 files=[Path(__file__),Path(shadow.__file__),Path(q.__file__),C/'manifest.json',C/'gpu_core.v',C/'lint/gpu_core.v',P/'gpu-span-step-enable-v1/manifest.json',BASE/'command.json']
 put(O/'prepared.json',dict(candidate_sha256=sha(C/'gpu_core.v'),source_sha256={str(p.relative_to(O)):sha(p) for p in (O/'model/frozen').rglob('*') if p.is_file()},evidence_sha256={str(p.relative_to(R)):sha(p) for p in files},baseline='Existing owner/queue proof replay; prior exact queue/color components bridge baseline to immediate parent. Combined native immediate-parent comparison is a separate required gate.',new_simulation_probes='Original full nine-writer step shadows and literal blend overlay; no probe drives DUT.'))

def seal():
 native=json.loads((O/'result.json').read_text());abort=json.loads((O/'abort/result.json').read_text())
 assert native['passed'] and abort['passed']
 logs=list((O/'runs').glob('*/run.log'))+list((O/'abort').glob('candidate-*.log'));assert len(logs)==20
 totals={}
 for log in logs:
  s=log.read_text();assert 'FATAL' not in s
  for tag in ['SPAN_STEP_AUDIT','ENDPOINT_BLEND_AUDIT']:
   m=re.search(tag+r' (.*)',s);assert m,(log,tag)
   for key,value in re.findall(r'(\w+)=(\d+)',m[1]):totals[tag+':'+key]=totals.get(tag+':'+key,0)+int(value)
 assert all(v>0 for v in totals.values()),totals
 prep=json.loads((O/'prepared.json').read_text())
 for n,h in prep['source_sha256'].items():assert sha(O/n)==h,n
 for n,h in prep['evidence_sha256'].items():assert sha(R/n)==h,n
 put(O/'endpoint-result.json',dict(passed=True,replay_pairs=16,abort_pairs=4,exact_outputs_and_cycles=True,totals=totals,full_original_step_writer_chain_compared_each_edge=True,limits=['The existing four real-MMIO abort cases precede first pixels; this is not arbitrary active-AXI cancellation coverage.','Immediate-parent complete CPU/native comparison and routed timing remain separate gates.']))
 files={str(p.relative_to(O)):sha(p) for p in O.rglob('*') if p.is_file() and 'obj' not in p.parts}
 put(O/'manifest.json',dict(passed=True,files=files,evidence_sha256=prep['evidence_sha256']))
 print(json.dumps(totals,indent=2));print('MANIFEST',sha(O/'manifest.json'))

if __name__=='__main__':
 for action in sys.argv[1:]:
  if action in ['prepare','seal']:globals()[action]()
  else:getattr(q,action)()
