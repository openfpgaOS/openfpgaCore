#!/usr/bin/env python3
"""Normalize compressed RAW upper halves before the aligner one-hot mux."""
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
REF = ART / 'match-preselect-all-v1/immediate/VexiiRiscv.v'
SHA = '0b4b291a6d8d13d9e4b9fe1ba3eb58a2bf2e69f73308b8d936bcfee25e05e846'
CHECK = ROOT / 'tools/check_vexii_os20_timing.py'
CHECK_SHA = '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
P = 'AlignerPlugin_logic_'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def balanced(terms):
    if len(terms) == 1:
        return terms[0]
    split = len(terms)//2
    return '(' + balanced(terms[:split]) + ' | ' + balanced(terms[split:]) + ')'


def prove(out, name, tb):
    directory = out/name
    directory.mkdir()
    (directory/'tb.sv').write_text(tb)
    with (directory/'build.log').open('w') as log:
        subprocess.run(['verilator', '--binary', '--timing', '--top-module', 'tb',
                        '-Wno-fatal', '--Mdir', str(directory/'obj'), '-j', '2', str(directory/'tb.sv')],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (directory/'build.log').read_text())
    with (directory/'proof.log').open('w') as log:
        subprocess.run([str(directory/'obj/Vtb')], check=True, stdout=log, stderr=subprocess.STDOUT)
    print((directory/'proof.log').read_text(), end='', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART/'raw-normalize-v1/immediate')
    args = parser.parse_args()
    src = REF.read_text()
    assert digest(src.encode()) == SHA
    assert digest(CHECK.read_bytes()) == CHECK_SHA

    def once(text):
        assert src.count(text) == 1, (text, src.count(text))

    # Pin all overlapping candidate-word mappings and early is32 classifiers.
    # The final slice has no following halfword: its upper half stays zero.
    for index in range(8):
        high = f'{P}slices_data_{index+1}' if index < 7 else "16'd0"
        sep = ',' if index < 7 else ', '
        once(f'  assign {P}slicesInstructions_{index} = {{{high}{sep}{P}slices_data_{index}}};')
        if index < 7:
            once(f'  assign {P}scanners_{index}_checker_1_required = ({P}slices_data_{index}[1 : 0] == 2\'b11);')

    added, changes, audit = [], [], []
    selectors, masks, raws = [], [], []
    for lane in range(2):
        selector = f'{P}extractors_{lane}_slicesOh'
        selectors.append(selector)
        masks.append(f'{P}extractors_{lane}_usableMask')
        raw = f'decode_ctrls_0_up_Decode_INSTRUCTION_RAW_{lane}'
        raws.append(raw)
        ctx = f'{P}extractors_{lane}_ctx_instruction'
        once(f'  assign {P}feeder_lanes_{lane}_isRvc = ({ctx}[1 : 0] != 2\'b11);')
        old = f'''  always @(*) begin
    {raw} = {ctx};
    if({P}feeder_lanes_{lane}_isRvc) begin
      {raw}[31 : 16] = 16'h0;
    end
  end
'''
        once(old)
        # Check every existing data-mux selector aliases this same priority
        # vector, and every zero fallback remains a literal zero.
        aliases = []
        for slot in range(8-lane):
            suffix = '' if slot == 0 else '_' + str(slot)
            alias = f'_zz_{P}extractors_{lane}_redo{suffix}'
            once(f'  assign {alias} = {selector}[{slot}];')
            aliases.append(alias)
        concat = aliases[0]
        for alias in aliases[1:]:
            concat = '{' + alias + ',' + concat + '}'
        once(f'  assign _zz_{P}extractors_{lane}_ctx_instruction = {concat};')
        old_terms = []
        for slot in range(8-lane):
            if lane == 0:
                alias = f'_zz_{P}extractors_0_ctx_instruction_{slot*2+1}'
                zero = f'_zz_{P}extractors_0_ctx_instruction_{slot*2+2}'
                once(f'  assign {alias} = _zz_{P}extractors_0_ctx_instruction[{slot}];')
                once(f'  assign {zero} = 32\'h0;')
            else:
                alias = f'_zz_{P}extractors_1_ctx_instruction[{slot}]'
                zero = "32'h0"
            old_terms.append(f'({alias} ? {P}slicesInstructions_{slot+lane} : {zero})')
        # Spinal groups the seven-source lane as 4+3, versus floor splitting.
        old_or = balanced(old_terms) if lane == 0 else '('+balanced(old_terms[:4])+' | '+balanced(old_terms[4:6])+' | '+old_terms[6]+')'
        if lane == 1:
            old_or = '(' + balanced(old_terms[:4]) + ' | (' + balanced(old_terms[4:6]) + ' | ' + old_terms[6] + '))'
        once(f'  assign {ctx} = {old_or};')

        upper = []
        dependencies = []
        for index in range(lane, 7):
            slot = index-lane
            name = f'aligner_raw_upper_sel_{lane}_{index}'
            assert not re.search(r'\b' + name + r'\b', src)
            added += [f'  (* keep , syn_keep *) wire {name} /* synthesis syn_keep = 1 */;',
                      f'  assign {name} = {selector}[{slot}] && {P}scanners_{index}_checker_1_required;']
            upper.append(f'({name} ? {P}slices_data_{index+1} : 16\'h0)')
            dependencies.append(dict(selector_bit=slot, low2_slice=index, upper16_slice=index+1))
        new = f'''  always @(*) begin
    {raw} = {{{balanced(upper)},{ctx}[15 : 0]}};
  end
'''
        changes.append((old, new))
        audit.append(dict(lane=lane, low16_unchanged=ctx+'[15 : 0]', upper_terms=dependencies,
                          final_slice_upper_constant_zero=True))

    assert len(added) == 26
    added_text = '\n'.join(added)+'\n'
    candidate = src.replace(changes[0][0], added_text + changes[0][0], 1)
    for old, new in changes:
        candidate = candidate.replace(old, new, 1)
    reverse = candidate.replace(added_text, '', 1)
    for old, new in changes:
        reverse = reverse.replace(new, old, 1)
    assert reverse == src

    spec = importlib.util.spec_from_file_location('cone_checker', CHECK)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    # Extracting the actual select cone with only the two masks as inputs also
    # proves it has no data-word dependency beyond that abstraction boundary.
    select_cone, width = checker.cone(src, 'selector_cone', masks, selectors)
    assert width == 15
    tb = select_cone + '\nmodule tb;\n'
    tb += f'reg [7:0] {masks[0]}; reg [6:0] {masks[1]}; wire [14:0] result;\n'
    tb += 'selector_cone a(' + ','.join(masks+['result']) + ');\n'
    tb += '''initial begin
  for(integer i=0;i<32768;i++) begin
'''
    tb += '    {' + ','.join(masks) + "}=15'(i);\n"
    tb += '''    #1;
    if((result[14:7] & (result[14:7]-8'd1)) != 0 || (result[6:0] & (result[6:0]-7'd1)) != 0)
      $fatal(1,"Real aligner selects overlap: masks=%x selects=%x",i,result);
  end
  $display("PASS: actual aligner priority selects are zero/one-hot for all32768 usable-mask pairs; no other cone inputs");
  $finish;
end
endmodule
'''
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    prove(out, 'selectors', tb)

    # Proof-only declaration normalization; emitted qualifiers retain KEEP.
    plain = candidate
    for line in added[::2]:
        name = re.search(r'wire (\w+)', line)[1]
        assert plain.count(line) == 1
        plain = plain.replace(line, f'  wire {name};', 1)
    data = [f'{P}slices_data_{index}' for index in range(8)]
    inputs = masks + data
    old_cone, width = checker.cone(src, 'reference', inputs, raws)
    new_cone, new_width = checker.cone(plain, 'candidate', inputs, raws)
    assert width == new_width == 64
    tb = old_cone + new_cone + '\nmodule tb;\n'
    tb += f'reg [7:0] {masks[0]}; reg [6:0] {masks[1]};\n'
    tb += '\n'.join(f'reg [15:0] {name};' for name in data) + '\nwire [63:0] original, result;\n'
    tb += 'reference a(' + ','.join(inputs+['original']) + ');\n'
    tb += 'candidate b(' + ','.join(inputs+['result']) + ');\n'
    tb += '''initial begin
  for(integer pattern=0;pattern<(1<<24);pattern++) begin
'''
    for index, name in enumerate(data):
        tb += f'    {name}={{{{14{{pattern[{16+index}]}}}},pattern[{2*index+1}:{2*index}]}};\n'
    tb += '''    for(integer choice=0;choice<9;choice++) begin
'''
    tb += f'      {masks[0]}=choice==8 ? 8\'h0 : (8\'h1 << choice);\n'
    tb += f'      {masks[1]}=(choice==0 || choice==8) ? 7\'h0 : (7\'h1 << (choice-1));\n'
    tb += '''      #1;
      if(original !== result) $fatal(1,"RAW normalization mismatch pattern=%x choice=%d old=%x new=%x",pattern,choice,original,result);
    end
  end
  $display("PASS: both actual RAW cones, all2^24 relevant data-bit inputs x9 selector cases =150994944 comparisons; every output bit covered by pinned bitwise dependencies");
  $finish;
end
endmodule
'''
    # Exact bitwise source guards above exclude cross-bit shifts/arithmetic.
    # Each output bit2..15 (and18..31) depends on all16 low control bits plus
    # one arbitrary bit in each of8 slices:24 independent inputs, exhausted.
    # Bits0/1/16/17 depend only on the16 low control bits, also exhausted.
    # Replicating the8 arbitrary payload bits across14 positions evaluates every
    # output position's complete local truth table in parallel. Independently
    # proving zero/one-hot selection covers every usable-mask pair; for each
    # one-hot pair the output is independent of the other lane's selector.
    prove(out, 'data', tb)
    (out/'VexiiRiscv.v').write_text(candidate)
    shutil.copy2(__file__, out/'raw_normalize.py')
    shutil.copy2(CHECK, out/'check_vexii_os20_timing.py')
    (out/'raw-normalize.patch').write_text(''.join(difflib.unified_diff(src.splitlines(True), candidate.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    (out/'bit-dependency-audit.json').write_text(json.dumps(audit, indent=2)+'\n')
    manifest = dict(reference=str(REF.relative_to(ROOT)), reference_sha256=SHA,
                    candidate_sha256=digest(candidate.encode()), script_sha256=digest(Path(__file__).read_bytes()),
                    checker_sha256=CHECK_SHA, new_register_bits=0, pipeline_cycles_added=0,
                    retained_one_bit_upper_qualifiers=13, low16_selection_unchanged=True,
                    original_select_control_flush_and_storage_unchanged=True,
                    zero_selection_and_compressed_high_half_zero_preserved=True,
                    all_other_rtl_exactly_unchanged=True, selector_input_patterns=32768,
                    data_bit_input_patterns=1<<24, data_selector_cases=9, comparisons=(1<<24)*9,
                    bitwise_independence_guarded=True, whole_cpu_validation='pending')
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
