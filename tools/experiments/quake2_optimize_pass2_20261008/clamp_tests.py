#!/usr/bin/env python3
"""Run actual extracted old/new projection functions on host and RV32."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-optimize-pass2-20261008/components/clamp-v1'
SDK = OUT.parents[1] / 'work/base/src/sdk'
QSIM = ROOT / 'docs/measurements/quake2-optimize-20261008/demo-screen-v2/qsim/qsim'

TEST = r'''#include "shared.h"
#include <stdio.h>
#if defined(__SSE__)
#include <xmmintrin.h>
#endif
float ofgl_pc_xc, ofgl_pc_yc, ofgl_pc_xs, ofgl_pc_ys, ofgl_depth_scale;
void project_old(ofgl_cvert_t *v);
void project_new(ofgl_cvert_t *v);
static uint32_t count, errors, valid, rejected, depth_min, depth_max, xy_clamped, checksum;

static float frombits(uint32_t bits) {
    float result; memcpy(&result, &bits, 4); return result;
}
static uint32_t bits(float value) {
    uint32_t result; memcpy(&result, &value, 4); return result;
}
static void check(const float p[7]) {
    ofgl_cvert_t old, new;
    memset(&old, 0xa5, sizeof(old));
    old.x=p[0]; old.y=p[1]; old.z=p[2];
    ofgl_pc_xc=p[3]; ofgl_pc_yc=p[4]; ofgl_pc_xs=p[5]; ofgl_pc_ys=p[6];
    memcpy(&new, &old, sizeof(old));
    project_old(&old); project_new(&new);
    if (memcmp(&old, &new, sizeof(old))) {
        if (errors < 8) printf("mismatch case=%u xyz=%08x,%08x,%08x sx=%08x/%08x sy=%08x/%08x depth=%08x/%08x\n",
            count,bits(p[0]),bits(p[1]),bits(p[2]),bits(old.sx),bits(new.sx),bits(old.sy),bits(new.sy),old.depth,new.depth);
        errors++;
    }
    if (new.projected_valid == 1) {
        valid++;
        if (new.depth == 1) depth_min++;
        if (new.depth == 2147483520) depth_max++;
        if (bits(new.sx)==0x44ffe000u || bits(new.sx)==0xc4ffe000u ||
            bits(new.sy)==0x44ffe000u || bits(new.sy)==0xc4ffe000u) xy_clamped++;
    } else rejected++;
    const unsigned char *raw = (const unsigned char *)&new;
    for (unsigned i=0; i<sizeof(new); i++) checksum=(checksum^raw[i])*16777619u;
    count++;
}
static void rounding_mode(unsigned mode) {
#if defined(__riscv)
    __asm__ volatile ("fsrm zero, %0" : : "r"(mode));
#else
    (void)mode;
#endif
}
static uint32_t next(uint32_t *state) {
    *state ^= *state << 13; *state ^= *state >> 17; *state ^= *state << 5; return *state;
}

int main(void) {
#if defined(__SSE__)
    _mm_setcsr(_mm_getcsr() & ~0x8040u);
#endif
    static const uint32_t mantissas[]={0,1,0x200000,0x3fffff,0x400000,0x7ffffe,0x7fffff};
    static const uint32_t edges[]={0,0x80000000u,1,0x80000001u,0x007fffffu,0x00800000u,
        0x3effffffu,0x3f000000u,0x3f000001u,0x3f7fffffu,0x3f800000u,0x3f800001u,
        0x44ffdfffu,0x44ffe000u,0x44ffe001u,0x4efffffeu,0x4effffffu,0x4f000000u,
        0x7f7fffffu,0x7f800000u,0x7f800001u,0x7fc00000u,0x7fffffffu};
    for (unsigned mode=0; mode<5; mode++) {
        rounding_mode(mode);
        for (unsigned scale=0; scale<2; scale++) {
            ofgl_depth_scale=scale ? 16.0f : 1.0f;
            for (unsigned slot=0; slot<7; slot++) {
                float p[7]={0,0,1,0,0,1,1};
                for (unsigned exponent=0; exponent<256; exponent++)
                    for (unsigned m=0; m<sizeof(mantissas)/sizeof(mantissas[0]); m++)
                        for (unsigned sign=0; sign<2; sign++) {
                            p[slot]=frombits((sign<<31)|(exponent<<23)|mantissas[m]); check(p);
                        }
                for (unsigned e=0; e<sizeof(edges)/sizeof(edges[0]); e++)
                    for (unsigned sign=0; sign<2; sign++) {
                        p[slot]=frombits(edges[e]^(sign<<31));check(p);
                    }
            }
            uint32_t random=0x9bd91a17u;
            for (unsigned i=0; i<32768; i++) {
                float p[7];
                for (unsigned k=0; k<7; k++) p[k]=frombits(next(&random));
                check(p);
                p[0]=((int32_t)(next(&random)%65537u)-32768)*0.0625f;
                p[1]=((int32_t)(next(&random)%65537u)-32768)*0.0625f;
                p[2]=((int32_t)(next(&random)%16385u)-8192)*0.0625f;
                p[3]=160; p[4]=120; p[5]=160; p[6]=160;
                check(p);
            }
        }
    }
    rounding_mode(0);
    printf("CLAMP_TEST cases=%u errors=%u valid=%u rejected=%u depth_min=%u depth_max=%u xy_clamped=%u checksum=%08x\n",
        count,errors,valid,rejected,depth_min,depth_max,xy_clamped,checksum);
    return errors || !valid || !rejected || !depth_min || !depth_max || !xy_clamped;
}
'''

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def run(cmd, name):
    started=time.monotonic(); env=dict(os.environ)
    if name.startswith('host-sanitize'): env['ASAN_OPTIONS']='detect_leaks=0'
    with (OUT/(name+'.log')).open('w') as log:
        p=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
    result=dict(command=cmd,returncode=p.returncode,seconds=time.monotonic()-started)
    (OUT/(name+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    assert p.returncode==0,result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','host','target']);a=ap.parse_args()
    if a.action=='prepare':
        assert not (OUT/'clamp_test.c').exists()
        (OUT/'clamp_test.c').write_text(TEST);return
    sources=[str(OUT/n)for n in ['clamp_test.c','projection_old.c','projection_new.c']]
    if a.action=='host':
        for name,flags in [('host',['-O3','-ffast-math']),
                            ('host-sanitize',['-O1','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
            run(['cc','-std=c11',*flags,*sources,'-lm','-o',str(OUT/name)],name+'-build')
            run([str(OUT/name)],name+'-run')
        return
    compiler=['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
        '-v',f'{ROOT}:{ROOT}','-w',str(OUT),'-e','CPATH=','--entrypoint','riscv64-unknown-elf-gcc','openfpgaos-firmware']
    flags=['-march=rv32imafc_zicsr','-mabi=ilp32f','-O3','-flto=auto','-ffast-math',
        '-fno-math-errno','-fno-trapping-math','-fsingle-precision-constant','-fno-tree-loop-distribute-patterns',
        '-ffunction-sections','-fdata-sections','-falign-functions=64','-falign-loops=8','-nostdinc',
        '-isystem',str(SDK/'musl/include'),'-nostdlib','-static','-T',str(SDK/'app.ld'),
        '-L'+str(SDK/'musl/lib'),'-Wl,--gc-sections','-Wl,--no-warn-rwx-segments']
    cmd=[*compiler,*flags,'-o',str(OUT/'clamp-target.elf'),
         *[str(SDK/'musl/lib'/n)for n in ['crt1.o','crti.o','crtn.o']],*sources,
         '-Wl,--start-group','-lc','-lm','-lgcc','-Wl,--end-group']
    run(cmd,'target-build');dest=OUT/'target-run';assert not dest.exists()
    run([str(QSIM),str(OUT/'clamp-target.elf'),'--out',str(dest),'--max-insns','2e9',
         '--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp'],'target-run')
    summary=json.loads((dest/'summary.json').read_text());console=(dest/'console.log').read_text()
    assert summary['stop_reason']=='guest exit(0)',summary
    assert 'errors=0 'in console,console
    (OUT/'tests.json').write_text(json.dumps(dict(passed=True,
        test_source_sha256=sha(OUT/'clamp_test.c'),target_elf_sha256=sha(OUT/'clamp-target.elf'),
        old_sha256=sha(OUT/'projection_old.c'),new_sha256=sha(OUT/'projection_new.c'),
        qsim_sha256=sha(QSIM),console=console,target_rounding_modes=['RNE','RTZ','RDN','RUP','RMM'],
        depth_scales=[1,16],nonfinite_inputs='All seven input/projection floats, both signs and all exponent transitions',
        caveat='Functional actual RV32 execution, not a cycle or Pocket FPS benchmark.'),indent=2)+'\n')
    print((OUT/'tests.json').read_text())

if __name__=='__main__':main()
