/* The source is unchanged. Only physical register access is replaced by
 * counted host accessors. Instrumentation counts actual render/init entries. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "generated_regs.h"
#define OFOS_REGS_H
#define OF_TARGET_SDRAM_BASE 0u
#define OF_TARGET_SDRAM_UNCACHED_BASE 0u

static uint32_t test_status, sample_word;
static unsigned status_reads, sample_writes, render_calls, init_calls;
int of_mixer_use_sw;
static uint32_t read_status(void) { ++status_reads; return test_status; }
static volatile uint32_t *write_sample(void) { ++sample_writes; return &sample_word; }
#define AUDIO_STATUS read_status()
#define AUDIO_PCM_SAMPLE (*write_sample())
#include "sources/sw_mixer.c"

__attribute__((no_instrument_function))
void __cyg_profile_func_enter(void *fn, void *caller) {
    (void)caller;
    if (fn == (void *)sw_mixer_render) ++render_calls;
    if (fn == (void *)sw_mixer_init) ++init_calls;
}
__attribute__((no_instrument_function))
void __cyg_profile_func_exit(void *fn, void *caller) { (void)fn; (void)caller; }

static void counters(void) {
    status_reads = sample_writes = render_calls = init_calls = 0;
    sample_word = 0xdeadbeefu;
}

int main(void) {
    of_mixer_use_sw = 1;
    test_status = AUDIO_FIFO_FULL | AUDIO_FIFO_LEVEL_MASK;
    counters();
    for (unsigned i = 0; i < 1000; ++i) sw_mixer_pump();
    assert(status_reads == 1000 && sample_writes == 0 && render_calls == 0);
    assert(init_calls == 1 && sw_pump_busy == 0 && sample_word == 0xdeadbeefu);
    /* Even a stale active voice must not fetch samples or advance. */
    sw_mix_regs[VF_CTRL] = 1;
    sw_mix_regs[VF_ADDR] = 1; /* deliberately inaccessible */
    sw_mix_regs[VF_LEN] = 128;
    sw_mix_regs[VF_POS_INT] = 19;
    uint32_t before[SW_MIX_APERTURE_WORDS];
    for (unsigned i = 0; i < SW_MIX_APERTURE_WORDS; ++i) before[i] = sw_mix_regs[i];
    counters();
    for (unsigned i = 0; i < 1000; ++i) sw_mixer_pump();
    assert(status_reads == 1000 && sample_writes == 0 && render_calls == 0 && init_calls == 0);
    for (unsigned i = 0; i < SW_MIX_APERTURE_WORDS; ++i) assert(before[i] == sw_mix_regs[i]);
    /* Warm firmware reset repeats only the one-time backing-store init. */
    sw_mixer_inited = 0;
    counters();
    for (unsigned i = 0; i < 1000; ++i) sw_mixer_pump();
    assert(status_reads == 1000 && sample_writes == 0 && render_calls == 0 && init_calls == 1);
    /* Prove instrumentation detects a real normal-path render and write. */
    test_status = 1023;
    counters();
    sw_mixer_pump();
    assert(status_reads == 1 && render_calls == 1 && sample_writes == 1);
    assert(sample_word == 0 && sw_pump_busy == 0);
    /* A normal hardware-mixer configuration never touches the FIFO. */
    of_mixer_use_sw = 0;
    counters();
    for (unsigned i = 0; i < 1000; ++i) sw_mixer_pump();
    assert(status_reads == 0 && render_calls == 0 && sample_writes == 0 && init_calls == 0);
    puts("PASS: full FIFO skips rendering/PCM writes in 3000 software-pump calls; normal positive control and hardware bypass pass");
    return 0;
}
