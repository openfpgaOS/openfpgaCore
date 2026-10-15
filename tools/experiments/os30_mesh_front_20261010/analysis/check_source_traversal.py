#!/usr/bin/env python3
"""Execute the actual DL switch/recursive traversal with passive stub handlers.

The sole inserted statement records the command before dispatch. Handler
semantics are deliberately stubbed: this proves command order/consumption only.
"""
from pathlib import Path
import hashlib,json,re,struct,subprocess
H=Path(__file__).resolve().parent;Q=H.parents[1]/'queued-front-v1'
A=Q/'work/sm64-v2';src=A/'src/sm64/sm64/src/pc/gfx/gfx_pc.c';inc=A/'src/sm64/sm64/include';gbi=inc/'PR/gbi.h'
s=src.read_text();start=s.index('static void gfx_run_dl(Gfx* cmd) {');end=s.index('\nstatic void gfx_sp_reset()',start);body=s[start:end]
assert body.count('for (;;) {')==1
instrumented=body.replace('for (;;) {','for (;;) {\n        observe(cmd);',1)
macro_cmd=['gcc','-E','-dM','-DF3DEX_GBI_2E','-I'+str(inc),str(gbi)]
macros=subprocess.check_output(macro_cmd,text=True)
keep='\n'.join(line for line in macros.splitlines() if re.match(r'#define (G_|F3DEX_)',line))
names=set(re.findall(r'\b(gfx_\w+)\s*\(',body))-{'gfx_run_dl'}
head='''#define _GNU_SOURCE 1
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <assert.h>
typedef struct { struct { uint32_t w0,w1; } words; } Gfx;
#define C0(pos,width) ((cmd->words.w0>>(pos))&((1U<<(width))-1))
#define C1(pos,width) ((cmd->words.w1>>(pos))&((1U<<(width))-1))
#define seg_addr(w) ((void*)(uintptr_t)(w))
static FILE *output;
static unsigned long count;
static void observe(Gfx *p){uint32_t v[3]={(uint32_t)(uintptr_t)p,p->words.w0,p->words.w1};assert(++count<200000);assert(fwrite(v,4,3,output)==3);}
'''
tail='''
int main(int argc,char**argv) {
 assert(argc==4);FILE *f=fopen(argv[1],"rb");assert(f);
 size_t n=64u*1024*1024;void *p=mmap((void*)0x10000000,n,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0);assert(p==(void*)0x10000000);
 assert(fread(p,1,n,f)==n);assert(!fclose(f));assert(!mprotect(p,n,PROT_READ));
 output=fopen(argv[3],"wb");assert(output);gfx_run_dl((Gfx*)(uintptr_t)strtoul(argv[2],0,0));assert(!fclose(output));printf("%lu commands\\n",count);return 0;
}
'''
c=H/'source-traversal.c';exe=H/'source-traversal';out=H/'source-traversal.bin'
c.write_text(head+keep+'\n'+'\n'.join('#define '+n+'(...) ((void)0)' for n in sorted(names))+'\n'+instrumented+tail)
cmd=['gcc','-std=c11','-O2','-Wall','-Wextra',str(c),'-o',str(exe)]
r=subprocess.run(cmd,capture_output=True,text=True);(H/'source-traversal-build.log').write_text(r.stdout+r.stderr);r.check_returncode()
snap=Q/'fixture-v2/capture/run-reference/snapshot';state=json.loads((snap/'state.json').read_text())
run=subprocess.run([str(exe),str(snap/'sdram.bin'),str(state['commands']),str(out)],capture_output=True,text=True);run.check_returncode()
events=json.loads((H/'coverage-frame360-events.json').read_text());expected=b''.join(struct.pack('<III',e['address'],e['w0'],e['w1']) for e in events)
assert out.read_bytes()==expected
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
record={'passed':True,'commands':len(events),'source_function_sha256':hashlib.sha256(body.encode()).hexdigest(),'compile_command':cmd,'macro_command':macro_cmd,'run_stdout':run.stdout,'inputs_sha256':{str(p):sha(p) for p in [Path(__file__).resolve(),src,gbi,inc/'PR/ultratypes.h',c,exe,out,snap/'sdram.bin',H/'coverage-frame360-events.json']},'scope':'Actual source switch and recursive G_DL/tail branch/rectangle pointer consumption, with one passive observe insertion. Renderer handlers are stubbed; no arithmetic/material execution claim.'}
(H/'source-traversal-proof.json').write_text(json.dumps(record,indent=2)+'\n');print(run.stdout.strip())
