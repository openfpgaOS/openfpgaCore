#!/usr/bin/env python3
"""Audit direct raw-instruction FP classification against the generated cones.

Does not modify or generate a CPU. Builds a tiny extracted combinational test.
The compared classifier is meaningful whenever RVC decompression has no fault;
when it faults, the independent existing fault gate forces ISA LEGAL false.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / 'docs/measurements/os30-dual-20261004'
RTL = CAMPAIGN / 'generator-v5/VexiiRiscv_os30_dual_rvc_stage1.v'
EXTRACT = CAMPAIGN / 'generator-v5/decompression-equivalence/tb.sv'
OUT = CAMPAIGN / 'raw-fp-flags-audit-v1'


def main():
    OUT.mkdir(exist_ok=True)
    source = RTL.read_text()
    original = EXTRACT.read_text().split('endmodule', 1)[0] + 'endmodule\n'
    assigns = dict(re.findall(r'^  assign (\w+) = (.*);$', source, re.M))
    declarations = {}
    for match in re.finditer(r'^  wire\s+(?:\[[^\]]+\]\s+)?(\w+);$', source, re.M):
        declarations[match.group(1)] = match.group(0)
    instruction = 'decode_ctrls_1_down_Decode_INSTRUCTION_0'
    fpu = 'decode_ctrls_1_down_DecoderPlugin_logic_NEED_FPU_0'
    rm = 'decode_ctrls_1_down_DecoderPlugin_logic_NEED_RM_0'
    visited = set()

    def visit(name):
        if name == instruction or name in visited:
            return
        assert name in assigns and name in declarations, name
        visited.add(name)
        for dep in re.findall(r'\b[A-Za-z_]\w*\b', assigns[name]):
            if dep == instruction or dep in assigns:
                visit(dep)

    visit(fpu)
    visit(rm)
    cone = ('module fp_class(input [31:0] inst, output need_fpu, need_rm);\n' +
            '\n'.join(declarations[name] for name in sorted(visited)) + '\n' +
            '\n'.join('assign ' + name + ' = ' + assigns[name].replace(instruction, 'inst') + ';'
                      for name in sorted(visited)) + '\n' +
            f'assign need_fpu={fpu};\nassign need_rm={rm};\nendmodule\n')
    tb = r'''
module tb;
 reg [31:0] raw;
 wire [32:0] decompressed;
 wire old_fpu, old_rm, direct_fpu, direct_rm;
 wire ordinary_fpu, ordinary_rm;
 wire compressed = raw[1:0] != 2'b11;
 wire [4:0] rvc_family = {raw[1:0],raw[15:13]};
 // C.FLW, C.FSD, C.FSW, C.FLWSP, C.FSWSP. The existing RV32F
 // decompressor emits C.FSD too; normal ISA legality rejects that result.
 wire compressed_fpu = rvc_family==5'd3 || rvc_family==5'd5 ||
                       rvc_family==5'd7 || rvc_family==5'd19 ||
                       rvc_family==5'd23;
 reg fpu_enable;
 reg [2:0] frm;
 wire [2:0] old_inst_rm=decompressed[15:13];
 wire [2:0] new_inst_rm=raw[14:12];
 wire [2:0] old_rm_value=old_inst_rm==3'd7 ? frm : old_inst_rm;
 wire [2:0] new_rm_value=new_inst_rm==3'd7 ? frm : new_inst_rm;
 wire old_trigger=(old_fpu && !fpu_enable) || (old_rm && old_rm_value>=3'd5);
 wire new_trigger=(direct_fpu && !fpu_enable) || (direct_rm && new_rm_value>=3'd5);
 original decompressor(raw,decompressed);
 fp_class old_decode(decompressed[32:1],old_fpu,old_rm);
 fp_class direct_decode(raw,ordinary_fpu,ordinary_rm);
 assign direct_fpu = compressed ? compressed_fpu : ordinary_fpu;
 assign direct_rm = !compressed && ordinary_rm;
 integer checked=0, faults=0, compressed_checked=0, ordinary_checked=0;
 task check;
 begin
  #1;
  if (decompressed[0]) faults++;
  else begin
   checked++;
   if(compressed)compressed_checked++;else ordinary_checked++;
   if({old_fpu,old_rm} !== {direct_fpu,direct_rm})
     $fatal(1,"FP classifier mismatch raw=%h expanded=%h old=%b%b direct=%b%b",
            raw,decompressed[32:1],old_fpu,old_rm,direct_fpu,direct_rm);
  end
  // Existing independent fault gate forces LEGAL false even if the source
  // decompressor's instruction bits are don't-care for a rejected encoding.
  for(integer state=0;state<16;state++) begin
   {fpu_enable,frm}=4'(state);
   #1;
   if((decompressed[0] || old_trigger) !== (decompressed[0] || new_trigger))
    $fatal(1,"FP trap mismatch raw=%h expanded=%h fs=%b frm=%d",raw,decompressed[32:1],fpu_enable,frm);
  end
 end
 endtask
 initial begin
  for(integer i=0;i<65536;i++) begin raw={$random,16'(i)};check();end
  for(integer i=0;i<1000000;i++) begin raw=$random;check();end
  $display("PASS: direct RAW FP flags and all16 FP CSR states per input; checked=%0d compressed=%0d ordinary=%0d fault-masked=%0d",
           checked,compressed_checked,ordinary_checked,faults);
  $finish;
 end
endmodule
'''
    bench = OUT / 'tb.sv'
    bench.write_text(original + cone + tb)
    command = ['verilator', '--binary', '--timing', '-Wno-fatal', '-j', '2',
               '--top-module', 'tb', '--Mdir', str(OUT / 'obj'), str(bench)]
    with (OUT / 'build.log').open('w') as log:
        subprocess.run(command, cwd=ROOT, check=True, stdout=log, stderr=subprocess.STDOUT)
    result = subprocess.run([str(OUT / 'obj/Vtb')], cwd=ROOT, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (OUT / 'run.log').write_text(result.stdout)
    inputs = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in [RTL, EXTRACT, Path(__file__), bench]}
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        purpose='Combinational audit only; no CPU RTL modification or timing claim',
        input_hashes=inputs, generated_classifier_cone=sorted(visited),
        build_command=command, results=result.stdout), indent=2) + '\n')
    print(result.stdout, end='')


if __name__ == '__main__':
    main()
