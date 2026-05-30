/*
 * openfpgaOS mixer HAL — thin facade over the HW audio_mixer (v2).
 *
 * Hardware fetches samples from absolute SDRAM byte addresses, does
 * 2-tap linear interp + per-channel volume ramp, mixes 32 voices, and
 * drives audio_output's dcfifo at 48 kHz.  The public of_mixer_* API is
 * unchanged from the old swmixer-backed HAL so existing apps link.
 *
 * Programming model (see audio_mixer.v + axi_periph_slave.v):
 *   MIX_VOICE_SEL      = <voice>        // latches target voice index
 *   MIX_VOICE_<field>  = <value>        // writes the corresponding VTBL slot
 *   MIX_VOICE_POS      read-only        // returns pos_int for selected voice
 *   MIX_IRQ_PENDING    = W1C bitmap     // one bit per retired one-shot voice
 *
 * MIX_VOICE_SEL is shared between the write path and the pos readback, so
 * a concurrent caller (interrupt context + main thread) can interleave
 * and read the wrong voice.  Firmware convention: call of_mixer_* only
 * from the main thread.
 */

#include "mixer.h"
#include "regs.h"
#include "cache.h"
#include "of_error.h"
#include "of_syscall_numbers.h"
#include "../kernel/syscall.h"

extern void of_irq_register_mixer_end(void (*cb)(uint32_t ended_mask));

extern void of_term_printf(const char *fmt, ...);

/* v3 flat MMIO: every per-voice register has its own address, so main
 * thread + ISR writes never race.  Group and master volume are HW-side
 * (composed in audio_mixer.v's S_WR_VOL stage), so set_master_volume
 * and set_group_volume are now O(1) MMIO writes.  The old SEL+field
 * race-guarding (mixer_irq_save/restore) is gone. */

#define MIXER_MAX_VOICES     32
#define MIXER_OUTPUT_RATE    48000

/* Reserve voice 31 for the pocket `of_audio_*` stereo stream so SFX
 * playback never steals it.  audio.c programs voice 31 directly. */
#define MIXER_SCRATCH_VOICE  31

/* Shadow state.  The HW now owns group + master composition, so we
 * only mirror per-voice volume + pan to recompute the (CPU-side)
 * pan-weighted vol_target whenever the app changes either one. */
static uint32_t active_shadow;                    /* bit i = voice i active */
static uint8_t  ctrl_shadow[MIXER_MAX_VOICES];    /* {loop[2], stereo[1], active[0]} */
static int8_t   priority_shadow[MIXER_MAX_VOICES];
static uint8_t  vol_shadow[MIXER_MAX_VOICES];     /* per-voice 0..255 (pre-pan) */
static uint8_t  pan_shadow[MIXER_MAX_VOICES];     /* 0=L, 128=C, 255=R */
static uint8_t  group_shadow[MIXER_MAX_VOICES];   /* mirror of HW packed reg */
static uint64_t generation_shadow[MIXER_MAX_VOICES];

#define MIXER_NUM_GROUPS 4
#define MIXER_ENDED_QUEUE_SIZE 64

static int mixer_initialized;
static uint64_t ended_queue[MIXER_ENDED_QUEUE_SIZE];
static uint8_t  ended_head;
static uint8_t  ended_tail;
static uint8_t  ended_count;
static uint32_t ended_legacy_latch;
static uint32_t ended_queue_overflows;

static inline int voice_in_range(int voice);
static void write_voice_group_packed(void);

static inline uint32_t mixer_irq_save_local(void)
{
    uint32_t prev;
    __asm__ volatile("csrrci %0, mstatus, 0x8"
                     : "=r"(prev) :: "memory");
    return prev & 0x8u;
}

static inline void mixer_irq_restore_local(uint32_t prev)
{
    if (prev)
        __asm__ volatile("csrrsi zero, mstatus, 0x8" ::: "memory");
}

static inline uint64_t mixer_generation_next(uint64_t generation)
{
    const uint64_t mask = (UINT64_C(1) << 56) - 1;
    generation = (generation + 1) & mask;
    return generation ? generation : 1;
}

static inline of_mixer_handle_t mixer_make_handle_locked(int voice)
{
    uint64_t generation = mixer_generation_next(generation_shadow[voice]);
    generation_shadow[voice] = generation;
    return (generation << 8) | (uint8_t)voice;
}

static inline of_mixer_handle_t mixer_current_handle_locked(int voice)
{
    uint64_t generation = generation_shadow[voice];
    if (!generation)
        return OF_MIXER_HANDLE_INVALID;
    return (generation << 8) | (uint8_t)voice;
}

static inline int mixer_handle_voice_index(of_mixer_handle_t handle)
{
    int voice = (int)(handle & 0xFFu);
    uint64_t generation = handle >> 8;
    if (handle == OF_MIXER_HANDLE_INVALID || generation == 0)
        return -1;
    if (!voice_in_range(voice))
        return -1;
    return voice;
}

static inline int mixer_validate_handle_locked(of_mixer_handle_t handle)
{
    int voice = mixer_handle_voice_index(handle);
    if (voice < 0)
        return -1;
    if (!(active_shadow & (1u << voice)))
        return -1;
    if (generation_shadow[voice] != (handle >> 8))
        return -1;
    return voice;
}

static void mixer_ended_push_locked(of_mixer_handle_t handle)
{
    if (handle == OF_MIXER_HANDLE_INVALID)
        return;

    if (ended_count == MIXER_ENDED_QUEUE_SIZE) {
        ended_tail = (uint8_t)((ended_tail + 1u) & (MIXER_ENDED_QUEUE_SIZE - 1u));
        ended_count--;
        ended_queue_overflows++;
    }

    ended_queue[ended_head] = handle;
    ended_head = (uint8_t)((ended_head + 1u) & (MIXER_ENDED_QUEUE_SIZE - 1u));
    ended_count++;
}

static uint32_t mixer_reap_ended_pending(void)
{
    uint32_t ended = MIX_IRQ_PENDING;
    if (!ended)
        return 0;

    uint32_t irq = mixer_irq_save_local();
    ended = MIX_IRQ_PENDING;
    if (ended) {
        uint32_t active_ended = ended & active_shadow;

        for (int i = 0; i < MIXER_MAX_VOICES; i++) {
            if (!(active_ended & (1u << i)))
                continue;
            mixer_ended_push_locked(mixer_current_handle_locked(i));
            ctrl_shadow[i] = 0;
        }

        active_shadow &= ~ended;
        ended_legacy_latch |= active_ended;
        MIX_IRQ_CLEAR = ended;
    }
    mixer_irq_restore_local(irq);
    return ended;
}

/* Equal-power pan: quarter-cosine/sine, 256 entries.
 * pan_cos[i] ~= cos(i*pi/510)*255, pan_sin[i] ~= sin(i*pi/510)*255. */
static const uint8_t pan_cos[256] = {
    255,255,255,255,255,255,255,255,255,255,255,254,254,254,254,254,
    254,254,253,253,253,253,253,252,252,252,252,251,251,251,251,250,
    250,250,249,249,249,248,248,248,247,247,247,246,246,245,245,244,
    244,243,243,243,242,242,241,241,240,239,239,238,238,237,237,236,
    235,235,234,234,233,232,232,231,230,230,229,228,228,227,226,225,
    225,224,223,222,222,221,220,219,218,218,217,216,215,214,213,213,
    212,211,210,209,208,207,206,205,204,203,203,202,201,200,199,198,
    197,196,195,194,193,192,191,190,188,187,186,185,184,183,182,181,
    180,179,178,176,175,174,173,172,171,169,168,167,166,165,164,162,
    161,160,159,157,156,155,154,152,151,150,149,147,146,145,143,142,
    141,140,138,137,136,134,133,132,130,129,128,126,125,123,122,121,
    119,118,116,115,114,112,111,109,108,107,105,104,102,101, 99, 98,
     96, 95, 94, 92, 91, 89, 88, 86, 85, 83, 82, 80, 79, 77, 76, 74,
     73, 71, 70, 68, 67, 65, 64, 62, 61, 59, 58, 56, 55, 53, 51, 50,
     48, 47, 45, 44, 42, 41, 39, 38, 36, 34, 33, 31, 30, 28, 27, 25,
     24, 22, 20, 19, 17, 16, 14, 13, 11,  9,  8,  6,  5,  3,  2,  0,
};
static const uint8_t pan_sin[256] = {
      0,  2,  3,  5,  6,  8,  9, 11, 13, 14, 16, 17, 19, 20, 22, 24,
     25, 27, 28, 30, 31, 33, 34, 36, 38, 39, 41, 42, 44, 45, 47, 48,
     50, 51, 53, 55, 56, 58, 59, 61, 62, 64, 65, 67, 68, 70, 71, 73,
     74, 76, 77, 79, 80, 82, 83, 85, 86, 88, 89, 91, 92, 94, 95, 96,
     98, 99,101,102,104,105,107,108,109,111,112,114,115,116,118,119,
    121,122,123,125,126,127,129,130,132,133,134,136,137,138,140,141,
    142,143,145,146,147,149,150,151,152,154,155,156,157,159,160,161,
    162,164,165,166,167,168,169,171,172,173,174,175,176,178,179,180,
    181,182,183,184,185,186,187,188,190,191,192,193,194,195,196,197,
    198,199,200,201,202,203,203,204,205,206,207,208,209,210,211,212,
    213,213,214,215,216,217,218,218,219,220,221,222,222,223,224,225,
    225,226,227,228,228,229,230,230,231,232,232,233,234,234,235,235,
    236,237,237,238,238,239,239,240,241,241,242,242,243,243,243,244,
    244,245,245,246,246,247,247,247,248,248,248,249,249,249,250,250,
    250,251,251,251,251,252,252,252,252,253,253,253,253,253,254,254,
    254,254,254,254,254,255,255,255,255,255,255,255,255,255,255,255,
};

static inline int voice_in_range(int voice)
{
    return voice >= 0 && voice < MIXER_MAX_VOICES;
}

static inline uint32_t align_down_u32(uint32_t v, uint32_t align)
{
    return v & ~(align - 1u);
}

static inline uint32_t align_up_u32(uint32_t v, uint32_t align)
{
    return (v + align - 1u) & ~(align - 1u);
}

static inline int is_power_of_two_u32(uint32_t v)
{
    return v && ((v & (v - 1u)) == 0);
}

static int sample_byte_count(uint32_t sample_count, uint32_t bytes_per_sample,
                             uint32_t *bytes_out)
{
    if (!bytes_out || bytes_per_sample == 0)
        return 0;
    if (sample_count > (UINT32_MAX / bytes_per_sample))
        return 0;
    *bytes_out = sample_count * bytes_per_sample;
    return 1;
}

static int mixer_sdram_addr(const void *ptr, uint32_t bytes,
                            uint32_t *addr_out, int *needs_flush_out)
{
    uintptr_t p = (uintptr_t)ptr;
    uintptr_t end = p + (uintptr_t)bytes;
    uintptr_t sdram_lo = (uintptr_t)OF_TARGET_SDRAM_BASE;
    uintptr_t sdram_hi = sdram_lo + (uintptr_t)OF_TARGET_SDRAM_SIZE;
    uintptr_t uncached_lo = (uintptr_t)OF_TARGET_SDRAM_UNCACHED_BASE;
    uintptr_t uncached_hi = uncached_lo + (uintptr_t)OF_TARGET_SDRAM_SIZE;

    if (!ptr || !addr_out || !needs_flush_out || end < p)
        return 0;

    if (p >= sdram_lo && end <= sdram_hi) {
        *addr_out = (uint32_t)p;
        *needs_flush_out = 1;
        return 1;
    }

    if (p >= uncached_lo && end <= uncached_hi) {
        uintptr_t offset = p - uncached_lo;
        *addr_out = (uint32_t)(sdram_lo + offset);
        *needs_flush_out = 0;
        return 1;
    }

    return 0;
}

/* Persistent audio reservations, carved top-down before the app starts.
 * The stream ring is always reserved; a boot-time .ofsf bank reserves
 * exactly its file size below that. */
static uint32_t audio_reserve_top;
static uint32_t audio_reserve_cursor;
static uint32_t audio_reserved_base;
static uint32_t audio_reserve_floor = OF_TARGET_SDRAM_BASE;
static uint32_t audio_stream_base;
static int audio_memory_initialized;

void of_mixer_memory_init(void)
{
    if (audio_memory_initialized)
        return;

    audio_memory_initialized = 1;
    audio_reserve_top = align_down_u32(OF_TARGET_AUDIO_RESERVE_TOP,
                                       OF_TARGET_AUDIO_RESERVE_ALIGN);
    audio_reserve_cursor = audio_reserve_top;
    audio_reserved_base = audio_reserve_top;

    audio_stream_base =
        (uint32_t)(uintptr_t)of_mixer_reserve_persistent(OF_TARGET_AUDIO_STREAM_SIZE,
                                                         OF_TARGET_AUDIO_RESERVE_ALIGN);
}

int of_mixer_memory_set_floor(uint32_t floor)
{
    of_mixer_memory_init();
    floor = align_up_u32(floor, OF_TARGET_AUDIO_RESERVE_ALIGN);
    if (floor > audio_reserved_base)
        return -1;
    audio_reserve_floor = floor;
    return 0;
}

void *of_mixer_reserve_persistent(uint32_t size, uint32_t align)
{
    if (!audio_memory_initialized)
        of_mixer_memory_init();

    if (!is_power_of_two_u32(align))
        return (void *)0;
    if (align < 4u)
        align = 4u;

    if (size > UINT32_MAX - 3u)
        return (void *)0;
    size = (size + 3u) & ~3u;
    if (size == 0)
        return (void *)(uintptr_t)audio_reserve_cursor;
    if (audio_reserve_cursor < size)
        return (void *)0;

    uint32_t base = align_down_u32(audio_reserve_cursor - size, align);
    if (base < audio_reserve_floor || base < OF_TARGET_SDRAM_BASE)
        return (void *)0;

    audio_reserve_cursor = base;
    if (base < audio_reserved_base)
        audio_reserved_base = base;
    return (void *)(uintptr_t)base;
}

uint32_t of_mixer_reserved_base(void)
{
    of_mixer_memory_init();
    return audio_reserved_base;
}

uint32_t of_mixer_reserved_size(void)
{
    of_mixer_memory_init();
    return audio_reserve_top - audio_reserved_base;
}

uint32_t of_mixer_app_memory_top(void)
{
    of_mixer_memory_init();
    return audio_reserved_base;
}

uint32_t of_mixer_stream_base(void)
{
    of_mixer_memory_init();
    return audio_stream_base;
}

uint32_t of_mixer_stream_uncached_base(void)
{
    of_mixer_memory_init();
    return audio_stream_base - OF_TARGET_SDRAM_BASE + OF_TARGET_SDRAM_UNCACHED_BASE;
}

/* Compose pan-weighted L/R from vol_shadow + pan_shadow and write the
 * result to this voice's VOL_TARGET register.  The HW then composes
 * with group_vol × master_vol before the existing per-channel ramp.
 * Equal-power: vol_l = vol × cos(pan), vol_r = vol × sin(pan). */
static void apply_vol_pan(int voice)
{
    int v = vol_shadow[voice];
    int p = pan_shadow[voice];
    int vol_l = (v * pan_cos[p]) >> 8;
    int vol_r = (v * pan_sin[p]) >> 8;
    MIX_VOICE_VOL_TARGET(voice) = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

static inline void write_ctrl(int voice, uint8_t ctrl)
{
    ctrl_shadow[voice] = ctrl;
    if (ctrl & 1u) active_shadow |=  (1u << voice);
    else           active_shadow &= ~(1u << voice);
    MIX_VOICE_CTRL(voice) = ctrl;
}

void of_mixer_init(int max_voices, int output_rate)
{
    (void)max_voices;
    (void)output_rate;
    of_mixer_memory_init();

    /* Deactivate every voice and clear pending IRQ bits. */
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        vol_shadow[i]      = 255;
        pan_shadow[i]      = 128;
        priority_shadow[i] = 0;
        group_shadow[i]    = 0;
        ctrl_shadow[i]     = 0;
        generation_shadow[i] = mixer_generation_next(generation_shadow[i]);
        MIX_VOICE_CTRL(i)        = 0;
        MIX_VOICE_VOL_LR(i)      = 0;
        MIX_VOICE_VOL_TARGET(i)  = 0;
        MIX_VOICE_VOL_RATE(i)    = 0;
    }
    /* HW group + master defaults: full-scale (slave-side reset already
     * sets these to 0xFF; rewrite anyway so warm restarts are clean). */
    MIX_MASTER_VOL       = 0xFF;
    MIX_GROUP_VOL(0)     = 0xFF;
    MIX_GROUP_VOL(1)     = 0xFF;
    MIX_GROUP_VOL(2)     = 0xFF;
    MIX_GROUP_VOL(3)     = 0xFF;
    MIX_VOICE_GROUP_LO   = 0;
    MIX_VOICE_GROUP_HI   = 0;
    active_shadow  = 0;
    ended_head = 0;
    ended_tail = 0;
    ended_count = 0;
    ended_legacy_latch = 0;
    ended_queue_overflows = 0;
    MIX_IRQ_CLEAR  = 0xFFFFFFFFu;
    MIX_CTRL       = MIX_CTRL_ENABLE;
    mixer_initialized = 1;
}

static int alloc_voice(int priority)
{
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (i == MIXER_SCRATCH_VOICE) continue;
        if (!(active_shadow & (1u << i))) return i;
    }
    int victim = -1;
    int lowest = priority;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (i == MIXER_SCRATCH_VOICE) continue;
        if (priority_shadow[i] < lowest) {
            lowest = priority_shadow[i];
            victim = i;
        }
    }
    /* Re-programming in play_internal overrides any fade-out we could
     * schedule here, so stealing is a hard cut.  The new voice's
     * VOL_LR=0 -> ramp-to-target at VOL_RATE=8 covers the start click. */
    return victim;
}

/* Group-aware allocator.  MUSIC scans low→high so the SFX-preferred
 * high end stays available for short, time-critical effects; every
 * other group (including untagged voices, which init to SFX=0) scans
 * high→low.  The scan covers all 31 non-reserved slots in either
 * direction, so a free slot anywhere is found in pass 1 — there is no
 * separate "opposite end" pass.  Stealing prefers a same-group victim
 * first so a busy group never silences the other group's audio while
 * its own range still holds something stealable. */
static int alloc_voice_grouped(int priority, int group)
{
    if (group == OF_MIXER_GROUP_MUSIC) {
        for (int i = 0; i < MIXER_MAX_VOICES; i++) {
            if (i == MIXER_SCRATCH_VOICE) continue;
            if (!(active_shadow & (1u << i))) return i;
        }
    } else {
        for (int i = MIXER_MAX_VOICES - 1; i >= 0; i--) {
            if (i == MIXER_SCRATCH_VOICE) continue;
            if (!(active_shadow & (1u << i))) return i;
        }
    }

    int victim = -1;
    int lowest = priority;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (i == MIXER_SCRATCH_VOICE) continue;
        if (group_shadow[i] != (uint8_t)group) continue;
        if (priority_shadow[i] < lowest) {
            lowest = priority_shadow[i];
            victim = i;
        }
    }
    if (victim >= 0) return victim;

    lowest = priority;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (i == MIXER_SCRATCH_VOICE) continue;
        if (priority_shadow[i] < lowest) {
            lowest = priority_shadow[i];
            victim = i;
        }
    }
    return victim;
}

/* Program an already-allocated voice slot for fresh playback.  Shared by
 * both of_mixer_play (back-compat scan-from-zero) and the grouped alloc
 * path; the caller has chosen `voice` and (for the grouped path) tagged
 * group_shadow before this runs so apply_vol_pan composes against the
 * correct group_vol[]. */
static of_mixer_handle_t program_voice_play(int voice, const void *pcm,
                                            uint32_t sample_count,
                                            uint32_t sample_rate,
                                            int priority, int volume)
{
    extern volatile uint32_t play_counter_diag;
    play_counter_diag++;

    uint32_t sample_bytes;
    uint32_t sdram_addr;
    int needs_flush;
    if (!sample_byte_count(sample_count, sizeof(int16_t), &sample_bytes) ||
        !mixer_sdram_addr(pcm, sample_bytes, &sdram_addr, &needs_flush))
        return OF_MIXER_HANDLE_INVALID;

    uint32_t rate = ((uint64_t)sample_rate << 16) / MIXER_OUTPUT_RATE;
    uint32_t irq = mixer_irq_save_local();
    of_mixer_handle_t handle = mixer_make_handle_locked(voice);

    active_shadow &= ~(1u << voice);
    ctrl_shadow[voice] = 0;
    mixer_irq_restore_local(irq);

    /* A one-shot voice may have retired in hardware while the app was
     * busy and before of_mixer_poll_ended() ran. If we reuse that slot
     * with its old IRQ bit still pending, the next poll would clear
     * active_shadow/ctrl_shadow for this fresh playback and callers
     * would treat the live voice as ended. Clear the selected slot's
     * stale end bit before re-arming it. */
    MIX_IRQ_CLEAR = 1u << voice;

    /* Force the sample data out to SDRAM before the HW mixer reads it.
     * The mixer fetches directly from SDRAM on its own AXI master,
     * bypassing the CPU D-cache; any CPU stores are still dirty in L1
     * until we force writeback.  Use cbo.flush (writeback + invalidate)
     * rather than cbo.clean — the bank_preload path showed that on this
     * VexiiRiscv config, only flush reliably moves data to SDRAM for
     * the mixer's AXI read path to see. */
    if (needs_flush)
        of_cache_flush_range((void *)pcm, sample_bytes);

    MIX_VOICE_ADDR(voice)       = sdram_addr;
    MIX_VOICE_LEN(voice)        = sample_count;
    MIX_VOICE_RATE(voice)       = rate;
    MIX_VOICE_POS_WR(voice)     = 0;
    MIX_VOICE_LOOP_START(voice) = 0;
    MIX_VOICE_LOOP_END(voice)   = sample_count;
    MIX_VOICE_VOL_LR(voice)     = 0;      /* start silent */
    MIX_VOICE_VOL_RATE(voice)   = 8;      /* ~0.67 ms ramp to target */

    vol_shadow[voice]      = volume & 0xFF;
    pan_shadow[voice]      = 128;
    priority_shadow[voice] = (int8_t)priority;
    apply_vol_pan(voice);

    irq = mixer_irq_save_local();
    write_ctrl(voice, 1u);   /* active | mono | no-loop */
    mixer_irq_restore_local(irq);
    return handle;
}

static of_mixer_handle_t play_internal_h(const void *pcm, uint32_t sample_count,
                                         uint32_t sample_rate, int priority,
                                         int volume, int fmt16)
{
    if (!mixer_initialized || !pcm || sample_count == 0)
        return OF_MIXER_HANDLE_INVALID;
    if (!fmt16)
        return OF_MIXER_HANDLE_INVALID;   /* HW v2 is 16-bit only */
    mixer_reap_ended_pending();
    int voice = alloc_voice(priority);
    if (voice < 0)
        return OF_MIXER_HANDLE_INVALID;
    return program_voice_play(voice, pcm, sample_count, sample_rate,
                              priority, volume);
}

int of_mixer_play(const uint8_t *pcm_s16, uint32_t sample_count,
                  uint32_t sample_rate, int priority, int volume)
{
    of_mixer_handle_t handle = play_internal_h(pcm_s16, sample_count,
                                               sample_rate, priority,
                                               volume, 1);
    return handle == OF_MIXER_HANDLE_INVALID ? -1 : (int)(handle & 0xFFu);
}

of_mixer_handle_t of_mixer_play_h(const uint8_t *pcm_s16,
                                  uint32_t sample_count,
                                  uint32_t sample_rate,
                                  int priority,
                                  int volume)
{
    return play_internal_h(pcm_s16, sample_count, sample_rate,
                           priority, volume, 1);
}

/* Atomic alloc-and-tag for callers that know which group the voice
 * belongs to.  Tags group_shadow before programming so apply_vol_pan
 * composes against the right group_vol[]; allocation prefers the
 * group's end of the slot range to keep the other group's range free
 * for its own voices.  Out-of-range groups are clamped to SFX. */
int of_mixer_alloc_for_group(int group, const uint8_t *pcm_s16,
                             uint32_t sample_count, uint32_t sample_rate,
                             int priority, int volume)
{
    of_mixer_handle_t handle = of_mixer_alloc_for_group_h(group, pcm_s16,
                                                          sample_count,
                                                          sample_rate,
                                                          priority,
                                                          volume);
    return handle == OF_MIXER_HANDLE_INVALID ? -1 : (int)(handle & 0xFFu);
}

of_mixer_handle_t of_mixer_alloc_for_group_h(int group,
                                             const uint8_t *pcm_s16,
                                             uint32_t sample_count,
                                             uint32_t sample_rate,
                                             int priority,
                                             int volume)
{
    if (!mixer_initialized || !pcm_s16 || sample_count == 0)
        return OF_MIXER_HANDLE_INVALID;
    if (group < 0 || group >= MIXER_NUM_GROUPS) group = OF_MIXER_GROUP_SFX;
    mixer_reap_ended_pending();
    int voice = alloc_voice_grouped(priority, group);
    if (voice < 0)
        return OF_MIXER_HANDLE_INVALID;
    group_shadow[voice] = (uint8_t)group;
    write_voice_group_packed();
    return program_voice_play(voice, pcm_s16, sample_count, sample_rate,
                              priority, volume);
}

/* Read the group tag for a voice slot.  Used by the SW MIDI engine's
 * 1 kHz envelope ISR to validate ownership before writing to a slot
 * it allocated previously — if the slot has been reassigned to a
 * different group, the MIDI ISR drops its stale reference rather than
 * trampling the new owner's volume registers.  Returns -1 if the
 * voice index is out of range. */
int of_mixer_voice_group(int voice)
{
    if (!voice_in_range(voice)) return -1;
    return (int)group_shadow[voice];
}

/* HW v2 mixer is 16-bit-only (see audio_mixer.v).  We implement 8-bit
 * playback by expanding s8 -> s16 into a persistent SDRAM buffer and
 * handing that to play_internal.  Steady-state playback is identical
 * to 16-bit. */
int of_mixer_play_8bit(const uint8_t *pcm_s8, uint32_t sample_count,
                       uint32_t sample_rate, int priority, int volume)
{
    of_mixer_handle_t handle = of_mixer_play_8bit_h(pcm_s8, sample_count,
                                                    sample_rate, priority,
                                                    volume);
    return handle == OF_MIXER_HANDLE_INVALID ? -1 : (int)(handle & 0xFFu);
}

of_mixer_handle_t of_mixer_play_8bit_h(const uint8_t *pcm_s8,
                                       uint32_t sample_count,
                                       uint32_t sample_rate,
                                       int priority,
                                       int volume)
{
    if (!pcm_s8 || sample_count == 0) return OF_MIXER_HANDLE_INVALID;

    int16_t *s16 = (int16_t *)of_mixer_alloc_samples(sample_count * sizeof(int16_t));
    if (!s16) return OF_MIXER_HANDLE_INVALID;

    const int8_t *src = (const int8_t *)pcm_s8;
    for (uint32_t i = 0; i < sample_count; i++)
        s16[i] = (int16_t)((int16_t)src[i] << 8);

    return play_internal_h(s16, sample_count, sample_rate, priority, volume, 1);
}

static of_mixer_handle_t retrigger_voice_h(int voice, const uint8_t *pcm_s16,
                                           uint32_t sample_count,
                                           uint32_t sample_rate,
                                           int volume,
                                           of_mixer_handle_t handle)
{
    if (!voice_in_range(voice) || !pcm_s16 || sample_count == 0)
        return OF_MIXER_HANDLE_INVALID;
    uint32_t sample_bytes;
    uint32_t sdram_addr;
    int needs_flush;
    if (!sample_byte_count(sample_count, sizeof(int16_t), &sample_bytes) ||
        !mixer_sdram_addr(pcm_s16, sample_bytes, &sdram_addr, &needs_flush))
        return OF_MIXER_HANDLE_INVALID;

    uint32_t rate = ((uint64_t)sample_rate << 16) / MIXER_OUTPUT_RATE;
    int v = volume & 0xFF;

    if (handle == OF_MIXER_HANDLE_INVALID) {
        uint32_t irq = mixer_irq_save_local();
        handle = mixer_make_handle_locked(voice);
        active_shadow &= ~(1u << voice);
        ctrl_shadow[voice] = 0;
        mixer_irq_restore_local(irq);
    }

    /* Same SDRAM flush as play_internal — see comment there. */
    if (needs_flush)
        of_cache_flush_range((void *)pcm_s16, sample_bytes);

    MIX_VOICE_ADDR(voice)       = sdram_addr;
    MIX_VOICE_LEN(voice)        = sample_count;
    MIX_VOICE_RATE(voice)       = rate;
    MIX_VOICE_POS_WR(voice)     = 0;
    MIX_VOICE_LOOP_START(voice) = 0;
    MIX_VOICE_LOOP_END(voice)   = sample_count;
    MIX_VOICE_VOL_LR(voice)     = (uint32_t)((v << 8) | v);   /* snap */
    MIX_VOICE_VOL_RATE(voice)   = 0;

    vol_shadow[voice] = v;
    pan_shadow[voice] = 128;
    apply_vol_pan(voice);

    uint32_t irq = mixer_irq_save_local();
    write_ctrl(voice, 1u);   /* active | mono | no-loop */
    mixer_irq_restore_local(irq);
    return handle;
}

void of_mixer_retrigger(int voice, const uint8_t *pcm_s16,
                        uint32_t sample_count, uint32_t sample_rate,
                        int volume)
{
    (void)retrigger_voice_h(voice, pcm_s16, sample_count, sample_rate, volume,
                            OF_MIXER_HANDLE_INVALID);
}

of_mixer_handle_t of_mixer_retrigger_h(of_mixer_handle_t handle,
                                       const uint8_t *pcm_s16,
                                       uint32_t sample_count,
                                       uint32_t sample_rate,
                                       int volume)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    of_mixer_handle_t new_handle = OF_MIXER_HANDLE_INVALID;
    if (voice >= 0) {
        new_handle = mixer_make_handle_locked(voice);
        active_shadow &= ~(1u << voice);
        ctrl_shadow[voice] = 0;
    }
    mixer_irq_restore_local(irq);
    if (voice < 0)
        return OF_MIXER_HANDLE_INVALID;
    return retrigger_voice_h(voice, pcm_s16, sample_count, sample_rate, volume,
                             new_handle);
}

/* Stopping a voice was previously a 2-MMIO sequence: snap VOL_LR=0,
 * then CTRL=0 (deactivate).  The VOL_LR snap was the click source —
 * if the per-channel ramp hadn't fully faded the voice down (typical
 * when a callsite stopped on env_done while the HW vol_lr was still
 * trailing the latest vol_target write), the snap forced an instant
 * step from non-zero to 0 in one sample.  ~25% full-scale steps →
 * audible pops on note-off, voice steal cleanup, and stop_all.
 *
 * The fix: just deactivate (CTRL=0).  HW skips inactive voices in
 * the per-sample mix loop, so the voice stops contributing the next
 * sample regardless of vol_lr.  Whatever residual vol_lr remained
 * gets reset on the next program_voice_play that reuses this slot
 * (which writes VOL_LR=0 explicitly before re-arming).
 *
 * Callers that NEED a hard mute (rare) should ramp via
 *   of_mixer_set_vol_lr(v, 0, 0); of_mixer_set_volume_ramp(v, 16);
 * and stop a couple ticks later — see voice_force_off in
 * of_smp_voice.c for the pattern. */
void of_mixer_stop(int voice)
{
    if (!voice_in_range(voice)) return;
    write_ctrl(voice, 0);
}

void of_mixer_stop_h(of_mixer_handle_t handle)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0)
        write_ctrl(voice, 0);
    mixer_irq_restore_local(irq);
}

void of_mixer_stop_all(void)
{
    uint32_t irq = mixer_irq_save_local();
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        MIX_VOICE_CTRL(i) = 0;
        ctrl_shadow[i]    = 0;
        if (active_shadow & (1u << i))
            generation_shadow[i] = mixer_generation_next(generation_shadow[i]);
    }
    active_shadow = 0;
    mixer_irq_restore_local(irq);
}

void of_mixer_set_volume(int voice, int volume)
{
    if (!voice_in_range(voice)) return;
    vol_shadow[voice] = volume & 0xFF;
    apply_vol_pan(voice);
}

void of_mixer_set_volume_h(of_mixer_handle_t handle, int volume)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0) {
        vol_shadow[voice] = volume & 0xFF;
        apply_vol_pan(voice);
    }
    mixer_irq_restore_local(irq);
}

void of_mixer_set_pan(int voice, int pan)
{
    if (!voice_in_range(voice)) return;
    pan_shadow[voice] = pan & 0xFF;
    apply_vol_pan(voice);
}

void of_mixer_set_pan_h(of_mixer_handle_t handle, int pan)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0) {
        pan_shadow[voice] = pan & 0xFF;
        apply_vol_pan(voice);
    }
    mixer_irq_restore_local(irq);
}

int of_mixer_voice_active(int voice)
{
    if (!voice_in_range(voice)) return 0;
    return (active_shadow >> voice) & 1u;
}

int of_mixer_handle_active(of_mixer_handle_t handle)
{
    mixer_reap_ended_pending();
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    mixer_irq_restore_local(irq);
    return voice >= 0;
}

int of_mixer_handle_group(of_mixer_handle_t handle)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    int group = voice >= 0 ? (int)group_shadow[voice] : -1;
    mixer_irq_restore_local(irq);
    return group;
}

int of_mixer_handle_voice(of_mixer_handle_t handle)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_handle_voice_index(handle);
    if (voice >= 0 && generation_shadow[voice] != (handle >> 8))
        voice = -1;
    mixer_irq_restore_local(irq);
    return voice;
}

/* Hardware mixer runs autonomously — no CPU pump needed.  of_mixer_pump
 * is a no-op retained only for the services table (svc->mixer_pump). */
void of_mixer_pump(void)      {}

void of_mixer_set_loop(int voice, int loop_start, int loop_end)
{
    if (!voice_in_range(voice)) return;

    uint8_t cur = ctrl_shadow[voice];
    if (loop_start < 0) {
        write_ctrl(voice, cur & ~4u);   /* clear loop bit */
    } else {
        MIX_VOICE_LOOP_START(voice) = (uint32_t)loop_start;
        if (loop_end > 0) MIX_VOICE_LOOP_END(voice) = (uint32_t)loop_end;
        write_ctrl(voice, cur | 4u);    /* set loop bit */
    }
}

void of_mixer_set_loop_h(of_mixer_handle_t handle, int loop_start, int loop_end)
{
    mixer_reap_ended_pending();
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0) {
        uint8_t cur = ctrl_shadow[voice];
        if (loop_start < 0) {
            write_ctrl(voice, cur & ~4u);
        } else {
            MIX_VOICE_LOOP_START(voice) = (uint32_t)loop_start;
            if (loop_end > 0) MIX_VOICE_LOOP_END(voice) = (uint32_t)loop_end;
            write_ctrl(voice, cur | 4u);
        }
    }
    mixer_irq_restore_local(irq);
}

void of_mixer_set_rate(int voice, int sample_rate_hz)
{
    if (!voice_in_range(voice)) return;
    uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
    MIX_VOICE_RATE(voice) = rate;
}

void of_mixer_set_rate_h(of_mixer_handle_t handle, int sample_rate_hz)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0) {
        uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
        MIX_VOICE_RATE(voice) = rate;
    }
    mixer_irq_restore_local(irq);
}

void of_mixer_set_rate_raw(int voice, uint32_t rate_fp16)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_RATE(voice) = rate_fp16;
}

void of_mixer_set_rate_raw_h(of_mixer_handle_t handle, uint32_t rate_fp16)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0)
        MIX_VOICE_RATE(voice) = rate_fp16;
    mixer_irq_restore_local(irq);
}

void of_mixer_set_vol_lr(int voice, int vol_l, int vol_r)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_VOL_TARGET(voice) = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_set_vol_lr_h(of_mixer_handle_t handle, int vol_l, int vol_r)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0)
        MIX_VOICE_VOL_TARGET(voice) = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
    mixer_irq_restore_local(irq);
}

/* Bidi loop deferred in HW v2 — log via no-op, forward-loop still works. */
void of_mixer_set_bidi(int voice, int enable)
{
    (void)voice; (void)enable;
}

void of_mixer_set_bidi_h(of_mixer_handle_t handle, int enable)
{
    (void)handle; (void)enable;
}

int of_mixer_get_position(int voice)
{
    if (!voice_in_range(voice)) return 0;
    return (int)(MIX_VOICE_POS(voice) & 0x3FFFFFu);
}

int of_mixer_get_position_h(of_mixer_handle_t handle)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    int pos = voice >= 0 ? (int)(MIX_VOICE_POS(voice) & 0x3FFFFFu) : -1;
    mixer_irq_restore_local(irq);
    return pos;
}

void of_mixer_set_position(int voice, int sample_offset_idx)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_POS_WR(voice) = (uint32_t)sample_offset_idx;
}

void of_mixer_set_position_h(of_mixer_handle_t handle, int sample_offset_idx)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0)
        MIX_VOICE_POS_WR(voice) = (uint32_t)sample_offset_idx;
    mixer_irq_restore_local(irq);
}

void of_mixer_set_voice(int voice, int sample_rate_hz, int vol_l, int vol_r)
{
    if (!voice_in_range(voice)) return;
    uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
    MIX_VOICE_RATE(voice)       = rate;
    MIX_VOICE_VOL_TARGET(voice) = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_set_voice_h(of_mixer_handle_t handle,
                          int sample_rate_hz,
                          int vol_l,
                          int vol_r)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0) {
        uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
        MIX_VOICE_RATE(voice)       = rate;
        MIX_VOICE_VOL_TARGET(voice) = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
    }
    mixer_irq_restore_local(irq);
}

void of_mixer_set_voice_raw(int voice, uint32_t rate_fp16, int vol_l, int vol_r)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_RATE(voice)       = rate_fp16;
    MIX_VOICE_VOL_TARGET(voice) = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_set_voice_raw_h(of_mixer_handle_t handle,
                              uint32_t rate_fp16,
                              int vol_l,
                              int vol_r)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0) {
        MIX_VOICE_RATE(voice)       = rate_fp16;
        MIX_VOICE_VOL_TARGET(voice) = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
    }
    mixer_irq_restore_local(irq);
}

void of_mixer_set_volume_ramp(int voice, int rate)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_VOL_RATE(voice) = (uint32_t)(rate & 0xFF);
}

void of_mixer_set_volume_ramp_h(of_mixer_handle_t handle, int rate)
{
    uint32_t irq = mixer_irq_save_local();
    int voice = mixer_validate_handle_locked(handle);
    if (voice >= 0)
        MIX_VOICE_VOL_RATE(voice) = (uint32_t)(rate & 0xFF);
    mixer_irq_restore_local(irq);
}

uint32_t of_mixer_poll_ended(void)
{
    mixer_reap_ended_pending();
    uint32_t irq = mixer_irq_save_local();
    uint32_t ended = ended_legacy_latch;
    ended_legacy_latch = 0;
    mixer_irq_restore_local(irq);
    return ended;
}

uint32_t of_mixer_poll_ended_h(of_mixer_handle_t *out_handles,
                               uint32_t max_handles)
{
    mixer_reap_ended_pending();

    uint32_t irq = mixer_irq_save_local();
    if (!out_handles || max_handles == 0) {
        uint32_t pending = ended_count;
        mixer_irq_restore_local(irq);
        return pending;
    }

    uint32_t copied = 0;
    while (copied < max_handles && ended_count > 0) {
        out_handles[copied++] = ended_queue[ended_tail];
        ended_tail = (uint8_t)((ended_tail + 1u) & (MIXER_ENDED_QUEUE_SIZE - 1u));
        ended_count--;
    }
    mixer_irq_restore_local(irq);
    return copied;
}

/* Update the packed voice→group mapping in HW.  32 voices × 2 bits split
 * across MIX_VOICE_GROUP_LO (voices 0..15) and MIX_VOICE_GROUP_HI (16..31).
 * Read-modify-write through the local shadow so per-voice updates don't
 * stomp other voices' group assignments. */
static void write_voice_group_packed(void)
{
    uint32_t lo = 0, hi = 0;
    for (int i = 0; i < 16; i++)
        lo |= ((uint32_t)(group_shadow[i] & 0x3)) << (i * 2);
    for (int i = 16; i < 32; i++)
        hi |= ((uint32_t)(group_shadow[i] & 0x3)) << ((i - 16) * 2);
    MIX_VOICE_GROUP_LO = lo;
    MIX_VOICE_GROUP_HI = hi;
}

void of_mixer_set_group(int voice, int group)
{
    if (!voice_in_range(voice)) return;
    if (group < 0 || group >= MIXER_NUM_GROUPS) return;
    group_shadow[voice] = (uint8_t)group;
    write_voice_group_packed();
}

/* O(1): single MMIO write — HW composes group_vol × master_vol × per-voice
 * vol_target inside audio_mixer's S_WR_VOL stage every sample. */
void of_mixer_set_group_volume(int group, int volume)
{
    if (group < 0 || group >= MIXER_NUM_GROUPS) return;
    MIX_GROUP_VOL(group) = volume & 0xFF;
}

/* O(1): single MMIO write.  Same composition path as set_group_volume. */
void of_mixer_set_master_volume(int volume)
{
    MIX_MASTER_VOL = volume & 0xFF;
}

/* Filter surface — the HW mixer v2 doesn't implement per-voice SVF; kept
 * as a no-op so older app binaries link cleanly. */
void of_mixer_set_filter(int voice, int cutoff_q016, int q, int enable)
{
    (void)voice; (void)cutoff_q016; (void)q; (void)enable;
}

void of_mixer_set_filter_h(of_mixer_handle_t handle,
                           int cutoff_q016,
                           int q,
                           int enable)
{
    (void)handle; (void)cutoff_q016; (void)q; (void)enable;
}

#define MIXER_LEGACY_ALLOC_SLOTS 64
typedef struct {
    void *ptr;
    uint32_t size;
} mixer_legacy_alloc_t;
static mixer_legacy_alloc_t legacy_allocs[MIXER_LEGACY_ALLOC_SLOTS];

/* Legacy allocator service.  The mixer can read any SDRAM buffer now,
 * so this ABI is backed by app mmap memory instead of a hidden fixed
 * pool. */
void *of_mixer_alloc_samples(uint32_t size)
{
    if (size > UINT32_MAX - 4095u)
        return (void *)0;
    size = (size + 4095u) & ~4095u;
    if (!size)
        return (void *)0;

    int slot = -1;
    for (int i = 0; i < MIXER_LEGACY_ALLOC_SLOTS; i++) {
        if (!legacy_allocs[i].ptr) {
            slot = i;
            break;
        }
    }
    if (slot < 0)
        return (void *)0;

    void *ptr = syscall_alloc_app_mmap(size);
    if (!ptr)
        return (void *)0;
    legacy_allocs[slot].ptr = ptr;
    legacy_allocs[slot].size = size;
    return ptr;
}

void of_mixer_free_samples(void)
{
    for (int i = 0; i < MIXER_LEGACY_ALLOC_SLOTS; i++) {
        if (!legacy_allocs[i].ptr)
            continue;
        syscall_free_app_mmap(legacy_allocs[i].ptr, legacy_allocs[i].size);
        legacy_allocs[i].ptr = (void *)0;
        legacy_allocs[i].size = 0;
    }
}

long of_mixer_dispatch(long fid, long a0, long a1,
                       long a2, long a3, long a4)
{
    switch (fid) {
    case OF_MIXER_FID_INIT:
        of_mixer_init((int)a0, (int)a1);
        return 0;
    case OF_MIXER_FID_PLAY:
        return of_mixer_play((const uint8_t *)a0, (uint32_t)a1,
                             (uint32_t)a2, (int)a3, (int)a4);
    case OF_MIXER_FID_STOP:
        of_mixer_stop((int)a0);
        return 0;
    case OF_MIXER_FID_STOP_ALL:
        of_mixer_stop_all();
        return 0;
    case OF_MIXER_FID_SET_VOLUME:
        of_mixer_set_volume((int)a0, (int)a1);
        return 0;
    case OF_MIXER_FID_PUMP:
        of_mixer_pump();
        return 0;
    case OF_MIXER_FID_VOICE_ACTIVE:
        return of_mixer_voice_active((int)a0);
    case OF_MIXER_FID_SET_PAN:
        of_mixer_set_pan((int)a0, (int)a1);
        return 0;
    case OF_MIXER_FID_SET_LOOP:
        of_mixer_set_loop((int)a0, (int)a1, (int)a2);
        return 0;
    case OF_MIXER_FID_SET_RATE:
        of_mixer_set_rate((int)a0, (int)a1);
        return 0;
    case OF_MIXER_FID_SET_VOL_LR:
        of_mixer_set_vol_lr((int)a0, (int)a1, (int)a2);
        return 0;
    case OF_MIXER_FID_SET_BIDI:
        of_mixer_set_bidi((int)a0, (int)a1);
        return 0;
    case OF_MIXER_FID_GET_POSITION:
        return of_mixer_get_position((int)a0);
    case OF_MIXER_FID_SET_POSITION:
        of_mixer_set_position((int)a0, (int)a1);
        return 0;
    case OF_MIXER_FID_SET_VOICE:
        of_mixer_set_voice((int)a0, (int)a1, (int)a2, (int)a3);
        return 0;
    case OF_MIXER_FID_ALLOC_SAMPLES:
        return (long)of_mixer_alloc_samples((uint32_t)a0);
    case OF_MIXER_FID_FREE_SAMPLES:
        of_mixer_free_samples();
        return 0;
    case OF_MIXER_FID_SET_RATE_RAW:
        of_mixer_set_rate_raw((int)a0, (uint32_t)a1);
        return 0;
    case OF_MIXER_FID_SET_VOICE_RAW:
        of_mixer_set_voice_raw((int)a0, (uint32_t)a1, (int)a2, (int)a3);
        return 0;
    case OF_MIXER_FID_SET_VOLUME_RAMP:
        of_mixer_set_volume_ramp((int)a0, (int)a1);
        return 0;
    case OF_MIXER_FID_POLL_ENDED:
        return (long)of_mixer_poll_ended();
    case OF_MIXER_FID_SET_END_CALLBACK:
        of_irq_register_mixer_end((void (*)(uint32_t))a0);
        return 0;
    case OF_MIXER_FID_RETRIGGER:
        of_mixer_retrigger((int)a0, (const uint8_t *)a1,
                           (uint32_t)a2, (uint32_t)a3, (int)a4);
        return 0;
    case OF_MIXER_FID_PLAY_8BIT:
        return of_mixer_play_8bit((const uint8_t *)a0, (uint32_t)a1,
                                  (uint32_t)a2, (int)a3, (int)a4);
    case OF_MIXER_FID_SET_GROUP:
        of_mixer_set_group((int)a0, (int)a1);
        return 0;
    case OF_MIXER_FID_SET_GROUP_VOLUME:
        of_mixer_set_group_volume((int)a0, (int)a1);
        return 0;
    case OF_MIXER_FID_SET_MASTER_VOLUME:
        of_mixer_set_master_volume((int)a0);
        return 0;
    default:
        return OF_ERR_NOT_SUPPORTED;
    }
}
