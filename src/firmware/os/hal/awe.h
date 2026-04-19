/*
 * AWE coprocessor HAL — kernel-internal prototypes.
 * Bound into the services table by services_table.c so apps reach
 * these via OF_SVC->awe_*.
 */

#ifndef HAL_AWE_H
#define HAL_AWE_H

#include <stdint.h>

struct awe_voice_t;

void awe_init(void);

void of_awe_voice_load_impl(int voice, const struct awe_voice_t *v);
void of_awe_voice_trigger_impl(int voice);
void of_awe_voice_release_impl(int voice);
void of_awe_voice_stop_impl(int voice);

void of_awe_channel_set_volume_impl(int ch, int v);
void of_awe_channel_set_expression_impl(int ch, int v);
void of_awe_channel_set_pan_impl(int ch, int v);
void of_awe_channel_set_bend_impl(int ch, int v);
void of_awe_channel_set_mod_impl(int ch, int v);
void of_awe_channel_set_sustain_impl(int ch, int on);
void of_awe_channel_set_brightness_impl(int ch, int v);
void of_awe_channel_set_resonance_impl(int ch, int v);
void of_awe_channel_set_reverb_send_impl(int ch, int v);
void of_awe_channel_set_chorus_send_impl(int ch, int v);

void of_awe_set_master_volume_impl(int v);
void of_awe_set_bend_range_impl(int cents);

uint64_t of_awe_active_mask_impl(void);
uint32_t of_awe_tick_count_impl(void);
void     of_awe_set_hw_envelope_impl(int enabled);
int      of_awe_hw_envelope_enabled(void);  /* read-side helper for SW */

void     of_awe_set_reverb_level_impl(int level);
void     of_awe_set_reverb_feedback_impl(int feedback);
void     of_awe_set_chorus_level_impl(int level);
void     of_awe_set_chorus_rate_impl(int rate);
void     of_awe_set_chorus_depth_impl(int depth);
/* Phase 5c — Ramp1 (mod env) trigger. */
void     of_awe_ramp1_trigger_impl(int voice, int stage, uint32_t rate);

#endif /* HAL_AWE_H */
