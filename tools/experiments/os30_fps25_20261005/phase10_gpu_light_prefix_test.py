#!/usr/bin/env python3
"""Exhaust literal old/new color functions, including all independent channels."""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
O = P / 'gpu-light-prefix-v1'
D = O / 'literal'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def put(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


def function(source, name):
    found = re.findall(r'function \[[^\]]+\] ' + name + r';.*?endfunction', source, re.S)
    assert len(found) == 1, name
    return found[0]


def main():
    prepared = json.loads((O / 'prepared.json').read_text())
    assert sha(O / 'gpu_core.v') == prepared['source_sha256']
    assert sha(O / 'baseline.v') == prepared['parent_sha256']
    old, new = (O / 'baseline.v').read_text(), (O / 'gpu_core.v').read_text()
    D.mkdir(exist_ok=False)
    shutil.copy2(__file__, D / 'recipe.py')
    funcs = [function(new, 'rgb565_light_scale')]
    for name in ('rgb565_modulate', 'rgb565_gouraud'):
        funcs.append(function(old, name).replace(name, 'old_' + name))
        funcs.append(function(new, name).replace(name, 'new_' + name))
    sv = '''module probe(input [15:0] texel,input [5:0] light,input [4:0] r,b,
 output [6:0] increment,output[15:0] old_mod,new_mod,old_gour,new_gour);
''' + '\n'.join(funcs) + '''
assign increment=rgb565_light_scale(light);
assign old_mod=old_rgb565_modulate(texel,light);
assign new_mod=new_rgb565_modulate(texel,light);
assign old_gour=old_rgb565_gouraud(texel,r,light,b);
assign new_gour=new_rgb565_gouraud(texel,r,light,b);
endmodule
'''
    (D / 'probe.v').write_text(sv)
    cpp = r'''#include "Vprobe.h"
#include <cstdio>
#include <cstdint>
int main(){
 Vprobe t; uint64_t checks=0;
 for(unsigned l=0;l<64;l++) {
  t.light=l; t.texel=0; t.r=0; t.b=0; t.eval();
  if(t.increment!=l+1){std::fprintf(stderr,"increment mismatch %u\n",l);return 1;}
 }
 // Full modulate domain. Gouraud is separable: red/blue have only their
 // own texel/channel inputs, and green only texel.green/light. Across this
 // traversal every red 32x32, green64x64 and blue32x32 pair is exercised,
 // with all remaining texel channels varying as well.
 for(unsigned x=0;x<65536;x++) for(unsigned l=0;l<64;l++) {
  t.texel=x;t.light=l;t.r=l&31;t.b=l>>1;t.eval();
  unsigned mr=(((x>>11)&31)*(l+1))>>6;
  unsigned mg=(((x>>5)&63)*(l+1))>>6;
  unsigned mb=((x&31)*(l+1))>>6;
  unsigned gr=(((x>>11)&31)*((l&31)+1))>>5;
  unsigned gg=(((x>>5)&63)*(l+1))>>6;
  unsigned gb=((x&31)*((l>>1)+1))>>5;
  unsigned mod=(mr<<11)|(mg<<5)|mb;
  unsigned gour=(gr<<11)|(gg<<5)|gb;
  if(t.old_mod!=t.new_mod || t.new_mod!=mod ||
     t.old_gour!=t.new_gour || t.new_gour!=gour) {
   std::fprintf(stderr,"color mismatch texel=%u light=%u\n",x,l);return 2;
  }
  checks++;
 }
 std::printf("PASS increments=64 color_pairs=%llu full_modulate=4194304 gouraud_red_pairs=1024 gouraud_green_pairs=4096 gouraud_blue_pairs=1024\n",(unsigned long long)checks);
}
'''
    (D / 'main.cpp').write_text(cpp)
    cmd = ['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal',
           '--top-module', 'probe', '--Mdir', str(D / 'obj'), '-CFLAGS', '-std=c++17 -O2',
           str(D / 'probe.v'), str(D / 'main.cpp')]
    put(D / 'command.json', cmd)
    with (D / 'build.log').open('x') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
    with (D / 'run.log').open('x') as f:
        subprocess.run([str(D / 'obj/Vprobe')], stdout=f, stderr=subprocess.STDOUT, check=True)
    log = (D / 'run.log').read_text()
    assert 'PASS increments=64 color_pairs=4194304' in log
    put(D / 'result.json', dict(passed=True, literal_functions_extracted=True,
        increment_inputs=64, full_modulate_domain=65536 * 64,
        complete_gouraud_independent_channel_domains=dict(red=32 * 32, green=64 * 64, blue=32 * 32),
        independent_integer_oracle=True, coupled_rgb_vertex_domain_exhausted=False,
        source_sha256=sha(O / 'gpu_core.v'), parent_sha256=sha(O / 'baseline.v'),
        explanation='Gouraud source independently multiplies/truncates each channel. The traversal exhausts each channel Cartesian domain; it does not enumerate every joint RGB vertex tuple.',
        no_cycle_or_register_change=True, synthesis_or_timing_claim=False))
    print(log.strip())


if __name__ == '__main__':
    main()
