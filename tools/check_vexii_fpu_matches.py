#!/usr/bin/env python3
"""Compare retimed FPU matches with the original generated ID pipeline."""
from pathlib import Path
import argparse
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reference', type=Path, required=True,
                    help='Original CPU before FPU match retiming')
parser.add_argument('--netlist', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
old = args.reference.read_text()
new = args.netlist.read_text()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
# Extract actual original register updates and require each to be gated only
# by the shared pipeline ready chain checked by the transformation.
blocks={stage:re.findall(rf'    if\(execute_ctrl{stage}_down_isReady\) begin\n((?:      [^\n]*\n)+)    end',old) for stage in range(12)}
fields=[]
for stage in range(4,13):
 for kind in ['RD_PHYS','RD_RFID']: fields.append((stage-1, f'execute_ctrl{stage}_up_{kind}_lane0',f'execute_ctrl{stage-1}_down_{kind}_lane0',f'execute_ctrl{stage}_down_{kind}_lane0'))
for rs in range(1,4):
 for kind in ['PHYS','RFID']: fields.append((0,f'execute_ctrl1_up_RS{rs}_{kind}_lane0',f'execute_ctrl0_down_RS{rs}_{kind}_lane0',f'execute_ctrl1_down_RS{rs}_{kind}_lane0'))
text='module tb;\nreg clk=0;\nreg ready;\nwire execute_ctrl0_down_isReady=ready;\n'
updates=[];randoms=[]
aliases={a for _,_,_,a in fields}
for stage,q,d,alias in fields:
 stmt=f'      {q} <= {d};'
 assert old.count(stmt)==1
 candidates=[b for b in blocks[stage] if stmt in b]
 assert len(candidates)==1 and all(re.fullmatch(r'      \w+ <= [^;]+;',line) for line in candidates[0].splitlines())
 decl=re.search(rf'^  reg\s+(\[[^]]+\])\s+{q};$',old,re.M).group(1)
 text+=f'reg {decl} {q};\nwire {decl} {alias}={q};\n'
 if d not in aliases: text+=f'reg {decl} {d};\n'
 updates.append(stmt)
 if d not in aliases: randoms.append(f'{d}=$random;')
text+='always @(posedge clk) if(ready) begin\n'+'\n'.join(updates)+'\nend\n'
checks=[]
for rs in range(1,4):
 for stage in range(4,13):
  name=f'fp_match_RS{rs}_{stage}'
  snippet=re.search(rf'  reg {name};\n  always @\(posedge clk\) if\(execute_ctrl0_down_isReady\)\n    {name} <= [^;]+;',new).group()
  text+=snippet+'\n'
  checks.append(f'if ({name} !== ((execute_ctrl{stage}_down_RD_PHYS_lane0 == execute_ctrl1_down_RS{rs}_PHYS_lane0) && (execute_ctrl{stage}_down_RD_RFID_lane0 == execute_ctrl1_down_RS{rs}_RFID_lane0))) $fatal(1,"Match retiming failed: {name}");')
text+='initial begin\nfor(integer i=0;i<100000;i++) begin\nready=(i==0 || ($random & 3)!=0);\n'+'\n'.join(randoms)+'\n#1; clk=1; #1;\n'+'\n'.join(checks)+'\nclk=0;\nend\n$display("PASS: 27 FPU matches equal original pipeline registers for 100000 cycles with stalls");\n$finish;\nend\nendmodule\n'
(out/'tb.v').write_text(text)
with (out/'build.log').open('w') as f:
 subprocess.run(['verilator','--binary','--timing','--top-module','tb','-j','4','-Wno-fatal','--Mdir',str(out/'obj'),str(out/'tb.v')],stdout=f,stderr=subprocess.STDOUT,check=True)
with (out/'test.log').open('w') as f:
 subprocess.run([str(out/'obj/Vtb')],stdout=f,stderr=subprocess.STDOUT,check=True)
print((out/'test.log').read_text())
