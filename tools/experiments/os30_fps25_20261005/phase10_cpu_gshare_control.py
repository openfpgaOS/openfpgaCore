#!/usr/bin/env python3
"""Private exact GShare read-hash and late-cancel write-enable factors."""
from pathlib import Path
import difflib, hashlib, json, re, shutil, subprocess, sys

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'cpu-control-compose-v1/VexiiRiscv.v'
O=P/'cpu-gshare-control-v1'
PARENT='878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
H='HistoryPlugin_logic_onFetch_'
Y='fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH'
W='GSharePlugin_logic_mem_write_valid'

def put(p,d):
    with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')

def prepare():
    assert sha(B)==PARENT
    s=B.read_text()
    old=re.search(r'^  assign '+Y+r' = .*;\n',s,re.M).group()
    expr=old.split(' = ',1)[1].rstrip(';\n')
    lhs,rhs=expr[1:-1].rsplit(' ^ ',1)
    assert rhs=='_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_4'
    new=('  // Precompute the two priority groups before the late branch redirect.\n'
         f'  (* keep *) wire [12:0] os30_gshare_hash_low = {lhs} ^\n'
         f'      {{1\'b0, ({H}ports_0_valid ? {H}ports_0_payload_history : {H}value)}};\n'
         f'  (* keep *) wire [12:0] os30_gshare_hash_high = {lhs} ^\n'
         f'      {{1\'b0, ({H}ports_2_valid ? {H}ports_2_payload_history : {H}ports_1_payload_history)}};\n'
         f'  assign {Y} = ({H}ports_2_valid || {H}ports_1_valid) ?\n'
         '      os30_gshare_hash_high : os30_gshare_hash_low;\n')
    oldw=re.search(r'^  assign '+W+r' = .*;\n',s,re.M).group()
    assert oldw==f'  assign {W} = ((LearnPlugin_logic_learn_valid && LearnPlugin_logic_learn_payload_isBranch) && (! GSharePlugin_logic_onLearn_overflow));\n'
    neww=('  // All non-cancel qualification is independent of the older LSU trap.\n'
          '  (* keep *) wire os30_gshare_write_allow = execute_ctrl3_up_LANE_SEL_lane0 &&\n'
          '      execute_ctrl3_down_isReady && (|execute_ctrl3_down_early0_BranchPlugin_SEL_lane0) &&\n'
          '      LearnPlugin_logic_learn_payload_isBranch && !GSharePlugin_logic_onLearn_overflow;\n'
          f'  assign {W} = os30_gshare_write_allow && !execute_lane0_ctrls_3_upIsCancel;\n')
    hunks=[dict(old=old,new=new),dict(old=oldw,new=neww)]
    candidate=s
    for h in hunks:
        assert candidate.count(h['old'])==1
        candidate=candidate.replace(h['old'],h['new'])
    # Bind the single-input combinational arbiter and every valid alias.
    aliases=[('LearnPlugin_logic_learn_valid','LearnPlugin_logic_arbitrated_toFlow_valid'),
        ('LearnPlugin_logic_arbitrated_toFlow_valid','LearnPlugin_logic_arbitrated_valid'),
        ('LearnPlugin_logic_arbitrated_valid','streamArbiter_3_io_output_valid'),
        ('LearnPlugin_logic_buffered_0_valid','early0_BranchPlugin_logic_jumpLogic_learn_valid')]
    for a,b in aliases:assert f'  assign {a} = {b};\n' in s
    arb=re.search(r'module StreamArbiter_2 \(.*?\nendmodule',s,re.S).group()
    assert 'assign io_output_valid = io_inputs_0_valid;' in arb
    assert not re.search(r'\balways\b',arb)
    instance=re.search(r'  StreamArbiter_2 streamArbiter_3 \(.*?\n  \);',s,re.S).group()
    assert re.search(r'\.io_inputs_0_valid\s*\(LearnPlugin_logic_buffered_0_valid\s*\)',instance)
    assert re.search(r'\.io_output_valid\s*\(streamArbiter_3_io_output_valid\s*\)',instance)
    learn=re.search(r'^  assign early0_BranchPlugin_logic_jumpLogic_learn_valid = .*;\n',s,re.M).group()
    history=re.search(r'  always @\(\*\) begin\n    '+H+r'valueNext = .*?\n  end\n',s,re.S).group()
    O.mkdir(exist_ok=False)
    shutil.copyfile(B,O/'baseline.v');shutil.copyfile(__file__,O/'recipe.py')
    (O/'VexiiRiscv.v').write_text(candidate)
    put(O/'hunks.json',hunks)
    put(O/'source-binding.json',dict(valid_aliases=aliases,arbiter_module=arb,instance=instance,learn=learn,history=history))
    (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),candidate.splitlines(True),fromfile='878e/VexiiRiscv.v',tofile='gshare/VexiiRiscv.v')))
    d=O/'literal';d.mkdir()
    # Extracted hash syntax keeps the actual reversed PC wiring. Inputs span
    # all independent selectors, histories, PC bits and cancel qualifications.
    ids={H+'value':'saved',H+'valueNext':'selected',H+'ports_0_valid':'v[0]',H+'ports_1_valid':'v[1]',H+'ports_2_valid':'v[2]',
        H+'ports_0_payload_history':'h0',H+'ports_1_payload_history':'h1',H+'ports_2_payload_history':'h2',
        '_zz_'+Y+'_4':"{1'b0,selected}",'_zz_'+Y+'_1':'pc[6]','_zz_'+Y+'_2':'pc[7]',
        '_zz_'+Y+'_3':'{pc[8],pc[9],pc[10],pc[11],pc[12]}','_zz_'+Y:'pc',Y:'y',
        'execute_ctrl3_up_LANE_SEL_lane0':'c[0]','execute_ctrl3_down_isReady':'c[1]',
        'execute_lane0_ctrls_3_upIsCancel':'c[2]','execute_ctrl3_down_early0_BranchPlugin_SEL_lane0':'c[3]',
        'LearnPlugin_logic_learn_payload_isBranch':'c[4]','GSharePlugin_logic_onLearn_overflow':'c[5]',
        'early0_BranchPlugin_logic_jumpLogic_learn_valid':'learn','LearnPlugin_logic_learn_valid':'learn',W:'wy'}
    def short(body):
        for a in sorted(ids,key=len,reverse=True):body=re.sub(r'\b'+a+r'\b',lambda m:ids[a],body)
        return body
    modules=[]
    for kind,body,wb in [('old',old,oldw),('new',new,neww),('bad',new.replace(H+'ports_2_valid || '+H+'ports_1_valid',H+'ports_1_valid'),neww.replace('!execute_lane0_ctrls_3_upIsCancel',"1'b1"))]:
        modules.append(f'module cone_{kind}(input [12:0] pc,input [11:0] saved,h0,h1,h2,input [2:0] v,input [5:0] c,output [12:0] y,output wy);\nreg [11:0] selected; wire learn;\n'+short(history+learn+body+wb)+'endmodule\n')
    modules.append('module proof(input [12:0] pc,input [11:0] saved,h0,h1,h2,input [2:0] v,input [5:0] c,output [12:0] old_y,new_y,bad_y,output old_w,new_w,bad_w);\n'+''.join(f'cone_{k} {k}_inst(pc,saved,h0,h1,h2,v,c,{k}_y,{k}_w);\n' for k in ['old','new','bad'])+'endmodule\n')
    (d/'proof.v').write_text(''.join(modules))
    (d/'main.cpp').write_text(r'''#include "Vproof.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
int main(int argc,char**argv){Verilated::commandArgs(argc,argv);Vproof t;uint64_t checks=0,negHash=0,negWrite=0;uint32_t rng=0x619a204f;
 auto random=[&](){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;};
 auto check=[&](){t.eval();checks++;negHash+=(t.old_y!=t.bad_y);negWrite+=(t.old_w!=t.bad_w);return t.old_y==t.new_y&&t.old_w==t.new_w;};
 // All selectors/qualifiers, all payload bit bases/complements. Every
 // output bit is independent and linear in data for fixed selectors.
 for(unsigned v=0;v<8;v++)for(unsigned c=0;c<64;c++)for(unsigned basis=0;basis<62;basis++)for(unsigned inv=0;inv<2;inv++){
  uint64_t data=basis<61?(1ull<<basis):0;if(inv)data=~data;
  t.v=v;t.c=c;t.pc=data&8191;t.saved=(data>>13)&4095;t.h0=(data>>25)&4095;t.h1=(data>>37)&4095;t.h2=(data>>49)&4095;
  if(!check())return 1;
 }
 for(unsigned i=0;i<262144;i++){t.v=random()&7;t.c=random()&63;t.pc=random()&8191;t.saved=random()&4095;t.h0=random()&4095;t.h1=random()&4095;t.h2=random()&4095;if(!check())return 2;}
 if(!negHash||!negWrite)return 3;
 std::printf("{\"passed\":true,\"checks\":%llu,\"hash_negative_failures\":%llu,\"cancel_negative_failures\":%llu,\"two_state_only\":true}\n",(unsigned long long)checks,(unsigned long long)negHash,(unsigned long long)negWrite);
}
''')
    put(O/'prepared.json',dict(parent_sha256=PARENT,candidate_sha256=sha(O/'VexiiRiscv.v'),recipe_sha256=sha(Path(__file__)),new_state=False,new_cycles=False,production_modified=False,physical_unmeasured=True))
    print('SOURCE',sha(O/'VexiiRiscv.v'))

def prove():
    prep=json.loads((O/'prepared.json').read_text());assert sha(Path(__file__))==prep['recipe_sha256'] and sha(O/'VexiiRiscv.v')==prep['candidate_sha256']
    d=O/'literal'
    cmds={'lint':['verilator','--lint-only','--Wno-fatal','--top-module','VexiiRiscv',str(O/'VexiiRiscv.v')],
          'build':['verilator','--cc','--exe','--build','-j','4','--Wno-fatal','--top-module','proof','--Mdir',str(d/'obj'),str(d/'proof.v'),str(d/'main.cpp')]}
    put(O/'commands.json',cmds)
    for n,c in cmds.items():
        with (O/(n+'.log')).open('x') as f:subprocess.run(c,stdout=f,stderr=subprocess.STDOUT,check=True)
    r=subprocess.run([str(d/'obj/Vproof')],capture_output=True,text=True,check=True)
    (O/'proof.log').write_text(r.stdout+r.stderr);data=json.loads(r.stdout);assert data['passed']
    put(O/'result.json',data)
    files=[Path(__file__)]+[p for p in O.iterdir() if p.is_file()]+[d/'proof.v',d/'main.cpp']
    put(O/'manifest.json',dict(passed=True,native_and_physical_pending=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}))
    print(r.stdout.strip());print('MANIFEST',sha(O/'manifest.json'))

if __name__=='__main__':globals()[sys.argv[1]]()
