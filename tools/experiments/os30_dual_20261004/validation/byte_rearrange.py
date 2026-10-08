#!/usr/bin/env python3
"""Freeze and prove a private byte-before-way load-data factorization."""
from pathlib import Path
import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'docs/measurements/os30-dual-20261004'
REFERENCE = BASE / 'raw-normalize-v1/immediate/VexiiRiscv.v'
REFERENCE_SHA = '06c797895939e09a2a63a0eb921f2ae73beb8e8e55ef4ac98521c8c75f65d048'

OLD = '''  always @(*) begin
    LsuPlugin_logic_onCtrl_loadData_shifted[7 : 0] = _zz_LsuPlugin_logic_onCtrl_loadData_shifted;
    LsuPlugin_logic_onCtrl_loadData_shifted[15 : 8] = _zz_LsuPlugin_logic_onCtrl_loadData_shifted_2;
    LsuPlugin_logic_onCtrl_loadData_shifted[23 : 16] = LsuPlugin_logic_onCtrl_loadData_splitted_2;
    LsuPlugin_logic_onCtrl_loadData_shifted[31 : 24] = LsuPlugin_logic_onCtrl_loadData_splitted_3;
  end'''

NEW = '''  // The byte map preserves zero and bitwise OR. Select each source's bytes
  // before the late cache-way/IO choice; preserve simultaneous-hit OR semantics.
  function [31:0] os30_lsu_byte_map;
    input [31:0] word_data;
    input [1:0] byte_address;
    begin
      case(byte_address)
        2'b00 : os30_lsu_byte_map[7:0] = word_data[7:0];
        2'b01 : os30_lsu_byte_map[7:0] = word_data[15:8];
        2'b10 : os30_lsu_byte_map[7:0] = word_data[23:16];
        default : os30_lsu_byte_map[7:0] = word_data[31:24];
      endcase
      os30_lsu_byte_map[15:8] = byte_address[1] ? word_data[31:24] : word_data[15:8];
      os30_lsu_byte_map[31:16] = word_data[31:16];
    end
  endfunction
  wire os30_lsu_byte_way0 = !LsuPlugin_logic_onCtrl_io_cmdSent && execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[0];
  wire os30_lsu_byte_way1 = !LsuPlugin_logic_onCtrl_io_cmdSent && execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[1];
  always @(*) begin
    LsuPlugin_logic_onCtrl_loadData_shifted =
      (LsuPlugin_logic_onCtrl_io_cmdSent ? os30_lsu_byte_map(LsuPlugin_logic_onCtrl_io_rsp_payload_data, execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0]) : 32'h0) |
      (os30_lsu_byte_way0 ? os30_lsu_byte_map(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0, execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0]) : 32'h0) |
      (os30_lsu_byte_way1 ? os30_lsu_byte_map(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1, execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0]) : 32'h0);
  end'''

LOW_CASE = '''  always @(*) begin
    case(_zz_LsuPlugin_logic_onCtrl_loadData_shifted_1)
      2'b00 : _zz_LsuPlugin_logic_onCtrl_loadData_shifted = LsuPlugin_logic_onCtrl_loadData_splitted_0;
      2'b01 : _zz_LsuPlugin_logic_onCtrl_loadData_shifted = LsuPlugin_logic_onCtrl_loadData_splitted_1;
      2'b10 : _zz_LsuPlugin_logic_onCtrl_loadData_shifted = LsuPlugin_logic_onCtrl_loadData_splitted_2;
      default : _zz_LsuPlugin_logic_onCtrl_loadData_shifted = LsuPlugin_logic_onCtrl_loadData_splitted_3;
    endcase
  end'''
HIGH_CASE = '''  always @(*) begin
    case(_zz_LsuPlugin_logic_onCtrl_loadData_shifted_3)
      1'b0 : _zz_LsuPlugin_logic_onCtrl_loadData_shifted_2 = LsuPlugin_logic_onCtrl_loadData_splitted_1;
      default : _zz_LsuPlugin_logic_onCtrl_loadData_shifted_2 = LsuPlugin_logic_onCtrl_loadData_splitted_3;
    endcase
  end'''

LEAVES = [
    ('1:0', 'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0'),
    ('31:0', 'execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0'),
    ('31:0', 'execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1'),
    ('31:0', 'execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0'),
    ('', 'LsuPlugin_logic_onCtrl_io_cmdSent'),
    ('31:0', 'LsuPlugin_logic_onCtrl_io_rsp_payload_data'),
]
RESULT = 'execute_ctrl4_down_LsuPlugin_logic_onCtrl_loadData_RESULT_lane0'
ASSIGNS = [
    '_zz_execute_ctrl4_down_LsuL1Plugin_logic_MUXED_DATA_lane0',
    '_zz_execute_ctrl4_down_LsuL1Plugin_logic_MUXED_DATA_lane0_1',
    'execute_ctrl4_down_LsuL1Plugin_logic_MUXED_DATA_lane0',
    'execute_ctrl4_down_LsuL1Plugin_logic_BYPASSED_DATA_lane0',
    'execute_ctrl4_down_LsuL1_READ_DATA_lane0',
    'LsuPlugin_logic_onCtrl_loadData_input',
    *[f'LsuPlugin_logic_onCtrl_loadData_splitted_{n}' for n in range(4)],
    '_zz_LsuPlugin_logic_onCtrl_loadData_shifted_1',
    '_zz_LsuPlugin_logic_onCtrl_loadData_shifted_3',
    RESULT,
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, path):
    with path.open('w') as log:
        subprocess.run(list(map(str, command)), check=True, stdout=log, stderr=subprocess.STDOUT)


def assignment(source, name):
    matches = re.findall(r'^  assign ' + re.escape(name) + r' = [^\n]+;$', source, re.M)
    assert len(matches) == 1, name
    return matches[0]


def declaration(source, name):
    matches = re.findall(r'^  (?:reg|wire)\s+(?:\[[^\]]+\]\s+)?' + re.escape(name) + ';$', source, re.M)
    assert len(matches) == 1, name
    return matches[0]


def make_module(name, source, candidate=False):
    ports = [f'input wire {"["+width+"] " if width else ""}{signal}' for width, signal in LEAVES]
    ports.append('output wire [31:0] ' + RESULT)
    result = f'module {name}(\n' + ',\n'.join(ports) + '\n);\n'
    if candidate:
        assert source.count(NEW) == 1
        result += declaration(source, 'LsuPlugin_logic_onCtrl_loadData_shifted') + '\n'
        # Copy the actual emitted candidate block, not a second model of it.
        start = source.index(NEW)
        result += source[start:start+len(NEW)] + '\n'
        result += assignment(source, RESULT) + '\n'
    else:
        names = [x for x in ASSIGNS if x != RESULT]
        names += ['_zz_LsuPlugin_logic_onCtrl_loadData_shifted',
                  '_zz_LsuPlugin_logic_onCtrl_loadData_shifted_2',
                  'LsuPlugin_logic_onCtrl_loadData_shifted']
        result += '\n'.join(declaration(source, signal) for signal in names) + '\n'
        result += '\n'.join(assignment(source, signal) for signal in ASSIGNS) + '\n'
        for block in [LOW_CASE, HIGH_CASE, OLD]:
            assert source.count(block) == 1
            start = source.index(block)
            result += source[start:start+len(block)] + '\n'
    return result + 'endmodule\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=BASE/'load-byte-factor-v1/immediate')
    args = parser.parse_args()
    out = args.output.resolve()
    assert sha(REFERENCE) == REFERENCE_SHA
    src = REFERENCE.read_text()
    assert src.count(OLD) == 1 and 'os30_lsu_byte_' not in src
    new = src.replace(OLD, NEW, 1)
    assert new.replace(NEW, OLD, 1) == src
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, out/'byte_rearrange.py')
    (out/'VexiiRiscv.v').write_text(new)
    (out/'byte-before-way.patch').write_text(''.join(difflib.unified_diff(
        src.splitlines(True), new.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    original = make_module('reference', src)
    actual = make_module('candidate', (out/'VexiiRiscv.v').read_text(), True)
    (out/'reference.sv').write_text(original)
    (out/'candidate.sv').write_text(actual)
    tb = 'module tb;\n'
    for width, signal in LEAVES:
        tb += f'reg {"["+width+"] " if width else ""}{signal};\n'
    tb += 'wire [31:0] expected, actual;\n'
    for module, result in [('reference','expected'),('candidate','actual')]:
        connections = [f'.{signal}({signal})' for _,signal in LEAVES] + [f'.{RESULT}({result})']
        tb += f'{module} dut_{module}(' + ','.join(connections) + ');\n'
    tb += '''integer count=0, both_hits=0, no_hits=0, io_priority=0;
initial begin
  execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0=0;
  for(integer control=0;control<32;control++) begin
    {LsuPlugin_logic_onCtrl_io_cmdSent,execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0,execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0]}=5'(control);
    for(integer bitpos=0;bitpos<32;bitpos++) begin
      for(integer data_bits=0;data_bits<8;data_bits++) begin
        execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0=data_bits[0] ? (32'h1<<bitpos) : 32'h0;
        execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1=data_bits[1] ? (32'h1<<bitpos) : 32'h0;
        LsuPlugin_logic_onCtrl_io_rsp_payload_data=data_bits[2] ? (32'h1<<bitpos) : 32'h0;
        #1;
        if(expected !== actual) $fatal(1,"load-byte mismatch ctrl=%x bit=%0d data=%x expected=%x actual=%x",control,bitpos,data_bits,expected,actual);
        count=count+1;
        if(execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0==2'b11) both_hits=both_hits+1;
        if(execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0==2'b00) no_hits=no_hits+1;
        if(LsuPlugin_logic_onCtrl_io_cmdSent) io_priority=io_priority+1;
      end
    end
  end
  $display("PASS load-byte actual cones: cases=%0d both_hits=%0d no_hits=%0d io_priority=%0d; all96 data basis bits across all32 controls",count,both_hits,no_hits,io_priority);
  $finish;
end
endmodule
'''
    (out/'tb.sv').write_text(tb)
    run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal','--Mdir',out/'obj','-j','2',
         out/'reference.sv',out/'candidate.sv',out/'tb.sv'],out/'build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)',(out/'build.log').read_text())
    run([out/'obj/Vtb'],out/'proof.log')
    # A standalone full-netlist lint catches function/declaration integration.
    run(['verilator','--lint-only','-Wno-fatal','--top-module','VexiiRiscv',out/'VexiiRiscv.v'],out/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)',(out/'lint.log').read_text())
    replay=out/'replay';replay.mkdir()
    shutil.copy2(REFERENCE,replay/'VexiiRiscv.v')
    run(['patch','--batch','--forward','-p1','-d',replay,'-i',out/'byte-before-way.patch'],out/'replay.log')
    assert sha(replay/'VexiiRiscv.v')==sha(out/'VexiiRiscv.v')
    manifest=dict(reference=str(REFERENCE.relative_to(ROOT)),reference_sha256=REFERENCE_SHA,
        candidate_sha256=sha(out/'VexiiRiscv.v'),script_sha256=sha(out/'byte_rearrange.py'),
        exact_single_combinational_block_replacement=True,reverse_source_equality=True,
        every_sequential_block_unchanged=True,top_interface_unchanged=True,
        downstream_sign_sc_amo_fpu_consumers_unchanged=True,
        actual_original_dependency_assignments={name:assignment(src,name) for name in ASSIGNS},
        actual_candidate_cone_extracted_from_emitted_source=True,
        control_states=32,data_bit_triples=8,data_bit_positions=32,cases=8192,
        independent_data_basis_vectors=96,all_basis_vectors_checked_under_every_control=True,
        proof_method='Exact control exhaust plus all data basis bits. For fixed controls both actual cones contain only bit projection, zero masking and OR, so preserve zero and OR; equality on each input basis extends to arbitrary simultaneous 96-bit data words. No one-hot-hit assumption.',
        both_hit_cases=2048,no_hit_cases=2048,io_priority_cases=4096,
        added_state_bits=0,added_pipeline_cycles=0,
        exact_patch_replay=True,replay_sha256=sha(replay/'VexiiRiscv.v'),
        whole_cpu_regression='Not run; awaiting selection',physical_result='Not measured')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    files=['VexiiRiscv.v','byte_rearrange.py','byte-before-way.patch','manifest.json',
           'reference.sv','candidate.sv','tb.sv','build.log','proof.log','lint.log','replay.log','obj/Vtb']
    (out/'recipe.json').write_text(json.dumps(dict(reference=manifest['reference'],reference_sha256=REFERENCE_SHA,
        files={name:sha(out/name) for name in files},replay_sha256=sha(replay/'VexiiRiscv.v')),indent=2)+'\n')
    print((out/'proof.log').read_text(),end='')
    print('Candidate SHA256',manifest['candidate_sha256'])


if __name__ == '__main__':
    main()
