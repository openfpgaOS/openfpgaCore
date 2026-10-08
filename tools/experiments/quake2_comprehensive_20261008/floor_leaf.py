#!/usr/bin/env python3
"""Make and verify a leaf RV32 UV-floor helper against the shipped musl."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/quake2-comprehensive-20261008'
OUT=P/'components/floor-leaf-v1'
REL=Path('src/quake2/openfpga/of_gl1.c')
SOURCE_SHA='29818e61c88a358f30cde8a4282676c6904a71b12b77c98934844233ea84fbdc'

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,indent=2)+'\n')

def apply(source):
    before=''' * depending on the current floating-point rounding mode. Preserve signed
 * zero and leave large/nonfinite inputs on the original library path. */'''
    after=''' * depending on the current floating-point rounding mode. Signed zero is
 * unchanged; floats at least 2^23 are already integral. The bundled RV32
 * floorf also returns infinity/NaN input bits unchanged, so those paths need
 * no call or floating-point operation. Keep the library on other hosts. */'''
    assert source.count(before)==1
    source=source.replace(before,after)
    before='''\t\treturn (float)integral;
\t}
#endif
\treturn floorf(v);
}'''
    after='''\t\treturn (float)integral;
\t}
\treturn v;
#else
\treturn floorf(v);
#endif
}'''
    assert source.count(before)==1
    return source.replace(before,after)

WRAPPERS=r'''
/* Operand dependencies keep all measured FP work between the FCSR accesses
 * even with fast-math. noipa prevents interprocedural purity inference. */
__attribute__((noinline,noclone,noipa))
static float original_observed(float v, uint32_t *flags) {
#if defined(__riscv)
    __asm__ volatile ("csrw fflags, zero" : "+f"(v) : : "memory");
    float result=original_floor(v);
    __asm__ volatile ("csrr %0, fflags" : "=r"(*flags) : "f"(result) : "memory");
    return result;
#else
    *flags=0;return original_floor(v);
#endif
}
__attribute__((noinline,noclone,noipa))
static float candidate_observed(float v, uint32_t *flags) {
#if defined(__riscv)
    __asm__ volatile ("csrw fflags, zero" : "+f"(v) : : "memory");
    float result=ofgl_uv_floor(v);
    __asm__ volatile ("csrr %0, fflags" : "=r"(*flags) : "f"(result) : "memory");
    return result;
#else
    *flags=0;return ofgl_uv_floor(v);
#endif
}
'''

def harness():
    path=ROOT/'tools/experiments/quake2_optimize_pass2_20261008/floor_tests.py'
    spec=importlib.util.spec_from_file_location('floor_test_original',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    test=m.TEST.replace('static uint32_t count, errors, oracle_errors, checksum;',
        'static uint32_t count, errors, oracle_errors, checksum, flag_errors;\n'+WRAPPERS)
    before='''    expected = original_floor(input);
    actual = ofgl_uv_floor(input);'''
    after='''    uint32_t expected_flags, actual_flags;
    expected = original_observed(input, &expected_flags);
    actual = candidate_observed(input, &actual_flags);
    if (expected_flags != actual_flags) {
        if (flag_errors < 8) printf("flags input=%08x expected=%x actual=%x\\n",bits,expected_flags,actual_flags);
        flag_errors++;
    }'''
    assert before in test;test=test.replace(before,after)
    test=test.replace('    return errors || oracle_errors ? 1 : 0;',
        '    printf("FLOOR_FLAGS errors=%u\\n",flag_errors);\n    return errors || oracle_errors || flag_errors ? 1 : 0;')
    m.OUT=OUT;m.SDK=P/'work/base/src/sdk';m.TEST=test
    return m,path

def prepare():
    base=P/'work/base';work=P/'work/floor_leaf';assert sha(base/REL)==SOURCE_SHA
    assert not OUT.exists() and not work.exists()
    OUT.mkdir(parents=True);shutil.copytree(base,work)
    old=(base/REL).read_text();new=apply(old);(work/REL).write_text(new)
    (OUT/'of_gl1.c').write_text(new)
    (OUT/'change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),
        fromfile=str(REL),tofile=str(REL))))
    start=new.index('/* Floor the texture-rebase quotient')
    end=new.index('/* Emit a finite, visible triangle.',start)
    (OUT/'helper.h').write_text(new[start:end])
    m,path=harness();(OUT/'floor_test.c').write_text(m.TEST)
    cmd=['llvm-objdump','-d','--mattr=+f,+c','--no-show-raw-insn',
        '--disassemble-symbols=floorf',str(base/'.obj/quake2/app.elf')]
    result=subprocess.run(cmd,check=True,capture_output=True,text=True)
    (OUT/'baseline-floorf-disassembly.txt').write_text(result.stdout)
    write(OUT/'manifest.json',dict(baseline_source_sha256=SOURCE_SHA,candidate_source_sha256=sha(work/REL),
        helper_sha256=sha(OUT/'helper.h'),candidate=str(work),reused_test_recipe=str(path),
        reused_test_recipe_sha256=sha(path),reference_elf_sha256=sha(base/'.obj/quake2/app.elf'),
        reference_disassembly_sha256=sha(OUT/'baseline-floorf-disassembly.txt'),
        justification=['RV32 musl floorf returns raw input when exponent is at least23, including infinities and NaNs.',
            'For positive and negative zero its force-evaluated large+zero is exact, and returns the same sign of zero.',
            'All other inputs already use the exact F-extension conversion; no extra approximation is introduced.',
            'Other hosts retain the original floorf call.']))
    print((OUT/'manifest.json').read_text())

def tests(action):
    import sys
    m,_=harness();assert (OUT/'floor_test.c').read_text()==m.TEST
    sys.argv=[__file__,action];m.main()
    if action=='target':
        console=(OUT/'target-run/console.log').read_text()
        assert 'FLOOR_FLAGS errors=0' in console
        result=json.loads((OUT/'tests.json').read_text());result['fp_exception_flags_equal']=True
        result['wrapper_ordering']='noipa wrappers plus input/output operand dependencies around FCSR accesses'
        write(OUT/'tests.json',result)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','host','target']);args=ap.parse_args()
    if args.action=='prepare':prepare()
    else:tests(args.action)
