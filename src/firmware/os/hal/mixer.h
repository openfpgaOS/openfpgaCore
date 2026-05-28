/*
 * openfpgaOS Hardware Mixer HAL
 *
 * 32-voice hardware PCM mixer with stereo 8-bit volume,
 * 16.16 fixed-point resampling, forward/bidi looping,
 * 16-bit and 8-bit signed sample support.
 */

#ifndef OFOS_MIXER_H
#define OFOS_MIXER_H

#include <stdint.h>

typedef uint64_t of_mixer_handle_t;

#define OF_MIXER_HANDLE_INVALID ((of_mixer_handle_t)0)

void of_mixer_init(int max_voices, int output_rate);

/* Play 16-bit signed mono PCM from SDRAM. Returns voice index or -1. */
int of_mixer_play(const uint8_t *pcm_s16, uint32_t sample_count,
                  uint32_t sample_rate, int priority, int volume);

/* Play 8-bit signed mono PCM from SDRAM. Returns voice index or -1. */
int of_mixer_play_8bit(const uint8_t *pcm_s8, uint32_t sample_count,
                       uint32_t sample_rate, int priority, int volume);

/* Retrigger an existing voice with new sample data (no stop/start gap).
 * Redirects ADDR/LEN/POS in-place — eliminates click on note retrigger. */
void of_mixer_retrigger(int voice, const uint8_t *pcm_s16,
                        uint32_t sample_count, uint32_t sample_rate,
                        int volume);

void of_mixer_stop(int voice);
void of_mixer_stop_all(void);

/* Mono volume (0-255), sets both L and R equally */
void of_mixer_set_volume(int voice, int volume);

/* Pan (0=left, 128=center, 255=right) */
void of_mixer_set_pan(int voice, int pan);

/* Per-voice active check (software-tracked) */
int of_mixer_voice_active(int voice);

/* No-op: hardware mixer runs autonomously */
void of_mixer_pump(void);
void of_mixer_pump_auto(void);

/* Loop control. loop_start=-1 disables. loop_end sets LOOP_END register. */
void of_mixer_set_loop(int voice, int loop_start, int loop_end);

/* Playback rate from Hz (kernel computes 16.16 fixed-point) */
void of_mixer_set_rate(int voice, int sample_rate_hz);

/* Playback rate as raw 16.16 fixed-point (no division) */
void of_mixer_set_rate_raw(int voice, uint32_t rate_fp16);

/* Independent stereo volume (0-255 each) */
void of_mixer_set_vol_lr(int voice, int vol_l, int vol_r);

/* Bidirectional (ping-pong) loop enable/disable */
void of_mixer_set_bidi(int voice, int enable);

/* Position read (integer sample index) */
int of_mixer_get_position(int voice);

/* Position write (sample offset) */
void of_mixer_set_position(int voice, int sample_offset);

/* Rate (Hz) + stereo volume in one call */
void of_mixer_set_voice(int voice, int sample_rate_hz, int vol_l, int vol_r);

/* Rate (16.16 raw) + stereo volume in one call */
void of_mixer_set_voice_raw(int voice, uint32_t rate_fp16, int vol_l, int vol_r);

/* Volume ramp speed (0=instant, 1=fast ~5ms, higher=slower) */
void of_mixer_set_volume_ramp(int voice, int rate);

/* Poll for ended voices. Returns bitmask of voices that finished
 * since last poll. Clears the hardware IRQ bits and updates
 * software voice tracking. */
uint32_t of_mixer_poll_ended(void);

/* Group / master volume */
#define OF_MIXER_GROUP_SFX   0
#define OF_MIXER_GROUP_MUSIC 1
#define OF_MIXER_GROUP_VOICE 2
#define OF_MIXER_GROUP_AUX   3

void of_mixer_set_group(int voice, int group);
void of_mixer_set_group_volume(int group, int volume);
void of_mixer_set_master_volume(int volume);

/* Group-aware atomic alloc-and-tag.  Searches MUSIC slots ascending
 * and every other group descending, so the two groups land at
 * opposite ends of the slot range under low load.  Steal path
 * prefers same-group victims before crossing groups.  Returns voice
 * index or -1.  Voice 31 stays reserved for the of_audio stream. */
int of_mixer_alloc_for_group(int group, const uint8_t *pcm_s16,
                             uint32_t sample_count, uint32_t sample_rate,
                             int priority, int volume);

/* Read the group tag for a voice (-1 if voice index is out of range).
 * Cheap shadow read; safe from ISR context. */
int of_mixer_voice_group(int voice);

/* Stable logical voice handles.  These APIs reject stale handles after
 * a physical slot naturally ends, is stopped, stolen, or reused. */
of_mixer_handle_t of_mixer_play_h(const uint8_t *pcm_s16,
                                  uint32_t sample_count,
                                  uint32_t sample_rate,
                                  int priority,
                                  int volume);
of_mixer_handle_t of_mixer_play_8bit_h(const uint8_t *pcm_s8,
                                       uint32_t sample_count,
                                       uint32_t sample_rate,
                                       int priority,
                                       int volume);
of_mixer_handle_t of_mixer_alloc_for_group_h(int group,
                                             const uint8_t *pcm_s16,
                                             uint32_t sample_count,
                                             uint32_t sample_rate,
                                             int priority,
                                             int volume);
of_mixer_handle_t of_mixer_retrigger_h(of_mixer_handle_t handle,
                                       const uint8_t *pcm_s16,
                                       uint32_t sample_count,
                                       uint32_t sample_rate,
                                       int volume);
void of_mixer_stop_h(of_mixer_handle_t handle);
int of_mixer_handle_active(of_mixer_handle_t handle);
int of_mixer_handle_group(of_mixer_handle_t handle);
int of_mixer_handle_voice(of_mixer_handle_t handle);
void of_mixer_set_volume_h(of_mixer_handle_t handle, int volume);
void of_mixer_set_pan_h(of_mixer_handle_t handle, int pan);
void of_mixer_set_loop_h(of_mixer_handle_t handle, int loop_start, int loop_end);
void of_mixer_set_rate_h(of_mixer_handle_t handle, int sample_rate_hz);
void of_mixer_set_rate_raw_h(of_mixer_handle_t handle, uint32_t rate_fp16);
void of_mixer_set_vol_lr_h(of_mixer_handle_t handle, int vol_l, int vol_r);
void of_mixer_set_bidi_h(of_mixer_handle_t handle, int enable);
int of_mixer_get_position_h(of_mixer_handle_t handle);
void of_mixer_set_position_h(of_mixer_handle_t handle, int sample_offset);
void of_mixer_set_voice_h(of_mixer_handle_t handle,
                          int sample_rate_hz,
                          int vol_l,
                          int vol_r);
void of_mixer_set_voice_raw_h(of_mixer_handle_t handle,
                              uint32_t rate_fp16,
                              int vol_l,
                              int vol_r);
void of_mixer_set_volume_ramp_h(of_mixer_handle_t handle, int rate);
void of_mixer_set_filter_h(of_mixer_handle_t handle,
                           int cutoff_q016,
                           int q,
                           int enable);
uint32_t of_mixer_poll_ended_h(of_mixer_handle_t *out_handles,
                               uint32_t max_handles);

/* Retired per-voice SVF filter surface.  Kept as a compatibility no-op. */
void of_mixer_set_filter(int voice, int cutoff_q016, int q, int enable);

/* Boot-time persistent audio reservations and legacy sample allocation. */
void of_mixer_memory_init(void);
int of_mixer_memory_set_floor(uint32_t floor);
void *of_mixer_reserve_persistent(uint32_t size, uint32_t align);
uint32_t of_mixer_reserved_base(void);
uint32_t of_mixer_reserved_size(void);
uint32_t of_mixer_app_memory_top(void);
uint32_t of_mixer_stream_base(void);
uint32_t of_mixer_stream_uncached_base(void);
void *of_mixer_alloc_samples(uint32_t size);
void of_mixer_free_samples(void);

/* SBI vendor dispatcher for OF_EID_MIXER.  Switches on FID; called
 * from kernel/syscall.c.  Centralising here keeps every FID case next
 * to its HAL function so a new mixer entry point can't be added to
 * the enum without showing up in the dispatcher diff. */
long of_mixer_dispatch(long fid, long a0, long a1,
                       long a2, long a3, long a4);

/* IRQ save/restore wrappers retired in v3 — flat MMIO writes are
 * race-free on their own (each per-voice register has its own address
 * so main thread + ISR never share a SEL latch).  Kept as no-ops so
 * existing call sites compile unchanged. */
static inline uint32_t of_mixer_irq_save(void)    { return 0; }
static inline void     of_mixer_irq_restore(uint32_t prev) { (void)prev; }

#endif /* OFOS_MIXER_H */
