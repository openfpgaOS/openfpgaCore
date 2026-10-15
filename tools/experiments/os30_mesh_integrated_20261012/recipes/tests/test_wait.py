#!/usr/bin/env python3
"""Exercise the actual app wait and SDK kick bodies against a bounded credit model."""
from pathlib import Path
import hashlib
import json
import os
import subprocess

E = Path(__file__).resolve().parent
W = E.parents[1]
GPU = W / 'src/sm64/sm64/src/pc/gfx/gfx_gpu.c'
SDK = W / 'src/sdk/include/of_gpu.h'
BUILD = W / 'evidence/app-build-v3/build-result.json'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def function(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]

wait = function(GPU.read_text(), 'void gfx_gpu_mesh_wait_source(void)')
kick = function(SDK.read_text(), 'static inline void of_gpu_kick(void)')
now = function(SDK.read_text(), 'static inline void of_gpu_kick_now(void)')
packet = function(GPU.read_text(), 'void gfx_gpu_mesh_packet(uint32_t serial')
assert packet.count('of_gpu_kick();') == 1
assert 'of_gpu_kick_now();' not in packet
build = json.loads(BUILD.read_text())
assert sha(GPU) == build['source_sha256'][str(GPU.relative_to(W))]
assert sha(SDK) == build['source_sha256'][str(SDK.relative_to(W))]

prefix = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static unsigned sm64_mesh_front_capability;
static uint32_t gfx_mesh_stats[16];
static uint32_t _gpu_cmd_words, _gpu_unflushed_sync;
#define OF_GPU_KICK_MIN_WORDS 512u
static unsigned flushes, reads, checks, busy_polls, require_publish;
static uint32_t initial_status, published_words, regs[0x22];
static void _gpu_flush_cmd_stream(void) {
    ++flushes;
    published_words += _gpu_cmd_words;
    _gpu_cmd_words = 0;
    _gpu_unflushed_sync = 0;
}
static volatile uint32_t *read_regs(void) {
    ++reads;
    assert(reads < 100); /* An unpublished-credit deadlock must fail promptly. */
    if (reads == 1) regs[0x21] = initial_status;
    else {
        if (require_publish) assert(published_words == 5);
        regs[0x21] = reads <= busy_polls + 1 ? initial_status : initial_status & ~1u;
    }
    return regs;
}
#define BRAM_GEO_REGS read_regs()
'''
suffix = r'''
static void reset_model(void) {
    memset(gfx_mesh_stats, 0, sizeof(gfx_mesh_stats));
    flushes = reads = busy_polls = require_publish = 0;
    published_words = _gpu_cmd_words = _gpu_unflushed_sync = 0;
    sm64_mesh_front_capability = 1;
}
static void check(int condition) { ++checks; assert(condition); }
int main(void) {
    /* Probe absent/unknown: no MMIO access and no publication. */
    for (unsigned capability = 0; capability <= 2; capability += 2) {
        reset_model(); sm64_mesh_front_capability = capability;
        initial_status = 0x21; _gpu_cmd_words = 5;
        gfx_gpu_mesh_wait_source();
        check(reads == 0 && flushes == 0 && _gpu_cmd_words == 5);
    }
    /* Every low status combination, with arbitrary live-count bits, must
       publish exactly once iff owned+validated and not yet copying. */
    for (unsigned status = 0; status < 4096; ++status) {
        reset_model(); initial_status = status; busy_polls = 7;
        _gpu_cmd_words = 5;
        gfx_gpu_mesh_wait_source();
        unsigned should_publish = (status & 1) && (status & 32) && !(status & 128);
        check(flushes == should_publish);
        check(gfx_mesh_stats[13] == ((status & 1) ? 7u : 0u));
        check(reads == ((status & 1) ? 9u : 2u));
    }
    /* Small pending marker: normal kick intentionally retains it. Credit
       cannot progress until wait_source explicitly publishes those5 words. */
    reset_model(); initial_status = 0x21; busy_polls = 11;
    _gpu_cmd_words = 5; require_publish = 1;
    of_gpu_kick(); check(flushes == 0 && _gpu_cmd_words == 5);
    gfx_gpu_mesh_wait_source();
    check(flushes == 1 && published_words == 5 && gfx_mesh_stats[13] == 11);
    /* Already-copying owner: complete reservation removes GPU dependency. */
    reset_model(); initial_status = 0xa1; busy_polls = 13; _gpu_cmd_words = 5;
    gfx_gpu_mesh_wait_source();
    check(flushes == 0 && _gpu_cmd_words == 5 && gfx_mesh_stats[13] == 13);
    /* Raw/preflight owner has not been armed; no marker can unblock it. */
    reset_model(); initial_status = 1; busy_polls = 9; _gpu_cmd_words = 5;
    gfx_gpu_mesh_wait_source(); check(flushes == 0 && gfx_mesh_stats[13] == 9);
    /* Verify actual SDK threshold and fence/flip synchronization override. */
    const unsigned words[] = {0, 1, 5, 511, 512, 513, 1024};
    for (unsigned i = 0; i < sizeof(words)/sizeof(words[0]); ++i)
      for (unsigned sync = 0; sync < 2; ++sync) {
        reset_model(); _gpu_cmd_words = words[i]; _gpu_unflushed_sync = sync;
        of_gpu_kick();
        check(flushes == (unsigned)(words[i] >= 512 || sync));
      }
    printf("{\"pass\":true,\"checks\":%u,\"status_combinations\":4096}\n", checks);
    return 0;
}
'''
source = prefix + now + '\n' + kick + '\n' + wait + '\n' + suffix
(E / 'actual_wait_test.c').write_text(source)
command = ['gcc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
           str(E / 'actual_wait_test.c'), '-o', str(E / 'actual_wait_test')]
subprocess.run(command, check=True)
environment = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
run = subprocess.run([str(E / 'actual_wait_test')], check=True, text=True, capture_output=True, env=environment)
result = json.loads(run.stdout)
result.update(command=command, stderr=run.stderr, sanitizer_environment={'ASAN_OPTIONS': 'detect_leaks=0'},
              sanitizer_note='LeakSanitizer cannot run under the execution harness ptrace; this test allocates no dynamic memory. AddressSanitizer and UBSan remain enabled.',
              limitation='Actual C bodies and SDK threshold logic; mocked publication/credit progress. Full RTL/native liveness remains a separate validation.',
              input_sha256={str(p): sha(p) for p in [GPU, SDK, BUILD, Path(__file__), E / 'actual_wait_test.c']},
              extracted_wait_sha256=hashlib.sha256(wait.encode()).hexdigest())
(E / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
print(run.stdout, end='')
