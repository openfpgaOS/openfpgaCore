#!/usr/bin/env python3
"""Check buffered SDRAM reads tolerate undefined same-address read/write data.

Stages the existing buffered-writeback regression with a poisoned collision
read, and asserts each consumed head has the current pointer and no collision.
This tests the schedule; actual Quartus RAM placement is checked separately.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from common import FIXTURES, new_output, verify_fixtures
ROOT = FIXTURES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bank-row-track', type=int, choices=[0, 1], default=1)
    args = parser.parse_args()
    verify_fixtures()
    out = new_output(args.output)
    frozen = out / 'sources'
    frozen.mkdir()
    originals = [FIXTURES / name for name in
                 ['tb_sdram.v', 'sdram_model.v', 'tb_sdram_main.cpp',
                  'sync_fifo.v', 'axi_sdram_slave.v', 'common.v', 'io_sdram.v']]
    for source in originals:
        shutil.copy2(source, frozen / source.name)
    io = (frozen / 'io_sdram.v').read_text()
    replacements = [
        ('inout   wire    [15:0]  phy_dq,',
         'input wire [15:0] phy_dq_in,\noutput wire [15:0] phy_dq_out_port,\noutput wire phy_dq_oe_port,'),
        ("assign          phy_dq = phy_dq_oe ? phy_dq_out : 16'bZZZZZZZZZZZZZZZZ;",
         'assign phy_dq_out_port = phy_dq_out;\nassign phy_dq_oe_port = phy_dq_oe;'),
        ('phy_dq_neg <= phy_dq;', 'phy_dq_neg <= phy_dq_in;')]
    for old, new in replacements:
        assert io.count(old) == 1, old
        io = io.replace(old, new)
    (frozen / 'io_sdram_test.v').write_text(io)
    slave = frozen / 'axi_sdram_slave.v'
    text = slave.read_text()
    old = '    wbuf_head <= wbuf[wbuf_rptr];'
    assert text.count(old) == 1
    text = text.replace(old, '''    // Deliberately poison collision data: it must never be consumed.
    wbuf_head <= (wbuf_we && wbuf_wptr == wbuf_rptr)
                 ? 36'hadeadbeef : wbuf[wbuf_rptr];''')
    audit = '''
reg audit_collision;
reg [3:0] audit_head_pointer;
integer audit_collisions = 0;
integer audit_consumed = 0;
always @(posedge clk) begin
    audit_collision <= wbuf_we && wbuf_wptr == wbuf_rptr;
    audit_head_pointer <= wbuf_rptr;
    if (!reset) begin
        if (wbuf_we && wbuf_wptr == wbuf_rptr)
            audit_collisions <= audit_collisions + 1;
        if (state == S_WR_BURST && wbuf_active && sdram_wr_data_next
            && !(started && (wr_op_done_seen || sdram_wr_done))) begin
            if (audit_collision) $fatal(1, "Consumed collision read");
            if (audit_head_pointer != wbuf_rptr) $fatal(1, "Consumed stale RAM pointer");
            if (wbuf_we) $fatal(1, "Fill overlaps buffered drain");
            audit_consumed <= audit_consumed + 1;
        end
    end
end
final $display("WBUF_AUDIT collisions=%0d consumed=%0d", audit_collisions, audit_consumed);
'''
    assert text.count('endmodule') == 1
    slave.write_text(text.replace('endmodule', audit + '\nendmodule'))
    harness = frozen / 'tb_sdram_main.cpp'
    text = harness.read_text()
    assert text.count('    delete tb;') == 1
    harness.write_text(text.replace('    delete tb;', '    tb->final();\n    delete tb;'))
    manifest = dict(originals={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in originals},
                    staged={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in frozen.iterdir()}, collision_value='adeadbeef',
                    scope='Behavioral buffered-SDRAM schedule audit, not primitive-level RAM simulation')
    sources = ['tb_sdram.v', 'sdram_model.v', 'sync_fifo.v', 'axi_sdram_slave.v',
               'io_sdram_test.v', 'common.v', 'tb_sdram_main.cpp']
    command = ['verilator', '--cc', '--exe', '--build', '--trace', '-j', '2',
               '-Wall', '-Wno-fatal', '-Wno-BADVLTPRAGMA', '--top-module', 'tb_sdram',
               '-GWCONT_TIE=0', '-GBANK_ROW_TRACK=' + str(args.bank_row_track), '--Mdir', str(out / 'obj'), '-CFLAGS', '-std=c++17 -O2',
               *[str(frozen / name) for name in sources]]
    manifest['build_command'] = command
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (out / 'build.log').open('w') as log:
        subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT, check=True)
    with (out / 'run.log').open('w') as log:
        run = subprocess.run([str(out / 'obj/Vtb_sdram')], cwd=out,
                             stdout=log, stderr=subprocess.STDOUT)
    data = (out / 'run.log').read_text()
    checks = re.search(r'=== Results: (\d+) passed, (\d+) failed ===', data)
    audit = re.search(r'WBUF_AUDIT collisions=(\d+) consumed=(\d+)', data)
    result = dict(bank_row_track=args.bank_row_track, exit_code=run.returncode,
                  passed=int(checks[1]) if checks else None,
                  failed=int(checks[2]) if checks else None,
                  poisoned_collisions=int(audit[1]) if audit else None,
                  checked_consumptions=int(audit[2]) if audit else None)
    result['passed_check'] = (result['passed'] == 491 and result['failed'] == 0 and
                              result['poisoned_collisions'] == 32 and result['checked_consumptions'] == 335)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['passed_check'] or run.returncode or not checks or result['failed'] or not audit or not all(
            result[k] for k in ('poisoned_collisions', 'checked_consumptions')):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
