#!/usr/bin/env python3
"""Capture actual raw-slot CPU producer and native-FMA command reference."""
import argparse,subprocess,sys,json,os
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
HERE=Path(__file__).resolve().parent;OUT=CAMP/'phase10/raw-slot-ring-v1';SOURCE=CAMP/'phase5/qsim';FMA=CAMP/'phase10/qsim-fma26-v1'
def prepare():
 q=OUT/'functional-qsim';q.mkdir(exist_ok=False)
 for p in SOURCE.iterdir():
  if p.suffix in ('.c','.h') or p.name=='Makefile':(q/p.name).write_bytes(p.read_bytes())
 for n in ['fpu.c','fpu_fast.h']:(q/n).write_bytes((FMA/n).read_bytes())
 p=q/'Makefile';p.write_text(p.read_text().replace('CFLAGS  := $(OPT)','CFLAGS  := -DQSIM_RETAINED_FMA26 $(OPT)'))
 (q/'raw_slot_model.h').write_bytes((HERE/'phase10_raw_slot_model.h').read_bytes())
 p=q/'gpu.c';s=p.read_text().replace('#include <string.h>','#include <string.h>\n#include <stdio.h>')
 s=s.replace('    /* published, not yet executed command words', '''    uint32_t raw_slot[32][6],raw_context[5],raw_valid,raw_projected;
    unsigned raw_context_valid,raw_internal;
    uint64_t raw_uploads,raw_contexts,raw_triangles,raw_rejected,raw_formatted,raw_draws;
    /* published, not yet executed command words''')
 anchor='static void exec_command(gpu_t *g, uint32_t hdr, const uint32_t *p)';assert s.count(anchor)==1;s=s.replace(anchor,'#include "raw_slot_model.h"\n\n'+anchor)
 anchor='    if (g->cmd_cb) g->cmd_cb(g->cmd_ctx, hdr, p);';assert s.count(anchor)==1
 s=s.replace(anchor,'''    if(op>=0x60&&op<=0x62){rs_execute(g,hdr,p);return;}
    if(!g->raw_internal&&(op==0x50||op==0x53||op==0x56))g->raw_projected=0;
'''+anchor);p.write_text(s)
 p=q/'gpu.h';p.write_text(p.read_text()+'\nvoid gpu_raw_slot_report(gpu_t *,const char *);\n')
 p=q/'main.c';s=p.read_text();a='    env_free(&E);';assert s.count(a)==1;s=s.replace(a,'    if(E.gpu)gpu_raw_slot_report(E.gpu,out);\n'+a);p.write_text(s)
 with (OUT/'functional-build.log').open('x') as log:subprocess.run(['make','-j4'],cwd=q,stdout=log,stderr=subprocess.STDOUT,check=True)
 write(OUT/'functional-model.json',dict(scope=__doc__,elf_sha256=sha(OUT/'app.elf'),simulator_sha256=sha(q/'qsim'),sources={p.name:sha(p) for p in q.iterdir() if p.is_file()},limits='CPU producer instruction cost measured; native-FMA raw cull/packet reference executes, but qsim legacy0x50..57 are not rasterized. Actual RTL replay required for rendering. No worker timing or hardware FPS claim.'))
def capture():
 m=module('raw_slot_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
 sys.argv=[str(Path(__file__)),'normal','--elf',str(OUT/'app.elf'),'--exe',str(OUT/'functional-qsim/qsim'),'--geometry','0','--audio'];m.main()
if __name__=='__main__':
 for action in sys.argv[1:]:globals()[action]()
