#!/usr/bin/env python3
"""Exact final integer DATA byte/half/word plane; architectural control unchanged."""
import difflib,hashlib,importlib.util,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-aligner-enable-v9';O=P/'cpu-writeback-plane-v10'
CPU=Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
OLD="  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = (execute_ctrl4_up_LANE_SEL_lane0 ? lane0_IntFormatPlugin_logic_stages_2_wb_payload : 32'h0);"
NEW='''  // Prequalify the format controls so late load sign bits enter one DATA
  // AND/OR plane. Original raw source masks and architectural validity remain.
  (* keep *) wire os30_wb_copy_byte = execute_ctrl4_up_LANE_SEL_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 != 2'b00);
  (* keep *) wire os30_wb_copy_upper = execute_ctrl4_up_LANE_SEL_lane0 &&
      execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0[1];
  (* keep *) wire os30_wb_sign_byte = execute_ctrl4_up_LANE_SEL_lane0 &&
      execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00);
  (* keep *) wire os30_wb_sign_half = execute_ctrl4_up_LANE_SEL_lane0 &&
      execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b01);
  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = {
      (({16{os30_wb_copy_upper}} & lane0_IntFormatPlugin_logic_stages_2_raw[31:16]) |
       {16{(os30_wb_sign_byte && lane0_IntFormatPlugin_logic_stages_2_raw[7]) ||
           (os30_wb_sign_half && lane0_IntFormatPlugin_logic_stages_2_raw[15])}}),
      (({8{os30_wb_copy_byte}} & lane0_IntFormatPlugin_logic_stages_2_raw[15:8]) |
       {8{os30_wb_sign_byte && lane0_IntFormatPlugin_logic_stages_2_raw[7]}}),
      ({8{execute_ctrl4_up_LANE_SEL_lane0}} & lane0_IntFormatPlugin_logic_stages_2_raw[7:0])};'''

def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def module(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'tools/experiments/os30_fps25_20261005'/('phase10_'+name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def prepare():
 assert sha(BASE/'VexiiRiscv.v')=='b511dba179677c9f2b3fe433caeb0c5399a4fe1dc688e5d85587f6888d9cbeab'
 s=(BASE/'VexiiRiscv.v').read_text();assert s.count(OLD)==1
 t=s.replace(OLD,NEW);O.mkdir(exist_ok=False);(O/'VexiiRiscv.v').write_text(t);shutil.copy2(__file__,O/'recipe.py')
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),t.splitlines(True),fromfile='cpu-b511',tofile='writeback-plane')))
 save(O/'prepared.json',dict(parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),
  added_state=0,added_cycles=0,only_final_stage2_DATA_assignment_changed=True,
  wb_valid_fire_commit_cancel_raw_source_masking_and_original_formatter_unchanged=True,
  timing_manifest_sha256=sha(P/'compact-timing-audit-v11/manifest.json')))
 print(sha(O/'VexiiRiscv.v'))
def prove():
 d=O/'proof';d.mkdir(exist_ok=False);s=(BASE/'VexiiRiscv.v').read_text()
 a=s.index('  assign lane0_IntFormatPlugin_logic_stages_2_hits =');b=s.index('  assign FpuAddSharedPlugin_logic_inserter_portsRs1_0_mode',a)
 declarations='\n'.join(x for x in s.splitlines() if re.match(r'  (wire|reg)\s+',x) and 'lane0_IntFormatPlugin_logic_stages_2_' in x)
 aliases={'early0_MulPlugin_logic_formatBus_valid':'hits[0]','FpuF2iPlugin_logic_iwb_valid':'hits[1]',
 'LsuPlugin_logic_iwb_valid':'hits[2]','early0_MulPlugin_logic_formatBus_payload':'mul_data',
 'FpuF2iPlugin_logic_iwb_payload':'f2i_data','LsuPlugin_logic_iwb_payload':'lsu_data',
 'execute_ctrl4_up_LANE_SEL_lane0':'lane','execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0':'width_id',
 'execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0':'signed_load',
 'lane0_IntFormatPlugin_logic_stages_2_':'fmt_'}
 def short(t):
  for x,y in aliases.items():t=t.replace(x,y)
  return t
 old=OLD.replace('lane0_integer_WriteBackPlugin_logic_stages_2_muxed','old_data')
 new=NEW.replace('lane0_integer_WriteBackPlugin_logic_stages_2_muxed','new_data')
 # Apply short names before constructing the intentionally incorrect halfword path.
 bad=short(new).replace('new_data','bad_data').replace('os30_wb_','bad_wb_')
 assert bad.count('bad_wb_sign_half && fmt_raw[15]')==1
 bad=bad.replace('bad_wb_sign_half && fmt_raw[15]',"1'b0")
 hdr='''module probe(input[2:0]hits,input[1:0]width_id,input signed_load,lane,
 input[31:0]mul_data,f2i_data,lsu_data,output[31:0]old_data,new_data,bad_data);
'''
 (d/'probe.sv').write_text(hdr+short(declarations+'\n'+s[a:b]+old+'\n'+new)+'\n'+bad+'\nendmodule\n')
 (d/'main.cpp').write_text('''#include "Vprobe.h"
#include <cstdint>
#include <cstdio>
int main(){Vprobe d;uint64_t n=0,negative=0;uint32_t r=0x541207bd;auto rnd=[&](){r^=r<<13;r^=r>>17;r^=r<<5;return r;};
auto check=[&](){d.eval();if(d.old_data!=d.new_data){printf("FAIL %llu\\n",(unsigned long long)n);return false;}negative+=d.old_data!=d.bad_data;n++;return true;};
for(unsigned ctl=0;ctl<128;ctl++){d.hits=ctl&7;d.width_id=(ctl>>3)&3;d.signed_load=(ctl>>5)&1;d.lane=(ctl>>6)&1;
for(unsigned b=0;b<194;b++){d.mul_data=d.f2i_data=d.lsu_data=b==193?~0u:0;
if(b<192){unsigned bit=b%96,inv=b/96;uint32_t x=1u<<(bit%32);d.mul_data=d.f2i_data=d.lsu_data=inv?~0u:0;
if(bit<32)d.mul_data=inv?~x:x;else if(bit<64)d.f2i_data=inv?~x:x;else d.lsu_data=inv?~x:x;}
if(!check())return 1;}
for(unsigned k=0;k<1024;k++){d.mul_data=rnd();d.f2i_data=rnd();d.lsu_data=rnd();if(!check())return 1;}}
if(!negative)return 2;printf("PASS checks=%llu negative=%llu\\n",(unsigned long long)n,(unsigned long long)negative);}
''')
 cmd=['verilator','--cc','--exe','--build','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),str(d/'probe.sv'),str(d/'main.cpp'),'-j','3'];save(d/'command.json',cmd)
 with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run([str(d/'obj/Vprobe')],capture_output=True,text=True,check=True);(d/'run.log').write_text(r.stdout+r.stderr)
 match=re.fullmatch(r'PASS checks=155904 negative=(\d+)\n',r.stdout);assert match and int(match[1])
 save(d/'result.json',dict(passed=True,checks=155904,all_128_hits_width_signed_lane_controls=True,
  payload_basis_and_complements_96bits=True,random_triplets=131072,negative_missing_halfword_sign=int(match[1]),
  literal_original_formatter_and_candidate_assignment=True,
  algebra='width0 keeps low8 and copies signed raw7 above; width1 keeps low16 and copies signed raw15 above; width2/3 keeps raw32; lane0 zeroes every bit. Independent prequalified masks implement these disjoint cases exactly for arbitrary raw bits, including all/no/multiple source hits.',
  architectural_control_and_stages_unchanged=True))
def inherited():
 m=module('cpu_aligner_enable_v9');m.O=O;m.BASE=BASE;return m
def native_prepare():
 # The parent b511 model already owns the aligner shadow: inherit that complete
 # monitor suite once, then append only the new final-DATA equality assertion.
 m=module('cpu_rvc_plane_v7');m.O=O;m.BASE=BASE;m.native_prepare()
 p=O/'native/frozen'/CPU;s=p.read_text();e=s.index('\nendmodule',s.index('module VexiiRiscv ('))
 monitor='''
wire[31:0] writeback_plane_reference=execute_ctrl4_up_LANE_SEL_lane0?lane0_IntFormatPlugin_logic_stages_2_wb_payload:32'b0;
reg[63:0] writeback_plane_checks=0;
always @(posedge clk)if(!reset)begin
 assert(writeback_plane_reference==lane0_integer_WriteBackPlugin_logic_stages_2_muxed);
 writeback_plane_checks<=writeback_plane_checks+1;
end
final $display("WRITEBACK_PLANE_COVER checks=%0d",writeback_plane_checks);
'''
 p.write_text(s[:e]+monitor+s[e:]);prep=json.loads((O/'native/prepared.json').read_text());prep['sources_sha256'][str(p.relative_to(O/'native'))]=sha(p)
 (O/'native/prepared.json').write_text(json.dumps(prep,indent=2)+'\n')
def native_model():inherited().native_model()
def native_run():inherited().native_run()
def extra():inherited().extra()
def seal():
 for n in ('proof/result.json','native/result.json','native/extra/result.json'):assert json.loads((O/n).read_text())['passed']
 logs=list((O/'native/runs').glob('*/*-candidate.log'))+list((O/'native/extra').glob('*/*-candidate.log'));assert len(logs)==10
 direct=[];coverage={n:0 for n in ('READY','FP_RS2','FP_RS3','INTWB','RVC','ALIGNER_UPDATE','WRITEBACK_PLANE')}
 for p in logs:
  ref=BASE/p.relative_to(O);a=p.read_text();b=ref.read_text()
  uart=lambda t:t.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
  cyc=lambda t:int(re.search(r'cycles=(\d+) PC=',t)[1])
  assert uart(a)==uart(b) and cyc(a)==cyc(b)
  direct.append(dict(candidate_log=str(p.relative_to(O)),reference_log=str(ref.relative_to(ROOT)),reference_sha256=sha(ref),candidate_sha256=sha(p),cycles=cyc(a)))
  for n in coverage:coverage[n]+=int(re.search(n+r'_COVER checks=(\d+)',a)[1])
 save(O/'result.json',dict(passed=True,all10_direct_b511_UART_cycle_comparisons=True,runs=direct,live_checks=coverage,
  added_state=0,added_cycles=0,controls_and_stages_unchanged=True,no_fit=True))
 (O/'REPORT.txt').write_text('''Private final integer writeback DATA plane: PASS

Only the final stage2 DATA assignment changes. Four kept combinational masks
prequalify lane, width and signedness before late raw payload bits arrive.
Width0 retains low8 and sign/zero extends raw7; width1 retains low16 and
sign/zero extends raw15; widths2/3 retain raw32. A disabled lane returns zero.
Raw source masking, original formatter, all write-valid/fire/commit/cancel
signals, pipeline registers and every other source byte remain unchanged.

The literal original formatter and new assignment pass155904 comparisons:
all128 source-hit/width/sign/lane combinations with96-bit payload bases,
complements and131072 random triplets. Omitting halfword sign replication
fails5403 checks. The algebra covers arbitrary raw bits and multiple/no hits.

Ten actual CPU workloads match the exact b511 parent UART and cycle counts.
Each of the original ready/FP/IntWB/RVC/aligner monitors and the new final-DATA
comparison passes14519017 live checks. The initial native compile failure
was duplicate inherited test-monitor declarations, preserved under
native-duplicate-monitor-attempt1; the CPU candidate never changed.

No state/cycles, ISA, cache, queue setting or clock changes. No Quartus mapper
or fitter was run. This establishes exact behavior, not routed timing gain.
''')
 shutil.copy2(__file__,O/'final-recipe.py')
 files=[p for p in O.rglob('*') if p.is_file() and not any(c in p.relative_to(O).parts for c in ('obj','frozen')) and p.name!='manifest.json']
 save(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p) for p in files},parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),production_unchanged=True));print(sha(O/'manifest.json'),sha(O/'VexiiRiscv.v'))
if __name__=='__main__':globals()[sys.argv[1]]()
