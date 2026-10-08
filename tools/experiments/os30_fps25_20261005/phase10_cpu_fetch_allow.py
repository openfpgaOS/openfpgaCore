#!/usr/bin/env python3
"""Private exact buffered-halfword consumption predicate; no added state/cycles."""
import hashlib, importlib.util, json, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-rvc-bits-v2'; O=P/'cpu-rvc-control-v3'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
OLD='''  (* keep *) wire fetch_ready_allow = !AlignerPlugin_api_haltIt &&
      !(|(AlignerPlugin_logic_buffer_mask & ~AlignerPlugin_logic_buffer_usedMask[1:0]));'''
NEW='''  // Only the first two slices occupy the saved fetch buffer. Express its
  // complete consumption directly, without priority -> valid -> usage muxes.
  // Equivalent for every scanner-valid/width/mask combination (single lane).
  (* keep *) wire fetch_ready_allow = !AlignerPlugin_api_haltIt && (
      !(|AlignerPlugin_logic_buffer_mask) ||
      (AlignerPlugin_logic_scanners_0_valid &&
        (!AlignerPlugin_logic_buffer_mask[1] || AlignerPlugin_logic_scanners_0_checker_1_required)) ||
      (!AlignerPlugin_logic_scanners_0_valid && AlignerPlugin_logic_scanners_1_valid &&
        !AlignerPlugin_logic_buffer_mask[0] &&
        (!AlignerPlugin_logic_scanners_1_checker_1_required || fetch_logic_ctrls_3_down_valid)));'''
def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def prepare():
    O.mkdir(exist_ok=False);s=(BASE/'VexiiRiscv.v').read_text()
    assert s.count(OLD)==1
    assert 'assign AlignerPlugin_logic_usedMask_0 = 4\'b0000;' in s
    assert 'assign AlignerPlugin_logic_extractors_0_first = 1\'b1;' in s
    (O/'VexiiRiscv.v').write_text(s.replace(OLD,NEW))
    (O/'original-predicate.txt').write_text(OLD+'\n');(O/'candidate-predicate.txt').write_text(NEW+'\n')
    shutil.copy2(__file__,O/'recipe.py')
    save(O/'prepared.json',dict(parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),added_state_bits=0,added_cycles=0,single_extractor=True,used_mask_zero=True))
def prove():
    d=O/'proof';d.mkdir(exist_ok=False)
    # Literal original priority/extractor/usage behavior, including unavailable
    # live-fetch suppression and arbitrary (not just reachable) scanner values.
    (d/'probe.sv').write_text('''module probe(input [3:0] valid,width,input [1:0] mask,input fetch_valid,halt,
output old_allow,new_allow);
wire [3:0] oh={valid[3]&&!(|valid[2:0]),valid[2]&&!(|valid[1:0]),valid[1]&&!valid[0],valid[0]};
wire [3:0] usage=(oh[0]?{2'b0,width[0],1'b1}:4'b0)|
 (oh[1]?{1'b0,width[1],1'b1,1'b0}:4'b0)|
 (oh[2]?{width[2],1'b1,2'b0}:4'b0)|(oh[3]?4'b1000:4'b0);
wire extractor_valid=(|oh)&&!halt&&!(!fetch_valid&&(|usage[3:2]));
wire [3:0] used=extractor_valid?usage:4'b0;
assign old_allow=!halt&&!(|(mask&~used[1:0]));
assign new_allow=!halt&& (!(|mask)||(valid[0]&&(!mask[1]||width[0]))||
 (!valid[0]&&valid[1]&&!mask[0]&&(!width[1]||fetch_valid)));
endmodule
''')
    (d/'main.cpp').write_text('''#include "Vprobe.h"
#include <cstdio>
int main(){Vprobe d;unsigned n=0;for(unsigned bits=0;bits<4096;bits++){
d.valid=bits&15;d.width=(bits>>4)&15;d.mask=(bits>>8)&3;d.fetch_valid=(bits>>10)&1;d.halt=(bits>>11)&1;d.eval();
if(d.old_allow!=d.new_allow){printf("FAIL %u\\n",bits);return 1;}n++;}
printf("PASS %u\\n",n);}
''')
    cmd=['verilator','--cc','--exe','--build','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),str(d/'probe.sv'),str(d/'main.cpp'),'-j','4']
    save(d/'command.json',cmd)
    with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    r=subprocess.run([str(d/'obj/Vprobe')],capture_output=True,text=True,check=True);(d/'run.log').write_text(r.stdout+r.stderr)
    assert r.stdout=='PASS 4096\n';save(d/'result.json',dict(passed=True,exhaustive_abstract_input_combinations=4096,unreachable_combinations_included=True,added_state_bits=0,added_cycles=0))
def native_module():
    spec=importlib.util.spec_from_file_location('native',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_rvc_native.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.O=O;m.N=O/'native';return m
def native_prepare():
    m=native_module();m.prepare();n=m.N;p=n/'frozen'/m.CPU;s=p.read_text()
    e=s.index('\nendmodule',s.index('module VexiiRiscv ('))
    monitor='''
wire fetch_allow_reference=!AlignerPlugin_api_haltIt &&
 !(|(AlignerPlugin_logic_buffer_mask & ~AlignerPlugin_logic_buffer_usedMask[1:0]));
always @(posedge clk) if(!reset) assert(fetch_allow_reference==fetch_ready_allow);
'''
    p.write_text(s[:e]+monitor+s[e:]);prep=json.loads((n/'prepared.json').read_text());prep['sources_sha256'][str(p.relative_to(n))]=sha(p)
    (n/'prepared.json').write_text(json.dumps(prep,indent=2)+'\n');shutil.copy2(__file__,n/'control-recipe.py')
def native_model():native_module().model()
def native_run():native_module().run()
if __name__=='__main__':globals()[sys.argv[1]]()
