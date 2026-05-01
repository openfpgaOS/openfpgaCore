/*
 * openfpgaOS HAL initialization
 */

#include "hal.h"
#include "regs.h"

/* DEBUG instrumentation: write a single character to UART directly
 * (bypassing of_term, which isn't initialized yet during of_init).
 * Used to bisect tb_system boot hangs — UART hardware is set up by
 * the bootloader so direct writes work from of_init onwards. */
static void uart_dbg_putc(char c) {
    while (!(UART_STATUS & UART_TX_RDY)) ;
    UART_TX_DATA = (uint32_t)(uint8_t)c;
}
static void uart_dbg_puts(const char *s) {
    while (*s) uart_dbg_putc(*s++);
}

void of_init(void) {
    uart_dbg_puts("[init] enter\n");
    uint32_t features = HW_FEATURES;

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
