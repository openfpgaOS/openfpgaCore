#!/usr/bin/env python3
"""Remove the full-ring comparator from enabled ring RAM data selection."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import difflib

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'fbwq-single-read-v1/gpu_core.v'
OUT = ART / 'gpu-ring-data-v1'
SHA = '4ab7ecda7d6ab8c751a89c629d978e406ba0fbdda380e23dd90004f6d8c27544'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    src = BASE.read_text()
    assert digest(BASE) == SHA
    old = '''always @(posedge clk) begin
    if (dma_ring_wr_raw || cpu_ring_write)
        ring_bram[ring_wr_addr] <= cpu_ring_write ? reg_wdata : dma_ring_wdata_raw;
end'''
    new = old.replace('cpu_ring_write ? reg_wdata : dma_ring_wdata_raw',
                      'dma_ring_wr_raw ? dma_ring_wdata_raw : reg_wdata')
    assert src.count(old) == 1
    candidate = src.replace(old, new, 1)
    assert candidate.replace(new, old, 1) == src
    # Copy the exact actual control equations. req/full are unrestricted
    # cutpoints: all their possible values are checked, rather than assuming
    # software-selected modes or any sequential state invariant.
    lines = [
        "localparam DMA_S_IDLE    = 2'd0;",
        "localparam DMA_S_R       = 2'd2;",
        "wire       dma_desc_empty = (dma_desc_count == 2'd0);",
        'wire       dma_pull_busy = (dma_state != DMA_S_IDLE) || !dma_desc_empty;',
        'wire       dma_busy = dma_pull_busy;',
        'wire cpu_ring_write = cpu_ring_req && !cpu_ring_full && !dma_busy;',
        'assign dma_ring_wr_raw    = (dma_state == DMA_S_R) && m_rd_rvalid;',
        'assign dma_ring_wdata_raw = m_rd_rdata;',
    ]
    for line in lines:
        assert src.count(line) == 1, line
    assert len(re.findall(r'ring_bram\[[^\n]+\]\s*<=', src)) == 1
    assert len(re.findall(r'assign\s+dma_ring_wr_raw\s*=', src)) == 1
    assert len(re.findall(r'wire\s+dma_busy\s*=', src)) == 1
    old_rhs = re.search(r'<= ([^;]+);', old)[1]
    new_rhs = re.search(r'<= ([^;]+);', new)[1]
    tb = '''module tb;
reg [1:0] dma_state, dma_desc_count;
reg m_rd_rvalid, cpu_ring_req, cpu_ring_full;
reg [31:0] reg_wdata, m_rd_rdata;
wire dma_ring_wr_raw;
wire [31:0] dma_ring_wdata_raw;
'''
    tb += '\n'.join(lines) + '\n'
    tb += f'wire [31:0] old_data = {old_rhs};\nwire [31:0] new_data = {new_rhs};\n'
    tb += '''initial begin
  for(integer control=0;control<128;control++) begin
    {dma_state,dma_desc_count,m_rd_rvalid,cpu_ring_req,cpu_ring_full}=7'(control);
    for(integer bitpos=0;bitpos<32;bitpos++) begin
      for(integer pair=0;pair<4;pair++) begin
        reg_wdata=pair[0] ? (32'h1<<bitpos) : 32'h0;
        m_rd_rdata=pair[1] ? (32'h1<<bitpos) : 32'h0;
        #1;
        if(dma_ring_wr_raw && !dma_busy) $fatal(1,"DMA write without busy");
        if(cpu_ring_write && dma_busy) $fatal(1,"CPU write while busy");
        if(dma_ring_wr_raw && cpu_ring_write) $fatal(1,"Writer overlap");
        if((dma_ring_wr_raw || cpu_ring_write) && old_data !== new_data)
          $fatal(1,"Enabled data mismatch control=%x bit=%d pair=%x",control,bitpos,pair);
      end
    end
  end
  $display("PASS: actual DMA/CPU control equations, all128 control states x4 data-bit values x32 positions=16384 cases; enabled write data exact");
  $finish;
end
endmodule
'''
    OUT.mkdir(exist_ok=False)
    (OUT / 'tb.sv').write_text(tb)
    with (OUT / 'build.log').open('w') as log:
        subprocess.run(['verilator', '--binary', '--timing', '--top-module', 'tb',
                        '-Wno-fatal', '--Mdir', str(OUT / 'obj'), '-j', '2',
                        str(OUT / 'tb.sv')], check=True, stdout=log, stderr=subprocess.STDOUT)
    warnings = (OUT / 'build.log').read_text()
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', warnings)
    with (OUT / 'proof.log').open('w') as log:
        subprocess.run([str(OUT / 'obj/Vtb')], check=True, stdout=log, stderr=subprocess.STDOUT)
    (OUT / 'gpu_core.v').write_text(candidate)
    shutil.copy2(__file__, OUT / 'simplify_ring_data.py')
    (OUT / 'ring-data.patch').write_text(''.join(difflib.unified_diff(
        src.splitlines(True), candidate.splitlines(True), fromfile='a/gpu_core.v', tofile='b/gpu_core.v')))
    manifest = dict(reference=str(BASE.relative_to(ROOT)), reference_sha256=SHA,
                    candidate_sha256=digest(OUT / 'gpu_core.v'), script_sha256=digest(Path(__file__)),
                    actual_guarded_control_equations=lines,
                    unchanged_write_enable='dma_ring_wr_raw || cpu_ring_write',
                    unchanged_write_address='ring_wr_addr',
                    actual_dma_write_implies_busy=True, actual_cpu_write_implies_not_busy=True,
                    enabled_data_equivalent_all_controls=True,
                    independent_cutpoints=['cpu_ring_req', 'cpu_ring_full'],
                    exhaustive_control_states=128, data_bit_pairs=4, output_positions=32,
                    bitwise_proof_extends_to_arbitrary_words=True,
                    all_other_source_bytes_exactly_unchanged=True,
                    added_state_bits=0, added_cycles=0,
                    whole_gpu_validation='pending independent retained rendering',
                    physical_benefit='requires full fit')
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print((OUT / 'proof.log').read_text(), end='')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
