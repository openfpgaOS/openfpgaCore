//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

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
#include "config.h"
#include "of_services.h"
#include "../hal/hal.h"
#include "../hal/regs.h"
#include "../hal/file.h"

/* ======================================================================
 * Wrappers for SDK/HAL signature mismatches
 * ====================================================================== */

/* SDK: (index, 0x00RRGGBB)  →  HAL: (index, r, g, b) */
static void svc_video_set_palette(uint8_t index, uint32_t rgb) {
    of_video_set_palette(index, (uint8_t)(rgb >> 16),
                         (uint8_t)(rgb >> 8), (uint8_t)rgb);
}

static void svc_video_set_color_mode(int mode) {
    of_video_set_color_mode(mode);
}

/* SDK: (player, out_ptr) copies state  →  HAL: returns pointer */
static void svc_input_get_state(int player, void *out) {
    const of_input_state_t *state = of_input_get_state(player);
    if (out)
        __builtin_memcpy(out, state, sizeof(of_input_state_t));
}

static void svc_input_get_keyboard_state(void *out) {
    const of_keyboard_state_t *state = of_input_get_keyboard_state();
    if (out)
        __builtin_memcpy(out, state, sizeof(of_keyboard_state_t));
}

static void svc_input_read_mouse_state(void *out) {
    of_input_read_mouse_state((of_mouse_state_t *)out);
}

/* Timer policy is owned by syscall.c (shared 1 kHz tick + SW-mixer pump). */
extern void of_os_timer_set_callback(void (*cb)(void), uint32_t app_hz, int self_divide);

static inline uint32_t svc_irq_save_local(void)
{
    uint32_t prev;
    __asm__ volatile("csrrci %0, mstatus, 0x8"
                     : "=r"(prev) :: "memory");
    return prev & 0x8u;
}

static inline void svc_irq_restore_local(uint32_t prev)
{
    if (prev)
        __asm__ volatile("csrrsi zero, mstatus, 0x8" ::: "memory");
}

static void svc_timer_set_callback(void (*cb)(void), uint32_t hz) {
    /* Pin the hardware timer at 1 kHz regardless of the app's requested rate.
     * of_smp_tables bakes MIDI envelopes at 1 kHz and of_midi_pump accumulates
     * elapsed time to fire smp_voice_tick the right number of times per wake,
     * so this consumer always wants the raw 1 kHz tick (self_divide=1).  On a
     * SW-mixer build of_os_timer_set_callback also keeps the timer pinned ON
     * so PCM/SFX audio survives of_midi_stop(). */
    (void)hz;
    of_os_timer_set_callback(cb, 1000u, 1 /* consumer self-divides */);
}

static void svc_timer_stop(void) {
    of_os_timer_set_callback(NULL, 0u, 0);
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
    svc->video_acquire_next     = of_video_acquire_next;
    svc->video_buffer_addr      = of_video_buffer_addr;

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
    svc->mixer_alloc_for_group = of_mixer_alloc_for_group;
    svc->mixer_voice_group   = of_mixer_voice_group;

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

    /* Cache (append-only).  cbo.flush per line — writes back AND
     * invalidates dirty lines in the affected range so external AXI
     * masters (GPU m_rd_*, audio mixer voice fetch, …) reading DRAM
     * see the committed data.  Required because cbo.clean alone has
     * been unreliable on this VexiiRiscv config (see
     * project_audio_pan_flood / bank_preload comments). */
    svc->cache_flush_range = of_cache_flush_range;

    /* Input HID extensions */
    svc->input_get_keyboard_state = svc_input_get_keyboard_state;
    svc->input_read_mouse_state   = svc_input_read_mouse_state;

    /* Mixer stable handles */
    svc->mixer_play_h            = of_mixer_play_h;
    svc->mixer_play_8bit_h       = of_mixer_play_8bit_h;
    svc->mixer_alloc_for_group_h = of_mixer_alloc_for_group_h;
    svc->mixer_retrigger_h       = of_mixer_retrigger_h;
    svc->mixer_stop_h            = of_mixer_stop_h;
    svc->mixer_handle_active     = of_mixer_handle_active;
    svc->mixer_handle_group      = of_mixer_handle_group;
    svc->mixer_handle_voice      = of_mixer_handle_voice;
    svc->mixer_set_volume_h      = of_mixer_set_volume_h;
    svc->mixer_set_pan_h         = of_mixer_set_pan_h;
    svc->mixer_set_loop_h        = of_mixer_set_loop_h;
    svc->mixer_set_rate_h        = of_mixer_set_rate_h;
    svc->mixer_set_rate_raw_h    = of_mixer_set_rate_raw_h;
    svc->mixer_set_vol_lr_h      = of_mixer_set_vol_lr_h;
    svc->mixer_set_bidi_h        = of_mixer_set_bidi_h;
    svc->mixer_get_position_h    = of_mixer_get_position_h;
    svc->mixer_set_position_h    = of_mixer_set_position_h;
    svc->mixer_set_voice_h       = of_mixer_set_voice_h;
    svc->mixer_set_voice_raw_h   = of_mixer_set_voice_raw_h;
    svc->mixer_set_vol_rate_h    = of_mixer_set_volume_ramp_h;
    svc->mixer_poll_ended_h      = of_mixer_poll_ended_h;

    /* Video timing snapshot */
    svc->video_get_timing = of_video_get_timing;
    svc->video_set_refresh_vtotal = of_video_set_refresh_vtotal;
    svc->video_set_mode = of_video_set_mode;
    svc->video_get_mode = of_video_get_mode;
    svc->video_get_mode_count = of_video_get_mode_count;
    svc->video_get_mode_info = of_video_get_mode_info;
    svc->video_get_caps = of_video_get_caps;
    svc->video_check_mode = of_video_check_mode;

    /* OS/app configuration */
    svc->config_get = of_config_get;
    svc->config_get_int = of_config_get_int;
    svc->config_get_bool = of_config_get_bool;
    svc->config_next = of_config_next;

    /* File I/O idle hook: lets the app feed audio during blocking SD reads. */
    svc->file_set_idle_hook = of_file_set_idle_hook;

    /* Dock state */
    svc->input_is_docked = of_input_is_docked;
}

void services_table_set_smp_bank(const void *base, uint32_t size) {
    g_svc.smp_bank_preload_base = base;
    g_svc.smp_bank_preload_size = size;
}
