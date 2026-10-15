
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
static inline void of_gpu_kick_now(void) {
    _gpu_flush_cmd_stream();
}
static inline void of_gpu_kick(void) {
    if (_gpu_cmd_words >= OF_GPU_KICK_MIN_WORDS || _gpu_unflushed_sync)
        _gpu_flush_cmd_stream();
}
void gfx_gpu_mesh_wait_source(void) {
#ifndef OF_PC
    if(sm64_mesh_front_capability!=1u)return;
    /* A validated owner can be waiting for packet credit held by older staged
     * markers. Publish once before waiting; after copy starts, its complete
     * reservation makes progress independent of GPU consumption. */
    const uint32_t status=BRAM_GEO_REGS[0x21];
    if((status&0xa1u)==0x21u)of_gpu_kick_now();
    while(BRAM_GEO_REGS[0x21]&1u)++gfx_mesh_stats[13];
#endif
}

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
