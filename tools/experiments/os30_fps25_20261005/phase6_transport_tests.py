#!/usr/bin/env python3
"""Exercise real F8 transport functions and compare profiling-off RV32 code."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
P6 = ROOT / 'docs/measurements/os30-fps25-20261005/phase6'
CAND = P6 / 'candidate'
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase5/candidate/of_gpu.h'
OUT = P6 / 'transport-tests'


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
static uint32_t status_values[32];
static unsigned ns, is;
static char events[20000]; static unsigned ne;
static void event(char c) { assert(ne+1<sizeof events); events[ne++]=c; events[ne]=0; }
static uint32_t status_read(void) { event('S'); now+=10; assert(is<ns); return status_values[is++]; }
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
static volatile uint32_t reg_port;
static uint32_t reg_ctrl,reg_src,reg_len,reg_kick;
#define GPU_RING_WRPTR reg_port
#define GPU_CTRL reg_ctrl
#define GPU_DMA_SRC reg_src
#define GPU_DMA_LEN reg_len
#define GPU_DMA_KICK reg_kick
static uint32_t _gpu_batch_index,_gpu_batch_dma_addr,_gpu_batch_dma_base;
static uint32_t storage[8192],*_gpu_batch_buf_base=storage,*_gpu_batch_buf=storage;
static uint32_t uncached_storage[8192], cpu_words[4096];
static unsigned cpu_count, uncached_count;
static uint32_t _gpu_cmd_words,_gpu_unflushed_sync,_gpu_batch_inflight_mask;
static uint32_t _gpu_dbg_dma_waits,_gpu_dbg_dma_spin_iters;
static int _gpu_cpu_ring,_gpu_publish_uncached;
struct caps {unsigned platform_id;}; static struct caps cap;
static const struct caps *of_get_caps(void) { event('P');now+=2;return &cap; }
static void *of_uncached(void *p) { event('U');now+=1;assert(p==storage);return uncached_storage; }
static void _gpu_flush_cmd_cache_range(void *p,uint32_t n) { assert(p==storage && n==_gpu_cmd_words*4);event('C');now+=11; }
static void _gpu_drain_cmd_writeback(uint32_t n) { assert(n==_gpu_cmd_words*4);event('D');now+=13; }
/* Host MMIO observation adapters retain each actual store and add a trace.
 * Only adapter-local mock time advances; production has no per-word clocks. */
static void port_store(volatile uint32_t *p,uint32_t v) {
    assert(p==&reg_port && cpu_count<4096);*p=v;cpu_words[cpu_count++]=v;event('W');now+=3;
}
static void uncached_store(volatile uint32_t *p,uint32_t w,uint32_t v) {
    assert(p==uncached_storage && w==uncached_count);p[w]=v;uncached_count++;event('V');now+=2;
}
static void ctrl_store(uint32_t v) {reg_ctrl=v;event('Q');now+=7;}
static void src_store(uint32_t v) {reg_src=v;event('A');now+=2;}
static void len_store(uint32_t v) {reg_len=v;event('L');now+=2;}
static void kick_store(uint32_t v) {reg_kick=v;event('K');now+=2;}
static void real_fence(void) {__asm__ volatile("" ::: "memory");event('B');now++;}
'''

TESTS = r'''
static void reset(void) {
    now=timer_reads=hooks=ns=is=ne=0;events[0]=0;
    _gpu_cmd_words=_gpu_batch_inflight_mask=_gpu_unflushed_sync=_gpu_batch_index=0;
    _gpu_batch_dma_base=0x10000000;_gpu_batch_dma_addr=_gpu_batch_dma_base;
    _gpu_batch_buf_base=storage;_gpu_batch_buf=storage;
    _gpu_dbg_dma_waits=_gpu_dbg_dma_spin_iters=0;
    _gpu_cpu_ring=0;_gpu_publish_uncached=0;cap.platform_id=1;
    reg_port=reg_ctrl=reg_src=reg_len=reg_kick=cpu_count=uncached_count=0;
    memset(uncached_storage,0,sizeof uncached_storage);
    for(unsigned i=0;i<8192;i++)storage[i]=0x9e3779b9u*(i+1u);
    OF_GPU_WAIT_PROFILE_RESET();
}
static void check_profile(uint32_t t,uint32_t w,uint32_t b,uint32_t c,
                          uint32_t wait,unsigned reads) {
#if TEST_PROFILE
    uint32_t tt,ww,bb,cc,rr,dd,ff;unsigned before=timer_reads, old_ne=ne;
    gpu_submit_prof_read(&tt,&ww,&bb,&cc);
    gpu_submit_prof_read(&tt,&ww,&bb,&cc);
    gpu_wait_prof_read(&rr,&dd,&ff);
    assert(timer_reads==before && ne==old_ne && tt==t && ww==w && bb==b && cc==c);
    assert(rr==0 && ff==0 && dd==wait && timer_reads==reads);
#else
    (void)t;(void)w;(void)b;(void)c;(void)wait;(void)reads;assert(timer_reads==0);
#endif
}
static void report(const char *name,unsigned words) {
    /* F7 control and F8 on/off must have identical every-word payload,
     * register-write, status-poll, cache-call, wait-hook and state ordering. */
    uint32_t trace=2166136261u,payload=2166136261u;
    for(unsigned i=0;i<ne;i++)if(events[i]!='T')trace=(trace^(unsigned char)events[i])*16777619u;
    for(unsigned i=0;i<cpu_count;i++)payload=(payload^cpu_words[i])*16777619u;
    for(unsigned i=0;i<uncached_count;i++)payload=(payload^uncached_storage[i])*16777619u;
    printf("%s %u %u %u %u %u %u %u %u %u %u %u %u %u %u %u %u %u %u\n",
      name,words,trace,payload,is,hooks,_gpu_dbg_dma_waits,_gpu_dbg_dma_spin_iters,
      _gpu_batch_inflight_mask,_gpu_cmd_words,_gpu_batch_index,_gpu_batch_dma_addr,
      reg_ctrl,(uint32_t)reg_port,reg_src,reg_len,reg_kick,cpu_count,uncached_count);
}
static void cpu_case(unsigned n) {
    reset();_gpu_cpu_ring=1;_gpu_cmd_words=n;_gpu_unflushed_sync=1;
    status_values[0]=256;ns=1;_gpu_flush_cmd_stream();
    assert(cpu_count==n && !uncached_count && reg_port==storage[n-1]);
    assert(!memcmp(cpu_words,storage,n*4) && reg_ctrl==8 && is==1);
    assert(_gpu_cmd_words==0 && _gpu_unflushed_sync==0 && _gpu_batch_index==1);
    assert(_gpu_batch_buf==storage+4096 && _gpu_batch_inflight_mask==0);
    check_profile(3*n+17,n,1,1,0,2);report("cpu",n);
}
static void dma_case(unsigned n,int uncached,int discover,int block) {
    reset();_gpu_cmd_words=n;_gpu_unflushed_sync=1;
    _gpu_publish_uncached=discover?-1:uncached;cap.platform_id=uncached?2:1;
    if(block) {
        _gpu_batch_inflight_mask=3;
        uint32_t seq[]={64,64,0,4,4,0};memcpy(status_values,seq,sizeof seq);ns=6;
    }
    _gpu_flush_cmd_stream();
    assert(!cpu_count && reg_len==n && reg_kick==1 && reg_src==0x10000000);
    if(uncached)assert(uncached_count==n && !memcmp(uncached_storage,storage,n*4));
    else assert(uncached_count==0);
    assert(_gpu_cmd_words==0 && _gpu_unflushed_sync==0 && _gpu_batch_index==1);
    assert(_gpu_batch_buf==storage+4096 && _gpu_batch_inflight_mask==(block?0u:1u));
    assert(_gpu_publish_uncached==uncached);
    check_profile((uncached?2*n+2:24)+(discover?2:0),n,1,0,block?52:0,block?6:2);
    report(uncached?(block?"uncached-blocked":"uncached"):(block?"cached-blocked":"cached"),n);
}
int main(void) {
    for(unsigned cpu=0;cpu<2;cpu++) {
        reset();_gpu_cpu_ring=cpu;_gpu_batch_buf=NULL;_gpu_flush_cmd_stream();
        assert(ne==0 && is==0);check_profile(0,0,0,0,0,0);report("empty",cpu);
    }
    for(unsigned n=1;n<=65;n++) {
        cpu_case(n);dma_case(n,0,0,0);dma_case(n,1,0,0);
    }
    unsigned more[]={127,128,129,1024,4095};
    for(unsigned i=0;i<sizeof more/sizeof more[0];i++)cpu_case(more[i]);
    dma_case(33,0,1,0);dma_case(33,1,1,0);
    dma_case(33,0,0,1);dma_case(33,1,0,1);
    /* Natural clock, duration accumulator, words and both batch counts wrap. */
    reset();now=0xfffffff8u;_gpu_cpu_ring=1;_gpu_cmd_words=3;
    status_values[0]=256;ns=1;
#if TEST_PROFILE
    gpu_submit_prof_ticks=0xfffffff0u;gpu_submit_prof_words=0xffffffffu;
    gpu_submit_prof_batches=0xffffffffu;gpu_submit_prof_cpu_batches=0xffffffffu;
#endif
    _gpu_flush_cmd_stream();check_profile(10,2,0,0,0,2);report("all-wrap",3);
    /* Reset does not read timer/MMIO; repeated read does not clear totals. */
    unsigned before=timer_reads, old_ne=ne;OF_GPU_WAIT_PROFILE_RESET();
    assert(before==timer_reads && old_ne==ne);check_profile(0,0,0,0,0,TEST_PROFILE?2:0);
    report("reset",0);
    /* Two CPU batches and one DMA batch accumulate without reader resets. */
    reset();_gpu_cpu_ring=1;_gpu_cmd_words=1;status_values[0]=256;status_values[1]=256;ns=2;
    _gpu_flush_cmd_stream();check_profile(20,1,1,1,0,2);
    _gpu_select_batch_buffer(0);_gpu_cmd_words=2;_gpu_flush_cmd_stream();
    check_profile(43,3,2,2,0,4);
    _gpu_select_batch_buffer(0);_gpu_cpu_ring=0;_gpu_cmd_words=4;_gpu_flush_cmd_stream();
    check_profile(67,7,3,2,0,6);report("mixed",7);
    return 0;
}
'''

FUNCS = ['_gpu_select_batch_buffer', '_gpu_wait_dma_idle_debug',
         '_gpu_wait_dma_desc_slot_debug', '_gpu_flush_cmd_stream']

CROSS_PREFIX = r'''
#include <stdint.h>
#include <stddef.h>
#define OF_GPU_BATCH_BUFFER_BYTES 16384u
#define GPU_STATUS_DMA_BUSY 4u
#define GPU_STATUS_DMA_DESC_FULL 64u
#define GPU_STATUS_CPU_RING_MODE 256u
#define GPU_STATUS_CPU_RING_OVERFLOW 512u
#define GPU_CTRL_CPU_RING_PUBLISH 8u
#define OF_PLATFORM_MISTER 2u
#define OF_GPU_WAIT_HOOK() ((void)0)
static volatile uint32_t status_reg,port_reg,ctrl_reg,src_reg,len_reg,kick_reg;
#define GPU_STATUS status_reg
#define GPU_RING_WRPTR port_reg
#define GPU_CTRL ctrl_reg
#define GPU_DMA_SRC src_reg
#define GPU_DMA_LEN len_reg
#define GPU_DMA_KICK kick_reg
static uint32_t _gpu_batch_index,_gpu_batch_dma_addr,_gpu_batch_dma_base;
static uint32_t *_gpu_batch_buf_base,*_gpu_batch_buf;
static uint32_t _gpu_cmd_words,_gpu_unflushed_sync,_gpu_batch_inflight_mask;
static uint32_t _gpu_dbg_dma_waits,_gpu_dbg_dma_spin_iters;
static int _gpu_cpu_ring,_gpu_publish_uncached;
struct caps {unsigned platform_id;};
extern const struct caps *of_get_caps(void);
extern void *of_uncached(void *p);
extern void _gpu_flush_cmd_cache_range(void *p,uint32_t n);
extern void _gpu_drain_cmd_writeback(uint32_t n);
'''


def host_body(text):
    body = '\n\n'.join(function(text, n) for n in FUNCS)
    # The only substitutions besides the clock are observation adapters for
    # original volatile stores and the target-only fence opcode. The actual
    # SDK control flow, word loads, grouping, branches and polling are retained.
    body, n = re.subn(r'\*port = ([^;]+);', r'port_store(port, \1);', body)
    assert n == 17
    body = body.replace('dst[w] = _gpu_batch_buf[w];', 'uncached_store(dst, w, _gpu_batch_buf[w]);')
    for reg, fn in [('GPU_CTRL', 'ctrl_store'), ('GPU_DMA_SRC', 'src_store'),
                    ('GPU_DMA_LEN', 'len_store'), ('GPU_DMA_KICK', 'kick_store')]:
        body, n = re.subn(r'\b' + reg + r'\s*=\s*([^;]+);', fn + r'(\1);', body)
        assert n == 1, reg
    return body.replace('__asm__ volatile("fence" ::: "memory");', 'real_fence();')


def main():
    OUT.mkdir(exist_ok=False)
    baseline, candidate = BASE.read_text(), (CAND / 'of_gpu.h').read_text()
    app = (CAND / 'gfx_gpu.c').read_text()
    start = candidate.index('/* Optional episode timing')
    hooks = candidate[start:candidate.index('\n#ifndef OF_PC', start)]
    start = app.index('#if defined(SM64_PERF_DIAG) && SM64_PERF_DIAG && !defined(OF_PC)')
    app_hooks = app[start:app.index('\n#endif', start)+len('\n#endif')]
    app_hooks = app_hooks.replace('*(volatile uint32_t *)0x40000004', 'fake_clock()')
    reads = function(app, 'gpu_wait_prof_read') + '\n' + function(app, 'gpu_submit_prof_read')
    runs, expected = [], None
    for name, text, enabled in [('baseline', baseline, 0), ('off', candidate, 0), ('on', candidate, 1)]:
        source = PREFIX + '\n#define SM64_PERF_DIAG TEST_PROFILE\n' + app_hooks + hooks
        source += '\n#if TEST_PROFILE\n' + reads + '\n#endif\n' + host_body(text) + TESTS
        path = OUT / (name + '.c');path.write_text(source)
        cmd = ['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-DTEST_PROFILE='+str(enabled),
               str(path), '-o', str(OUT/name)]
        subprocess.run(cmd, check=True)
        run = subprocess.run([str(OUT/name)], capture_output=True, text=True, check=True)
        (OUT/(name+'.log')).write_text(run.stdout)
        if expected is None:expected=run.stdout
        assert run.stdout == expected, name
        runs.append(dict(name=name, source_sha256=sha(path), binary_sha256=sha(OUT/name),
                         log_sha256=sha(OUT/(name+'.log')), command=cmd))
    # Compare actual target code, including all store and fence instructions,
    # with no host observation substitutions. Public setup keeps mode, pointer,
    # data, inflight and cached platform decision dynamic at compile time.
    for name, path in [('baseline', BASE), ('off', CAND/'of_gpu.h')]:
        source = CROSS_PREFIX + hooks + '\n'.join(function(path.read_text(),n) for n in FUNCS) + r'''
void transport_state(uint32_t *p,uint32_t addr,uint32_t words,uint32_t mask,int cpu,int uc) {
    _gpu_batch_buf_base=p;_gpu_batch_dma_base=addr;_gpu_select_batch_buffer(0);
    _gpu_cmd_words=words;_gpu_batch_inflight_mask=mask;
    _gpu_cpu_ring=cpu;_gpu_publish_uncached=uc;
}
void transport_run(void) {_gpu_flush_cmd_stream();}
'''
        c=OUT/(name+'-rv32.c');c.write_text(source)
        obj=OUT/(name+'-rv32.o')
        cmd=['riscv64-elf-gcc','-O2','-march=rv32imafc','-mabi=ilp32f','-ffreestanding',
             '-c',str(c),'-o',str(obj)]
        subprocess.run(cmd,check=True)
        subprocess.run(['riscv64-elf-objcopy','-O','binary','--only-section=.text',
                        str(obj),str(OUT/(name+'-rv32.text'))],check=True)
    assert (OUT/'baseline-rv32.text').read_bytes()==(OUT/'off-rv32.text').read_bytes()
    # Reset remains at the one SDK-init call, and every timer hook is outside
    # the word loops: exactly two begins/two ends for mutually exclusive paths.
    assert candidate.count('OF_GPU_WAIT_PROFILE_RESET();')==1
    assert 'OF_GPU_WAIT_PROFILE_RESET();' in function(candidate,'of_gpu_init')
    submit=function(candidate,'_gpu_flush_cmd_stream')
    assert submit.count('OF_GPU_SUBMIT_PROFILE_BEGIN(')==2
    assert submit.count('OF_GPU_SUBMIT_PROFILE_END(')==2
    # All non-hook SDK operations are exactly identical to the F7 header.
    strip = re.sub(r'^\s*OF_GPU_SUBMIT_PROFILE_(?:BEGIN|END)\([^\n]*\);\n', '', submit, flags=re.M)
    assert strip==function(baseline,'_gpu_flush_cmd_stream')
    # OF_PC readers remain harmless zero stubs.
    pc=OUT/'ofpc.c';pc.write_text('#define OF_PC 1\n#include <stdint.h>\n'+reads+r'''
int main(void){uint32_t a=1,b=2,c=3,d=4;gpu_submit_prof_read(&a,&b,&c,&d);
if(a|b|c|d) {return 1;}
gpu_wait_prof_read(&a,&b,&c);return a|b|c;}
''')
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(pc),'-o',str(OUT/'ofpc')],check=True)
    subprocess.run([str(OUT/'ofpc')],check=True)
    result=dict(passed=True,header_sha256=sha(CAND/'of_gpu.h'),app_sha256=sha(CAND/'gfx_gpu.c'),
                baseline_header_sha256=sha(BASE),script_sha256=sha(Path(__file__)),runs=runs,
                scenarios_per_configuration=len(expected.splitlines()),
                cpu_word_lengths=list(range(1,66))+[127,128,129,1024,4095],
                command_poll_and_state_order_identical=True,empty_batch_timer_reads=0,
                timer_reads_per_nonempty_batch=2,rv32_off_text_identical=True,
                rv32_off_text_sha256=sha(OUT/'off-rv32.text'),
                uint32_timer_ticks_words_and_batch_wrap_passed=True,
                cached_uncached_discovery_descriptor_idle_paths_passed=True,
                init_reset_and_nonreset_read_passed=True,ofpc_zero_read_passed=True,
                limits=['Extracted actual SDK functions with deterministic MMIO and cache-service mocks; not physical timing.',
                        'Host observes every original MMIO/uncached store with adapters; RV32 equality uses original stores and fences.',
                        'CPU timing includes loads, MMIO stores, status check and publish. DMA timing covers source visibility only.',
                        'Added clock access/counter update costs perturb diagnostic execution; this is not an FPS optimization.'])
    (OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('runs','cpu_word_lengths')},indent=2))


if __name__=='__main__':main()
