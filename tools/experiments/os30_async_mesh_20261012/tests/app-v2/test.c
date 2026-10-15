
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
static int gpu_async_repair_word(void *opaque,uint32_t word) {
#ifndef OF_PC
    const struct AsyncRepairSink *sink=opaque;
    for(;;) {
        const uint32_t status=read_mmio(0x21);
        /* READY includes the matching acknowledged repair token. Reset
         * removes that gate, so no old word can enter a later unacked job.
         * The caller also rechecks epoch/token before sending END. */
        if(status&64u) {
            write_mmio(0x28,word);
            return 1;
        }
        if(read_mmio(0x2e)!=sink->epoch||read_mmio(0x25)!=sink->token)return 0;
        if(status&4u)gpu_async_fault();
        if(!(status&2u))return 0;
    }
#else
    (void)opaque;(void)word;return 0;
#endif
}
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
