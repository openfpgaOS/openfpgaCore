#!/usr/bin/env python3
"""Compile the actual frozen C collection loop, with a test display decoder."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
T=Path(__file__).resolve().parent
src=T.parent/'work/sm64/src/sm64/sm64/src/pc/gfx/gfx_pc.c'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
out=T/sys.argv[1];assert out.parent==T and not out.exists();out.mkdir()
s=src.read_text();start=s.index('    const uint32_t owned = load_bram_slots & load_queued_safe;',s.index('static __attribute__((noinline)) Gfx *gfx_queued_front_run'))
end=s.index('collected:',start)+len('collected:')
block=s[start:end]
prefix='''#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
typedef struct {unsigned n,slots[6];} Gfx;
static uint32_t load_bram_slots,load_queued_safe,load_queued_hash[4];
static unsigned load_bram_first;
static unsigned gfx_geometry_decode(const Gfx *c,unsigned *slots){memcpy(slots,c->slots,sizeof c->slots);return c->n;}
static Gfx *collect(Gfx *cmd,uint32_t *jobs,unsigned *result_count){
'''
suffix='\n*result_count=count;return cmd;\n}\n'
test=out/'test.c';test.write_text(prefix+block+suffix+(T/'admission_tail.c').read_text())
paths=[Path(__file__),src,T/'admission_tail.c',test]
before={str(p):sha(p)for p in paths};report={'status':'RUNNING','inputs_sha256':before,'extracted_block_sha256':hashlib.sha256(block.encode()).hexdigest(),'scope':'Actual C metadata/atomic TRI2 collection loop; display-list decoder is a test stub. Hardware metadata separately tested on real LOAD SCAN.'}
for name,command in [('build',['cc','-std=c11','-O2','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(test),'-o',str(out/'test')]),('run',[str(out/'test')])]:
 with (out/(name+'.log')).open('x')as f:r=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1'))
 report[name]={'command':command,'returncode':r.returncode,'log_sha256':sha(out/(name+'.log'))};(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
 assert r.returncode==0,(name,(out/(name+'.log')).read_text())
assert before=={str(p):sha(p)for p in paths}
report.update(status='PASS_ACTUAL_QGF_ADMISSION_C_LOOP',result=json.loads((out/'run.log').read_text()),executable_sha256=sha(out/'test'))
(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['result']))
