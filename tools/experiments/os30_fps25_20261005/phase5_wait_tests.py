#!/usr/bin/env python3
"""Exercise extracted SDK wait paths and prove profiling-off RV32 code equality."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
P5 = ROOT / 'docs/measurements/os30-fps25-20261005/phase5'
CAND = P5 / 'candidate'
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase4/combined-cold/SM64/src/sdk/include/of_gpu.h'
OUT = P5 / 'wait-tests-v2'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def function(source, name):
    match = re.search(r'^(?:static inline |static |void )[^{;\n]*\b' + name + r'\(', source, re.M)
    assert match, name
    brace = source.index('{', match.end())
    pos, depth = brace + 1, 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos]

PREFIX = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
static uint32_t now, timer_reads, hooks;
static uint32_t status_values[32], ring_values[32], fence_values[32];
static unsigned ns, is, nr, ir, nf, iff;
static char events[1024]; static unsigned ne;
static void event(char c) { assert(ne+1<sizeof events); events[ne++]=c; events[ne]=0; }
static uint32_t status_read(void) { event('S'); now+=10; assert(is<ns); return status_values[is++]; }
static uint32_t ring_read(void) { event('R'); now+=10; assert(ir<nr); return ring_values[ir++]; }
static uint32_t fence_read(void) { event('F'); now+=10; assert(iff<nf); return fence_values[iff++]; }
#if TEST_PROFILE
static uint32_t fake_clock(void) { event('T'); timer_reads++; return now; }
#endif
static void cooperate(void) { event('H'); hooks++; now+=3; }
#define OF_GPU_WAIT_HOOK() cooperate()
#define OF_GPU_RING_SIZE 16384u
#define OF_GPU_BATCH_BUFFER_BYTES 16384u
#define OF_GPU_COMMAND_STREAM_BATCH_WORDS 4095u
#define GPU_STATUS_DMA_BUSY 4u
#define GPU_STATUS_DMA_DESC_FULL 64u
#define GPU_STATUS_CPU_RING_MODE 256u
#define GPU_STATUS_CPU_RING_OVERFLOW 512u
#define GPU_CTRL_CPU_RING_PUBLISH 8u
#define OF_PLATFORM_MISTER 2u
#define GPU_STATUS status_read()
#define GPU_RING_RDPTR ring_read()
#define GPU_FENCE_REACHED fence_read()
static uint32_t reg_port,reg_ctrl,reg_src,reg_len,reg_kick;
#define GPU_RING_WRPTR reg_port
#define GPU_CTRL reg_ctrl
#define GPU_DMA_SRC reg_src
#define GPU_DMA_LEN reg_len
#define GPU_DMA_KICK reg_kick
static uint32_t _gpu_batch_index,_gpu_batch_dma_addr,_gpu_batch_dma_base;
static uint32_t storage[8192],*_gpu_batch_buf_base=storage,*_gpu_batch_buf=storage;
static uint32_t _gpu_cmd_words,_gpu_unflushed_sync,_gpu_batch_inflight_mask;
static uint32_t _gpu_wrptr,_gpu_known_rdptr;
static const uint32_t _gpu_ring_mask=OF_GPU_RING_SIZE-1;
static uint32_t _gpu_dbg_dma_waits,_gpu_dbg_dma_spin_iters;
static uint32_t _gpu_dbg_ring_waits,_gpu_dbg_ring_spin_iters,_gpu_dbg_min_ring_free;
static int _gpu_cpu_ring,_gpu_publish_uncached;
struct caps {unsigned platform_id;}; static struct caps cap;
static const struct caps *of_get_caps(void) { return &cap; }
static void *of_uncached(void *p) { return p; }
static void _gpu_flush_cmd_cache_range(void *p,uint32_t n) { (void)p;(void)n;event('C'); }
static void _gpu_drain_cmd_writeback(uint32_t n) { (void)n;event('D'); }
'''

TESTS = r'''
static void reset(void) {
    now=timer_reads=hooks=ns=is=nr=ir=nf=iff=ne=0;events[0]=0;
    _gpu_wrptr=100;_gpu_known_rdptr=104;_gpu_cmd_words=0;
    _gpu_batch_inflight_mask=_gpu_unflushed_sync=_gpu_batch_index=0;
    _gpu_batch_dma_base=0x10000000;_gpu_batch_dma_addr=_gpu_batch_dma_base;
    _gpu_batch_buf_base=storage;_gpu_batch_buf=storage;
    _gpu_dbg_dma_waits=_gpu_dbg_dma_spin_iters=_gpu_dbg_ring_waits=_gpu_dbg_ring_spin_iters=0;
    _gpu_dbg_min_ring_free=OF_GPU_RING_SIZE;
    _gpu_cpu_ring=0;_gpu_publish_uncached=0;
    reg_port=reg_ctrl=reg_src=reg_len=reg_kick=0;
    OF_GPU_WAIT_PROFILE_RESET();
}
static void statuses(uint32_t a,uint32_t b,uint32_t c) {
    status_values[0]=a;status_values[1]=b;status_values[2]=c;ns=3;
}
static void rings(uint32_t a,uint32_t b,uint32_t c) {
    ring_values[0]=a;ring_values[1]=b;ring_values[2]=c;nr=3;
}
static void check_profile(uint32_t r,uint32_t d,uint32_t f,unsigned reads) {
#if TEST_PROFILE
    uint32_t rr,dd,ff;unsigned before=timer_reads;
    gpu_wait_prof_read(&rr,&dd,&ff);gpu_wait_prof_read(&rr,&dd,&ff);
    assert(timer_reads==before && rr==r && dd==d && ff==f);
    assert(timer_reads==reads);
#else
    (void)r;(void)d;(void)f;(void)reads;assert(timer_reads==0);
#endif
}
static void report(const char *name) {
    /* Remove only added timer observations: GPU polls/hooks/state must match
     * the unmodified-header control in both content and order. */
    char trace[1024];unsigned j=0;for(unsigned i=0;i<ne;i++)if(events[i]!='T')trace[j++]=events[i];trace[j]=0;
    printf("%s %s %u %u %u %u %u %u %u %u %u %u %u %u\n",name,trace,is,ir,iff,hooks,
      _gpu_dbg_dma_waits,_gpu_dbg_dma_spin_iters,_gpu_dbg_ring_waits,_gpu_dbg_ring_spin_iters,
      _gpu_batch_inflight_mask,_gpu_cmd_words,reg_ctrl,reg_port);
}
int main(void) {
    reset();statuses(0,0,0);_gpu_batch_inflight_mask=3;_gpu_wait_dma_idle_debug();
    assert(is==1 && hooks==0 && _gpu_batch_inflight_mask==0);check_profile(0,0,0,0);report("idle-ready");
    reset();statuses(4,4,0);_gpu_batch_inflight_mask=3;_gpu_wait_dma_idle_debug();
    assert(is==3 && hooks==2 && _gpu_dbg_dma_waits==1 && _gpu_dbg_dma_spin_iters==2);
    check_profile(0,26,0,2);report("idle-blocked");
    reset();_gpu_batch_inflight_mask=1;_gpu_wait_dma_desc_slot_debug();
    assert(is==0);check_profile(0,0,0,0);report("descriptor-skip");
    reset();statuses(0,0,0);_gpu_batch_inflight_mask=3;_gpu_wait_dma_desc_slot_debug();
    assert(is==1);check_profile(0,0,0,0);report("descriptor-ready");
    reset();statuses(64,64,0);_gpu_batch_inflight_mask=3;_gpu_wait_dma_desc_slot_debug();
    assert(is==3 && hooks==2 && _gpu_batch_inflight_mask==3);check_profile(0,26,0,2);report("descriptor-blocked");
    reset();_gpu_known_rdptr=200;_gpu_stream_reserve_words(4);
    assert(is==0 && ir==0);check_profile(0,0,0,0);report("ring-cached-ready");
    reset();statuses(0,0,0);rings(120,0,0);_gpu_ring_ensure(16);
    assert(is==1 && ir==1 && hooks==0 && _gpu_dbg_ring_waits==1);
    check_profile(0,0,0,0);report("ring-refreshed-ready");
    reset();statuses(0,0,0);rings(104,108,120);_gpu_stream_reserve_words(4);
    assert(is==1 && ir==3 && hooks==2 && _gpu_dbg_ring_spin_iters==2);
    check_profile(26,0,0,2);report("ring-blocked");
    /* Actual CPU-ring flush returns before DMA descriptor/cache submission,
     * but reserve still reaches the ring-space wait. MMIO stores are retained. */
    reset();_gpu_cpu_ring=1;_gpu_cmd_words=3;storage[0]=11;storage[1]=22;storage[2]=33;
    statuses(256,0,0);rings(104,108,120);_gpu_ring_ensure(16);
    assert(is==2 && ir==3 && hooks==2 && reg_port==33 && reg_ctrl==8 && _gpu_cmd_words==0);
    check_profile(26,0,0,2);report("cpu-ring-transport");
    /* Actual DMA flush: descriptor wait, reused-buffer wait, and ring wait
     * are successive episodes. Their categories must not overlap. */
    reset();_gpu_cmd_words=3;_gpu_batch_inflight_mask=3;
    uint32_t seq[]={64,0,4,0,0};memcpy(status_values,seq,sizeof seq);ns=5;
    rings(104,108,120);_gpu_ring_ensure(16);
    assert(is==5 && ir==3 && hooks==4 && _gpu_dbg_dma_waits==2);
    assert(reg_len==3 && reg_kick==1 && reg_src==0x10000000);
    check_profile(26,26,0,6);report("dma-and-ring");
    reset();fence_values[0]=7;nf=1;of_gpu_wait(7);
    assert(iff==1 && hooks==0);check_profile(0,0,0,0);report("fence-ready");
    reset();fence_values[0]=0xffffffff;fence_values[1]=0;fence_values[2]=1;nf=3;of_gpu_wait(1);
    assert(iff==3 && hooks==2);check_profile(0,0,26,2);report("fence-token-wrap");
    reset();now=0xfffffff0;statuses(0,0,0);rings(104,108,120);
#if TEST_PROFILE
    gpu_wait_prof_ticks[0]=0xfffffff0;
#endif
    _gpu_ring_ensure(16);check_profile(10,0,0,2);report("timer-counter-wrap");
    /* Reset/read APIs do not touch timer/MMIO and reset all epochs together. */
    unsigned nr0=timer_reads;OF_GPU_WAIT_PROFILE_RESET();
    assert(timer_reads==nr0);check_profile(0,0,0,TEST_PROFILE?2:0);report("profile-reset");
    return 0;
}
'''

FUNCS = ['_gpu_select_batch_buffer', '_gpu_wait_dma_idle_debug', '_gpu_wait_dma_desc_slot_debug',
         '_gpu_ring_free_now', '_gpu_ring_free_known', '_gpu_note_ring_free', '_gpu_flush_cmd_stream',
         '_gpu_ring_ensure', '_gpu_stream_reserve_words', 'of_gpu_fence_reached', 'of_gpu_wait']

def main():
    OUT.mkdir(exist_ok=False)
    baseline, candidate, app = BASE.read_text(), (CAND / 'of_gpu.h').read_text(), (CAND / 'gfx_gpu.c').read_text()
    hooks = candidate[candidate.index('/* Optional episode timing'):candidate.index('\n#ifndef OF_PC', candidate.index('/* Optional episode timing'))]
    app_start = app.index('#if defined(SM64_PERF_DIAG) && SM64_PERF_DIAG && !defined(OF_PC)')
    app_hooks = app[app_start:app.index('\n#endif', app_start) + len('\n#endif')]
    # Only the timer expression is substituted; helper arithmetic and macros
    # are copied from the app. Test profiles choose the same compile guards.
    app_hooks = app_hooks.replace('*(volatile uint32_t *)0x40000004', 'fake_clock()')
    app_read = function(app, 'gpu_wait_prof_read')
    runs = []
    expected = None
    for name, text, enabled in [('baseline', baseline, 0), ('off', candidate, 0), ('on', candidate, 1)]:
        body = '\n\n'.join(function(text, n) for n in FUNCS)
        # Host-only architecture substitution: retain memory barriers, omit
        # the RISC-V fence opcode. Target code equality below uses real fence.
        host_body = body.replace('__asm__ volatile("fence" ::: "memory");', '__asm__ volatile("" ::: "memory");')
        source = PREFIX + '\n#define SM64_PERF_DIAG TEST_PROFILE\n' + app_hooks + hooks
        source += '\n#if TEST_PROFILE\n' + app_read + '\n#endif\n' + host_body + TESTS
        path = OUT / (name + '.c');path.write_text(source)
        cmd = ['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-DTEST_PROFILE='+str(enabled), str(path), '-o', str(OUT/name)]
        subprocess.run(cmd, check=True)
        run = subprocess.run([str(OUT/name)], capture_output=True, text=True, check=True)
        (OUT/(name+'.log')).write_text(run.stdout)
        if expected is None:expected=run.stdout
        assert run.stdout == expected, name
        runs.append(dict(name=name, source_sha256=sha(path), binary_sha256=sha(OUT/name), log_sha256=sha(OUT/(name+'.log')), command=cmd))
    # RV32 optimized wait code must be byte-identical with default hooks.
    # No libc is linked; fake volatile registers stand in for MMIO.
    cross_prefix = '''#include <stdint.h>\n#include <stddef.h>\n#define OF_GPU_RING_SIZE 16384u\n#define GPU_STATUS_DMA_BUSY 4u\n#define GPU_STATUS_DMA_DESC_FULL 64u\n#define OF_GPU_WAIT_HOOK() ((void)0)\nstatic volatile uint32_t status_reg,rdptr_reg,fence_reg;\n#define GPU_STATUS status_reg\n#define GPU_RING_RDPTR rdptr_reg\n#define GPU_FENCE_REACHED fence_reg\nstatic uint32_t _gpu_batch_inflight_mask,_gpu_dbg_dma_waits,_gpu_dbg_dma_spin_iters,_gpu_dbg_ring_waits,_gpu_dbg_ring_spin_iters,_gpu_dbg_min_ring_free,_gpu_wrptr,_gpu_known_rdptr;\nstatic const uint32_t _gpu_ring_mask=16383;\nextern void _gpu_flush_cmd_stream(void);\n'''
    cross_funcs = [n for n in FUNCS if n not in ('_gpu_select_batch_buffer','_gpu_flush_cmd_stream','_gpu_stream_reserve_words')]
    # External state setup keeps descriptor/mask and known-ring branches live
    # in the target object, rather than allowing static-zero specialization.
    exported = '\nvoid test_state(uint32_t m,uint32_t w,uint32_t r){_gpu_batch_inflight_mask=m;_gpu_wrptr=w;_gpu_known_rdptr=r;}\nvoid test_idle(void){_gpu_wait_dma_idle_debug();}\nvoid test_desc(void){_gpu_wait_dma_desc_slot_debug();}\nvoid test_ring(uint32_t n){_gpu_ring_ensure(n);}\nvoid test_fence(uint32_t n){of_gpu_wait(n);}\n'
    for name, text in [('baseline', baseline), ('off', candidate)]:
        s = cross_prefix + hooks + '\n'.join(function(text,n) for n in cross_funcs) + exported
        path=OUT/(name+'-rv32.c');path.write_text(s)
        subprocess.run(['riscv64-elf-gcc','-O2','-march=rv32imafc','-mabi=ilp32f','-ffreestanding','-c',str(path),'-o',str(OUT/(name+'-rv32.o'))],check=True)
        subprocess.run(['riscv64-elf-objcopy','-O','binary','--only-section=.text',str(OUT/(name+'-rv32.o')),str(OUT/(name+'-rv32.text'))],check=True)
    assert (OUT/'baseline-rv32.text').read_bytes()==(OUT/'off-rv32.text').read_bytes()
    # The only reset insertion is in SDK initialization; no frame reset exists.
    assert 'OF_GPU_WAIT_PROFILE_RESET();' in function(candidate,'of_gpu_init')
    assert candidate.count('OF_GPU_WAIT_PROFILE_RESET();')==1
    # OF_PC diagnostic header remains compilable, and its app read stub is zero.
    pc=OUT/'ofpc.c';pc.write_text('#define OF_PC 1\n#include "'+str(CAND/'of_gpu.h')+'"\n'+app_read+'\nint main(void){uint32_t r=1,d=2,f=3;(void)_gpu_dbg_dma_waits;(void)_gpu_dbg_dma_spin_iters;(void)_gpu_dbg_ring_waits;(void)_gpu_dbg_ring_spin_iters;gpu_wait_prof_read(&r,&d,&f);return r|d|f;}\n')
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(pc),'-o',str(OUT/'ofpc')],check=True)
    subprocess.run([str(OUT/'ofpc')],check=True)
    result=dict(passed=True,header_sha256=sha(CAND/'of_gpu.h'),app_sha256=sha(CAND/'gfx_gpu.c'),baseline_header_sha256=sha(BASE),
                script_sha256=sha(Path(__file__)),runs=runs,poll_hook_state_order_identical=True,no_wait_timer_reads=0,
                timer_reads_per_blocking_episode=2,rv32_off_text_identical=True,rv32_off_text_sha256=sha(OUT/'off-rv32.text'),
                uint32_timer_and_accumulator_wrap_passed=True,init_reset_and_nonreset_read_passed=True,ofpc_compile_and_zero_read_passed=True,
                limits=['Extracted real functions with deterministic MMIO/timer mocks; not physical GPU timing.',
                        'Host replaces only RISC-V fence instruction and timer MMIO; off-code check uses actual RV32 fence.',
                        'First failed MMIO poll and final counter update are outside episode timing; cooperative wait-hook work is inside.',
                        'DMA/ring/fence metrics omit direct MMIO submission, upload/LUT waits, nonfatal probes and presentation acquire.'])
    (OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='runs'},indent=2))

if __name__=='__main__':main()
