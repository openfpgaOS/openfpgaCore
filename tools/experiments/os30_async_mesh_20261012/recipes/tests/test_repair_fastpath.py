#!/usr/bin/env python3
"""Extract the actual v2 callback and exercise reset/credit interleavings."""
from pathlib import Path
import hashlib,json,os,re,subprocess
E=Path(__file__).resolve().parent;W=E.parent;G=W/'src/sm64/sm64/src/pc/gfx/gfx_gpu.c';O=E/'repair-fastpath-v1'
assert not O.exists();O.mkdir();s=G.read_text()
def function(name):
 a=s.index('static int '+name+'(');b=s.index('{',a);depth=1;i=b+1
 while depth:depth+=(s[i]=='{')-(s[i]=='}');i+=1
 return s[a:i]
callback=function('gpu_async_repair_word');host=callback
host=host.replace('BRAM_GEO_REGS[0x28]=word;','write_mmio(0x28,word);')
host=re.sub(r'BRAM_GEO_REGS\[(0x[0-9a-f]+)\]',r'read_mmio(\1)',host)
prefix=r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <setjmp.h>
struct AsyncRepairSink {uint32_t epoch,token;};
static uint32_t epoch,token,ack;
static unsigned pending,credit,fault,accepted,attempts,reads,operations;
static unsigned reset_at,grant_at,new_after_reset,checks,faults;
static jmp_buf jumped;
#define C(x) do{++checks;assert(x);}while(0)
static void action(void) {
 ++operations;C(operations<10000);
 if(reset_at&&operations==reset_at) {epoch++;token+=2;ack=0;pending=new_after_reset;credit=1;accepted=0;fault=0;}
 if(grant_at&&operations==grant_at)credit=1;
}
static uint32_t read_mmio(unsigned reg) {
 action();++reads;
 if(reg==0x21)return (pending?2u:0u)|(fault?4u:0u)|((pending&&!fault&&ack==token&&credit)?64u:0u);
 if(reg==0x2e)return epoch;
 if(reg==0x25)return token;
 abort();
}
static void write_mmio(unsigned reg,uint32_t word) {
 action();C(reg==0x28);C(word==0x12345678);attempts++;
 /* Independent protocol model: reset revokes ACK; writes cannot escape the
  * current acknowledged repair. A stale in-flight store faults, never writes. */
 if(pending&&!fault&&ack==token&&credit){accepted++;credit--;}
 else fault=1;
}
static void gpu_async_fault(void) {++faults;longjmp(jumped,1);}
static void setup(void) {epoch=19;token=27;ack=27;pending=1;credit=1;fault=0;accepted=attempts=reads=operations=0;reset_at=grant_at=new_after_reset=0;}
'''
suffix=r'''
int main(void) {
 struct AsyncRepairSink sink={19,27};
 setup();C(gpu_async_repair_word(&sink,0x12345678)==1);C(accepted==1&&attempts==1&&reads==1);
 /* Arbitrarily delayed credit eventually accepts once, without re-ACK. */
 for(unsigned wait=1;wait<=512;wait++) {
  setup();credit=0;grant_at=wait;
  C(gpu_async_repair_word(&sink,0x12345678)==1);C(accepted==1&&attempts==1&&ack==27);
 }
 /* Reset at every read/store boundary, including READY-read then DATA-store.
  * New unacknowledged work must never receive the cancelled old word. */
 for(unsigned later=0;later<2;later++)for(unsigned start_credit=0;start_credit<2;start_credit++)for(unsigned at=1;at<=128;at++) {
  setup();credit=start_credit;grant_at=127;reset_at=at;new_after_reset=later;
  int result=gpu_async_repair_word(&sink,0x12345678);
  if(epoch!=sink.epoch) {
   C(!accepted);C(ack==0);C(result==0||(result==1&&fault));
   /* The real caller's final epoch/token guard suppresses END even if the
    * callback observed READY immediately before the coupled reset. */
   int send_end=result==1&&epoch==sink.epoch&&token==sink.token;C(!send_end);
  } else {C(result==1&&accepted==1);}
 }
 setup();credit=0;token+=2;ack=0;C(gpu_async_repair_word(&sink,0x12345678)==0);C(!attempts);
 setup();credit=0;pending=0;C(gpu_async_repair_word(&sink,0x12345678)==0);C(!attempts);
 setup();credit=0;fault=1;if(!setjmp(jumped)){gpu_async_repair_word(&sink,0x12345678);abort();}C(!attempts&&faults==1);
 printf("{\"status\":\"PASS\",\"checks\":%u,\"credit_delays\":512,\"reset_interleavings\":512}\n",checks);
}
'''
(O/'test.c').write_text(prefix+host+suffix)
cmd=['cc','-std=c11','-O1','-g','-fsanitize=address,undefined',str(O/'test.c'),'-o',str(O/'test')]
subprocess.run(cmd,check=True);r=subprocess.run([str(O/'test')],capture_output=True,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
(O/'run.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
result=json.loads(r.stdout);sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
result.update(scope='Actual C sink with independent MMIO ready/ACK/epoch model, token cancellation, output pressure, and coupled reset races; not an RTL equivalence or speed test.',inputs_sha256={str(p):sha(p)for p in [Path(__file__).resolve(),G,O/'test.c']},callback_sha256=hashlib.sha256(callback.encode()).hexdigest(),substitutions=['MMIO reads become read_mmio; the DATA store becomes write_mmio. No condition/control-flow transformation.'],commands=[cmd,[str(O/'test')]],sanitizers='ASan+UBSan; LSan disabled under ptrace')
(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
