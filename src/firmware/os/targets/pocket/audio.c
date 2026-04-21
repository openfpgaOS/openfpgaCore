/*
 * openfpgaOS Pocket audio target — honest stereo PCM path.
 *
 * of_audio_write() pushes interleaved stereo s16 into an SDRAM ring
 * buffer; swmixer voice 31 plays that ring at the configured source
 * rate in forward-loop mode, so the stream composes with SF2 voices
 * and mixer SFX into a single output through swmixer_tick().
 *
 * This replaces the old mono-scratch / ping-pong-voice-29-30 hack.
 */

#include "audio.h"
#include "regs.h"
#include "swmixer.h"

/* Ring: 2048 stereo pairs = 16 KB = ~42 ms slack at 48 kHz. */
#define AUDIO_RING_PAIRS  2048

/* Interleaved L,R,L,R...  Lives in SDRAM (.bss); read by the mixer tick
 * via the D-cache, same as every other sample buffer. */
static int16_t  audio_ring[AUDIO_RING_PAIRS * 2];
static uint32_t audio_write_idx;   /* next stereo pair to fill (mod AUDIO_RING_PAIRS) */

#define AUDIO_VOICE  31

static void configure_stream_voice(uint32_t rate_fp16)
{
    swmixer_voice_t *sv = swmixer_voice(AUDIO_VOICE);
    if (!sv) return;
    sv->sample     = audio_ring;
    sv->len        = AUDIO_RING_PAIRS;
    sv->loop_start = 0;
    sv->loop_end   = AUDIO_RING_PAIRS;
    sv->pos_int    = 0;
    sv->pos_frac   = 0;
    sv->rate_fp16  = rate_fp16;
    sv->fmt16      = 1;
    sv->stereo     = 1;
    sv->loop_mode  = SWMIXER_LOOP_FORWARD;
    sv->dir_rev    = 0;
    sv->end_pending = 0;
    sv->vol_l_cur = sv->vol_r_cur = 255;
    sv->vol_l_tgt = sv->vol_r_tgt = 255;
    sv->vol_rate  = 0;
    sv->active    = 1;
}

void of_audio_init(void)
{
    for (int i = 0; i < AUDIO_RING_PAIRS * 2; i++) audio_ring[i] = 0;
    audio_write_idx = 0;
    /* Stream voice is dormant until the first write; of_mixer_init leaves
     * it inactive.  configure_stream_voice() activates it on first use. */
    swmixer_voice_t *sv = swmixer_voice(AUDIO_VOICE);
    if (sv) sv->active = 0;

    /* Start the SDRAM → audio_output DMA.  HW now streams silence to
     * I2S at 48 kHz; swmixer_tick() fills the ring with real audio as
     * voices activate.  CPU is off the sample-accurate deadline — it
     * just has to keep the ring non-empty, which 85 ms of buffering
     * makes trivial. */
    swmixer_dma_start();

    /* Enable the 1 kHz timer now.  Without this, apps that only use
     * of_mixer_* (no of_timer_set_callback, no MIDI) never see
     * swmixer_tick() run and voice state stays frozen at pos_int=0.
     * Enabling it here means any call that goes through of_audio_init
     * (which the OS invokes at boot for every target) gets a ticking
     * mixer for free.  The app loader has already finished by the
     * time of_audio_init runs, so the old "ISR vs loader SDRAM race"
     * rationale no longer applies. */
    TIMER_PERIOD = CPU_FREQ_HZ / 1000u;
    TIMER_CTRL   = TIMER_CTRL_ENABLE;
}

static inline uint32_t ring_room_pairs(void)
{
    swmixer_voice_t *sv = swmixer_voice(AUDIO_VOICE);
    if (!sv || !sv->active) return AUDIO_RING_PAIRS;
    uint32_t read_pair = sv->pos_int;
    /* gap = pairs written but not yet consumed. */
    uint32_t gap = (audio_write_idx + AUDIO_RING_PAIRS - read_pair) & (AUDIO_RING_PAIRS - 1);
    /* Leave one pair unused so wrap is unambiguous. */
    return AUDIO_RING_PAIRS - 1 - gap;
}

int of_audio_write(const int16_t *samples, int count)
{
    if (!samples || count <= 0) return 0;

    swmixer_voice_t *sv = swmixer_voice(AUDIO_VOICE);
    if (!sv || !sv->active) {
        configure_stream_voice(0x10000);  /* default: 48 kHz 1:1 */
    }

    uint32_t room = ring_room_pairs();
    if ((uint32_t)count > room) count = (int)room;
    if (count <= 0) return 0;

    uint32_t idx = audio_write_idx;
    for (int i = 0; i < count; i++) {
        audio_ring[(idx * 2)]     = samples[i * 2];      /* L */
        audio_ring[(idx * 2) + 1] = samples[i * 2 + 1];  /* R */
        idx = (idx + 1) & (AUDIO_RING_PAIRS - 1);
    }
    audio_write_idx = idx;
    return count;
}

int of_audio_get_free(void)
{
    return (int)ring_room_pairs();
}

/* Streaming wrapper: same ring, reconfigurable source rate for rate
 * conversion in the swmixer tick. */
int of_audio_stream_open(int sample_rate)
{
    if (sample_rate <= 0) return -1;
    uint32_t rate = ((uint64_t)sample_rate << 16) / SWMIXER_OUTPUT_RATE;
    for (int i = 0; i < AUDIO_RING_PAIRS * 2; i++) audio_ring[i] = 0;
    audio_write_idx = 0;
    configure_stream_voice(rate);
    return 0;
}

int of_audio_stream_write(const int16_t *samples, int count)
{
    return of_audio_write(samples, count);
}

int of_audio_stream_ready(void)
{
    return of_audio_get_free() >= (AUDIO_RING_PAIRS / 4);
}

void of_audio_stream_close(void)
{
    swmixer_voice_t *sv = swmixer_voice(AUDIO_VOICE);
    if (sv) sv->active = 0;
    audio_write_idx = 0;
}
