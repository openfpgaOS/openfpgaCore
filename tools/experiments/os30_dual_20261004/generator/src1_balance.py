#!/usr/bin/env python3
"""Freeze a local lane-zero integer SRC1 one-hot data OR rewrite."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
REF = ART / 'fp-preselect-v1/immediate/VexiiRiscv.v'
SHA = 'a85cdf3621b3e0257f7fd2ceeb5288c554cf50bc76f1b7bf728006ca726370ad'
CHECK = ROOT / 'tools/check_vexii_os20_timing.py'
CHECK_SHA = '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
SEL = 'execute_lane0_bypasser_integer_RS1_sel'
ENABLES = 'execute_lane0_bypasser_integer_RS1_bypassEnables'
DEST = '_zz_execute_ctrl1_down_integer_RS1_lane0_1'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def balanced(terms):
    if len(terms) == 1:
        return terms[0]
    split = len(terms) // 2
    return '(' + balanced(terms[:split]) + ' | ' + balanced(terms[split:]) + ')'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART / 'src1-balance-v1/immediate')
    args = parser.parse_args()
    src = REF.read_text()
    assert digest(src.encode()) == SHA
    assert digest(CHECK.read_bytes()) == CHECK_SHA

    def once(text):
        assert src.count(text) == 1, (text, src.count(text))

    keep = '  (* keep , syn_keep *) reg        [31:0]   ' + DEST + ' /* synthesis syn_keep = 1 */ ;'
    once(keep)
    once('  assign when_ExecuteLanePlugin_l196 = ' + SEL + '[0];')
    once('  assign _zz_execute_ctrl1_down_integer_RS1_lane0 = ' + SEL + '[8 : 1];')
    once('    ' + ENABLES + "[8] = 1'b1;")
    data = [f'execute_ctrl{stage}_down_lane{lane}_integer_WriteBackPlugin_logic_DATA_lane{lane}'
            for stage in range(2, 6) for lane in range(2)]
    data.append('execute_lane0_bypasser_integer_RS1_port_data')
    # Pin all eight masked terms of the original balanced OR, and the exact
    # selector-zero override. None of their data values are approximated.
    old_terms = []
    for index in range(8):
        suffix = '' if index == 0 else '_' + str(index * 2)
        select = '_zz_' + DEST + suffix
        zero = '_zz_' + DEST + '_' + str(index * 2 + 1)
        once(f'  assign {select} = _zz_execute_ctrl1_down_integer_RS1_lane0[{index}];')
        once(f'  assign {zero} = 32\'h0;')
        old_terms.append(f'({select} ? {data[index+1]} : {zero})')
    old = f'''  always @(*) begin
    {DEST} = {balanced(old_terms)};
    if(when_ExecuteLanePlugin_l196) begin
      {DEST} = {data[0]};
    end
  end
'''
    once(old)
    # Keep the real priority/select network and its aliases unchanged. Only
    # express all nine already-exclusive data terms in the same OR network.
    new_terms = [f'({SEL}[{index}] ? {value} : 32\'h0)' for index, value in enumerate(data)]
    new = f'''  always @(*) begin
    {DEST} = {balanced(new_terms)};
  end
'''
    candidate = src.replace(old, new, 1)
    assert candidate.replace(new, old, 1) == src
    assert candidate.count(keep) == 1

    spec = importlib.util.spec_from_file_location('cone_checker', CHECK)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    # Normalize only the output's KEEP syntax for the declaration parser. The
    # candidate CPU retains the exact original KEEP declaration byte-for-byte.
    ordinary = '  reg [31:0] ' + DEST + ';'
    inputs = [ENABLES] + data
    outputs = [SEL, DEST]
    reference_cone, width = checker.cone(src.replace(keep, ordinary, 1), 'reference', inputs, outputs)
    candidate_cone, new_width = checker.cone(candidate.replace(keep, ordinary, 1), 'candidate', inputs, outputs)
    assert width == new_width == 41
    tb = reference_cone + candidate_cone + '\nmodule tb;\n'
    tb += f'reg [8:0] {ENABLES};\n'
    tb += '\n'.join(f'reg [31:0] {value};' for value in data) + '\n'
    tb += 'wire [40:0] original, result;\n'
    tb += 'reference a(' + ','.join(inputs + ['original']) + ');\n'
    tb += 'candidate b(' + ','.join(inputs + ['result']) + ');\n'
    tb += '''initial begin
  for(integer enables=0;enables<512;enables++) begin
'''
    tb += f'    {ENABLES}=9\'(enables);\n'
    tb += '''    for(integer bitpos=0;bitpos<32;bitpos++) begin
      for(integer values=0;values<512;values++) begin
'''
    for index, value in enumerate(data):
        tb += f'        {value}=values[{index}] ? (32\'h1 << bitpos) : 32\'h0;\n'
    tb += '''        #1;
        if((original[40:32] & (original[40:32]-9'd1)) != 0)
          $fatal(1,"Original SRC1 selection is not zero/one-hot: enables=%x sel=%x",enables,original[40:32]);
        if(enables[8] && original[40:32]==0)
          $fatal(1,"Original SRC1 fallback is not selected: enables=%x",enables);
        if(original !== result)
          $fatal(1,"SRC1 mismatch enables=%x values=%x bit=%d old=%x new=%x",enables,values,bitpos,original,result);
      end
    end
  end
  $display("PASS: actual SRC1 priority cone and both 32-bit muxes, all512 enable patterns x512 data-bit patterns x32 bit positions =8388608 cases");
  $finish;
end
endmodule
'''
    # The muxes are bitwise: expressions contain only OR and scalar-selected
    # full-width data/zero, with no arithmetic, data slices or bit mixing.
    # Therefore exhaustive local input bits at every output bit prove arbitrary
    # simultaneous 32-bit values, not just the sparse words used by the harness.
    assert len(old_terms) == 8 and len(new_terms) == 9
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'tb.sv').write_text(tb)
    with (out / 'build.log').open('w') as log:
        subprocess.run(['verilator', '--binary', '--timing', '--top-module', 'tb',
                        '-Wno-fatal', '--Mdir', str(out / 'obj'), '-j', '2', str(out / 'tb.sv')],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (out / 'build.log').read_text())
    with (out / 'proof.log').open('w') as log:
        subprocess.run([str(out / 'obj/Vtb')], check=True, stdout=log, stderr=subprocess.STDOUT)
    (out / 'VexiiRiscv.v').write_text(candidate)
    shutil.copy2(__file__, out / 'src1_balance.py')
    shutil.copy2(CHECK, out / 'check_vexii_os20_timing.py')
    (out / 'src1-balance.patch').write_text(''.join(difflib.unified_diff(src.splitlines(True), candidate.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    manifest = dict(reference=str(REF.relative_to(ROOT)), reference_sha256=SHA,
                    candidate_sha256=digest(candidate.encode()), script_sha256=digest(Path(__file__).read_bytes()),
                    checker_sha256=CHECK_SHA, changed_cone=DEST, new_register_bits=0, pipeline_cycles_added=0,
                    original_priority_select_cone_unchanged=True, original_keep_unchanged=True,
                    all_other_rtl_exactly_unchanged=True, exhaustive_enable_patterns=512,
                    exhaustive_data_bit_patterns=512, output_bit_positions=32, comparisons=8388608,
                    data_independence='Both exact guarded mux expressions are bitwise scalar-selected 32-bit ORs.',
                    whole_cpu_validation='not run: parent requested proof-only fallback pending fitted timing')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print((out / 'proof.log').read_text(), end='')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
