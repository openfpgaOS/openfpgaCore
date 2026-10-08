#!/usr/bin/env python3
"""Exhaustive IEEE predicates and actual indexed-loop tests for W guard."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT/'docs/measurements/os30-fps25-20261005'
BASE = CAMP/'phase7/combined-v1'
CAND = CAMP/'phase9/w-guard-v1'
SIGN = CAMP/'phase9/w-sign-v1'
OUT = CAND/'tests'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def module(name,path):
    spec = importlib.util.spec_from_file_location(name,path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def guard(source,name):
    loop = source[source.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run('):]
    a = loop.index('            const float w1 = ')
    b = loop.index('            ++live;',a)
    text = loop[a:b]
    text = re.sub(r'            const float w[123] = [^;]+;\n','',text)
    text = text.replace('goto fallback_command;','return 0;')
    return f'static __attribute__((noinline,noclone)) int {name}(float w1,float w2,float w3) {{\n'+text+'return 1;\n}\n'


def predicate():
    OUT.mkdir(exist_ok=True)
    dest = OUT/'predicate'
    dest.mkdir(exist_ok=False)
    helper = module('old_indexed_test',Path(__file__).with_name('phase7_eligibility_tests.py'))
    b,c,s = (p.read_text() for p in (BASE/'gfx_pc.c',CAND/'gfx_pc.c',SIGN/'gfx_pc.c'))
    source = r'''#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#define __riscv 1
#define TARGET_OPENFPGA 1
static float fp(uint32_t u) {float f;memcpy(&f,&u,4);return f;}
static __attribute__((noinline,noclone)) int baseline_unary(float w) {return w>0.0f && w<32767.0f;}
static __attribute__((noinline,noclone)) int baseline_sign(float w) {return w<0.0f;}
'''+helper.function(s,'gfx_cpu_w_negative')+'\n'+guard(b,'baseline_guard')+guard(c,'candidate_guard')+r'''
static unsigned rng=0x23c01d98u;
static unsigned next(void) {unsigned x=rng;x^=x<<13;x^=x>>17;x^=x<<5;return rng=x;}
int main(void) {
 uint64_t accepted=0,negative=0,triples=0;
 for(uint64_t u=0;u<0x100000000ull;u++) {
  uint32_t bits=u;float w=fp(bits);
  int a=baseline_unary(w),b=(bits-1u<0x46fffdffu);
  int c=baseline_sign(w),d=gfx_cpu_w_negative(&w);
  if(a!=b || c!=d) {printf("MISMATCH %08x %d %d %d %d\n",bits,a,b,c,d);return 1;}
  accepted+=a;negative+=c;
 }
 const uint32_t edges[]={0,1,2,0x007fffff,0x00800000,0x3e800000,0x3f000000,0x3f800000,
  0x40000000,0x46fffdfe,0x46fffdff,0x46fffe00,0x46fffe01,0x46ffffff,0x47000000,
  0x7f7fffff,0x7f800000,0x7f800001,0x7fc00000,0x7fffffff,0x80000000,0x80000001,
  0x807fffff,0x80800000,0xbf800000,0xff7fffff,0xff800000,0xff800001,0xffc00000,0xffffffff};
 const unsigned n=sizeof(edges)/sizeof(edges[0]);
 for(unsigned a=0;a<n;a++)for(unsigned b=0;b<n;b++)for(unsigned c=0;c<n;c++) {
  if(baseline_guard(fp(edges[a]),fp(edges[b]),fp(edges[c])) !=
     candidate_guard(fp(edges[a]),fp(edges[b]),fp(edges[c])))return 2;
  triples++;
 }
 for(unsigned i=0;i<1000000;i++) {
  uint32_t a=next(),b=next(),c=next();
  if((i&7)==0)b=c=a;
  if((i&7)==1)a=1u+next()%0x46fffdffu,b=a,c=a;
  if((i&7)==2)a=1u+next()%0x46fffdffu,b=1u+next()%0x46fffdffu,c=1u+next()%0x46fffdffu;
  if(baseline_guard(fp(a),fp(b),fp(c))!=candidate_guard(fp(a),fp(b),fp(c)))return 3;
  triples++;
 }
 printf("{\"passed\":true,\"raw_ieee_encodings\":4294967296,\"positive_bounded_encodings\":%llu,\"negative_encodings\":%llu,\"triple_cases\":%llu}\n",
  (unsigned long long)accepted,(unsigned long long)negative,(unsigned long long)triples);
 return 0;
}
'''
    (dest/'test.c').write_text(source)
    command = ['cc','-std=c11','-O3','-fno-fast-math','-frounding-math','-fno-finite-math-only',str(dest/'test.c'),'-o',str(dest/'test')]
    subprocess.run(command,check=True)
    (dest/'test.asm').write_text(subprocess.check_output(['objdump','-d',str(dest/'test')],text=True))
    start = time.monotonic()
    r = subprocess.run([str(dest/'test')],capture_output=True,text=True,check=True,timeout=300)
    (dest/'stdout').write_text(r.stdout);(dest/'stderr').write_text(r.stderr)
    result = json.loads(r.stdout)
    assert result['positive_bounded_encodings'] == 0x46fffdff
    assert result['negative_encodings'] == 0x7f800000
    result.update(elapsed_seconds=time.monotonic()-start,command=command,
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in (BASE/'gfx_pc.c',CAND/'gfx_pc.c',SIGN/'gfx_pc.c')},
        evidence_sha256={p.name:sha(p) for p in dest.iterdir() if p.is_file()},recipe_sha256=sha(Path(__file__)),
        scope='All32-bit IEEE encodings tested against strict host floating comparisons; actual extracted three-W guard and W-sign helper tested. Output booleans only, not NaN fflags. Retained-A code generation and timing remain separate.')
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


def indexed():
    OUT.mkdir(exist_ok=True)
    dest = OUT/'indexed'
    dest.mkdir(exist_ok=False)
    path = Path(__file__).with_name('phase7_eligibility_tests.py')
    recipe = path.read_text()
    # Both sides now contain the selected F9 rejection reuse; preserve its
    # count, instead of asserting the earlier F8->F9 reduction again.
    anchor = "assert results['candidate']['rejection_evaluations'] < results['baseline']['rejection_evaluations']"
    assert recipe.count(anchor)==1
    recipe = recipe.replace(anchor,anchor.replace(' < ',' == '))
    copied = dest/'indexed-recipe.py'
    copied.write_text(recipe)
    m = module('w_guard_indexed',copied)
    m.BASE,m.CAND,m.OUT = BASE/'gfx_pc.c',CAND/'gfx_pc.c',dest
    sys.argv=[str(copied)]
    m.main()


if __name__ == '__main__':
    {'predicate':predicate,'indexed':indexed}[sys.argv[1]]()
