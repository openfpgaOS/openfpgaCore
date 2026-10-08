#!/usr/bin/env python3
"""Private late-load forwarding reassociation and literal RTL proof, no CAD."""
from pathlib import Path
import difflib,hashlib,json,re,shutil,subprocess,sys
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'cpu-cache64-rvcpipe-v1/helper-audit/candidate-matched-helpers.v'
O=P/'cpu-load-forward-v1'; D=O/'literal'
SHA='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,a):
    with p.open('x') as f:json.dump(a,f,indent=2,sort_keys=True);f.write('\n')
def block(s,rs):
    pat=rf'  always @\(\*\) begin\n    _zz_execute_ctrl1_down_integer_{rs}_lane0_1 = .*?\n  end\n'
    m=list(re.finditer(pat,s,re.S));assert len(m)==1
    return m[0].group()
def factor(old,rs):
    out=f'_zz_execute_ctrl1_down_integer_{rs}_lane0_1'
    sel=f'_zz_execute_ctrl1_down_integer_{rs}_lane0'
    when='when_ExecuteLanePlugin_l196'+('_1' if rs=='RS2' else '')
    rhs=old.split(f'{out} = ',1)[1].split(';',1)[0]
    prev='execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0'
    current='execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0'
    assert rhs.count(current)==1
    early=rhs.replace(current,prev)
    name='os30_integer_'+rs.lower()
    return f'''  // Keep the older writeback register contribution off the late load leg.
  // Architectural writeback, formatting and forwarding priority are unchanged.
  (* keep *) wire [31:0] {name}_early = {when}
      ? execute_ctrl2_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0
      : {early};
  (* keep *) wire {name}_late_enable = (!{when}) && {sel}[1];
  always @(*) begin
    {out} = {name}_early |
        ({{32{{{name}_late_enable}}}} & lane0_integer_WriteBackPlugin_logic_stages_2_muxed);
  end
'''
def prepare():
    assert sha(B)==SHA
    old=B.read_text();s=old;hunks={}
    for rs in ['RS1','RS2']:
        a=block(old,rs);b=factor(a,rs);assert s.count(a)==1
        s=s.replace(a,b,1);hunks[rs]={'old':a,'new':b}
    for line in ['  assign lane0_integer_WriteBackPlugin_logic_stages_2_merged = (execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0 | lane0_integer_WriteBackPlugin_logic_stages_2_muxed);','  assign execute_ctrl4_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass = lane0_integer_WriteBackPlugin_logic_stages_2_merged;','  assign execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 = execute_ctrl4_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass;']:
        assert old.count(line)==s.count(line)==1
    O.mkdir(exist_ok=False);(O/'VexiiRiscv.v').write_text(s)
    shutil.copy2(B,O/'baseline.v');shutil.copy2(__file__,O/'recipe.py')
    put(O/'hunks.json',hunks)
    (O/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),fromfile='aecb/VexiiRiscv.v',tofile='load-forward/VexiiRiscv.v')))
    put(O/'prepared.json',dict(parent_sha256=SHA,candidate_sha256=sha(O/'VexiiRiscv.v'),recipe_sha256=sha(Path(__file__)),scope='Only integer RS1/RS2 forwarding early term and final late DATA OR; all writeback/formatter/control/register bytes unchanged',new_state=False,new_cycles=False,new_CAD=False,production_modified=False))
    literal_prepare(hunks)
    print('SOURCE',sha(O/'VexiiRiscv.v'))
def literal_prepare(hunks):
    D.mkdir(exist_ok=False)
    modules=[]
    for rs,h in hunks.items():
        sel=f'_zz_execute_ctrl1_down_integer_{rs}_lane0'
        out=sel+'_1';when='when_ExecuteLanePlugin_l196'+('_1' if rs=='RS2' else '')
        for kind in ['old','new','bad']:
            body=h['old' if kind=='old' else 'new']
            if kind=='bad':
                a=f'(!{when}) && {sel}[1]';assert body.count(a)==1
                body=body.replace(a,f'{sel}[1]')
            modules.append(f'''module {kind}_{rs}(input [4:0] selects,
 input [31:0] d2,d3,p4,f4,d5,rf, output [31:0] result);
wire [3:0] {sel}=selects[4:1];
wire {when}=selects[0];
wire [31:0] execute_ctrl2_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0=d2;
wire [31:0] execute_ctrl3_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0=d3;
wire [31:0] execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0=p4;
wire [31:0] lane0_integer_WriteBackPlugin_logic_stages_2_muxed=f4;
wire [31:0] lane0_integer_WriteBackPlugin_logic_stages_2_merged=p4|f4;
wire [31:0] execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0=lane0_integer_WriteBackPlugin_logic_stages_2_merged;
wire [31:0] execute_ctrl5_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0=d5;
wire [31:0] execute_lane0_bypasser_integer_{rs}_port_data=rf;
reg [31:0] {out};
{body}
assign result={out};
endmodule
''')
    top='module proof(input [4:0] selects,input [31:0] d2,d3,p4,f4,d5,rf,output [31:0] old1,new1,bad1,old2,new2,bad2);\n'
    for n,rs in enumerate(['RS1','RS2'],1):
        for kind in ['old','new','bad']:
            top+=f'{kind}_{rs} u_{kind}{n}(selects,d2,d3,p4,f4,d5,rf,{kind}{n});\n'
    (D/'proof.v').write_text(''.join(modules)+top+'endmodule\n')
    (D/'main.cpp').write_text(r'''#include "Vproof.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
int main(int argc,char **argv) {
 Verilated::commandArgs(argc,argv);Vproof t;
 uint64_t checks=0,negative=0;uint32_t state=0x76542139;
 auto random=[&](){state^=state<<13;state^=state>>17;state^=state<<5;return state;};
 auto check=[&](unsigned s,const uint32_t *v) {
  t.selects=s;t.d2=v[0];t.d3=v[1];t.p4=v[2];t.f4=v[3];t.d5=v[4];t.rf=v[5];t.eval();
  if(t.old1!=t.new1||t.old2!=t.new2||t.old1!=t.old2) {
   std::fprintf(stderr,"FAIL selectors=%u check=%llu\n",s,(unsigned long long)checks);std::exit(1);
  }
  if(t.old1!=t.bad1||t.old2!=t.bad2)negative++;
  checks++;
 };
 uint32_t v[6];
 for(unsigned s=0;s<32;s++)for(unsigned b=0;b<64;b++) {
  for(unsigned k=0;k<6;k++)v[k]=(b>>k&1)?UINT32_MAX:0;check(s,v);
 }
 auto exhaustive=checks;
 for(unsigned s=0;s<32;s++)for(unsigned k=0;k<192;k++)for(unsigned invert=0;invert<2;invert++) {
  for(unsigned j=0;j<6;j++)v[j]=invert?UINT32_MAX:0;
  v[k/32]^=1u<<(k%32);check(s,v);
 }
 auto basis=checks-exhaustive;
 for(unsigned n=0;n<262144;n++) {
  unsigned s=random()&31;for(auto &x:v)x=random();check(s,v);
 }
 if(!negative)return 2;
 std::printf("{\"passed\":true,\"actual_extracted_RTL\":true,\"arbitrary_selectors\":32,\"exhaustive_one_bit_vectors\":%llu,\"full_width_basis_complement_vectors\":%llu,\"full_width_random_vectors\":262144,\"vectors\":%llu,\"RS_outputs_compared\":2,\"negative_control_failures\":%llu}\n",(unsigned long long)exhaustive,(unsigned long long)basis,(unsigned long long)checks,(unsigned long long)negative);
}
''')
def prove():
    candidate=O/'VexiiRiscv.v';d=json.loads((O/'prepared.json').read_text());assert sha(candidate)==d['candidate_sha256']
    lint=['verilator','--lint-only','-Wno-fatal','--top-module','VexiiRiscv',str(candidate)]
    put(O/'lint-command.json',lint)
    with (O/'lint.log').open('x') as f:subprocess.run(lint,stdout=f,stderr=subprocess.STDOUT,check=True)
    cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','proof','--Mdir',str(D/'obj'),'-CFLAGS','-O2',str(D/'proof.v'),str(D/'main.cpp')]
    put(D/'command.json',cmd)
    with (D/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (D/'result.json').open('x') as f:subprocess.run([str(D/'obj/Vproof')],stdout=f,stderr=subprocess.STDOUT,check=True)
    print((D/'result.json').read_text())
def seal():
    d=json.loads((O/'prepared.json').read_text());h=json.loads((O/'hunks.json').read_text());s=B.read_text();assert sha(B)==SHA
    for rs in ['RS1','RS2']:
        assert s.count(h[rs]['old'])==1;s=s.replace(h[rs]['old'],h[rs]['new'],1)
    assert s==(O/'VexiiRiscv.v').read_text();assert sha(O/'VexiiRiscv.v')==d['candidate_sha256']
    r=json.loads((D/'result.json').read_text());assert r['passed'] and r['negative_control_failures']>0
    put(O/'result.json',dict(passed=True,literal_component_passed=True,exact_two_forwarding_hunks=True,candidate_sha256=d['candidate_sha256'],parent_sha256=SHA,native_integration_pending=True,passed_for_native_screen=True,passed_for_map_or_fit=False,production_modified=False,deployment_ready=False,literal=r))
    (O/'REPORT.txt').write_text('PASS: literal component proof for integer late-load forwarding reassociation.\n\nOnly RS1/RS2 forwarding data expressions change over exact aecb. The existing ctrl4 data is P4|F4. The early term substitutes P4 and retains the original youngest-stage priority override; the final term ORs selected F4 with explicit !youngest qualification. This is identical even for arbitrary/non-onehot selector combinations. Original formatting, architectural writeback DATA, source masks, bypass selectors, valid/commit/cancel and all registers remain unchanged. Two kept early data nets and two kept late enables are combinational only.\n\nActual extracted old/new RTL passes every 2048 one-bit selector/data combination, 12288 full-width bases/complements, and 262144 full-width random vectors for both RS outputs. The negative control omits !youngest and fails, showing the arbitrary-selector priority cases are exercised. Full CPU lint passes.\n\nNative regression and actual renderer integration remain pending under root ownership; no map, fit, extra pipeline stage, memory, ISA or cache change. A six-to-five late-data LUT path is a mapping hypothesis only. More combinational duplication or formatter fanout may offset that gain.\n')
    files=[p for p in O.rglob('*') if p.is_file() and 'obj' not in p.parts]+[D/'obj/Vproof',B,Path(__file__),P/'cpu-load-forward-feasibility-v1/manifest.json']
    put(O/'manifest.json',dict(passed=True,passed_for_native_screen=True,passed_for_map_or_fit=False,deployment_ready=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files))}))
    print('MANIFEST',sha(O/'manifest.json'))
if __name__=='__main__':
    assert len(sys.argv)==2 and sys.argv[1] in ['prepare','prove','seal'];globals()[sys.argv[1]]()
