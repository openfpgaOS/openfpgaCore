#!/usr/bin/env python3
"""Private command-equivalent SDRAM address selection; no extra state/cycles."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'compact-lod5-core-v3/frozen'
OUT = P / 'sdram-address-mux-v1'
REL = 'src/fpga/targets/pocket/io_sdram.v'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def prepare():
    OUT.mkdir(exist_ok=False)
    source = (BASE / REL).read_text()
    # Address is sampled only for ACT, READ, WRITE, PRECHARGE and LMR.
    # Hoist selection out of their late enables. All other cycles may change
    # ignored address bits. The address output has no internal consumers.
    pattern = r'^\s*phy_a(?:\[10\])? <= [^;]+;[^\n]*\n'
    removed = re.findall(pattern, source, re.M)
    assert len(removed) == 22, len(removed)
    candidate = re.sub(pattern, '\n', source, flags=re.M)
    assert len(re.findall(r'\bphy_a\b', candidate)) == 1
    address = '''
// Decode address sources independently from command-valid and delay counters.
// Only address-bearing commands consume these pins; NOP/AUTOREF ignore them.
// PRECHARGE consumes only A10. Its bank selection remains unchanged.
always @(posedge controller_clk) begin
    case (state)
        ST_BOOT_0, ST_IDLE: phy_a <= 13'h400;
        ST_BOOT_3: phy_a <= 13'b000000_011_0_001;
        ST_BOOT_4: phy_a <= 13'b00000_010_00_000;
        ST_WRITE_0, ST_WRITE_4_NR_PRECHG, ST_READ_0, ST_BURSTWR_0:
            phy_a <= addr[22:10];
        ST_WRITE_2, ST_READ_2, ST_BURSTWR_3:
            phy_a <= {3'd0, addr[9:0]};
        ST_WRITE_4_NEWROW:
            phy_a <= {2'd0, ((BANK_ROW_TRACK != 0) &&
                            (nr_prechg_bank != addr[24:23])), 10'd0};
        ST_READ_6:
            phy_a <= {2'd0, ((BANK_ROW_TRACK != 0) &&
                            (phy_ba != addr[24:23])), 10'd0};
        default: phy_a <= 13'd0;
    endcase
end

'''
    assert candidate.count('endmodule') == 1
    candidate = candidate.replace('endmodule', address + 'endmodule')
    (OUT / 'io_sdram.v').write_text(candidate)
    (OUT / 'baseline.v').write_text(source)
    shutil.copy2(__file__, OUT / 'recipe.py')
    save(OUT / 'prepared.json', dict(parent_sha256=sha(BASE / REL),
        candidate_sha256=sha(OUT / 'io_sdram.v'), removed_assignments=removed,
        state_bits_added=0, command_cycles_changed=False,
        ignored_pin_values_may_change=True))


def twin(source, name):
    source = source.replace('module io_sdram #(', 'module ' + name + ' #(')
    source = source.replace('inout   wire    [15:0]  phy_dq,',
        'input wire [15:0] phy_dq_in,\noutput wire [15:0] phy_dq_out_port,\noutput wire phy_dq_oe_port,')
    source = source.replace("assign          phy_dq = phy_dq_oe ? phy_dq_out : 16'bZZZZZZZZZZZZZZZZ;",
        'assign phy_dq_out_port = phy_dq_out;\nassign phy_dq_oe_port = phy_dq_oe;')
    return re.sub(r'\bphy_dq\b', 'phy_dq_in', source)


def test(spread=False):
    work = OUT / ('bank-test' if spread else 'test')
    work.mkdir(exist_ok=False)
    shutil.copy2(__file__, work / 'recipe.py')
    candidate = twin((OUT / 'io_sdram.v').read_text(), 'io_sdram_candidate')
    reference = twin((OUT / 'baseline.v').read_text(), 'io_sdram_reference')
    header = candidate.split(');', 1)[0] + ');\n'
    header = header.replace('module io_sdram_candidate #(', 'module io_sdram #(')
    header = re.sub(r'output\s+reg\b', 'output wire', header)
    ports = re.findall(r'^(input|output)\s+(?:wire|reg)\s*(\[[^\]]+\])?\s*(\w+)', header, re.M)
    assert len(ports) == 46, len(ports)
    outputs = [(width, name) for direction, width, name in ports if direction == 'output']
    wrapper = header + '\n'.join(f'wire {width} ref_{name};' for width, name in outputs) + '\n'
    params = '#(.BANK_ROW_TRACK(BANK_ROW_TRACK), .REFRESH_INTERVAL(REFRESH_INTERVAL), .FAST_166(FAST_166))'
    for mod, inst in [('candidate', 'dut'), ('reference', 'ref_dut')]:
        connections = [f'.{name}({("ref_" if mod == "reference" and direction == "output" else "")}{name})'
                       for direction, width, name in ports]
        wrapper += f'io_sdram_{mod} {params} {inst} (\n' + ',\n'.join(connections) + '\n);\n'
    wrapper += 'integer checked_cycles = 0;\ninteger banks_seen = 0;\ninteger commands [0:7];\ninteger init_i;\ninitial for(init_i=0;init_i<8;init_i=init_i+1) commands[init_i]=0;\n'
    wrapper += 'always @(negedge controller_clk) if (reset_n) begin\nchecked_cycles=checked_cycles+1;\n'
    for width, name in outputs:
        if name != 'phy_a':
            wrapper += f'if ({name} !== ref_{name}) $fatal(1, "SDRAM miter {name}");\n'
    wrapper += '''
if (phy_cke) begin
    commands[{phy_ras,phy_cas,phy_we}]=commands[{phy_ras,phy_cas,phy_we}]+1;
    if ({phy_ras,phy_cas,phy_we} == 3'b011) banks_seen=banks_seen | (1 << phy_ba);
    case ({phy_ras,phy_cas,phy_we})
        3'b000, 3'b011, 3'b100, 3'b101:
            if (phy_a !== ref_phy_a) $fatal(1, "SDRAM address-bearing command mismatch");
        3'b010:
            if (phy_a[10] !== ref_phy_a[10]) $fatal(1, "SDRAM PRECHARGE A10 mismatch");
        default: begin end
    endcase
end
end
final begin
    $display("SDRAM_MITER cycles=%0d banks=%0d LMR=%0d REFRESH=%0d PRECHARGE=%0d ACT=%0d WRITE=%0d READ=%0d", checked_cycles, banks_seen, commands[0], commands[1], commands[2], commands[3], commands[4], commands[5]);
end
endmodule
'''
    (work / 'io_sdram_miter.v').write_text(wrapper + candidate + reference)
    testroot = ROOT / 'src/fpga/test'
    source_names = ['tb_sdram_cont.v', 'tb_sdram_cont_main.cpp', 'sdram_model_full.v']
    for n in source_names:
        shutil.copy2(testroot / n, work / n)
    driver = work / 'tb_sdram_cont_main.cpp'
    driver_text = driver.read_text()
    assert driver_text.count('    delete tb;') == 1
    if spread:
        for old, new in [('0x00200000', '0x00ffc000'), ('0x00800000', '0x01000000'),
                         ('0x00C00000', '0x02000000'), ('0x00D00000', '0x03000000'),
                         ('0x00E00000', '0x03800000')]:
            assert driver_text.count(old) == 1, old
            driver_text = driver_text.replace(old, new)
    driver.write_text(driver_text.replace('    delete tb;', '    tb->final();\n    delete tb;'))
    for n in ['axi_sdram_arbiter.v', 'axi_sdram_slave.v', 'sync_fifo.v']:
        shutil.copy2(BASE / 'src/fpga/common' / n, work / n)
        source_names.append(n)
    shutil.copy2(ROOT / 'src/fpga/targets/mister/synch_3.v', work / 'synch_3.v')
    source_names += ['synch_3.v', 'io_sdram_miter.v']
    results = {}
    for bank in [1, 0]:
        cmd = ['verilator', '--cc', '--exe', '--build', '-j', '4', '-Wno-fatal', '-Wno-BADVLTPRAGMA',
               '--public-flat-rw', '--top-module', 'tb_sdram_cont',
               '--Mdir', str(work / f'obj{bank}'), f'-GBANK_ROW_TRACK={bank}',
               '-CFLAGS', '-std=c++17 -O2', *[str(work / n) for n in source_names]]
        with (work / f'build{bank}.log').open('x') as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
        with (work / f'run{bank}.log').open('x') as f:
            subprocess.run([str(work / f'obj{bank}/Vtb_sdram_cont')], stdout=f, stderr=subprocess.STDOUT, check=True, timeout=300)
        text = (work / f'run{bank}.log').read_text()
        assert 'RESULT: PASS' in text
        summary = re.search(r'^SDRAM_MITER (.+)$', text, re.M)
        assert summary
        results[str(bank)] = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', summary[1])}
        assert all(results[str(bank)].values()), results[str(bank)]
        if spread:
            assert results[str(bank)]['banks'] == 15, results[str(bank)]
        print(bank, results[str(bank)], flush=True)
    result_dir = work if spread else OUT
    save(result_dir / 'result.json', dict(passed=True, cases=results,
         qualification='Exact cycle/output miter except unused SDRAM address pins; actual chip and mixed-master/scanout drivers.'))
    files = [p for p in result_dir.rglob('*') if p.is_file() and not any(x.startswith('obj') for x in p.relative_to(result_dir).parts)]
    save(result_dir / 'manifest.json', {str(p.relative_to(result_dir)): sha(p) for p in sorted(files)})


if __name__ == '__main__':
    {'prepare': prepare, 'test': test, 'banks': lambda: test(True)}[sys.argv[1]]()
