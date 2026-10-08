#!/usr/bin/env python3
"""Compare the generated os20 frontend and dispatch with their reference.

The reference must be generated with OS20_TIMING=0 and otherwise identical
settings. Extract the actual combinational RTL, including instruction
selection and decompression, instead of duplicating its expected equations.
"""
import argparse
from pathlib import Path
import re
import subprocess


def cone(source, module, inputs, outputs):
    source = source.split('\nendmodule', 1)[0]
    declarations = {m[1]: m[0] for m in re.finditer(
        r'^  (?:wire|reg)\s+(?:\[[^\n]+?\]\s+)?(\w+);$', source, re.M)}
    statements, drivers = [], {}
    for m in re.finditer(r'^  assign \w+\s*=.*?;\n|^  always @\(\*\) begin\n.*?^  end\n',
                         source, re.M | re.S):
        statement = m[0]
        lhs = set(re.findall(r'\b(\w+)\s*(?:\[[^;\n]*?\])?\s*=(?!=)', statement))
        for name in lhs & declarations.keys():
            drivers.setdefault(name, []).append(len(statements))
        statements.append(statement)
    instance = re.search(r'  VexiiRiscvEarlyRvcBank early_rvc\(.*?\);', source, re.S)
    if instance:
        for port in ('inst0', 'inst1', 'fault0', 'fault1'):
            name = re.search(r'\.' + port + r'\((\w+)\)', instance[0])[1]
            drivers.setdefault(name, []).append(len(statements))
        statements.append(instance[0])
    todo, visited, items = list(outputs), set(), set()
    while todo:
        name = todo.pop()
        if name in inputs or name in visited:
            continue
        visited.add(name)
        assert name in drivers and len(drivers[name]) == 1, name
        index = drivers[name][0]
        items.add(index)
        todo.extend((set(re.findall(r'\b\w+\b', statements[index])) & declarations.keys()) - visited)
    width = 0
    for name in outputs:
        size = re.search(r'\[(\d+):0\]', declarations[name])
        width += int(size[1]) + 1 if size else 1
    text = 'module ' + module + '(' + ','.join([*inputs, 'observed']) + ');\n'
    text += '\n'.join(re.sub(r'^  (wire|reg)', 'input wire', declarations[p]) for p in inputs)
    text += f'\noutput wire [{width-1}:0] observed;\n'
    text += 'assign observed = {' + ','.join(outputs) + '};\n'
    text += '\n'.join(declarations[p] for p in sorted(visited)) + '\n'
    text += '\n'.join(statements[i] for i in sorted(items)) + '\nendmodule\n'
    return text, width


def frontend_tb(reference, candidate):
    inputs = ['AlignerPlugin_logic_slices_mask', 'AlignerPlugin_logic_slices_last',
              'AlignerPlugin_api_haltIt', 'AlignerPlugin_api_singleFetch',
              'fetch_logic_ctrls_3_down_valid']
    inputs += [f'AlignerPlugin_logic_slices_data_{i}' for i in range(8)]
    outputs = ['AlignerPlugin_logic_buffer_usedMask']
    for lane in range(2):
        outputs += [f'AlignerPlugin_logic_extractors_{lane}_valid',
                    f'decode_ctrls_0_up_Decode_INSTRUCTION_{lane}',
                    f'decode_ctrls_0_up_Decode_DECOMPRESSION_FAULT_{lane}']
    old, width = cone(reference, 'reference', inputs, outputs)
    new, new_width = cone(candidate, 'candidate', inputs, outputs)
    assert width == new_width
    helpers = re.findall(r'module VexiiRiscvEarlyRvc\w+\(.*?\nendmodule', candidate, re.S)
    assert len(helpers) == 2
    text = old + new + '\n'.join(helpers) + '\nmodule tb;\n'
    text += f'wire [{width-1}:0] original, result;\n'
    for name in inputs:
        bits = 8 if name.endswith(('slices_mask', 'slices_last')) else 16 if '_data_' in name else 1
        text += f'reg [{bits-1}:0] {name};\n'
    text += 'reference ref_dut(' + ','.join(inputs + ['original']) + ');\n'
    text += 'candidate new_dut(' + ','.join(inputs + ['result']) + ');\n'
    text += '''task check; begin
    #1;
    if(original !== result)
        $fatal(1,"Frontend mismatch: reference=%h candidate=%h", original, result);
end endtask
initial begin
    for(integer code=0;code<65536;code++) for(integer slot=0;slot<8;slot++) begin
        AlignerPlugin_logic_slices_mask = 8'(255 << slot);
        AlignerPlugin_logic_slices_last = 0;
        {AlignerPlugin_api_haltIt, AlignerPlugin_api_singleFetch, fetch_logic_ctrls_3_down_valid}=3'b001;
'''
    for i in range(8):
        text += f"        AlignerPlugin_logic_slices_data_{i} = slot=={i} ? 16'(code) : 16'($random);\n"
    text += '''        check();
    end
    for(integer mask=0;mask<256;mask++) for(integer length=0;length<256;length++)
    for(integer flags=0;flags<8;flags++) begin
        AlignerPlugin_logic_slices_mask=8'(mask);
        AlignerPlugin_logic_slices_last=8'($random);
        {AlignerPlugin_api_haltIt, AlignerPlugin_api_singleFetch, fetch_logic_ctrls_3_down_valid}=3'(flags);
'''
    for i in range(8):
        text += f"        AlignerPlugin_logic_slices_data_{i} = (length & (1<<{i})) ? 16'hffff : 16'hfffc;\n"
    text += '        check();\n    end\n    for(integer n=0;n<1000000;n++) begin\n'
    text += '\n'.join('        ' + name + '=$random;' for name in inputs)
    text += '''
        check();
    end
    $display("PASS: complete frontend; 65536 encodings x 8 slots, all mask/length/control combinations, 1000000 random states");
    $finish;
end
endmodule
'''
    return text


def pipeline_tb(reference, candidate):
    """Exercise actual generated capture/forwarding blocks under stalls."""
    text = 'module tb;\nreg clk=0;\n'
    enables = ['execute_ctrl0_down_isReady', 'fetch_logic_ctrls_2_down_isReady',
               'when_AlignerPlugin_l256']
    text += '\n'.join('reg ' + name + ';' for name in enables) + '\n'
    inputs, checks = {}, []
    select0 = '_zz_execute_ctrl0_up_LANE_AGE_lane0'
    select1 = select0 + '_1'
    inputs.update({select0: 1, select1: 1})
    for rs in range(1, 4):
        for lane in range(2):
            for kind, width in [('PHYS', 5), ('RFID', 1)]:
                inputs[f'DispatchPlugin_logic_candidates_{lane}_ctx_hm_RS{rs}_{kind}'] = width
        for kind, width in [('PHYS', 5), ('RFID', 1)]:
            for direction in ('up', 'down'):
                name = f'execute_ctrl0_{direction}_RS{rs}_{kind}_lane0'
                text += f'wire [{width-1}:0] {name};\n'
                text += re.search(rf'^  assign {name} = .*?;$', reference, re.M)[0] + '\n'
        for stage in range(4, 13):
            for kind, width in [('PHYS', 5), ('RFID', 1)]:
                inputs[f'execute_ctrl{stage-1}_down_RD_{kind}_lane0'] = width
            name = f'fp_match_RS{rs}_{stage}'
            old = re.search(rf'  reg {name};\n  always @\(posedge clk\) '
                            rf'if\(execute_ctrl0_down_isReady\)\n    {name} <= [^;]+;', reference)[0]
            text += old.replace(name, 'ref_' + name) + '\n'
            text += re.search(rf'  reg {name};\n  always @\(posedge clk\) '
                              r'if\(execute_ctrl0_down_isReady\) begin\n.*?\n  end', candidate, re.S)[0] + '\n'
            checks.append(f'if({name} !== ref_{name}) $fatal(1,"{name} mismatch");')
    for bank in range(2):
        name = f'fetch_logic_ctrls_3_up_FetchL1Plugin_logic_BANKS_MUXES_{bank}'
        data = f'fetch_logic_ctrls_2_down_FetchL1Plugin_logic_BANKS_MUXES_{bank}'
        inputs[data] = 64
        text += f'reg [63:0] ref_bank{bank};\n'
        text += re.search(rf'  wire \[63:0\] {name};\n.*?{name}_lowbits <= [^;]*;',
                          candidate, re.S)[0] + '\n'
        text += 'always @(posedge clk) begin\n' + re.search(
            rf'    {name}_raw <= [^;]+;\n    {name}_enabled <= [^;]+;\n'
            rf'    if \({name}_enabled\) {name}_held <= [^;]+;', candidate)[0] + '\nend\n'
        text += (f'always @(posedge clk) if(fetch_logic_ctrls_2_down_isReady) '
                 f'ref_bank{bank} <= {data};\n')
        checks.append(f'if(ref_bank{bank} !== {name}) $fatal(1,"fetch bank {bank} mismatch");')
    fields = []
    for match in re.finditer(r'  wire \[(\d+):0\] (AlignerPlugin_logic_buffer_(?:data|hm_\w+));\n'
                            r'  reg .*?\n  \(\* preserve, dont_retime \*\) reg \2_enabled;\n', candidate):
        high, name = int(match[1]), match[2]
        fields.append(name)
        data = re.search(rf'      {name} <= ([^;]+);', reference)[1]
        inputs[data] = high + 1
        ref = f'ref_buffer{len(fields)}'
        text += f'reg [{high}:0] {ref};\n' + match[0]
        if name.endswith('_data'):
            text += re.search(rf'  reg \[7:0\] {name}_lowbits;\n'
                              rf'(?:  assign {name}\[[^\n]+\n)+', candidate)[0]
            text += 'always @(posedge clk) if(when_AlignerPlugin_l256) begin\n'
            text += re.search(rf'      {name}_lowbits <= [^;]+;', candidate)[0] + '\nend\n'
        else:
            text += re.search(rf'  assign {name} = [^;]+;', candidate)[0] + '\n'
        text += 'always @(posedge clk) begin\n' + re.search(
            rf'    {name}_raw <= [^;]+;\n    {name}_enabled <= when_AlignerPlugin_l256;\n'
            rf'    if \({name}_enabled\) {name}_held <= {name}_raw;', candidate)[0] + '\nend\n'
        text += f'always @(posedge clk) if(when_AlignerPlugin_l256) {ref} <= {data};\n'
        checks.append(f'if({ref} !== {name}) $fatal(1,"{name} mismatch");')
    assert len(fields) == 4, fields
    text += '\n'.join(f'reg [{width-1}:0] {name};' for name, width in inputs.items()) + '\n'
    text += 'initial begin\nfor(integer i=0;i<1000000;i++) begin\n'
    text += '\n'.join(name + '=(i==0 || ($random & 7)==0);' for name in enables) + '\n'
    text += '\n'.join(f"{name}={width}'({{$random,$random}});" for name, width in inputs.items())
    text += '\n#1;clk=1;#1;\n' + '\n'.join(checks) + '\nclk=0;\nend\n'
    text += 'execute_ctrl0_down_isReady=1;\n'
    text += ('for(integer a=0;a<64;a++) for(integer b=0;b<64;b++) '
             'for(integer d=0;d<64;d++) for(integer sel=0;sel<4;sel++) begin\n')
    for prefix, index in [('DispatchPlugin_logic_candidates_0_ctx_hm_RS1', 'a'),
                          ('DispatchPlugin_logic_candidates_1_ctx_hm_RS1', 'b')]:
        text += f"{prefix}_PHYS=5'({index}); {prefix}_RFID=1'({index}>>5);\n"
    text += "execute_ctrl3_down_RD_PHYS_lane0=5'(d); execute_ctrl3_down_RD_RFID_lane0=1'(d>>5);\n"
    text += f"{select0}=1'(sel); {select1}=1'(sel>>1);\n#1;clk=1;#1;\n"
    text += '\n'.join(checks) + '\nclk=0;\nend\n'
    text += '''$display("PASS: 27 FPU matches, 2 fetch banks and 4 aligner payloads; 1000000 stalled cycles and exhaustive FPU register/selector combinations");
$finish;
end
endmodule
'''
    return text


def dispatch_tb(reference, candidate):
    """Exhaust the complete scheduler input space, including blocked lanes."""
    widths = {}
    for lane in range(2):
        for key, width in [('ctx_valid', 1), ('flushHazards', 1),
                           ('fenceOlderHazards', 1), ('ctx_laneLayerHits', 2),
                           ('rsHazards', 2), ('reservationHazards', 2)]:
            widths[f'DispatchPlugin_logic_candidates_{lane}_{key}'] = width
    for name in ['DispatchPlugin_logic_scheduler_arbiters_1_candHazard',
                 'execute_freeze_valid', 'DispatchPlugin_api_haltDispatch']:
        widths[name] = 1
    inputs = list(widths)
    outputs = [f'DispatchPlugin_logic_scheduler_arbiters_{i}_doIt' for i in range(2)]
    outputs += [f'DispatchPlugin_logic_candidates_{i}_fire' for i in range(2)]
    outputs += [f'DispatchPlugin_logic_inserter_{i}_oh' for i in range(2)]
    old, width = cone(reference, 'reference', inputs, outputs)
    new, new_width = cone(candidate, 'candidate', inputs, outputs)
    assert width == new_width == 8 and sum(widths.values()) == 21
    text = old + new + '\nmodule tb;\nwire [7:0] original, result;\n'
    text += '\n'.join(f'reg [{widths[n]-1}:0] {n};' for n in inputs) + '\n'
    text += 'reference old_dut(' + ','.join(inputs + ['original']) + ');\n'
    text += 'candidate new_dut(' + ','.join(inputs + ['result']) + ');\n'
    text += 'initial begin for(integer i=0;i<(1<<21);i++) begin\n'
    text += '{' + ','.join(inputs) + "}=21'(i);\n"
    text += '''#1;
    if(original !== result) $fatal(1,"Dispatch mismatch: input=%h old=%h new=%h",i,original,result);
    if(result[3:2] == 2'b11 || result[1:0] == 2'b11) $fatal(1,"Dispatch selectors overlap");
end
$display("PASS: all 2097152 dispatch control states, both issue decisions, both fires and all four instruction selectors");
$finish;
end
endmodule
'''
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--netlist', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    reference, candidate = args.baseline.read_text(), args.netlist.read_text()
    for name, generate in [('frontend', frontend_tb), ('pipeline', pipeline_tb),
                           ('dispatch', dispatch_tb)]:
        directory = out / name
        directory.mkdir(exist_ok=True)
        (directory / 'tb.sv').write_text(generate(reference, candidate))
        with (directory / 'build.log').open('w') as log:
            subprocess.run(['verilator', '--binary', '--timing', '-Wno-fatal', '--top-module', 'tb',
                            '-j', '4', '--Mdir', str(directory / 'obj'), str(directory / 'tb.sv')],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        with (directory / 'test.log').open('w') as log:
            subprocess.run([str(directory / 'obj/Vtb')], stdout=log, stderr=subprocess.STDOUT, check=True)
        print((directory / 'test.log').read_text(), end='')


if __name__ == '__main__':
    main()
