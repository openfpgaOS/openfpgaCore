//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS software audio mixer (OS30 Pocket variant)
 * -----------------------------------------------------
 * The OS30 Pocket build cuts the hardware audio_mixer.v to free FPGA
 * budget.  This module emulates that mixer in C, BYTE-FOR-BYTE behind the
 * same MMIO voice-register contract, so hal/mixer.c and the Pocket stereo
 * stream (targets/pocket/audio.c) stay unmodified — every MIX_VOICE_x,
 * MIX_MASTER_x, MIX_CTRL, MIX_IRQ access is retargeted to this module's
 * voice-register backing store by regs.h when of_mixer_use_sw is set.
 * Backend selection is runtime: ONE os.bin links this module unconditionally
 * and of_init() sets of_mixer_use_sw from the HW_FEATURES OF_HW_MIXER_HW bit.
 *
 * Two halves:
 *
 *   1. Register backing store + decode.  A flat word array mirrors the HW
 *      slave's 0x000..0x8FF aperture; the few read/write-divergent
 *      registers (MIX_IRQ_PENDING / MIX_IRQ_CLEAR W1C, MIX_ACTIVE_MASK,
 *      MIX_VOICE_POS) route through sw_mixer_reg_read/_write.
 *
 *   2. Per-sample render + DAC pump.  sw_mixer_render() reproduces
 *      audio_mixer.v's exact per-sample arithmetic (Q16.16 position,
 *      2-tap linear interpolation, group*master*voice volume composition,
 *      per-channel volume ramp, loop / one-shot voice-end, /16 mixdown +
 *      s16 saturation).  sw_mixer_pump() polls the DAC FIFO status at
 *      AUDIO_BASE+0x00 and pushes finished stereo pairs to AUDIO_PCM_SAMPLE
 *      (AUDIO_BASE+0x04) until the FIFO is full.  Driven from the 1 kHz
 *      timer ISR (the audio-tick cadence).
 *
 * sw_mixer.c is always compiled and linked; its render/pump entry points
 * early-return when of_mixer_use_sw==0 (a HW audio_mixer.v is present), so
 * the HW-mixer build pays ~nothing.
 */

#ifndef OFOS_SW_MIXER_H
#define OFOS_SW_MIXER_H

#include <stdint.h>

/* One-time init: zero the register aperture, set group/master defaults,
 * deactivate all voices.  Called by of_mixer_init() (via the HW-MMIO write
 * to MIX_CTRL, which is harmless on the array) and from boot — idempotent. */
void sw_mixer_init(void);

/* Render `nframes` stereo s16 frames into `out` (interleaved L,R,L,R...).
 * Each frame walks the active voices exactly as audio_mixer.v's per-sample
 * FSM does and commits the advanced position / ramped volume back into the
 * register store, so MIX_VOICE_POS(v) reads stay coherent for callers
 * (e.g. the of_audio stream's ring read pointer).  The active set and the
 * read-only voice parameters are snapshotted once per call: voice register
 * writes take effect at the next render call (chunk-boundary exact, see
 * sw_mixer.c).  NOT reentrant — the sw_pump_busy guard in sw_mixer_pump()
 * is the only sanctioned call path on target. */
void sw_mixer_render(int16_t *out, int nframes);

/* Poll the DAC FIFO and push freshly-rendered stereo samples until full.
 * Safe to call from ISR or main-loop context; reentrancy is guarded. */
void sw_mixer_pump(void);

#endif /* OFOS_SW_MIXER_H */
