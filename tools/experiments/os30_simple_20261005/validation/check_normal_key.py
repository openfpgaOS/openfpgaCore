#!/usr/bin/env python3
"""Independent exhaustive check of the copied three-byte lighting memo key."""
from common import *
import subprocess,os
candidate=OUT.parent/'normal-key-v1/gfx_pc.c';baseline=OUT.parent/'baseline/gfx_pc.c'
assert sha(candidate)=='6cac5dc468f777bc75455e2da0aca84d6eef6a37335d9959c5278c6552a9a7ee'
b=baseline.read_text();c=candidate.read_text();out=OUT/'normal-key-v1-focused';out.mkdir(exist_ok=False)
start=c.index('/* The memo key contains only normal XYZ.');end=c.index('\n}\n',start)+3;added=c[start:end]
helper=added[added.index('static inline __attribute__((always_inline)) uint32_t gfx_lighting_normal_key'):]
restored=c[:start-1]+c[end:]
old_short='        uint32_t normal;memcpy(&normal,v->cn,sizeof(normal));normal&=0xffffffu;'
old_long='            uint32_t normal;\n            memcpy(&normal,v->cn,sizeof(normal));\n            normal&=0x00ffffffu;'
assert restored.count('        const uint32_t normal=gfx_lighting_normal_key(v->cn);')==2
restored=restored.replace('        const uint32_t normal=gfx_lighting_normal_key(v->cn);',old_short,1)
restored=restored.replace('            const uint32_t normal=gfx_lighting_normal_key(v->cn);',old_long,1)
assert restored==b,'Changes outside normal-key extraction'
src='''#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include <limits.h>
_Static_assert(CHAR_BIT==8 && sizeof(uint32_t)==4,"Expected byte/word sizes");
'''+helper+'''
static __attribute__((noinline)) uint32_t reference(const uint8_t*cn) {uint32_t normal;memcpy(&normal,cn,sizeof(normal));return normal&0x00ffffffu;}
static __attribute__((noinline)) uint32_t candidate(const uint8_t*cn) {return gfx_lighting_normal_key(cn);}
int main(void) {
 uint8_t storage[16]={0};uint64_t count=0;uint32_t check=0;
 /* Every possible normal XYZ key, distributed across all byte alignments. */
 for(uint32_t normal=0;normal<0x1000000u;normal++) {
  uint8_t*p=storage+(normal&7);p[0]=normal;p[1]=normal>>8;p[2]=normal>>16;p[3]=(normal^(normal>>11))&255;
  uint32_t expected=reference(p),got=candidate(p);assert(expected==got);check+=got;count++;
 }
 /* Alpha is excluded on LE, and the original native-word mask is retained otherwise. */
 for(unsigned alignment=0;alignment<8;alignment++)for(unsigned channel=0;channel<3;channel++)for(unsigned v=0;v<256;v++)for(unsigned alpha=0;alpha<256;alpha++) {
  uint8_t*p=storage+alignment;p[0]=0x81;p[1]=0x37;p[2]=0xc4;p[channel]=v;p[3]=alpha;
  assert(candidate(p)==reference(p));count++;
 }
 printf("PASS cases=%llu checksum=%08x every24bitnormal alignment0..7 alpha0..255\\n",(unsigned long long)count,check);return 0;
}
'''
(out/'functions.c').write_text(src);(out/'check_normal_key.py').write_text(Path(__file__).read_text());runs=[]
for name,flags in [('checked',['-O2','-fsanitize=address,undefined','-fno-omit-frame-pointer']),('fast',['-O3','-ffast-math','-fno-strict-aliasing','-fwrapv']),('unknown_endian',['-O2','-U__BYTE_ORDER__']),('fallback_endian',['-O2','-U__BYTE_ORDER__','-D__BYTE_ORDER__=__ORDER_BIG_ENDIAN__'])]:
 binary=out/name;subprocess.run(['gcc','-std=c11','-Wall','-Wextra',*flags,str(out/'functions.c'),'-o',str(binary)],check=True)
 result=subprocess.run([str(binary)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'));(out/(name+'.log')).write_text(result.stdout);assert result.returncode==0,result.stdout
 print(name,result.stdout.strip(),flush=True);runs.append({'name':name,'flags':flags,'output':result.stdout.strip(),'binary_sha256':sha(binary)})
record={'passed':True,'candidate_sha256':sha(candidate),'baseline_sha256':sha(baseline),'recipe_sha256':sha(Path(__file__)),'fixture_sha256':sha(out/'functions.c'),'reverse_source_exact':True,'mathematical_proof':'Little-endian native uint32 is b0+256*b1+65536*b2+16777216*b3. Mask0x00ffffff discards exactly b3, leaving explicit unsigned shifts of three bytes. Each shift is uint32 and at most0xff0000. Non-LE or unknown compiler endianness executes original memcpy/mask verbatim.','runs':runs,'scope':'Exhaustive24-bit keys plus alpha/alignment cases on host. Forced fallback branch verifies original expression is retained, not actual big-endian execution. Lighting, UV, alpha, geometry and control source reverse-compare exact. Target full-game replay independently required.'}
(out/'results.json').write_text(json.dumps(record,indent=2)+'\n')
