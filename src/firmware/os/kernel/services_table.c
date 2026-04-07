/*
 * openfpgaOS OS Services Table
 *
 * Populates a table of function pointers at 0x7A00 in BRAM. Apps call
 * OS services through this table with zero syscall overhead (~2 cycles
 * indirect call vs ~50 cycles ecall trap entry/exit).
 *
 * Where the SDK API signature differs from the HAL, thin wrappers
 * adapt the calling convention (e.g., video_set_palette packs RGB).
 */

#include "services_table.h"
#include "irq.h"
#include "../../api/of_services.h"
#include "../hal/hal.h"
#include "../hal/regs.h"

/* ======================================================================
 * Wrappers for SDK/HAL signature mismatches
 * ====================================================================== */

/* SDK: (index, 0x00RRGGBB)  →  HAL: (index, r, g, b) */
static void svc_video_set_palette(uint8_t index, uint32_t rgb) {
    of_video_set_palette(index, (uint8_t)(rgb >> 16),
                         (uint8_t)(rgb >> 8), (uint8_t)rgb);
}

/* SDK: writes SYS_COLOR_MODE directly (syscall.c pattern) */
static void svc_video_set_color_mode(int mode) {
    SYS_COLOR_MODE = (uint32_t)mode;
}

/* SDK: (player, out_ptr) copies state  →  HAL: returns pointer */
static void svc_input_get_state(int player, void *out) {
    const of_input_state_t *state = of_input_get_state(player);
    if (out)
        __builtin_memcpy(out, state, sizeof(of_input_state_t));
}

/* Timer set_callback: managed in syscall.c, replicate here */
extern void (*timer_callback_ptr)(void);  /* from syscall.c */

static void svc_timer_set_callback(void (*cb)(void), uint32_t hz) {
    timer_callback_ptr = cb;
    if (cb && hz > 0) {
        TIMER_PERIOD = CPU_FREQ_HZ / hz;
        TIMER_CTRL = TIMER_CTRL_ENABLE;
    } else {
        TIMER_CTRL = 0;
    }
}

static void svc_timer_stop(void) {
    TIMER_CTRL = 0;
    timer_callback_ptr = NULL;
}

/* Mixer end callback: wraps irq registration */
static void svc_mixer_set_end_callback(void (*cb)(uint32_t ended_mask)) {
    of_irq_register_mixer_end(cb);
}

/* Vsync callback: wraps irq registration */
static void svc_video_set_vsync_callback(void (*cb)(void)) {
    of_irq_register_vsync(cb);
}

/* File size by path: open, query, close */
extern long sys_openat_svc(const char *path);
extern void sys_close_svc(int fd);
extern long sys_file_size_fd(int fd);

static long svc_file_size(const char *path) {
    long fd = sys_openat_svc(path);
    if (fd < 0) return -1;
    long sz = sys_file_size_fd((int)fd);
    sys_close_svc((int)fd);
    return sz;
}

static long svc_file_size_fd(int fd) {
    return sys_file_size_fd(fd);
}

/* ======================================================================
 * Table population
 * ====================================================================== */

void services_table_init(void) {
    struct of_services_table *svc = (struct of_services_table *)OF_SVC_ADDR;

    svc->magic   = OF_SVC_MAGIC;
    svc->version = OF_SVC_VERSION;
    svc->count   = (sizeof(struct of_services_table) - 12) / sizeof(void *);

    /* Video */
    svc->video_init             = of_video_init;
    svc->video_get_surface      = of_video_get_surface;
    svc->video_flip             = of_video_flip;
    svc->video_wait_flip        = of_video_wait_flip;
    svc->video_vsync            = of_video_vsync;
    svc->video_set_palette      = svc_video_set_palette;
    svc->video_set_palette_bulk = of_video_set_palette_bulk;
    svc->video_set_palette_vga4 = of_video_set_palette_vga4;
    svc->video_clear            = of_video_clear;
    svc->video_flush_cache      = of_video_flush_cache;
    svc->video_set_display_mode = of_video_set_display_mode;
    svc->video_set_color_mode   = svc_video_set_color_mode;

    /* Input */
    svc->input_poll          = of_input_poll;
    svc->input_get_state     = svc_input_get_state;
    svc->input_poll_p0       = (void (*)(void *))of_input_poll_p0;
    svc->input_set_deadzone  = of_input_set_deadzone;

    /* Mixer */
    svc->mixer_init          = of_mixer_init;
    svc->mixer_play          = of_mixer_play;
    svc->mixer_stop          = of_mixer_stop;
    svc->mixer_stop_all      = of_mixer_stop_all;
    svc->mixer_set_volume    = of_mixer_set_volume;
    svc->mixer_set_pan       = of_mixer_set_pan;
    svc->mixer_voice_active  = of_mixer_voice_active;
    svc->mixer_pump          = of_mixer_pump;
    svc->mixer_set_loop      = of_mixer_set_loop;
    svc->mixer_set_rate      = of_mixer_set_rate;
    svc->mixer_set_rate_raw  = of_mixer_set_rate_raw;
    svc->mixer_set_vol_lr    = of_mixer_set_vol_lr;
    svc->mixer_set_bidi      = of_mixer_set_bidi;
    svc->mixer_get_position  = of_mixer_get_position;
    svc->mixer_set_position  = of_mixer_set_position;
    svc->mixer_set_voice     = of_mixer_set_voice;
    svc->mixer_set_voice_raw = of_mixer_set_voice_raw;
    svc->mixer_set_vol_rate  = of_mixer_set_vol_rate;
    svc->mixer_poll_ended    = of_mixer_poll_ended;
    svc->mixer_alloc_samples = of_mixer_alloc_samples;
    svc->mixer_free_samples  = of_mixer_free_samples;
    svc->mixer_set_end_callback = svc_mixer_set_end_callback;
    svc->mixer_retrigger     = of_mixer_retrigger;
    svc->mixer_play_8bit     = of_mixer_play_8bit;
    svc->mixer_set_group     = of_mixer_set_group;
    svc->mixer_set_group_volume = of_mixer_set_group_volume;
    svc->mixer_set_master_volume = of_mixer_set_master_volume;

    /* Audio */
    svc->audio_init     = of_audio_init;
    svc->audio_write    = of_audio_write;
    svc->audio_get_free = of_audio_get_free;
    svc->opl_write      = of_opl_write;
    svc->opl_reset      = of_opl_reset;
    svc->audio_stream_open  = of_audio_stream_open;
    svc->audio_stream_write = of_audio_stream_write;
    svc->audio_stream_ready = of_audio_stream_ready;
    svc->audio_stream_close = of_audio_stream_close;

    /* Timer */
    svc->timer_set_callback = svc_timer_set_callback;
    svc->timer_stop         = svc_timer_stop;
    svc->timer_get_us       = of_timer_get_us;
    svc->timer_get_ms       = of_timer_get_ms;
    svc->timer_delay_us     = of_timer_delay_us;

    /* Cache */
    svc->cache_flush       = of_cache_flush;
    svc->cache_clean_range = of_cache_clean_range;
    svc->cache_inval_range = of_cache_inval_range;

    /* Callbacks */
    svc->video_set_vsync_callback = svc_video_set_vsync_callback;

    /* File */
    svc->file_size    = svc_file_size;
    svc->file_size_fd = svc_file_size_fd;
}
