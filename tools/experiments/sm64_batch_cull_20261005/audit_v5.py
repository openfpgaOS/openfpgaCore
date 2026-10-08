#!/usr/bin/env python3
"""Independent exact-source probe-control and target-arithmetic audit."""
from pathlib import Path
import hashlib,json,re,subprocess
from audit_v2 import body,once

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005'
DEST=OUT/'independent-audit-v5'
GFX=OUT/'SM64/src/sm64/sm64/src/pc/gfx'
OLD=OUT/'independent-audit-v4'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    DEST.mkdir(exist_ok=True)
    expected={'gfx_geometry.h':'75ffdcb6ddd7111903f92564e17ebd8409594a88f7695a91fefa2b819cb4a2c6',
        'gfx_pc.c':'4665b1edd36eb7feffb8ef4203fbcc9faed15baad7e9cd9bed463663c340f927',
        'gfx_vertex_cull.h':'6c48e51766818099864b4629a1c06a28abd665da6ec4188605b755244b7788d2'}
    assert {n:sha(GFX/n) for n in expected}==expected
    g=(GFX/'gfx_geometry.h').read_text()
    a=g.index('        /* Different immutable batches sharing a hash keep independent')
    z=g.index('        if(entry)*entry=cached;',a)
    block=g[a:z]
    reverse=once(g,block,'        cached=&geometry_software_bounds_cache[hash];\n')
    assert reverse==(OLD/'gfx_geometry.h').read_text()
    for n in ('gfx_pc.c','gfx_vertex_cull.h'):
        assert (GFX/n).read_bytes()==(OLD/n).read_bytes()
    # Compile the exact new lookup fragment. All outcomes are determined by
    # the first empty/matching entry, or 16 occupied mismatches. Exhaust all
    # 1024 home indices and those 33 first-stop classes, including wraparound.
    harness='''#include <assert.h>
#include <stdint.h>
#include <stdio.h>
typedef struct {int unused;} Vtx;
struct GeometrySoftwareBounds {const Vtx *source;uint16_t count,retry;float box[6];};
static struct GeometrySoftwareBounds geometry_software_bounds_cache[1024];
static Vtx key,other;
static unsigned run(unsigned hash,const Vtx *vertices,unsigned count) {
struct GeometrySoftwareBounds *cached=0;
'''+block+'''
return (unsigned)(cached-geometry_software_bounds_cache) | (tries<<10);
}
int main(void) {
unsigned checks=0;
for(unsigned home=0;home<1024;home++) {
 for(unsigned kind=0;kind<2;kind++)for(unsigned first=0;first<16;first++) {
  for(unsigned i=0;i<1024;i++) {geometry_software_bounds_cache[i].source=&other;geometry_software_bounds_cache[i].count=8;}
  unsigned idx=(home+first)&1023;
  geometry_software_bounds_cache[idx].source=kind?&key:0;
  assert(run(home,&key,8)==(idx|(first<<10)));checks++;
 }
 for(unsigned i=0;i<1024;i++) {geometry_software_bounds_cache[i].source=&other;geometry_software_bounds_cache[i].count=8;}
 assert(run(home,&key,8)==(home|(16<<10)));checks++;
 // Same source with a different count is a mismatch, even at home.
 geometry_software_bounds_cache[home].source=&key;geometry_software_bounds_cache[home].count=16;
 assert(run(home,&key,8)==(home|(16<<10)));checks++;
}
printf("PASS exact lookup fragment: %u home/first-stop/count classes\\n",checks);
}
'''
    (DEST/'probe-control.c').write_text(harness)
    subprocess.run(['cc','-O2','-std=c11','-Wall','-Wextra','-Werror',str(DEST/'probe-control.c'),'-o',str(DEST/'probe-control')],check=True)
    log=subprocess.check_output([str(DEST/'probe-control')],text=True)
    (DEST/'probe-control.log').write_text(log)
    targets=[]
    for label,app in [('diagnostic','sm64-perfdiag'),('normal','sm64')]:
        obj=OUT/f'SM64/.obj/{app}/sm64/src/pc/gfx/gfx_pc.o'
        asm=subprocess.check_output(['llvm-objdump','-dr','--section=.text.geometry_confirm_outside',
            '--section=.text.gfx_sp_vertex','--section=.text.vertex_cull_materialize',
            '--section=.text.geometry_prepare_software',str(obj)],text=True)
        (DEST/f'gfx_pc-{label}.asm').write_text(asm)
        prev=(OLD/f'gfx_pc-{label}.asm').read_text()
        for n in ['geometry_confirm_outside','gfx_sp_vertex','vertex_cull_materialize']:
            assert body(asm,n)==body(prev,n),(label,n)
        prep=asm.split('Disassembly of section .text.geometry_prepare_software:',1)[1].split('Disassembly of section ',1)[0]
        assert not re.search(r'\bdivu?\b',prep)
        instructions=[(int(m[1],16),m[2],m[3]) for m in re.finditer(r'^\s*([0-9a-f]+):\s+[0-9a-f]+\s+(\S+)\s*(.*)$',prep,re.M)]
        probes=[x for x in instructions if 0x1b8<=x[0]<=0x33e and x[1]=='lw']
        wraps=[x for x in instructions if 0x1b8<=x[0]<=0x33e and x[1]=='andi' and x[2]=='a2, a2, 0x3ff']
        assert len(probes)==16 and len(wraps)==15
        assert (0x34a,'add','s2, s2, a6') in instructions # table base + saved home-byte offset
        build=json.loads((OUT/f'build-{label}.json').read_text())
        assert build['returncode']==0 and build['changed_inputs']==[]
        assert all(build['input_sha256']['src/sm64/sm64/src/pc/gfx/'+n]==h for n,h in expected.items())
        targets.append(dict(label=label,object_sha256=sha(obj),elf_sha256=build['elf_sha256'],
            assembly_sha256=sha(DEST/f'gfx_pc-{label}.asm'),software_divide_count=0,
            lookup_codegen='Sixteen unrolled source loads, fifteen &1023 wrapped index increments, then table base + preserved home offset.',
            probe_source_load_offsets=[hex(x[0]) for x in probes],budget_fallback_address='0x34a',
            confirmation_fallback_recovery_instructions_and_external_bindings_identical=True))
    for n in expected:(DEST/n).write_bytes((GFX/n).read_bytes())
    result=dict(passed=True,source_sha256=expected,exact_reverse_to_v4=True,
        probe_control_cases=34816,probe_control_source_sha256=sha(DEST/'probe-control.c'),
        probe_control_binary_sha256=sha(DEST/'probe-control'),probe_control_log_sha256=sha(DEST/'probe-control.log'),
        target=targets,source_review_sha256=sha(DEST/'REVIEW.txt'),script_sha256=sha(Path(__file__)),
        prior_audit_sha256=sha(OLD/'manifest.json'),
        scope='Independent source/probe-control and compiled-arithmetic audit; full tests/performance remain separate.',
        limitations=['Exhaustion is of exact lookup first-stop/control classes, not all table data or a whole-program formal proof.',
            'All data validation/cache publication/retry/capture code is unchanged and reviewed separately.'])
    (DEST/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(log.strip());print('PASS: v5 exact probe scope and normal/diagnostic target FP DAGs.')

if __name__=='__main__':main()
