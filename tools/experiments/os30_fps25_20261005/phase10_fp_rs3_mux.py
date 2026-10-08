#!/usr/bin/env python3
"""Private RS3-only exact priority-mux factoring; no CPU regeneration."""
import hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'cpu-fp-rs3-mux-v1';BASE=P/'compact-lod5-core-v2/frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
PREFIX='execute_lane0_bypasser_float_RS3'
OUTPUT='execute_ctrl1_down_float_RS3_lane0'

def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')

def prepare():
    OUT.mkdir(exist_ok=False);s=BASE.read_text();shutil.copy2(BASE,OUT/'baseline.v')
    line=re.findall(r'^  assign '+OUTPUT+r' = .*?;$',s,re.M);assert len(line)==1;old=line[0]
    d=[f'execute_ctrl{i}_down_lane0_float_WriteBackPlugin_logic_DATA_lane0' for i in range(4,13)]+[PREFIX+'_port_data']
    new='  // Exact earliest-stage FP RS3 forwarding, factored into local groups.\n'
    for g in range(3):
        i=3*g;new+=f'  wire [31:0] fp_rs3_group{g} = {PREFIX}_bypassEnables[{i}] ? {d[i]} : ({PREFIX}_bypassEnables[{i+1}] ? {d[i+1]} : {d[i+2]});\n'
    new+=f'  assign {OUTPUT} = (|{PREFIX}_bypassEnables[2:0]) ? fp_rs3_group0 :\n'
    new+=f'    (|{PREFIX}_bypassEnables[5:3]) ? fp_rs3_group1 :\n'
    new+=f'    (|{PREFIX}_bypassEnables[8:6]) ? fp_rs3_group2 :\n'
    new+=f'    ({PREFIX}_bypassEnables[9] ? {d[9]} : 32\'h0);'
    candidate=s.replace(old,new);(OUT/'VexiiRiscv.v').write_text(candidate)
    # Extract the literal original emitted selector/aliases, rather than a
    # second hand-written model of the same idea.
    declarations=[]
    for line in s.splitlines():
        if not re.match(r'^  (wire|reg)\s+',line):continue
        name=line.strip().rstrip(';').split()[-1]
        if ((PREFIX in name and ('bypassEnables_' in name or name.endswith('_sel'))) or name.startswith('_zz_'+OUTPUT)):
            declarations.append(line)
    aliases='\n'.join(re.findall(r'^  assign _zz_'+OUTPUT+r'(?:_\d+)? = .*?;$',s,re.M));assert aliases.count('assign ')==16
    a=s.index('  assign _zz_'+PREFIX+'_bypassEnables_bools_0 =')
    b=s.index(old,a)+len(old)
    block=s[a:b].replace('assign '+OUTPUT+' =','assign old_result =')
    newblock=new.replace('assign '+OUTPUT+' =','assign new_result =')
    probe='module fp_rs3_mux_probe(input wire [9:0] enables,input wire [319:0] payload,output wire [31:0] old_result,new_result);\n'
    probe+='\n'.join(declarations)+'\nwire [9:0] '+PREFIX+'_bypassEnables=enables;\n'
    for i,name in enumerate(d):probe+=f'wire [31:0] {name}=payload[{32*i}+:32];\n'
    probe+=aliases+'\n'+block+'\n'+newblock+'\nendmodule\n';(OUT/'probe.v').write_text(probe)
    (OUT/'original-assignment.txt').write_text(old+'\n');(OUT/'candidate-assignment.txt').write_text(new+'\n')
    save(OUT/'prepared.json',dict(original_cpu_sha256=sha(BASE),candidate_cpu_sha256=sha(OUT/'VexiiRiscv.v'),
        scope='Only RS3 FP forwarding data selection; matching, registers, control, FPU, ISA/cache flags unchanged',
        added_state_bits=0,added_cycles=0,regenerated_cpu=False,source_changes=1,fit_run=False,map_run=False))
    shutil.copy2(__file__,OUT/'recipe.py')

CPP=r'''#include "Vfp_rs3_mux_probe.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <stdexcept>
int main(){try{
 Vfp_rs3_mux_probe t;uint64_t checks=0;uint32_t random=0x194aec31;
 auto check=[&](unsigned enable){t.enables=enable;t.eval();unsigned selected=10;
  for(unsigned i=0;i<10;i++)if((enable>>i)&1){selected=i;break;}
  uint32_t want=selected==10?0:t.payload[selected];
  if(t.old_result!=want||t.new_result!=want){std::fprintf(stderr,"enable%03x old%08x new%08x want%08x\n",enable,t.old_result,t.new_result,want);throw std::runtime_error("priority mismatch");}checks++;};
 for(unsigned enable=0;enable<1024;enable++){
  for(unsigned k=0;k<10;k++)t.payload[k]=0;check(enable);
  for(unsigned source=0;source<10;source++)for(unsigned bit=0;bit<32;bit++){
   for(unsigned k=0;k<10;k++)t.payload[k]=0;t.payload[source]=1u<<bit;check(enable);
  }
  const uint32_t fpbits[10]={0,0x80000000,0x7f800000,0xff800000,0x7fc12345,0x7fa54321,1,0x807fffff,0x3f800000,0xffffffff};
  for(unsigned shift=0;shift<10;shift++){for(unsigned k=0;k<10;k++)t.payload[k]=fpbits[(k+shift)%10];check(enable);}
  for(unsigned sample=0;sample<16;sample++){for(unsigned k=0;k<10;k++){random^=random<<13;random^=random>>17;random^=random<<5;t.payload[k]=random;}check(enable);}
 }
 std::printf("{\"passed\":true,\"selector_vectors\":1024,\"bit_basis_per_selector\":320,\"tagged_fp_patterns_per_selector\":10,\"random_vectors_per_selector\":16,\"checks\":%llu}\n",(unsigned long long)checks);
}catch(const std::exception&e){std::fprintf(stderr,"FAIL%s\n",e.what());return1;}return0;}
'''.replace('return1','return 1').replace('return0','return 0')

def prove():
    d=OUT/'combinational';d.mkdir(exist_ok=False);(d/'main.cpp').write_text(CPP)
    cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','fp_rs3_mux_probe','--Mdir',str(d/'obj'),'-CFLAGS','-O2 -std=c++17',str(OUT/'probe.v'),str(d/'main.cpp')]
    save(d/'build-command.json',cmd)
    with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (d/'run.log').open('x') as f:subprocess.run([str(d/'obj/Vfp_rs3_mux_probe')],stdout=f,stderr=subprocess.STDOUT,check=True)
    result=json.loads((d/'run.log').read_text());assert result['passed'];save(d/'result.json',result);print(result)

if __name__=='__main__':
    assert sys.argv[1] in ('prepare','prove');globals()[sys.argv[1]]()
