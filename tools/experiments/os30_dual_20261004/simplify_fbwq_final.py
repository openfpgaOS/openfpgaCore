#!/usr/bin/env python3
"""Prove direct framebuffer-queue chain finality without length encoding."""
from pathlib import Path
import difflib
import hashlib
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'gpu-ring-data-v1/gpu_core.v'
OUT = ART / 'gpu-chain-final-v1'
SHA = 'c1d606620c3161ad6d6c3f3a649ea5dcdf84ceec8d47044381122d0c5c6d3a13'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert digest(BASE) == SHA
    original = BASE.read_text()
    old = """wire fbwq_head_chain_final = (fbwq_start_burst_words == 4'd8)
                          || (fbwq_count > {1'b0, fbwq_start_burst_words});"""
    new = """// Finality needs no encoded length: eight queued words always cap the
// run; otherwise an existing break before the queue tail fixes its length.
// Test the possible breaks in parallel, preserving the exact issue cycle.
wire fbwq_head_chain_final = (fbwq_count >= 5'd8)
                          || ((fbwq_count >= 5'd2) && !fbwq_link_next[fbwq_rd_ptr])
                          || ((fbwq_count >= 5'd3) && !fbwq_link_next[fbwq_rd_ptr_1])
                          || ((fbwq_count >= 5'd4) && !fbwq_link_next[fbwq_rd_ptr_2])
                          || ((fbwq_count >= 5'd5) && !fbwq_link_next[fbwq_rd_ptr_3])
                          || ((fbwq_count >= 5'd6) && !fbwq_link_next[fbwq_rd_ptr_4])
                          || ((fbwq_count >= 5'd7) && !fbwq_link_next[fbwq_rd_ptr_5]);"""
    assert original.count(old) == 1
    candidate = original.replace(old, new, 1)
    assert candidate.replace(new, old, 1) == original
    # Pin the actual widths: include unused count encodings, every vector,
    # and every circular starting point rather than relying on reachability.
    for decl in ['reg [15:0] fbwq_link_next;', 'reg [3:0]  fbwq_rd_ptr;', 'reg [4:0]  fbwq_count;']:
        # Whitespace in declarations is immaterial; identifier/type/width is not.
        pattern = re.escape(decl).replace(r'\ ', r'\s+')
        assert re.search(pattern, original), decl
    a = original.index('wire [3:0] fbwq_rd_ptr_1 =')
    b = original.index('// ----------------------------------------------------------------\n// AW-ahead overlap:', a)
    cone = original[a:b]
    assert 'wire [3:0] fbwq_start_burst_words =' in cone
    design = ''
    for label, expression in [('reference', old), ('candidate', new)]:
        design += f'''module {label}(input [3:0] fbwq_rd_ptr,
input [4:0] fbwq_count,input [15:0] fbwq_link_next,
output finality,output [3:0] words);
{cone}{expression}
assign finality=fbwq_head_chain_final;
assign words=fbwq_start_burst_words;
endmodule
'''
    design += '''module chain_proof(input [3:0] ptr,input [4:0] count,
input [15:0] links,output old_final,new_final,output [3:0] old_words,new_words);
reference a(ptr,count,links,old_final,old_words);
candidate b(ptr,count,links,new_final,new_words);
endmodule
'''
    cpp = '''#include "Vchain_proof.h"
#include <cstdio>
int main() {
 Vchain_proof dut;
 for(unsigned ptr=0;ptr<16;++ptr) for(unsigned count=0;count<32;++count)
  for(unsigned links=0;links<65536;++links) {
   dut.ptr=ptr;dut.count=count;dut.links=links;dut.eval();
   unsigned words=1;
   while(words<8 && count>words && (links & (1u<<((ptr+words-1)&15)))) ++words;
   bool expected=words==8 || count>words;
   if(dut.old_final!=dut.new_final || dut.old_final!=expected ||
      dut.old_words!=dut.new_words || dut.old_words!=words) {
    std::printf("FAIL ptr=%u count=%u links=%x old=%u new=%u expected=%u words=%u/%u/%u\\n",
     ptr,count,links,dut.old_final,dut.new_final,expected,dut.old_words,dut.new_words,words);
    return 1;
   }
  }
 std::puts("PASS all33,554,432 pointer/count/link combinations: actual original/new finality and independent queue-prefix oracle agree");
}
'''
    OUT.mkdir(exist_ok=False)
    (OUT / 'chain_proof.v').write_text(design)
    (OUT / 'chain_proof.cpp').write_text(cpp)
    with (OUT / 'build.log').open('w') as log:
        subprocess.run(['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal',
                        '--top-module', 'chain_proof', '--Mdir', str(OUT / 'obj'),
                        '-CFLAGS', '-std=c++17 -O2', str(OUT / 'chain_proof.v'),
                        str(OUT / 'chain_proof.cpp')], check=True, stdout=log, stderr=subprocess.STDOUT)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (OUT / 'build.log').read_text())
    with (OUT / 'proof.log').open('w') as log:
        subprocess.run([str(OUT / 'obj/Vchain_proof')], check=True, stdout=log, stderr=subprocess.STDOUT)
    (OUT / 'gpu_core.v').write_text(candidate)
    (OUT / 'chain-final.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True), fromfile='a/gpu_core.v', tofile='b/gpu_core.v')))
    shutil.copy2(__file__, OUT / 'simplify_fbwq_final.py')
    manifest = dict(reference=str(BASE.relative_to(ROOT)), reference_sha256=SHA,
                    candidate_sha256=digest(OUT / 'gpu_core.v'),
                    exact_scope='Only fbwq_head_chain_final combinational predicate.',
                    proof_inputs=33554432, all_pointer_positions=16, all_count_encodings=32,
                    all_link_vectors=65536, sequential_invariants_required=False,
                    independent_prefix_scan_oracle=True,
                    burst_length_and_all_channel_valid_enable_data_source_unchanged=True,
                    all_other_source_bytes_exactly_unchanged=True,
                    added_registers=0, added_cycles=0,
                    source_files={name:digest(OUT / name) for name in ['chain_proof.v', 'chain_proof.cpp', 'simplify_fbwq_final.py']},
                    whole_gpu_qualification='Pending retained rendering validation.',
                    physical_benefit='Requires full-core fit.')
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print((OUT / 'proof.log').read_text(), end='')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
