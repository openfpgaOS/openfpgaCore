/*
 * openfpgaOS Hardware Mixer HAL
 * Configures the 32-voice CRAM1 hardware mixer via MMIO registers.
 * All mixing happens in FPGA fabric — zero CPU cost during playback.
 */

#include "mixer.h"
#include "regs.h"

#define MIXER_MAX_VOICES    32
#define MIXER_OUTPUT_RATE   48000

static int mixer_initialized;

void of_mixer_init(int max_voices, int output_rate)
{
    (void)max_voices;
    (void)output_rate;
    /* Stop all voices and enable mixer */
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        MIX_VOICE_SEL = i;
        MIX_VOICE_CTRL = 0;
    }
    MIX_CTRL = 1;  /* enable */
    mixer_initialized = 1;
}

int of_mixer_play(const uint8_t *pcm_u8, uint32_t sample_count,
                  uint32_t sample_rate, int priority, int volume)
{
    (void)priority;
    if (!mixer_initialized) return -1;
    if (!pcm_u8 || sample_count == 0) return -1;

    /* Find a free voice */
    int best = -1;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        MIX_VOICE_SEL = i;
        /* Read ctrl — can't read individual voices, so just try sequentially */
        /* For now, track active state in software */
        best = i;  /* TODO: proper free voice tracking */
        break;
    }
    if (best < 0) return -1;

    /* Convert volume 0-255 to 4-bit hardware volume 0-15 */
    int hwvol = volume >> 4;
    if (hwvol > 15) hwvol = 15;

    /* Compute 0.16 fixed-point rate */
    uint32_t rate = ((uint64_t)sample_rate << 16) / MIXER_OUTPUT_RATE;

    /* Convert CPU address to CRAM1 word address.
     * Accepts both cached (0x31xxxxxx) and uncached (0x39xxxxxx) aliases. */
    uint32_t cpu_addr = (uint32_t)(uintptr_t)pcm_u8;
    uint32_t cram1_offset;
    if (cpu_addr >= 0x39000000 && cpu_addr < 0x3A000000)
        cram1_offset = cpu_addr - 0x39000000;
    else
        cram1_offset = cpu_addr - 0x31000000;
    uint32_t cram1_word_addr = cram1_offset >> 2;

    MIX_VOICE_SEL = best;
    MIX_VOICE_ADDR = cram1_word_addr;
    MIX_VOICE_LEN = sample_count;
    MIX_VOICE_RATE = rate;
    MIX_VOICE_CTRL = (hwvol << 4) | 1;  /* active=1, vol in bits[7:4] */

    return best;
}

void of_mixer_stop(int voice)
{
    if (voice >= 0 && voice < MIXER_MAX_VOICES) {
        MIX_VOICE_SEL = voice;
        MIX_VOICE_CTRL = 0;
    }
}

void of_mixer_stop_all(void)
{
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        MIX_VOICE_SEL = i;
        MIX_VOICE_CTRL = 0;
    }
}

void of_mixer_set_volume(int voice, int volume)
{
    if (voice >= 0 && voice < MIXER_MAX_VOICES) {
        int hwvol = volume >> 4;
        if (hwvol > 15) hwvol = 15;
        MIX_VOICE_SEL = voice;
        MIX_VOICE_CTRL = (hwvol << 4) | 1;  /* keep active */
    }
}

void of_mixer_set_pan(int voice, int pan)
{
    (void)voice;
    (void)pan;
    /* Hardware mixer doesn't support per-voice pan — mono to both channels */
}

int of_mixer_voice_active(int voice)
{
    (void)voice;
    /* Read active count from hardware status */
    return (MIX_STATUS & 0x1F) > 0;
}

/* No-op: hardware mixer runs autonomously */
void of_mixer_pump_auto(void) { }
void of_mixer_pump(void) { }
