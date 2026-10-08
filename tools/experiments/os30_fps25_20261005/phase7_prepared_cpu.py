#!/usr/bin/env python3
"""Exercise prepared UV/clip words on the retained A RV32 FPU and CPU RTL."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
CAND=CAMP/'phase7/prepared-v3'
BASE=CAMP/'phase6/candidate'
OLD=ROOT/'docs/measurements/os30-simple-20261005/validation/cpu-kernel'
OUT=CAND/'cpu-prepared'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def function(source,name):
    m=re.search(r'^(?:static inline |static |void )[^{;\n]*\b'+name+r'\(',source,re.M)
    assert m,name
    b=source.index('{',m.end());p=b+1;d=1
    while d:d+=(source[p]=='{')-(source[p]=='}');p+=1
    return source[m.start():p]

PREFIX=r'''#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <math.h>
volatile unsigned traps,irq_count;
#define RSPV_TO_FLOAT(x) (x)
#define VC_W_PRESCALE (1.0f/256.0f)
#define GPU_CMD_LOAD_VERT_CLIP 0x56u
#define GPU_CMD_DRAW_INDEXED_TRI 0x54u
static int g_vc_rgb_input;
static uint32_t g_vc_words,g_vc_loads;
struct RGBA {uint8_t r,g,b,a;};
struct LoadedVertex {float x,y,w,w_inv,u,v;struct RGBA color;};
struct ColorCombiner {uint8_t shader_input_mapping[2][4];};
static const struct RGBA *gfx_vc_resolve(uint8_t source,const struct LoadedVertex *v,
                                      const struct LoadedVertex *v1,struct RGBA *tmp) {
    (void)source;(void)v1;(void)tmp;return &v->color;
}
'''

TAIL=r'''
static void put(char c) {while(!(*(volatile unsigned *)0x4f000000u&2)){} *(volatile unsigned *)0x4f000004u=(unsigned char)c;}
static void puts_uart(const char*s) {while(*s)put(*s++);}
static void hex(unsigned v) {for(unsigned n=0;n<8;n++)put("0123456789abcdef"[(v>>(28-4*n))&15]);put(' ');}
static void fail(void) {puts_uart("KERNEL FAIL\n");for(;;){}}
#define assert(x) do {if(!(x))fail();}while(0)
static unsigned fold(unsigned h,unsigned x) {return (h^x)*16777619u;}
#define JOBS 512u
struct job {
 struct LoadedVertex v[3];uint8_t slot[3];unsigned mask;int rgb,textured;
 float uofs,vofs,bias;
};
static struct job jobs[JOBS];
static uint32_t random_state=0x56924acbu;
static uint32_t next(void) {unsigned x=random_state;x^=x<<13;x^=x>>17;x^=x<<5;return random_state=x;}
static void inputs(void) {
 for(unsigned i=0;i<JOBS;i++) {
  struct job *j=&jobs[i];j->mask=i&7;j->rgb=(i&8)?0:-1;j->textured=(i>>4)&1;
  j->uofs=(float)((int)(next()%4096)-2048)*8.0f;
  j->vofs=(float)((int)(next()%4096)-2048)*8.0f;j->bias=(i&32)?0.5f:0;
  for(unsigned k=0;k<3;k++) {
   struct LoadedVertex *v=&j->v[k];j->slot[k]=next()&31;
   v->x=(int16_t)next()*0.03125f+0.001953125f;
   v->y=(int16_t)next()*0.03125f-0.001953125f;
   v->w=(next()%32000u)+0.25f;
   v->u=(int16_t)next()*0.125f+0.00000762939453125f;
   v->v=(int16_t)next()*0.125f-0.00000762939453125f;
   v->w_inv=1.0f/v->w;
   if((i&192)==64)v->w_inv=4.0f;
   if((i&192)==128)v->w_inv=0.0f;
   if((i&192)==192)v->w_inv=1.5f;
   v->color=(struct RGBA){next(),next(),next(),next()};
   for(unsigned p=0;p<k;p++)if(j->slot[k]==j->slot[p])*v=j->v[p];
  }
 }
}
static __attribute__((noinline,noclone)) unsigned reference(const struct job *j,uint32_t *out) {
 struct gfx_vc_vtx v[3];
 for(unsigned k=0;k<3;k++)if(j->mask&(1u<<k))
  reference_prepare(&v[k],&j->v[k],j->textured,j->uofs,j->vofs,j->bias,j->rgb,255);
 return reference_emit(v,j->slot,j->mask,out);
}
static __attribute__((noinline,noclone)) unsigned candidate(const struct job *j,uint32_t *out) {
 struct gfx_vc_prepared v[32];uint32_t prepared=0;
 for(unsigned k=0;k<3;k++)if(j->mask&(1u<<k)) {
  unsigned bit=1u<<j->slot[k];if(prepared&bit)continue;prepared|=bit;
  candidate_prepare(&v[j->slot[k]],&j->v[k],j->textured,j->uofs,j->vofs,j->bias,j->rgb,255);
 }
 return candidate_emit(v,j->slot,j->mask,out);
}
int main(void) {
 inputs();unsigned hash=2166136261u;
 for(unsigned mode=0;mode<5;mode++) {
  __asm__ volatile("csrw frm,%0"::"r"(mode):"memory");
  for(unsigned i=0;i<JOBS;i++) {
   const struct job *j=&jobs[i];uint32_t a[32],b[32];
   for(unsigned k=0;k<32;k++)a[k]=b[k]=0x98765432u+k;
   g_vc_rgb_input=j->rgb;g_vc_words=g_vc_loads=0;
   __asm__ volatile("csrw fflags,zero":::"memory");
   unsigned an=reference(j,a),af;
   __asm__ volatile("csrr %0,fflags":"=r"(af)::"memory");
   unsigned aw=g_vc_words,al=g_vc_loads;
   g_vc_words=g_vc_loads=0;
   __asm__ volatile("csrw fflags,zero":::"memory");
   unsigned bn=candidate(j,b),bf;
   __asm__ volatile("csrr %0,fflags":"=r"(bf)::"memory");
   if(an!=bn || an>29 || af!=bf || aw!=g_vc_words || al!=g_vc_loads) {
    puts_uart("META ");hex(mode);hex(i);hex(an);hex(bn);hex(af);hex(bf);fail();
   }
   for(unsigned k=0;k<32;k++) {
    if(a[k]!=b[k]){puts_uart("WORD ");hex(mode);hex(i);hex(k);hex(a[k]);hex(b[k]);fail();}
    hash=fold(hash,a[k]);
   }
  }
 }
 __asm__ volatile("csrw fcsr,zero":::"memory");
 puts_uart("CHECK PASS 512 triangles x5 rounding modes; UV/clip words/counts/flags/counters ");hex(hash);put('\n');
 puts_uart("KERNEL PASS HAL init\n");return 0;
}
'''

def generate():
    OUT.mkdir(exist_ok=False);fw=OUT/'firmware';fw.mkdir()
    for name in ('start.S','link.ld','firmware.mif'):shutil.copy2(OLD/'firmware'/name,fw/name)
    header=(CAND/'gfx_gpu.h').read_text()
    structs='\n'.join(re.search(r'struct '+n+r' \{.*?\n\};',header,re.S).group()
                     for n in ('gfx_vc_vtx','gfx_vc_prepared'))
    source=PREFIX+structs+'\n'+function((BASE/'gfx_gpu.c').read_text(),'rgba_to_565')+'\n'
    source+=function((CAND/'gfx_pc.c').read_text(),'gfx_cpu_prepare_clip')+'\n'
    # Actual production front-end payload construction, keeping the old
    # cross-function float-record store boundary and the new asm boundary.
    old=(BASE/'gfx_pc.c').read_text();lo=old.index('                d->cx = RSPV_TO_FLOAT(v->x);',old.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run('))
    hi=old.index('                prepared |= bit;',lo)
    new=(CAND/'gfx_pc.c').read_text();lo2=new.index('            float u = 0.0f, tex_v = 0.0f;',new.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run('))
    hi2=new.index('        }\n        if (buf_vbo_len)',lo2)
    for name,typ,body in [('reference','gfx_vc_vtx',old[lo:hi]),('candidate','gfx_vc_prepared',new[lo2:hi2].replace('&payload[i]','d'))]:
        source+=f'''static __attribute__((noinline,noclone)) void {name}_prepare(struct {typ} *d,
 const struct LoadedVertex *v,int textured,float uofs,float vofs,float bias,int rgb_in,uint8_t alpha) {{
 const struct ColorCombiner storage={{{{0}}}};const struct ColorCombiner *comb=&storage;
'''+body+'}\n'
    for name,path,startmarker in [('reference',BASE/'gfx_gpu.c','void gfx_gpu_vtx_cache_tri('),('candidate',CAND/'gfx_gpu.c','void gfx_gpu_vtx_cache_batch(')]:
        text=path.read_text().split(startmarker,1)[1]
        a=text.index('    for (int k = 0; k < 3; k++) {');b=text.index('    _gpu_ring_commit(packet_words);',a)
        body=text[a:b]+'#endif\n'
        typ='const struct gfx_vc_vtx v[3]' if name=='reference' else 'const struct gfx_vc_prepared vertices[32]'
        source+=f'''static __attribute__((noinline,noclone)) unsigned {name}_emit({typ},
 const uint8_t slot[3],unsigned dirty_mask,uint32_t *out) {{
 uint32_t *packet=out;
'''+body+' return (unsigned)(packet-out);\n}\n'
    (fw/'kernel.c').write_text(source+TAIL)
    old=read(OLD/'manifest.json');exe=OLD/'Vtb_system'
    assert sha(exe)==old['simulator_sha256']
    for name,digest in old['unchanged_rtl_object_sha256'].items():assert sha(ROOT/name)==digest,name
    cmd=[str(fw) if t==str(OLD/'firmware') else t for t in old['compiler_command']]
    cmd=[('-mtune=sifive-5-series' if t=='-mtune=sifive-7-series' else t) for t in cmd]
    with (OUT/'build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    container=cmd[:cmd.index('riscv64-unknown-elf-gcc')]
    with (OUT/'compiler.txt').open('w') as f:subprocess.run([*container,'riscv64-unknown-elf-gcc','--version'],stdout=f,check=True)
    subprocess.run([*container,'riscv64-unknown-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],check=True)
    with (OUT/'kernel.asm').open('w') as f:subprocess.run([*container,'riscv64-unknown-elf-objdump','-d','kernel.elf'],stdout=f,check=True)
    manifest={'cpu_sha256':old['cpu_sha256'],'simulator_path':str(exe),'simulator_sha256':sha(exe),
       'unchanged_rtl_object_sha256':old['unchanged_rtl_object_sha256'],'compiler_command':cmd,
       'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in [BASE/'gfx_pc.c',BASE/'gfx_gpu.c',CAND/'gfx_pc.c',CAND/'gfx_gpu.c',CAND/'gfx_gpu.h',Path(__file__)]},
       'firmware_sha256':{p.name:sha(p) for p in fw.iterdir() if p.is_file()},'assembly_sha256':sha(OUT/'kernel.asm'),
       'scope':'Correctness of actual extracted frontend UV/clip preparation and ordered packet loops on unchanged retained A CPU/FPU RTL. Five legal rounding modes, all dirty masks, duplicate slots, depth clamps and untouched sentinels. Production fast-math and sifive-5-series tuning. No whole-game or component performance claim.'}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(OUT/'manifest.json')

def run():
    m=read(OUT/'manifest.json');exe=Path(m['simulator_path']);assert sha(exe)==m['simulator_sha256']
    for name,digest in m['firmware_sha256'].items():assert sha(OUT/'firmware'/name)==digest
    for name,digest in m['unchanged_rtl_object_sha256'].items():assert sha(ROOT/name)==digest
    log=OUT/'quiet.log';assert not log.exists()
    with log.open('w') as f:
        subprocess.run([str(exe),'kernel.bin','60000000'],cwd=OUT/'firmware',
                       env=dict(os.environ,SCAN_ENABLE='0'),stdout=f,stderr=subprocess.STDOUT,timeout=600,check=True)
    text=log.read_text()
    assert 'KERNEL PASS HAL init' in text and 'KERNEL FAIL' not in text,text[-2000:]
    assert 'CHECK PASS 512 triangles x5 rounding modes' in text
    assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
    result={'pass':True,'rounding_modes':5,'triangles':512,'comparisons':2560,'scope':m['scope'],
            'evidence_sha256':{p.name:sha(p) for p in [OUT/'manifest.json',log,OUT/'compiler.txt',OUT/'kernel.asm']}}
    (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':{'generate':generate,'run':run}[sys.argv[1]]()
