//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS HAL initialization
 */

#include "hal.h"
#include "regs.h"

/* Mixer-backend selector, defined in hal/mixer.c.  0 = HW audio_mixer.v MMIO,
 * 1 = CPU software mixer.  Set here at boot from HW_FEATURES (one os.bin). */
extern int of_mixer_use_sw;

/* Live CPU/RAM clock frequency (CPU_FREQ_HZ in regs.h).  Same one-os.bin
 * pattern as the mixer backend: reduced-clock bitstreams advertise their
 * real frequency in CLK_FREQ_REG (0xD4); 100 MHz bitstreams return 0 there
 * and the compile-time default stands.  Must be final before
 * of_timer_init() below — TIMER_PERIOD and the video pacing windows
 * derive from it. */
uint32_t g_cpu_freq_hz = OF_TARGET_CPU_FREQ_HZ;

#ifndef OF_BOOT_UART_DEBUG
#define OF_BOOT_UART_DEBUG 0
#endif

/* Optional boot instrumentation. Keep it compiled out by default because the
 * UART shares cart pins with Analogizer/SNAC hardware. */
#if OF_BOOT_UART_DEBUG
static void uart_dbg_putc(char c) {
    while (!(UART_STATUS & UART_TX_RDY)) ;
    UART_TX_DATA = (uint32_t)(uint8_t)c;
}
static void uart_dbg_puts(const char *s) {
    while (*s) uart_dbg_putc(*s++);
}
#else
static void uart_dbg_puts(const char *s) {
    (void)s;
}
#endif

void of_init(void) {
    uart_dbg_puts("[init] enter\n");
    uint32_t features = HW_FEATURES;

    /* Adopt the bitstream's advertised clock before anything derives time
     * from CPU_FREQ_HZ.  Plausibility-gated (50-150 MHz): 0 = register not
     * present (100 MHz bitstream / older RTL) → keep the compile default. */
    uint32_t clk_hz = CLK_FREQ_REG;
    if (clk_hz >= 50000000u && clk_hz <= 150000000u)
        g_cpu_freq_hz = clk_hz;

    uart_dbg_puts("[init] cache..\n");
    of_cache_init();
    uart_dbg_puts("[init] timer..\n");
    of_timer_init();
    uart_dbg_puts("[init] video..\n");
    of_video_init();
    uart_dbg_puts("[init] term..\n");
    of_term_init();
    uart_dbg_puts("[init] input..\n");
    of_input_init();
    /* of_disk_init must run before of_file_init: the latter's
     * bridge warmup DMA only fires when the bridge is the active
     * backend, and that's decided here. */
    uart_dbg_puts("[init] disk..\n");
    of_disk_init();
    uart_dbg_puts("[init] file..\n");
    of_file_init();

    if (features & HW_FEAT_MIXER) {
        /* Pick the mixer backend ONCE, before any MIX_* access: HW MMIO when
         * audio_mixer.v is present (HW_FEAT_MIXER_HW set), else the CPU
         * software mixer.  HW_FEAT_MIXER (bit 0) is set on every variant, so a
         * mixer is always available to apps; bit 1 only chooses the backend. */
#ifdef OF_MIXER_FORCE_SW
        of_mixer_use_sw = 1;   /* debug override: force the CPU software mixer */
#else
        of_mixer_use_sw = !(features & HW_FEAT_MIXER_HW);
#endif
        uart_dbg_puts("[init] mixer..\n");
        of_mixer_init(32, 48000);
        uart_dbg_puts("[init] audio..\n");
        of_audio_init();
    }
    if (features & HW_FEAT_SAVE_SLOTS) {
        uart_dbg_puts("[init] save..\n");
        of_save_init();
    }
    if (features & HW_FEAT_ANALOGIZER) {
        uart_dbg_puts("[init] analogizer..\n");
        of_analogizer_init();
    }
    if (features & HW_FEAT_LINK) {
        uart_dbg_puts("[init] link..\n");
        of_link_init(LINK_MODE_SLAVE);
    }
    uart_dbg_puts("[init] done\n");
}
