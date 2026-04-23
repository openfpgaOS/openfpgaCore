/*
 * openfpgaOS OS Services Table
 *
 * Populates a static table of function pointers in kernel BSS. Apps
 * call OS services through this table with zero syscall overhead
 * (~2 cycles indirect call vs ~50 cycles ecall trap entry/exit).
 *
 * The table address is handed to apps through the AT_OF_SVC auxv tag
 * in elf_exec(), so an app never has to know where the table lives.
 *
 * Where the SDK API signature differs from the HAL, thin wrappers
 * adapt the calling convention (e.g., video_set_palette packs RGB).
 */

#include "services_table.h"
#include "irq.h"
#include "bank_preload.h"
#include "of_services.h"
#include "../hal/hal.h"
#include "../hal/regs.h"

/* AWE fabric retired.  Service slots wired to no-op stubs so SDK apps
 * built against the old ABI still link; they'll just silently do nothing. */
struct awe_voice_t;
static void     svc_awe_noop_i(int v)                          { (void)v; }
static void     svc_awe_voice_load(int v, const struct awe_voice_t *p) { (void)v; (void)p; }
static void     svc_awe_ch_int(int c, int x)                   { (void)c; (void)x; }
static void     svc_awe_set_int(int v)                         { (void)v; }
static uint64_t svc_awe_active_mask(void)                      { return 0; }
static uint32_t svc_awe_tick_count(void)                       { return 0; }
static void     svc_awe_ramp1_trigger(int v, int s, uint32_t r){ (void)v; (void)s; (void)r; }

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
    /* Pin the hardware timer at 1 kHz regardless of the app's requested
     * rate.  The HW audio mixer runs autonomously so it no longer drives
     * the tick, but of_smp_tables still bakes MIDI envelopes at 1 kHz —
     * the pump accumulates elapsed time and fires smp_voice_tick the
     * appropriate number of times per wake, so 50 Hz MIDI callbacks
     * still resolve correctly on a 1 kHz hardware tick. */
    (void)hz;
    timer_callback_ptr = cb;
    if (cb) {
        TIMER_PERIOD = CPU_FREQ_HZ / 1000u;
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
extern void file_slot_register(uint32_t slot_id, const char *filename);

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

/* Single source of truth for the app-visible services table. Lives in
 * BSS (zero-initialized at boot), populated by services_table_init(). */
static struct of_services_table g_svc;

const struct of_services_table *services_table_get(void) {
    return &g_svc;
}

void services_table_init(void) {
    struct of_services_table *svc = &g_svc;

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
    svc->mixer_set_vol_rate  = of_mixer_set_volume_ramp;
    svc->mixer_poll_ended    = of_mixer_poll_ended;
    svc->mixer_alloc_samples = of_mixer_alloc_samples;
    svc->mixer_free_samples  = of_mixer_free_samples;
    svc->mixer_set_end_callback = svc_mixer_set_end_callback;
    svc->mixer_retrigger     = of_mixer_retrigger;
    svc->mixer_play_8bit     = of_mixer_play_8bit;
    svc->mixer_set_group     = of_mixer_set_group;
    svc->mixer_set_group_volume = of_mixer_set_group_volume;
    svc->mixer_set_master_volume = of_mixer_set_master_volume;
    svc->mixer_set_filter    = of_mixer_set_filter;

    /* Audio */
    svc->audio_init     = of_audio_init;
    svc->audio_write    = of_audio_write;
    svc->audio_get_free = of_audio_get_free;
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

    /* Filesystem */
    svc->file_slot_register = file_slot_register;

    /* SoundFont preload -- filled in by bank_preload() post-init */
    svc->smp_bank_preload_base = NULL;
    svc->smp_bank_preload_size = 0;

    /* AWE coprocessor (retired — slots kept for ABI stability) */
    svc->awe_voice_load              = svc_awe_voice_load;
    svc->awe_voice_trigger           = svc_awe_noop_i;
    svc->awe_voice_release           = svc_awe_noop_i;
    svc->awe_voice_stop              = svc_awe_noop_i;
    svc->awe_channel_set_volume      = svc_awe_ch_int;
    svc->awe_channel_set_expression  = svc_awe_ch_int;
    svc->awe_channel_set_pan         = svc_awe_ch_int;
    svc->awe_channel_set_bend        = svc_awe_ch_int;
    svc->awe_channel_set_mod         = svc_awe_ch_int;
    svc->awe_channel_set_sustain     = svc_awe_ch_int;
    svc->awe_channel_set_brightness  = svc_awe_ch_int;
    svc->awe_channel_set_resonance   = svc_awe_ch_int;
    svc->awe_channel_set_reverb_send = svc_awe_ch_int;
    svc->awe_channel_set_chorus_send = svc_awe_ch_int;
    svc->awe_set_master_volume       = svc_awe_set_int;
    svc->awe_set_bend_range          = svc_awe_set_int;
    svc->awe_active_mask             = svc_awe_active_mask;
    svc->awe_tick_count              = svc_awe_tick_count;
    svc->awe_set_hw_envelope         = svc_awe_set_int;
    svc->awe_set_reverb_level        = svc_awe_set_int;
    svc->awe_set_reverb_feedback     = svc_awe_set_int;
    svc->awe_set_chorus_level        = svc_awe_set_int;
    svc->awe_set_chorus_rate         = svc_awe_set_int;
    svc->awe_set_chorus_depth        = svc_awe_set_int;
    svc->awe_ramp1_trigger           = svc_awe_ramp1_trigger;
}

void services_table_set_smp_bank(const void *base, uint32_t size) {
    g_svc.smp_bank_preload_base = base;
    g_svc.smp_bank_preload_size = size;
}
