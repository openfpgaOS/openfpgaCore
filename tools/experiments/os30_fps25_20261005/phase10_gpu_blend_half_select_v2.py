#!/usr/bin/env python3
"""Move blend halfword selection before the late accumulator-address match."""
from pathlib import Path
import difflib,hashlib,json,re,shutil,subprocess,sys
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'gpu-parallel-control-v1/gpu_core.v'
O=P/'gpu-blend-half-select-v2'
PARENT='aae8ad7d90b58d0b8aa703411a010e3d366eb4d33f493018258fa530f52d1f0b'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
OLD='''wire [15:0] cbw_p2_dst_w   = p2_fb_addr[1] ? cbw_p2_acc_w[31:16]
                                           : cbw_p2_acc_w[15:0];'''
NEW='''// Choose both halfwords before the late same-address decision. Each
// byte then needs just one final masked-overlay mux; coherency is unchanged.
(* keep *) wire [15:0] cbw_p2_window_half = p2_fb_addr[1]
    ? cbw_p2_word_w[31:16] : cbw_p2_word_w[15:0];
(* keep *) wire [15:0] cbw_p2_acc_half = p2_fb_addr[1]
    ? fb_acc_data[31:16] : fb_acc_data[15:0];
(* keep *) wire [1:0] cbw_p2_acc_half_mask = p2_fb_addr[1]
    ? fb_acc_mask[3:2] : fb_acc_mask[1:0];
wire [15:0] cbw_p2_dst_w;
assign cbw_p2_dst_w[7:0] = (cbw_p2_acc_same_w && cbw_p2_acc_half_mask[0])
    ? cbw_p2_acc_half[7:0] : cbw_p2_window_half[7:0];
assign cbw_p2_dst_w[15:8] = (cbw_p2_acc_same_w && cbw_p2_acc_half_mask[1])
    ? cbw_p2_acc_half[15:8] : cbw_p2_window_half[15:8];'''
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
def prepare():
 assert sha(B)==PARENT
 s=B.read_text();assert s.count(OLD)==1
 candidate=s.replace(OLD,NEW)
 assert [x for x in s.splitlines() if '<=' in x]==[x for x in candidate.splitlines() if '<=' in x]
 O.mkdir(exist_ok=False);shutil.copyfile(B,O/'baseline.v');shutil.copyfile(__file__,O/'recipe.py')
 (O/'gpu_core.v').write_text(candidate);put(O/'hunk.json',dict(old=OLD,new=NEW))
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),candidate.splitlines(True),fromfile='aae8/gpu_core.v',tofile='half-select/gpu_core.v')))
 common=re.search(r'wire \[31:0\] cbw_acc_mask_w = .*?;',s,re.S).group()+'\n'+re.search(r'wire \[31:0\] cbw_p2_acc_w\s+= .*?;',s,re.S).group()+'\n'
 names={'p2_fb_addr':'addr','cbw_p2_word_w':'window','fb_acc_data':'acc','fb_acc_mask':'mask','cbw_p2_acc_same_w':'same','cbw_p2_dst_w':'y'}
 def short(body):
  for a,b in names.items():body=re.sub(r'\b'+a+r'\b',b,body)
  return body
 d=O/'literal';d.mkdir();modules=[]
 for kind,body in [('old',OLD),('new',NEW),('bad',NEW.replace('&& cbw_p2_acc_half_mask[0]',"&& 1'b1"))]:
  modules.append(f'module cone_{kind}(input [1:0] addr,input [31:0] window,acc,input [3:0] mask,input same,output [15:0] result);\n'+short(common+body)+'\nassign result=y;\nendmodule\n')
 modules.append('module proof(input [1:0] addr,input [31:0] window,acc,input [3:0] mask,input same,output [15:0] old_y,new_y,bad_y);\n'+''.join(f'cone_{k} {k}_inst(addr,window,acc,mask,same,{k}_y);\n' for k in ['old','new','bad'])+'endmodule\n')
 (d/'proof.v').write_text(''.join(modules))
 (d/'main.cpp').write_text(r'''#include "Vproof.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);Vproof t;uint64_t checks=0,negative=0;uint32_t rng=0x91468532;
 auto random=[&](){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;};
 auto check=[&](){t.eval();checks++;negative+=(t.old_y!=t.bad_y);return t.old_y==t.new_y;};
 for(unsigned ctrl=0;ctrl<128;ctrl++)for(unsigned bit=0;bit<65;bit++)for(unsigned inv=0;inv<2;inv++){
  uint64_t value=bit<64?(1ull<<bit):0;if(inv)value=~value;
  t.addr=ctrl&3;t.mask=(ctrl>>2)&15;t.same=ctrl>>6;t.window=value;t.acc=value>>32;if(!check())return 1;
 }
 for(unsigned i=0;i<262144;i++){t.addr=random()&3;t.mask=random()&15;t.same=random()&1;t.window=random();t.acc=random();if(!check())return 2;}
 if(!negative)return 3;
 std::printf("{\"passed\":true,\"checks\":%llu,\"negative_control_failures\":%llu,\"two_state_only\":true}\n",(unsigned long long)checks,(unsigned long long)negative);
}
''')
 # Retain all existing passive probes in the selected full-GPU lint wrapper.
 prev=P/'gpu-parallel-control-v1/lint';lint=O/'lint';lint.mkdir()
 t=(prev/'gpu_core.v').read_text();assert t.count(OLD)==1;(lint/'gpu_core.v').write_text(t.replace(OLD,NEW))
 cmd=[x.replace(str(prev),str(lint)) for x in json.loads((prev/'command.json').read_text())]
 put(lint/'command.json',cmd)
 put(O/'prepared.json',dict(parent_sha256=PARENT,candidate_sha256=sha(O/'gpu_core.v'),recipe_sha256=sha(Path(__file__)),new_state=False,new_cycles=False,production_modified=False,physical_pending=True))
 print('SOURCE',sha(O/'gpu_core.v'))
def prove():
 prep=json.loads((O/'prepared.json').read_text());assert sha(Path(__file__))==prep['recipe_sha256'] and sha(O/'gpu_core.v')==prep['candidate_sha256']
 d=O/'literal';commands={'lint':json.loads((O/'lint/command.json').read_text()),'build':['verilator','--cc','--exe','--build','-j','4','--Wno-fatal','--top-module','proof','--Mdir',str(d/'obj'),str(d/'proof.v'),str(d/'main.cpp')]}
 put(O/'commands.json',commands)
 for n,c in commands.items():
  with (O/(n+'.log')).open('x') as f:subprocess.run(c,stdout=f,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run([str(d/'obj/Vproof')],capture_output=True,text=True,check=True);(O/'proof.log').write_text(r.stdout+r.stderr)
 result=json.loads(r.stdout);assert result['passed'];put(O/'result.json',result)
 files=[Path(__file__),B,P/'gpu-parallel-control-v1/manifest.json']+[p for p in O.rglob('*') if p.is_file() and 'obj' not in p.parts]
 put(O/'manifest.json',dict(passed=True,native_and_physical_pending=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}))
 print(r.stdout.strip());print('MANIFEST',sha(O/'manifest.json'))
if __name__=='__main__':globals()[sys.argv[1]]()
