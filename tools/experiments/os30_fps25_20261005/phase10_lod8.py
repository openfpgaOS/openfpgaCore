#!/usr/bin/env python3
"""Private LOD8 persistent canonical36 alias; never modifies a parent or production."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'compact-lod5-core-v5/frozen'
OLD = P / 'lod5-components-v1/frozen'
OUT = P / 'lod8-canonical-alias-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def replace(text, old, new):
    assert text.count(old) == 1, (old[:90], text.count(old))
    return text.replace(old, new)


def save(path, value):
    with path.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def prepare():
    f = OUT / 'frozen'
    f.mkdir(parents=True, exist_ok=False)
    for n in json.loads((P / 'lod5-components-v1/sources.json').read_text()):
        src = BASE / 'src/fpga/common' / n if n.endswith('.v') and n != 'tb_load_alias.v' else OLD / n
        shutil.copy2(src, f / n)
    wrapper = f / 'phase10_load_alias.v'
    s = wrapper.read_text().replace('Private LOD5:', 'Private LOD8:')
    s = s.replace('// read-only, cacheable 32-byte raw view into its ordinary LoadedVertex slots.\n// All hardware jobs use dest0. No result DMA, persistent ownership or counters.',
                  '// read-only canonical36 view retains each successfully loaded slot.\n// Partial LOADs invalidate only their destination range; no result DMA/copy.')
    s = replace(s, 'output wire view_block,output reg view_valid,\n output reg [5:0] view_count',
                'output wire view_block,output wire [31:0] view_valid_slots')
    s = replace(s, 'reg [5:0] count;', 'reg [5:0] count;\n reg [4:0] destination;')
    s = replace(s, '.count(count),.dest(5\'d0)', '.count(count),.dest(destination)')
    s = replace(s, '.cycles(),.valid_slots(),.compute_cycles()', '.cycles(),.valid_slots(view_valid_slots),.compute_cycles()')
    s = replace(s, "32'h4c4f4405", "32'h4c4f4408")
    s = replace(s, '   default:reg_rdata=0;', "   10'h00d:reg_rdata=view_valid_slots;\n   default:reg_rdata=0;")
    s = replace(s, 'cz<=0;count<=0;', 'cz<=0;count<=0;destination<=0;')
    s = replace(s, '   view_valid<=0;view_count<=0;\n', '')
    s = replace(s, '    view_valid<=!unsupported && dma_error==0;view_count<=count;\n', '')
    s = replace(s, "10'h004:count<=reg_wdata[5:0];", "10'h004:begin count<=reg_wdata[5:0];destination<=reg_wdata[12:8];end")
    wrapper.write_text(s)

    alias = f / 'phase10_bram_alias.v'
    s = alias.read_text().replace('Read-only CPU cacheline view of a native raw LOAD result.',
        'LOD8 read-only dense canonical36 view of32 persistent native LOAD slots.')
    s = replace(s, 'input wire view_valid,input wire [5:0] view_count', 'input wire [31:0] view_valid_slots')
    s = s.replace("32'hbffffc00", "32'hbffff800")
    s = replace(s, 'reg active,read_error,held_valid,read_pending,read_zero;\n reg [5:0] held_count;\n reg [7:0] word_address;',
        'reg active,read_error,read_pending,read_zero;\n reg [31:0] held_valid_slots;\n reg [5:0] vertex;\n reg [3:0] field,read_field;')
    s = replace(s, "wire [9:0] end_word={2'b0,s_araddr[9:2]}+{2'b0,s_arlen}+10'd1;", '''wire [9:0] end_word={1'b0,s_araddr[10:2]}+{2'b0,s_arlen}+10'd1;
 // For every9-bit index0..511, floor(index*57/512)==floor(index/9).
 // Decode once on AR; all subsequent beats advance the field/slot counters.
 wire [14:0] start_product={6'b0,s_araddr[10:2]}*15'd57;
 wire [5:0] start_vertex=start_product[14:9];
 wire [9:0] start_field={1'b0,s_araddr[10:2]}-({4'b0,start_vertex}*10'd9);
 wire [2:0] raw_row=field<5?field[2:0]:field<7?3'd5:field==7?3'd6:3'd7;''')
    s = replace(s, 'assign view_read_addr={word_address[2:0],word_address[7:3]};',
                'assign view_read_addr={raw_row,vertex[4:0]};')
    converter = (P / 'loaded-vertex-writer-v2/geometry_loaded_vertex_writer.v').read_text()
    converter = converter[converter.index(' function [31:0] signed_short_float;'):converter.index(' always @(posedge clk)begin')]
    converter = converter.replace('raw_read_data', 'view_read_data')
    s = replace(s, ' always @(posedge clk)begin\n  if(reset||cpu_reset)', converter + ' always @(posedge clk)begin\n  if(reset||cpu_reset)')
    s = replace(s, 'active<=0;read_error<=0;held_valid<=0;held_count<=0;word_address<=0;',
        'active<=0;read_error<=0;held_valid_slots<=0;vertex<=0;field<=0;read_field<=0;')
    s = replace(s, 'read_zero<=read_error || !held_valid || {1\'b0,word_address[7:3]}>=held_count;\n    word_address<=word_address+1\'b1;issued<=issued+1\'b1;',
        "read_zero<=read_error || vertex>=32 || !held_valid_slots[vertex[4:0]];\n    read_field<=field;issued<=issued+1'b1;\n    if(field==8)begin field<=0;vertex<=vertex+1'b1;end\n    else field<=field+1'b1;")
    s = s.replace("read_zero?32'b0:view_read_data", "read_zero?32'b0:loaded_word")
    s = replace(s, 'end_word>256', 'end_word>512')
    s = replace(s, 'held_valid<=view_valid;held_count<=view_count;word_address<=s_araddr[9:2];',
                'held_valid_slots<=view_valid_slots;vertex<=start_vertex;field<=start_field[3:0];')
    # Assertions are observation only; no production gates depend on them.
    s = s.replace('\nendmodule', '''
`ifdef LOD8_ALIAS_CHECKS
 always @(posedge clk) if(!reset && !cpu_reset) begin
  assert(reserved<=2);
  if(active)begin assert(field<=8);assert(issued<=words);assert(sent<=issued);end
  if(take_ar)begin assert(start_vertex==s_araddr[10:2]/9);assert(start_field==s_araddr[10:2]%9);end
 end
`endif
endmodule''')
    alias.write_text(s)
    tb = f / 'tb_load_alias.v'
    s = tb.read_text().replace('wire view_active,view_block,view_valid;wire[5:0]view_count;',
                              'wire view_active,view_block;wire[31:0]view_valid_slots;')
    s = s.replace('.view_valid(view_valid),.view_count(view_count)', '.view_valid_slots(view_valid_slots)')
    tb.write_text(s)
    top = (BASE / 'src/fpga/targets/pocket/core_top.v').read_text()
    top = replace(top, 'wire geometry_view_active,geometry_view_block,geometry_view_valid;\n    wire [5:0] geometry_view_count;',
                  'wire geometry_view_active,geometry_view_block;\n    wire [31:0] geometry_view_valid_slots;')
    assert top.count('.view_valid(geometry_view_valid),.view_count(geometry_view_count)') == 2
    top = top.replace('.view_valid(geometry_view_valid),.view_count(geometry_view_count)', '.view_valid_slots(geometry_view_valid_slots)')
    (OUT / 'core_top.v').write_text(top)
    shutil.copyfile(Path(__file__).with_name('phase10_lod8_test.cpp'), f / 'phase10_load_alias_test.cpp')
    shutil.copyfile(__file__, OUT / 'prepare-recipe.py')
    save(OUT / 'prepared.json', dict(private_only=True, parent_prepared_sha256=sha(P / 'compact-lod5-core-v5/prepared.json'),
          id='0x4c4f4408', source_stride=24, alias_stride=36, alias_bytes=2048,
          validmask_read_offset=52, count_bits='5:0', destination_bits='12:8',
          actual_hardware_unchanged_except=['phase10_load_alias.v', 'phase10_bram_alias.v', 'core_top view wiring'],
          partial_failure='Only touched valid job slots invalidated; invalid count/dest changes none; unrelated slots always retained.',
          output_view='32 slots;9 words XYZWinv,Ufloat,Vfloat,RGBA,clip. Invalid slots and unused aperture words return0 withOKAY.',
          burst_error='Unaligned or end past2KiB returns all0 SLVERR; alias writes drain and returnSLVERR.',
          source_sha256={x.name:sha(x) for x in f.iterdir() if x.is_file()}, core_top_sha256=sha(OUT / 'core_top.v')))
    print(OUT)


def build():
    f, d = OUT / 'frozen', OUT / 'test'
    d.mkdir(exist_ok=False)
    cmd = ['verilator','--cc','--exe','--build','--assert','-j','4','-Wno-fatal','-Wno-WIDTH',
           '+define+LOD8_ALIAS_CHECKS','+define+GEOMETRY_SCAN_CHECKS','--top-module','tb_load_alias',
           '--Mdir',str(d/'obj'),'-CFLAGS','-O2 -std=c++17']
    cmd += [str(x) for x in sorted(f.glob('*.v'))] + [str(f/'phase10_load_alias_test.cpp')]
    save(d/'build-command.json',cmd)
    with (d/'build.log').open('x') as log:
        subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)


def run():
    f, d = OUT / 'frozen', OUT / 'test'
    with (d/'run.log').open('x') as log:
        subprocess.run([str(d/'obj/Vtb_load_alias'),str(f/'native.bin')],stdout=log,stderr=subprocess.STDOUT,check=True)
    r = json.loads((d/'run.log').read_text().splitlines()[-1]);assert r['passed']
    save(d/'result.json',r);print(r)


if __name__ == '__main__':
    globals()[sys.argv[1]]()
