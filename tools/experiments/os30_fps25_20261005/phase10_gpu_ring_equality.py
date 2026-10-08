#!/usr/bin/env python3
"""Private parallel ring-pointer equality over the exact compact-v10 GPU."""
import difflib
import shutil
import subprocess
import sys
from pathlib import Path
from phase10_experiment import CAMP, sha, write

P = CAMP / 'phase10'
BASE = P / 'compact-lod5-core-v10/frozen/src/fpga/common/gpu_core.v'
OUT = P / 'gpu-ring-equality-v16'
OLD = 'wire ring_empty = (ring_rdptr == command_wrptr);'
NEW = '''// Four independent six-input equalities keep this late status/control
// comparison parallel. The ring remains 4096 words; no pointer/state changes.
(* keep *) wire [3:0] ring_pointer_equal_group;
assign ring_pointer_equal_group[0] = (ring_rdptr[2:0] == command_wrptr[2:0]);
assign ring_pointer_equal_group[1] = (ring_rdptr[5:3] == command_wrptr[5:3]);
assign ring_pointer_equal_group[2] = (ring_rdptr[8:6] == command_wrptr[8:6]);
assign ring_pointer_equal_group[3] = (ring_rdptr[11:9] == command_wrptr[11:9]);
wire ring_empty = &ring_pointer_equal_group;'''


def prepare():
    assert sha(BASE) == '3c171d36bce942735040d3cdc2d96a5f9959b46b2b5c4896455d1c4763ffa4d1'
    old = BASE.read_text()
    assert old.count(OLD) == 1 and 'localparam RING_ADDR_BITS = 12;' in old
    new = old.replace(OLD, NEW)
    OUT.mkdir(exist_ok=False)
    (OUT / 'gpu_core.v').write_text(new)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True),
        fromfile='compact-v10/gpu_core.v', tofile='gpu-ring-equality-v16/gpu_core.v')))
    shutil.copy2(__file__, OUT / 'recipe.py')
    write(OUT / 'prepared.json', dict(parent_sha256=sha(BASE),
        candidate_sha256=sha(OUT / 'gpu_core.v'), added_FF=0, added_states=0,
        added_cycles=0, only_changed_expression='ring_empty', ring_words=4096,
        comparison_groups=4, bits_per_group=3,
        parent_final_manifest_sha256=sha(P / 'compact-lod5-core-v10/final-manifest.json'),
        timing_manifest_sha256=sha(P / 'compact-timing-audit-v10/manifest.json'),
        deployment_ready=False))
    print(sha(OUT / 'gpu_core.v'))


def literal():
    d = OUT / 'literal'
    d.mkdir(exist_ok=False)
    text = (OUT / 'gpu_core.v').read_text()
    assert text.count(NEW) == 1 and BASE.read_text().count(OLD) == 1
    (d / 'tb.v').write_text('''module tb_equality(input[11:0]ring_rdptr,command_wrptr,
 output old_empty,new_empty,bad_empty);
''' + OLD.replace('ring_empty', 'old_empty_wire') + '\n' + NEW + '''
assign old_empty=old_empty_wire;
assign new_empty=ring_empty;
assign bad_empty=&ring_pointer_equal_group[2:0];
endmodule
''')
    (d / 'main.cpp').write_text('''#include "Vtb_equality.h"
#include <cstdio>
int main(){Vtb_equality t;unsigned long checks=0,equal=0,negative=0;
for(unsigned a=0;a<4096;a++)for(unsigned b=0;b<4096;b++){
 t.ring_rdptr=a;t.command_wrptr=b;t.eval();
 if(t.old_empty!=t.new_empty){printf("FAIL %u %u\\n",a,b);return 1;}
 checks++;equal+=t.old_empty;negative+=t.old_empty!=t.bad_empty;
}
if(checks!=16777216||equal!=4096||negative!=28672)return 2;
t.final();printf("PASS checks=%lu equal=%lu negative=%lu\\n",checks,equal,negative);
}
''')
    cmd = ['verilator', '--cc', '--exe', '--build', '-j', '3', '-Wno-fatal',
           '--top-module', 'tb_equality', '--Mdir', str(d / 'obj'), '-CFLAGS', '-O2',
           str(d / 'tb.v'), str(d / 'main.cpp')]
    write(d / 'command.json', cmd)
    with (d / 'build.log').open('x') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    with (d / 'run.log').open('x') as log:
        subprocess.run([str(d / 'obj/Vtb_equality')], stdout=log, stderr=subprocess.STDOUT, check=True)
    assert (d / 'run.log').read_text() == 'PASS checks=16777216 equal=4096 negative=28672\n'
    write(d / 'result.json', dict(passed=True, all_pointer_pairs=16777216,
        equal_pairs=4096, missing_high_group_negative_mismatches=28672,
        covers_full_wrap_values=True, candidate_sha256=sha(OUT / 'gpu_core.v'),
        parent_sha256=sha(BASE), scope='Literal source equality, all two-state 12-bit pointer pairs.'))


if __name__ == '__main__':
    for name in sys.argv[1:]:
        assert name in ('prepare', 'literal')
        globals()[name]()
