#!/usr/bin/env python3
"""Exact RS2 forwarding trial over the qualified ready factor; private only."""
import hashlib,importlib.util,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
O=P/'cpu-fp-rs2-mux-v1';N=O/'native';OLD=P/'cpu-ready-factor-v1/native'
CPU=Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def module():
 spec=importlib.util.spec_from_file_location('native',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_rvc_native.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.O=O;m.N=N;m.OLD=OLD;return m
def prepare():
 assert json.loads((O/'combinational/result.json').read_text())['passed']
 N.mkdir(exist_ok=False);shutil.copytree(OLD/'frozen',N/'frozen')
 source=(O/'VexiiRiscv.v').read_text();ref=(OLD/'frozen'/CPU).read_text()
 a=ref.index('wire [31:0] fp_rs3_reference =');b=ref.index('\nendmodule',a);monitor=ref[a:b]
 orig=(O/'original-assignment.txt').read_text().strip()
 monitor+='\n'+orig.replace('assign execute_ctrl1_down_float_RS2_lane0 =','wire [31:0] fp_rs2_reference =')+'\n'+r'''
 reg [63:0] fp_rs2_checks=0,fp_rs2_freezes=0,fp_rs2_redirects=0,fp_rs2_multiple=0;
 reg [9:0] fp_rs2_seen=0;
 always @(posedge clk) if(!reset)begin
  assert(fp_rs2_reference==execute_ctrl1_down_float_RS2_lane0);
  fp_rs2_checks<=fp_rs2_checks+1;
  if(execute_freeze_valid)fp_rs2_freezes<=fp_rs2_freezes+1;
  if(early0_BranchPlugin_logic_flushPort_valid)fp_rs2_redirects<=fp_rs2_redirects+1;
  if(execute_ctrl1_up_LANE_SEL_lane0)begin
   fp_rs2_seen<=fp_rs2_seen|execute_lane0_bypasser_float_RS2_sel;
   if((execute_lane0_bypasser_float_RS2_bypassEnables[8:0] & (execute_lane0_bypasser_float_RS2_bypassEnables[8:0]-9'd1))!=0)fp_rs2_multiple<=fp_rs2_multiple+1;
  end
 end
 final $display("FP_RS2_COVER checks=%0d freezes=%0d redirects=%0d multiple=%0d seen=%0h",fp_rs2_checks,fp_rs2_freezes,fp_rs2_redirects,fp_rs2_multiple,fp_rs2_seen);
'''
 e=source.index('\nendmodule',source.index('module VexiiRiscv ('));source=source[:e]+'\n'+monitor+source[e:];(N/'frozen'/CPU).write_text(source)
 cmd=[x.replace(str(OLD),str(N)) for x in json.loads((OLD/'prepared.json').read_text())['command']]
 save(N/'prepared.json',dict(command=cmd,candidate_sha256=sha(O/'VexiiRiscv.v'),reference_manifest_sha256=sha(OLD.parent/'manifest.json'),sources_sha256={str(p.relative_to(N)):sha(p) for p in (N/'frozen').rglob('*') if p.is_file()}));shutil.copy2(__file__,N/'rs2-recipe.py')
def model():module().model()
def run():
 module().run();out=[]
 for p in (N/'runs').glob('*/*-candidate.log'):
  t=p.read_text();a=re.search(r'FP_RS2_COVER checks=(\d+) freezes=(\d+) redirects=(\d+) multiple=(\d+) seen=([0-9a-f]+)',t);assert a
  row=dict(zip(('checks','freezes','redirects','multiple','seen'),[int(x,16 if i==4 else 10) for i,x in enumerate(a.groups())]));assert row['checks']
  b=re.search(r'READY_COVER checks=(\d+) stalls=(\d+) rs=(\d+) cancel=(\d+) trap=(\d+)',t);assert b
  ready=dict(zip(('checks','stalls','rs','cancel','trap'),map(int,b.groups())));assert ready['checks'] and ready['stalls'] and ready['rs']
  out.append(dict(log=str(p.relative_to(O)),**row,ready=ready))
 save(N/'rs2-result.json',dict(passed=True,runs=out,total_checks=sum(x['checks'] for x in out),ready_retained=True))
if __name__=='__main__':globals()[sys.argv[1]]()
