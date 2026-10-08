#!/usr/bin/env python3
"""Qualify an early D-cache tag-data mux against the actual generated control cone."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
CPU = ART / 'timing-rescue-v4/rvc-skid-masked-control/VexiiRiscv.v'
OUT = ART / 'tag-select-audit-v1'
P = 'LsuL1Plugin_logic_'
C = P + 'lsu_ctrl_'
E = 'execute_ctrl4_down_'
src = CPU.read_text()
declarations = {}
for match in re.finditer(r'^  (wire|reg)\s+(\[\d+:0\]\s+)?(\w+);$', src, re.M):
    declarations[match[3]] = (match[1], (match[2] or '').strip())

def always_blocks():
    for start in re.finditer(r'^  always @\(\*\) begin\n', src, re.M):
        level = 0
        for token in re.finditer(r'\bbegin\b|\bend\b', src[start.start():]):
            level += 1 if token[0] == 'begin' else -1
            if level == 0:
                yield src[start.start():start.start() + token.end()] + '\n'
                break

blocks = list(always_blocks())
bool_inputs = [E+'LsuL1_SEL_lane0', E+'LsuL1_FLUSH_lane0', E+'LsuL1_INVALID_lane0',
               C+'askCbm', P+'writeback_full', C+'writebackHazard',
               P+'refill_slots_0_valid', P+'refill_slots_1_valid',
               P+'bus_read_rsp_valid', 'when_LsuL1Plugin_l466',
               C+'preventSideEffects', 'when_LsuL1Plugin_l1234',
               P+'refill_read_way']
wide_inputs = [E+'LsuL1Plugin_logic_WAYS_HITS_lane0', C+'needFlushs',
               P+'refill_slots_0_address', P+'refill_slots_1_address',
               E+'LsuL1_PHYSICAL_ADDRESS_lane0', P+'refill_read_rspAddress',
               '_zz_'+P+'waysWrite_tag_address', P+'refill_read_faulty',
               '_zz_'+P+'waysWrite_tag_fault', '_zz_'+P+'waysWrite_tag_fault_1']
inputs = set(bool_inputs + wide_inputs)
targets = [P+'waysWrite_mask', P+'waysWrite_tag_loaded', P+'waysWrite_tag_address',
           P+'waysWrite_tag_fault']
drivers, names, pending = {}, set(inputs), list(targets)
while pending:
    name = pending.pop()
    if name in inputs or name in drivers:
        continue
    continuous = re.findall(r'^  assign '+re.escape(name)+r' = [^\n]+;\n', src, re.M)
    procedural = [block for block in blocks if re.search(
        r'\b'+re.escape(name)+r'(?:\[[^\n]+?\])?\s*=', block)]
    found = continuous + procedural
    assert len(found) == 1, (name, len(found))
    block = found[0]
    written = set(re.findall(r'^\s*(?:assign )?(\w+)(?:\[[^\n]+?\])?\s*=', block, re.M))
    for output in written:
        assert output not in inputs
        drivers[output] = block
    deps = set(re.findall(r'\b\w+\b', block)) & set(declarations)
    names |= deps | written
    pending += list(deps - written)

early = ('  wire tag_early_cbm = '+E+'LsuL1_SEL_lane0 && '+C+'askCbm && '+C+'wayWriteReservation_win;\n'
         '  wire tag_early_flush = '+E+'LsuL1_SEL_lane0 && '+E+'LsuL1_FLUSH_lane0 && '
         + C+'wayWriteReservation_win && !(|{'+P+'refill_slots_1_valid,'+P+'refill_slots_0_valid}) && (|'+C+'needFlushs);\n')
# The changed select excludes live writeback/refill-address hazards. Write
# enables/arbitration and every data source remain the original generated RTL.
selected_blocks = sorted(set(drivers.values()))
modified = []
for block in selected_blocks:
    if any(re.search(r'\b'+re.escape(target)+r'\s*=', block) for target in targets[1:]):
        assert block.count('if('+C+'doCbm)') == 1
        assert block.count('if('+C+'doFlush)') == 1
        block = block.replace('if('+C+'doCbm)', 'if(EARLY ? tag_early_cbm : '+C+'doCbm)')
        block = block.replace('if('+C+'doFlush)', 'if(EARLY ? tag_early_flush : '+C+'doFlush)')
    modified.append(block)
ports = []
for name in sorted(inputs):
    ports.append('input wire '+declarations[name][1]+' '+name)
ports += ['output wire [1:0] mask', 'output wire [17:0] tag']
design = 'module cone #(parameter EARLY=0)(\n'+',\n'.join(ports)+'\n);\n'
for name in sorted(names - inputs):
    kind, width = declarations[name]
    design += '  '+kind+' '+width+' '+name+';\n'
design += early + '\n'.join(modified)
design += 'assign mask='+targets[0]+';\nassign tag={'+targets[3]+','+targets[2]+','+targets[1]+'};\nendmodule\n'
tb = 'module tb;\n'
for name in sorted(inputs):
    tb += 'logic '+declarations[name][1]+' '+name+';\n'
tb += 'wire [1:0] mask_old,mask_new; wire [17:0] tag_old,tag_new;\n'
connections = ','.join('.'+name+'('+name+')' for name in sorted(inputs))
tb += 'cone #(.EARLY(0)) old('+connections+',.mask(mask_old),.tag(tag_old));\n'
tb += 'cone #(.EARLY(1)) fresh('+connections+',.mask(mask_new),.tag(tag_new));\n'
tb += 'integer n; integer writes=0; integer invalidations=0; initial begin\n'
tb += 'for(n=0;n<(1<<19);n=n+1)begin\n'
for index, name in enumerate(bool_inputs):
    tb += name+f'=n[{index}];\n'
tb += E+'LsuL1Plugin_logic_WAYS_HITS_lane0=n[14:13];\n'
tb += C+'needFlushs=n[16:15];\n'
tb += E+"LsuL1_PHYSICAL_ADDRESS_lane0=32'h12340000;\n"
tb += P+"refill_read_rspAddress=32'h56780000;\n"
tb += '_zz_'+P+"waysWrite_tag_address=16'h9abc;\n"
tb += P+"refill_slots_0_address=n[17]?32'h0:32'h40;\n"
tb += P+"refill_slots_1_address=n[18]?32'h0:32'h40;\n"
tb += P+"refill_read_faulty=0;\n_zz_"+P+"waysWrite_tag_fault=1;\n_zz_"+P+"waysWrite_tag_fault_1=0;\n"
tb += '''#1;
if(mask_old !== mask_new)$fatal(1,"Mask mismatch %0d",n);
if(mask_old!=0)begin
  writes=writes+1;
  if(tag_old[0] !== tag_new[0])$fatal(1,"Loaded mismatch %0d",n);
  if(!when_LsuL1Plugin_l1234 && tag_old !== tag_new)$fatal(1,"Tag mismatch %0d old=%x new=%x",n,tag_old,tag_new);
  if(when_LsuL1Plugin_l1234)begin
    invalidations=invalidations+1;
    if(tag_old[0] !== 0 || tag_new[0] !== 0)$fatal(1,"Initializer tag not invalid");
  end
end
end
$display("PASS: 524288 control states, %0d enabled writes, %0d initializer invalidations",writes,invalidations);
$finish;
end endmodule
'''
OUT.mkdir(exist_ok=False)
(OUT/'tb.sv').write_text(design+tb)
with (OUT/'build.log').open('w') as log:
    subprocess.run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal',
                    '--Mdir',str(OUT/'obj'),'-j','2',str(OUT/'tb.sv')],check=True,
                   stdout=log,stderr=subprocess.STDOUT)
with (OUT/'run.log').open('w') as log:
    subprocess.run([str(OUT/'obj/Vtb')],check=True,stdout=log,stderr=subprocess.STDOUT)
manifest = dict(cpu=str(CPU.relative_to(ROOT)),cpu_sha256=hashlib.sha256(CPU.read_bytes()).hexdigest(),
                proof='Exhaustive actual generated write-control/tag mux cone, two refill valid/match states',
                control_states=1<<19,all_enabled_noninitializer_tag_bits_equal=True,
                initializer_loaded_remains_false=True,changes_sequential_state=False,
                caveat='Initializer-invalid tag address/fault bits are unobserved; complete CPU regressions and fitting still required',
                early_expressions=early,source_blocks=len(selected_blocks))
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print((OUT/'run.log').read_text())
