#!/usr/bin/env python3
"""Read already-exported v13 timing evidence; never invoke tools or edit RTL."""
import collections,json,re,shutil
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';A=P/'compact-timing-audit-v13';OLD=P/'compact-timing-audit-v12';OUT=P/'compact-timing-diagnosis-v13'
def family(r):
 t=r['to_node']
 if 'GSharePlugin_logic_mem' in t:return 'cpu_branch_to_gshare'
 if 'BAD_TARGET' in t:return 'cpu_branch_target_compare'
 if 'burst_w_idx' in t or 'lsu_w_sent' in t:return 'cpu_wrapper_write_burst'
 if 'tex_cache' in t:return 'texture_tag_to_state'
 if 'float_RS1' in t:return 'cpu_float_load_forward'
 if '|dsp_a' in t:return 'gpu_projector_operand'
 if 'decode_ctrls_1_up_' in t:return 'cpu_rs1_hazard_to_decode'
 raise AssertionError(t)
def main():
 OUT.mkdir(exist_ok=False)
 new=json.loads((A/'paths.json').read_text());old=json.loads((OLD/'paths.json').read_text());summary=json.loads((A/'result.json').read_text())
 assert summary['below_90_counts']==dict(slow_85=30,slow_0=5)
 assert summary['empty_targeted_reports_with_matched_registers_and_zero_path_count']==['slow_0_span_count_drain','slow_85_span_count_drain']
 groups={};details={}
 for corner in ('slow_85','slow_0'):
  selected=collections.defaultdict(list)
  for row in new[corner+'_below90']:selected[family(row)].append(row)
  groups[corner]={k:dict(endpoints=len(v),worst=min(v,key=lambda r:r['slack_ns']))for k,v in selected.items()}
  blocks=re.split(r'(?=Path #\d+: Setup)',(A/'reports'/f'{corner}_below90.rpt').read_text())[1:]
  details[corner]={}
  for name,rows in selected.items():
   row=rows[0];block=next(b for b in blocks if row['to_node'] in b.split('Data Arrival Path')[0])
   active=False;hops=[]
   for line in block.split('Data Required Path')[0].splitlines():
    fields=[v.strip()for v in line.split(';')]
    if len(fields)>7 and fields[-2]=='data path':active=True;continue
    if active and len(fields)>7 and fields[4]in('CELL','IC'):
     hops.append(dict(increment_ns=float(fields[2]),type=fields[4],fanout=int(fields[5]),element=fields[-2]))
   assert hops;details[corner][name]=dict(path=row,hops=hops)
 fixes={}
 for corner in ('slow_85','slow_0'):
  fixes[corner]={}
  for label in ('span_flags_drain','span_step_state','span_step_values','cpu_rs3_fetch','cpu_writeback','texture_ram','audio_accum','ring_status','geometry','render_cache','sdram'):
   rows=[r for r in new[corner+'_'+label]if r['relationship_ns']==10];assert rows
   fixes[corner][label]=min(rows,key=lambda r:r['slack_ns'])
  assert not new[corner+'_span_count_drain']
 source12=json.loads((P/'compact-lod5-core-v12/prepared.json').read_text())['source_sha256']
 source13=json.loads((P/'compact-lod5-core-v13/prepared.json').read_text())['source_sha256']
 changed=[k for k in source12.keys()|source13.keys()if source12.get(k)!=source13.get(k)]
 assert sorted(changed)==['src/fpga/common/gpu_core.v','src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
 write(OUT/'result.json',dict(readonly=True,fit_invoked=False,rtl_changed=False,complete_census=summary['below_90_counts'],families=groups,changed_file_closure=changed,selected_cones=fixes))
 write(OUT/'mapped-cones.json',details)
 report='''Compact-v13 routed timing diagnosis

The two GPU span-control changes and CPU fetch-hazard factor worked on their
intended paths. They do not close the complete routed implementation:
86.700 MHz slow85 and 87.382 MHz slow0, configured clock 100 MHz.
Setup remains -1.534 ns; hold +0.107 ns. Both the comparative >90 MHz gate
and configured 100 MHz timing gate remain false.

The complete sub-90 endpoint census is 30 hot / 5 cold (v12 was 46 / 23).
Both searches examined 1000 endpoints and ended above the threshold. There
are no equal-slack launch ties in this census. No report truncation or empty
report was treated as evidence of closure.

Effects of the selected changes:
* Count RAM -> accumulator/write-request drain: no combinational timing paths
  remain, with 49 matched count-RAM registers and 65 matched drain endpoints
  in each corner. This is an actual removed path, not an unmatched query.
  The replacement current-valid/count-valid -> drain path is 112.410 MHz hot
  and 112.334 cold. The v12 RAM path was 88.300/87.032 MHz.
* calc_step -> main state: 103.659/103.648 MHz, versus the v12 limiting
  86.513/87.336 MHz. calc_step -> span start values is 113.507/112.322 MHz.
  The encoded next-step data remains, but terminal control no longer depends
  on encoding and then comparing the NONE value.
* RS3 physical index -> fetch/aligner: 93.327/96.219 MHz, five logic levels;
  v12 dispatch/fetch was 88.865/90.236 MHz. This is the selected RS3 path,
  not a claim that all CPU hazard paths meet 90 MHz.

Current failing families:
1. Hot worst, five GShare endpoints: execute_ctrl3 BRANCH_CTRL[0] -> GShare
   read address bit7, 86.700 MHz, seven levels. The mapped chain is WideNor1,
   buffered learn taken, History onFetch valid, PC aggregator onehot,
   fetch WORD_PC mux (two cells), and GShare read-address XOR. Routing is
   8.834 ns of the 11.188 ns data path (79%). This is a branch/history/next-PC
   path into the predictor, not the fixed decode RS3 fetch-ready path.
2. Cold worst, one BTB endpoint: execute_ctrl2 BRANCH_CTRL[1] -> early branch
   BAD_TARGET, 87.382 MHz, nine levels. It traverses target-A selection, the
   target adder, PC_TRUE mux, then six serial Equal256 cells (~10/11/12/13/
   14/16). Logic accounts for 6.803 ns; routing 4.385 ns. The equality is a
   distinct potential factorization target; no source change is made here.
3. Three CPU-wrapper endpoints in both corners: wr_head[1] -> burst_w_idx
   and lsu_w_sent, worst87.673/88.590 MHz, eight levels. Actual chain:
   write-address mux -> decode_target -> match01 -> match02 -> burst_awlen
   encoder -> per_awlen -> lsu_w_sent -> burst index. Routing is 7.574 ns
   hot/7.156 cold. cpu_system source is unchanged from v12.
4. One texture state endpoint in both corners: tag RAM -> state.S_INIT,
   89.254/88.261 MHz, seven levels. RAM tag -> comb~3/~8 -> state~12/~18/
   ~19/~22/~23. This is hit/control-to-state, not the previously repaired
   tag-to-data-RAM address path, which now passes105.441/105.053 MHz.
5. Hot-only CPU RS1 hazard -> decode ownership: 19 endpoints at89.437..89.815
   MHz. execute_ctrl7 RD_PHYS -> RS1 hazard comparators -> WideOr115 ->
   hazard -> decode1 ready -> decode0 ready -> predictor/PC capture enables.
   The worst path has seven levels and 6.090 ns routing; decode ready nets
   have fanouts138/151. It is a different source/endpoint from selected RS3.
6. Hot-only floating load-forward endpoint: cache ways-hit[1] -> float RS1[5],
   88.613 MHz, six levels. Cache data mux -> formatter mux -> float writeback
   merge -> RS1 forward select. Routing is8.284/10.988 ns (~75%).
7. Hot-only GPU projector operand: XF_PROJ_YA -> dsp_a[29],89.518 MHz,
   five levels,8.503/11.228 ns routing (~76%). This is the projector owner mux,
   not span control. Its required improvement to90 is only~60 ps, but no
   isolated improvement can be assumed to preserve the remaining placement.

Prior local fixes still exceed90: cached-load -> integer regfile96.154/96.488;
audio accumulator95.456/90.992; ring STATUS104.102/104.866; geometry93.041/
91.827; render cache100.980/99.305; SDRAM main-clock97.733/101.010 MHz.
These path bounds do not imply100 MHz closure or native Pocket FPS.

Source closure changes exactly gpu_core.v and generated VexiiRiscv_os30.v
from v12. The predictor arithmetic, texture cache, CPU wrapper and all clocks
are unchanged; their new criticality reflects the new physical implementation.
No further fit, physical setting, RTL, production or device write was made.
'''
 (OUT/'REPORT.txt').write_text(report);shutil.copy2(__file__,OUT/'recipe.py')
 files=[OUT/'result.json',OUT/'mapped-cones.json',OUT/'REPORT.txt',OUT/'recipe.py',Path(__file__),A/'manifest.json',OLD/'manifest.json',P/'compact-lod5-core-v13/final-manifest.json',P/'compact-lod5-core-v13/prepared.json',P/'compact-lod5-core-v12/prepared.json']
 write(OUT/'manifest.json',dict(passed_readonly_audit=True,timing_closed=False,fit_invoked=False,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in files}))
 print(sha(OUT/'manifest.json'));print(json.dumps({c:{k:v['endpoints']for k,v in fam.items()}for c,fam in groups.items()}))
if __name__=='__main__':main()
