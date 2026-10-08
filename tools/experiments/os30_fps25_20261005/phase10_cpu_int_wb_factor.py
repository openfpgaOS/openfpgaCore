#!/usr/bin/env python3
"""Remove only redundant data qualification after a zero-preserving formatter."""
import hashlib,importlib.util,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-ready-factor-v1';O=P/'cpu-int-wb-factor-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
OLD="  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = (lane0_integer_WriteBackPlugin_logic_stages_2_hits[0] ? lane0_IntFormatPlugin_logic_stages_2_wb_payload : 32'h0);"
NEW="""  // The formatter maps all-zero raw data to zero for every width/sign.
  // With no source hits its payload is already zero; retain only lane gating
  // on DATA. Architectural wb/write-valid, commit and cancellation are unchanged.
  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = (execute_ctrl4_up_LANE_SEL_lane0 ? lane0_IntFormatPlugin_logic_stages_2_wb_payload : 32'h0);"""
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def prepare():
 s=(BASE/'VexiiRiscv.v').read_text();assert sha(BASE/'VexiiRiscv.v')=='726cb18a845d60aca7f74f374f9c3f459d381db5ca7a6810c032296736445c6c'
 for t in (OLD,'  assign lane0_integer_WriteBackPlugin_logic_stages_2_hits = lane0_IntFormatPlugin_logic_stages_2_wb_valid;','  assign lane0_IntFormatPlugin_logic_stages_2_wb_valid = (execute_ctrl4_up_LANE_SEL_lane0 && (|lane0_IntFormatPlugin_logic_stages_2_hits));'):
  assert s.count(t)==1,t
 O.mkdir(exist_ok=False);(O/'VexiiRiscv.v').write_text(s.replace(OLD,NEW));shutil.copy2(__file__,O/'recipe.py')
 import difflib
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),(O/'VexiiRiscv.v').read_text().splitlines(True),fromfile='ready-factor',tofile='integer-wb-factor')))
 save(O/'prepared.json',dict(parent_manifest_sha256=sha(BASE/'manifest.json'),parent_cpu_sha256=sha(BASE/'VexiiRiscv.v'),candidate_sha256=sha(O/'VexiiRiscv.v'),added_state_bits=0,added_cycles=0,scope='Only stage2 formatted DATA gate; all valid/write/commit/cancel and arithmetic unchanged.'))
def prove():
 d=O/'proof';d.mkdir(exist_ok=False)
 (d/'probe.sv').write_text('''module probe(input[2:0] hits,input[1:0] width_id,input signed_load,lane,
input[31:0] mul_data,f2i_data,lsu_data, output[31:0] old_data,new_data,formatted);
wire[31:0] raw=(hits[0]?mul_data:32'b0)|(hits[1]?f2i_data:32'b0)|(hits[2]?lsu_data:32'b0);
wire seg0=width_id<1,seg1=width_id<2;
wire sign0=signed_load&&raw[7];
wire sign1=signed_load&&(width_id==0?raw[7]:raw[15]);
assign formatted={seg1?{16{sign1}}:raw[31:16],seg0?{8{sign0}}:raw[15:8],raw[7:0]};
assign old_data=(lane&&(|hits))?formatted:32'b0;
assign new_data=lane?formatted:32'b0;
endmodule
''')
 (d/'main.cpp').write_text('''#include "Vprobe.h"
#include <cstdint>
#include <cstdio>
int main(){Vprobe d;uint64_t n=0;uint32_t r=0x541207bd;auto rnd=[&](){r^=r<<13;r^=r>>17;r^=r<<5;return r;};
auto check=[&](){d.eval();if(d.old_data!=d.new_data||(!d.hits&&d.formatted)){printf("FAIL\\n");return false;}n++;return true;};
for(unsigned ctl=0;ctl<128;ctl++){d.hits=ctl&7;d.width_id=(ctl>>3)&3;d.signed_load=(ctl>>5)&1;d.lane=(ctl>>6)&1;
for(unsigned b=0;b<194;b++){d.mul_data=d.f2i_data=d.lsu_data=b==193?~0u:0;
if(b<192){unsigned bit=b%96,inv=b/96;uint32_t x=1u<<(bit%32);d.mul_data=d.f2i_data=d.lsu_data=inv?~0u:0;
if(bit<32)d.mul_data=inv?~x:x;else if(bit<64)d.f2i_data=inv?~x:x;else d.lsu_data=inv?~x:x;}
if(!check())return 1;}
for(unsigned k=0;k<1024;k++){d.mul_data=rnd();d.f2i_data=rnd();d.lsu_data=rnd();if(!check())return 1;}}
printf("PASS %llu\\n",(unsigned long long)n);}
''')
 cmd=['verilator','--cc','--exe','--build','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),str(d/'probe.sv'),str(d/'main.cpp'),'-j','3'];save(d/'command.json',cmd)
 with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run([str(d/'obj/Vprobe')],capture_output=True,text=True,check=True);(d/'run.log').write_text(r.stdout+r.stderr);assert r.stdout=='PASS 155904\n'
 save(d/'result.json',dict(passed=True,checks=155904,all_128_hit_width_signed_lane_combinations=True,payload_basis_96_bits_and_complements=True,multiple_hits_and_no_hits_included=True,random_payload_triplets=131072,algebra='hits=0 => raw=0; every selected formatter output is a raw bit, zero, or repeated raw sign bit gated by signed. Hence formatted=0. With anyhit=1 the old/new data gates are identical. No valid-state or one-hot assumption.',scope='Two-state control/data equivalence; existing internal unused X defaults are not output-observable because corresponding segment is disabled.'))
def native_module():
 spec=importlib.util.spec_from_file_location('native',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_rvc_native.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.O=O;m.N=O/'native';return m
def native_prepare():
 m=native_module();m.prepare();p=m.N/'frozen'/m.CPU;s=p.read_text();e=s.index('\nendmodule',s.index('module VexiiRiscv ('))
 monitor='''
wire [31:0] integer_wb_reference=lane0_integer_WriteBackPlugin_logic_stages_2_hits[0]?lane0_IntFormatPlugin_logic_stages_2_wb_payload:32'b0;
reg [63:0] intwb_checks=0,intwb_zero=0,intwb_byte=0,intwb_half=0,intwb_word=0,intwb_signed=0,intwb_multi=0;
always @(posedge clk) if(!reset)begin
 assert(integer_wb_reference==lane0_integer_WriteBackPlugin_logic_stages_2_muxed);
 if(!(|lane0_IntFormatPlugin_logic_stages_2_hits))assert(lane0_IntFormatPlugin_logic_stages_2_wb_payload==0);
 intwb_checks<=intwb_checks+1;
 intwb_zero<=intwb_zero+!(|lane0_IntFormatPlugin_logic_stages_2_hits);
 intwb_multi<=intwb_multi+((lane0_IntFormatPlugin_logic_stages_2_hits&(lane0_IntFormatPlugin_logic_stages_2_hits-1'b1))!=0);
 if(lane0_IntFormatPlugin_logic_stages_2_wb_valid)begin
  intwb_byte<=intwb_byte+(execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0==0);
  intwb_half<=intwb_half+(execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0==1);
  intwb_word<=intwb_word+(execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0>=2);
  intwb_signed<=intwb_signed+execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0;
 end
end
final $display("INTWB_COVER checks=%0d zero=%0d byte=%0d half=%0d word=%0d signed=%0d multi=%0d",intwb_checks,intwb_zero,intwb_byte,intwb_half,intwb_word,intwb_signed,intwb_multi);
'''
 p.write_text(s[:e]+monitor+s[e:]);prep=json.loads((m.N/'prepared.json').read_text());prep['sources_sha256'][str(p.relative_to(m.N))]=sha(p);prep['native_reference_manifest_sha256']=sha(BASE/'manifest.json');(m.N/'prepared.json').write_text(json.dumps(prep,indent=2)+'\n');shutil.copy2(__file__,m.N/'integer-recipe.py')
def native_model():native_module().model()
def native_run():
 m=native_module();m.OLD=BASE/'native';m.run();out=[]
 for p in (m.N/'runs').glob('*/*-candidate.log'):
  a=re.search(r'INTWB_COVER checks=(\d+) zero=(\d+) byte=(\d+) half=(\d+) word=(\d+) signed=(\d+) multi=(\d+)',p.read_text());assert a
  x=dict(zip(('checks','zero','byte','half','word','signed','multi'),map(int,a.groups())));assert x['checks'] and x['zero'];out.append(dict(log=str(p.relative_to(O)),**x))
 assert all(sum(x[k] for x in out)>0 for k in ('byte','half','word','signed'))
 save(m.N/'integer-result.json',dict(passed=True,runs=out,total_checks=sum(x['checks'] for x in out)))
def collect():
 out=[]
 for p in list((O/'native/runs').glob('*/*-candidate.log'))+list((O/'native/halfword-v2').glob('*-candidate.log')):
  a=re.search(r'INTWB_COVER checks=(\d+) zero=(\d+) byte=(\d+) half=(\d+) word=(\d+) signed=(\d+) multi=(\d+)',p.read_text());assert a
  out.append(dict(log=str(p.relative_to(O)),**dict(zip(('checks','zero','byte','half','word','signed','multi'),map(int,a.groups())))))
 assert len(out)==8 and all(sum(x[k] for x in out)>0 for k in ('checks','zero','byte','half','word','signed'))
 assert json.loads((O/'native/halfword-v2/result.json').read_text())['passed']
 save(O/'native/integer-result.json',dict(passed=True,runs=out,total_checks=sum(x['checks'] for x in out),native_multiple_hits_observed=False,multiple_hit_priority_scope='exhaustive combinational proof',halfword_fixture_v1_watchdog='Preserved failed fixture: fully cache-resident loop triggered existing1.5Mcycle M1 inactivity watchdog; v2 periodic cold read maintains normal memory activity. No CPU mismatch.'))
 shutil.copy2(__file__,O/'final-recipe.py')
def seal():
 for p in (O/'proof/result.json',O/'native/result.json',O/'native/integer-result.json'):assert json.loads(p.read_text())['passed']
 files=[O/'VexiiRiscv.v',O/'prepared.json',O/'recipe.py',O/'candidate.patch']+list((O/'proof').glob('*.json'))+list((O/'proof').glob('*.sv'))+list((O/'proof').glob('*.cpp'))+[O/'proof/run.log',O/'native/prepared.json',O/'native/integer-recipe.py',O/'native/build.json',O/'native/result.json',O/'native/integer-result.json']+list((O/'native/runs').glob('*/*.log'))
 files += [O/'final-recipe.py'] + [p for folder in ('halfword','halfword-v2') for p in (O/'native'/folder).iterdir() if p.is_file()]
 save(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p) for p in files},parent_manifest_sha256=sha(BASE/'manifest.json'),scope='Exact data-only WB qualification removal; no valid/commit/cancel change, no state/cycle change, no map/fit/regeneration.'))
if __name__=='__main__':globals()[sys.argv[1]]()
