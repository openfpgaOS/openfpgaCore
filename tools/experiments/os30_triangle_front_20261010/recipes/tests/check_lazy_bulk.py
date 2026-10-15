#!/usr/bin/env python3
"""Extract the actual lazy helpers and pre-G_VTX lifetime block for host tests."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
T=Path(__file__).resolve().parent
source=T.parent/'work'/sys.argv[2]/'src/sm64/sm64/src/pc/gfx/gfx_pc.c'
text=source.read_text();sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
out=T/sys.argv[1];assert out.parent==T and not out.exists();out.mkdir()
def function(name):
 at=text.index(name+'(')
 while True:
  start=text.rfind('\n',0,at)+1;brace=text.find('{',at);semi=text.find(';',at)
  if semi<0 or brace<semi:break
  at=text.index(name+'(',at+1)
 depth=1;end=brace+1
 while depth:
  depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[start:end]
names=['load_alias_copy','load_alias_materialize','load_alias_materialize_all','load_alias_materialize_triangle','gfx_cpu_materialize_fog','gfx_cpu_flush_fog']
parts={name:function(name)for name in names}
vertex=text[text.index('static __attribute__((noinline)) void gfx_sp_vertex('):];start=vertex.index('    uint32_t overwritten =');end=vertex.index('\n#endif\n#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP',start)
parts['preload_lifetime']=vertex[start:end]
(out/'extracted.json').write_text(json.dumps(parts,indent=2)+'\n')
header='''#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define SM64_PERF_SCOPE(x) ((void)0)
#define LOAD_ALIAS_ENABLED 1
#define G_FOG 0x10000u
#define G_BL_CLR_FOG 3u
struct RGBA { uint8_t r,g,b,a; };
struct LoadedVertex {float x,y,z,w,w_inv,u,v;struct RGBA color;uint8_t clip_rej;};
static struct {struct LoadedVertex loaded_vertices[64];uint32_t geometry_mode;int16_t fog_mul,fog_offset;}rsp;
static struct {uint32_t other_mode_l;}rdp;
static bool configEnableFog;
static uint32_t raw[256],load_lazy_slots,cpu_fog_pending,gfx_front_stats[16];
static unsigned load_lazy_first;
static int16_t cpu_fog_mul,cpu_fog_offset;
#define LOAD_ALIAS_VIEW raw
'''
code=header+'\n\n'.join(parts[n]for n in names)+'\nstatic void before_load(size_t n_vertices,size_t dest_index){\n'+parts['preload_lifetime']+'\n(void)eager_fog;\n}\n'+(T/'lazy_bulk_test_tail.c').read_text()
(out/'test.c').write_text(code)
inputs={str(p):sha(p)for p in [Path(__file__),source,T/'lazy_bulk_test_tail.c',out/'extracted.json',out/'test.c']}
env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
commands=[]
for label,argv in [('build',['gcc','-std=c11','-O2','-ffp-contract=off','-fno-strict-aliasing','-fsanitize=address,undefined','-fno-omit-frame-pointer',out/'test.c','-o',out/'test']),('run',[out/'test'])]:
 with (out/(label+'.log')).open('x')as f:r=subprocess.run(list(map(str,argv)),stdout=f,stderr=subprocess.STDOUT,env=env)
 commands.append({'argv':list(map(str,argv)),'returncode':r.returncode,'log_sha256':sha(out/(label+'.log'))})
 assert not r.returncode,(label,(out/(label+'.log')).read_text()[-4000:])
report={'status':'PASS_ACTUAL_LAZY_C_FUNCTIONS','inputs_sha256':inputs,'commands':commands,'result':json.loads((out/'run.log').read_text()),'limitations':['Host lifetime/addressing validation, not target floating-point fog arithmetic or MMIO execution.','Actual helper bodies and actual pre-G_VTX block extracted; hardware success/failure and ordinary software load represented by harness.','Full new app source remains under development; final extracted-body equality must be checked again.']}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['result']))
