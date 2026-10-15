#!/usr/bin/env python3
"""Test the extracted F19 publication policy across all logical ring positions."""
from pathlib import Path
import hashlib
import json
import os
import subprocess

E = Path(__file__).resolve().parent
W = E.parents[1]
I = W.parent
GPU = W / 'src/sm64/sm64/src/pc/gfx/gfx_gpu.c'
SDK = W / 'src/sdk/include/of_gpu.h'
OLD = I / 'work-app-v3/src/sm64/sm64/src/pc/gfx/gfx_gpu.c'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def function(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]

wait = function(GPU.read_text(), 'void gfx_gpu_mesh_wait_source(void)')
assert wait == function(OLD.read_text(), 'void gfx_gpu_mesh_wait_source(void)')
kick = function(SDK.read_text(), 'static inline void of_gpu_kick(void)')
now = function(SDK.read_text(), 'static inline void of_gpu_kick_now(void)')
packet = function(GPU.read_text(), 'void gfx_gpu_mesh_packet(uint32_t serial')
policy = packet[packet.index('    if(!_gpu_cpu_ring)'):packet.index('#else')]
source = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
static uint32_t _gpu_cmd_words, _gpu_unflushed_sync, _gpu_wrptr;
static uint32_t _gpu_ring_mask = 16383u;
static unsigned _gpu_cpu_ring, flushes, rd_reads, checks;
static uint32_t hardware_read_pointer;
#define OF_GPU_KICK_MIN_WORDS 512u
static uint32_t read_pointer(void) { ++rd_reads; return hardware_read_pointer; }
#define GPU_RING_RDPTR read_pointer()
static void _gpu_flush_cmd_stream(void) {
    ++flushes; _gpu_cmd_words = 0; _gpu_unflushed_sync = 0;
}
''' + now + '\n' + kick + '\nstatic void feed(void) {\n' + policy + '\n}\n' + r'''
static void check(int condition) { ++checks; assert(condition); }
int main(void) {
    const unsigned occupancies[] = {0, 1, 126, 127, 128, 129, 2047, 4095};
    const unsigned staged[] = {5, 31, 511, 512, 1024};
    unsigned cases = 0, wrapped = 0;
    for (unsigned published = 0; published < 4096; ++published)
      for (unsigned oi = 0; oi < sizeof(occupancies)/sizeof(occupancies[0]); ++oi)
       for (unsigned si = 0; si < sizeof(staged)/sizeof(staged[0]); ++si)
        for (unsigned cpu = 0; cpu < 2; ++cpu)
         for (unsigned sync = 0; sync < 2; ++sync) {
            unsigned occupancy = occupancies[oi], words = staged[si];
            unsigned rd = (published + 4096 - occupancy) % 4096;
            unsigned wr = (published + words) % 4096;
            _gpu_cmd_words = words; _gpu_unflushed_sync = sync;
            _gpu_cpu_ring = cpu; _gpu_wrptr = wr * 4;
            hardware_read_pointer = rd * 4;
            flushes = rd_reads = 0;
            feed(); ++cases; wrapped += published + words >= 4096;
            unsigned should_flush = !cpu || sync || words >= 512 || occupancy < 128;
            check(flushes == should_flush);
            check(rd_reads == (unsigned)(cpu && !sync && words < 512));
            check(_gpu_cmd_words == (should_flush ? 0 : words));
         }
    printf("{\"pass\":true,\"cases\":%u,\"checks\":%u,\"wrap_cases\":%u}\n",
           cases, checks, wrapped);
    return 0;
}
'''
(E / 'actual_feed_test.c').write_text(source)
command = ['gcc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
           str(E / 'actual_feed_test.c'), '-o', str(E / 'actual_feed_test')]
subprocess.run(command, check=True)
run = subprocess.run([str(E / 'actual_feed_test')], check=True, capture_output=True, text=True,
                     env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1'))
result = json.loads(run.stdout)
result.update(command=command, stderr=run.stderr,
              unchanged_v3_wait_sha256=hashlib.sha256(wait.encode()).hexdigest(),
              prior_status_test=str(I / 'work-app-v3/evidence/batching-liveness-v1/result.json'),
              prior_status_test_sha256=sha(I / 'work-app-v3/evidence/batching-liveness-v1/result.json'),
              limitation='Actual publication C tail/SDK kicks with mocked MMIO/flush. All logical positions, selected valid occupancies, both backend choices and sync paths. No full native timing claim.',
              input_sha256={str(p): sha(p) for p in [GPU, SDK, OLD, Path(__file__), E / 'actual_feed_test.c']})
(E / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
print(run.stdout, end='')
